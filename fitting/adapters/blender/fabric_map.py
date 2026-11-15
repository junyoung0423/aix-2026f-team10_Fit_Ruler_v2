"""원단 정보 → 천 물성 매핑 (#8, C4). 표는 `config/drape/fabric_map_v0.yaml`([제안] 값).

착장 기본 설정(`drape_v1.json`)은 고치지 않는다. `derive_config`가 기본 설정 사본에 물성만 바꾼
파생 설정을
만들고(`tools/make_drape_config.py`와 같은 기록: name·version·status·parent·changes), 바뀐 것이
없으면
None을 돌려 기본 설정을 그대로 쓰게 한다. 검사 기준(thresholds)은 건드리지 않는다.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from fitting.domain import FabricInfo

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FABRIC_MAP = REPO_ROOT / "config" / "drape" / "fabric_map_v0.yaml"
STIFFNESS_KEYS = ("tension_stiffness", "compression_stiffness", "shear_stiffness")
DENSITY_KEY = "areal_density_kg_m2"  # drape 설정의 weld 블록


class FabricMapError(ValueError):
    """매핑 표 형식이 틀렸다."""


@dataclass(frozen=True)
class FabricMap:
    version: str
    stretch: dict[str, float]  # "default" 포함
    density: dict[str, float]  # "default" 포함
    sha256: str
    path: str


@dataclass(frozen=True)
class FabricProperties:
    """의류 1벌에 쓴 물성 값과 경고."""

    stiffness_multiplier: float
    areal_density_kg_m2: float
    stretch_value: str | None
    density_source: str  # composition / first_fiber / default
    warnings: list[str] = field(default_factory=list)


def _table(data: Any, key: str) -> dict[str, float]:
    table = data.get(key) if isinstance(data, dict) else None
    if not isinstance(table, dict) or "default" not in table:
        raise FabricMapError(f"{key}: default가 있는 표여야 합니다")
    out = {}
    for k, v in table.items():
        if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v) or v <= 0:
            raise FabricMapError(f"{key}.{k}: 0보다 큰 숫자여야 합니다 ({v!r})")
        out[str(k)] = float(v)
    return out


def load_fabric_map(path: Path | str = DEFAULT_FABRIC_MAP) -> FabricMap:
    path = Path(path)
    raw = path.read_bytes()
    data = yaml.safe_load(raw.decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("version"), str):
        raise FabricMapError(f"{path.name}: version이 없습니다")
    return FabricMap(
        version=data["version"],
        stretch=_table(data, "stretch_stiffness_multiplier"),
        density=_table(data, "fiber_areal_density_kg_m2"),
        sha256=hashlib.sha256(raw).hexdigest(),
        path=str(path),
    )


def properties_for(fabric: FabricInfo | None, table: FabricMap) -> FabricProperties:
    """원단 정보 → 물성. 정보가 없으면 기본값(배율 1.0), 모르는 값은 기본값 + 경고."""
    warnings: list[str] = []
    stretch = fabric.stretch if fabric is not None else None
    if stretch is None:
        mult = table.stretch["default"]
    elif stretch in table.stretch and stretch != "default":
        mult = table.stretch[stretch]
    else:
        mult = table.stretch["default"]
        warnings.append(f"모르는 신축성 값 {stretch!r}: 기본 배율 {mult}")

    density, source = table.density["default"], "default"
    if fabric is not None and fabric.composition:
        total, weighted, unknown = 0.0, 0.0, []
        for fiber, pct in fabric.composition.items():
            if fiber not in table.density or fiber == "default":
                unknown.append(fiber)
            total += pct
            weighted += pct * table.density.get(fiber, table.density["default"])
        density, source = weighted / total, "composition"
        if unknown:
            warnings.append(f"모르는 섬유 {unknown}: 기본 면밀도로 계산")
    elif fabric is not None and fabric.fibers:
        first = fabric.fibers[0]
        if first in table.density and first != "default":
            density, source = table.density[first], "first_fiber"
        else:
            warnings.append(f"모르는 섬유 {first!r}: 기본 면밀도 {density}")
    return FabricProperties(
        stiffness_multiplier=mult,
        areal_density_kg_m2=round(density, 6),
        stretch_value=stretch,
        density_source=source,
        warnings=warnings,
    )


def derive_config(
    base_cfg: dict[str, Any], base_file: str, props: FabricProperties, table: FabricMap
) -> dict[str, Any] | None:
    """기본 설정에서 물성만 바꾼 파생 설정. 바뀌는 값이 없으면 None(기본 설정을 그대로 쓴다)."""
    changes: list[str] = []
    cfg = copy.deepcopy(base_cfg)
    if props.stiffness_multiplier != 1.0:
        for key in STIFFNESS_KEYS:
            value = round(base_cfg["cloth"][key] * props.stiffness_multiplier, 6)
            cfg["cloth"][key] = value
            changes.append(f"cloth.{key}={json.dumps(value)}")
    if "weld" in cfg and DENSITY_KEY in cfg["weld"]:
        if abs(cfg["weld"][DENSITY_KEY] - props.areal_density_kg_m2) > 1e-12:
            cfg["weld"][DENSITY_KEY] = props.areal_density_kg_m2
            changes.append(f"weld.{DENSITY_KEY}={json.dumps(props.areal_density_kg_m2)}")
    if not changes:
        return None
    cfg.update(
        name=f"{base_cfg['name']}_fabric",
        version=f"{base_cfg['version']}+{table.version}",
        status="derived",
        description=(
            f"{base_cfg['name']} {base_cfg['version']}에 원단 매핑 {table.version}"
            f"(sha256 {table.sha256[:12]})을 적용한 파생 설정. 검사 기준은 그대로다"
        ),
        parent={"name": base_cfg["name"], "version": base_cfg["version"], "file": base_file},
        changes=changes,
    )
    return cfg


def write_config(cfg: dict[str, Any], path: Path) -> str:
    """파생 설정을 쓰고 sha256을 돌려준다(make_drape_config와 같은 바이트 형식)."""
    data = (json.dumps(cfg, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()
