"""착장 점검 실행 (D4, #8): 몸·옷 메시 → 관통 측정 → 판정 → 기록 JSON.

C4 착장 어댑터는 Blender 안에서 같은 관통 정의(C2 `metrics`)로 이미 판정한다. 이 점검은 그 결과를
Blender 없는 PC·CI에서 다시 확인할 때 쓴다(`tools.check_drape --c2-run`). 착장 흐름 안에서 부를 때는
결과가 통과가 아니면 `DRAPE_INVALID`로 실패한다. `metrics()`는 포트 지표에, `to_record()`는
기록에 쓴다.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from fitting.verify.drape_check import (
    DrapeCheckResult,
    DrapeCheckThresholds,
    judge_drape,
    summarize_penetration,
)

from .geometry import face_centers, is_closed, read_obj, signed_distance, triangle_areas

RECORD_NAME = "drape_check.json"
METHOD = "kd-tree(k=8) + 삼각형 위 가장 가까운 점, 부호는 보간한 정점 법선(바깥 +)"


@dataclass(frozen=True)
class MeshInput:
    vertices: np.ndarray
    faces: np.ndarray
    source: str = ""


def check_drape(
    body: MeshInput,
    garment: MeshInput,
    thresholds: DrapeCheckThresholds,
    *,
    k: int = 8,
    worst: int = 5,
) -> tuple[DrapeCheckResult, np.ndarray]:
    """몸과 착장된 옷 메시로 관통을 재고 판정한다. (결과, 옷 면별 부호 거리)를 돌려준다."""
    body_v = np.asarray(body.vertices, dtype=np.float64)
    body_f = np.asarray(body.faces, dtype=np.int64)
    garment_v = np.asarray(garment.vertices, dtype=np.float64)
    garment_f = np.asarray(garment.faces, dtype=np.int64)

    garment_finite = bool(np.all(np.isfinite(garment_v)))
    body_closed = is_closed(body_f) and bool(np.all(np.isfinite(body_v)))
    centers = face_centers(garment_v, garment_f)
    areas = np.nan_to_num(triangle_areas(garment_v, garment_f), nan=0.0, posinf=0.0)
    signed, _ = signed_distance(centers, body_v, body_f, k=k)
    summary = summarize_penetration(areas, signed, thresholds.penetration_depth_m)

    order = np.argsort(np.nan_to_num(signed, nan=np.inf))[:worst]
    deepest = [
        {
            "face": int(i),
            "depth_mm": round(float(-signed[i]) * 1000, 2),
            "point_m": [round(float(x), 4) for x in centers[i]],
        }
        for i in order
        if np.isfinite(signed[i]) and signed[i] < 0
    ]
    details = {
        "method": METHOD,
        "body": {
            "source": body.source,
            "vertices": int(len(body_v)),
            "faces": int(len(body_f)),
        },
        "garment": {
            "source": garment.source,
            "vertices": int(len(garment_v)),
            "faces": int(len(garment_f)),
        },
        "deepest": deepest,
    }
    result = judge_drape(
        summary,
        thresholds,
        body_closed=body_closed,
        garment_finite=garment_finite,
        details=details,
    )
    return result, signed


def load_thresholds(config_path: Path) -> tuple[DrapeCheckThresholds, dict[str, Any]]:
    """착장 설정 파일(`config/drape/drape_v*.json`)에서 기준값을 읽는다. (기준, 설정 기록)."""
    path = Path(config_path)
    config = json.loads(path.read_text(encoding="utf-8"))
    source = f"{config.get('name', path.stem)} v{config.get('version', '?')}"
    record = {"file": path.name, "name": config.get("name"), "version": config.get("version")}
    record["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return DrapeCheckThresholds.from_config(config, source=source), record


def load_obj(path: Path) -> MeshInput:
    vertices, faces = read_obj(path)
    return MeshInput(vertices=vertices, faces=faces, source=Path(path).name)


def load_c2_run(run_dir: Path) -> tuple[MeshInput, MeshInput, dict[str, Any] | None]:
    """C2 착장 실행 폴더(`drape_input.json`, `garment_draped_co.npy`)에서 몸·착장 옷을 읽는다.

    세 번째 값은 같은 폴더의 `drape_metrics.json`(Blender로 잰 C2 결과)이 있으면 그 내용이다.
    같은 메시를 Blender 없이 다시 재서 C2 결과와 비교하려고 쓴다.
    """
    run_dir = Path(run_dir)
    data = json.loads((run_dir / "drape_input.json").read_text(encoding="utf-8"))
    body = data["body"]
    garment = data["garment"]
    draped = np.load(run_dir / "garment_draped_co.npy")
    triangles = [face for face in garment["faces"] if len(face) == 3]
    metrics_path = run_dir / "drape_metrics.json"
    c2 = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else None
    return (
        MeshInput(np.asarray(body["vertices"]), np.asarray(body["faces"]), "drape_input.json:body"),
        MeshInput(draped, np.asarray(triangles), "garment_draped_co.npy"),
        c2,
    )


def write_record(
    out_dir: Path,
    result: DrapeCheckResult,
    *,
    config: dict[str, Any] | None = None,
    seconds: float | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """`out_dir/drape_check.json`에 결과를 쓴다(성공·실패 모두)."""
    record = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **result.to_record(),
        "config": config,
        "seconds": None if seconds is None else round(seconds, 2),
        **(extra or {}),
    }
    path = Path(out_dir) / RECORD_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def check_drape_files(
    body_path: Path,
    garment_path: Path,
    config_path: Path,
    out_dir: Path,
) -> tuple[DrapeCheckResult, Path]:
    """OBJ 두 개를 읽어 점검하고 기록을 남긴다."""
    started = time.monotonic()
    thresholds, config = load_thresholds(config_path)
    result, _ = check_drape(load_obj(body_path), load_obj(garment_path), thresholds)
    path = write_record(out_dir, result, config=config, seconds=time.monotonic() - started)
    return result, path
