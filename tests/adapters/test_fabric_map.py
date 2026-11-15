"""원단 → 천 물성 매핑 (#8). 표 값은 [제안]이라 값 자체보다 규칙을 확인한다."""

import json
from pathlib import Path

import pytest

from fitting.adapters.aihub.tables import AIHUB_FIBER, AIHUB_STRETCH
from fitting.adapters.blender import fabric_map as fm
from fitting.domain import FabricInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
DRAPE_V1 = REPO_ROOT / "config" / "drape" / "drape_v1.json"


@pytest.fixture(scope="module")
def table():
    return fm.load_fabric_map()


def test_table_covers_every_aihub_value(table):
    assert AIHUB_STRETCH <= set(table.stretch)
    assert {key for key, _ in AIHUB_FIBER.values()} <= set(table.density)


def test_default_density_and_contain_little_match_drape_v1(table):
    base = json.loads(DRAPE_V1.read_text(encoding="utf-8"))
    assert table.density["default"] == base["weld"]["areal_density_kg_m2"]
    assert table.stretch["default"] == table.stretch["contain little"] == 1.0


def test_no_fabric_means_drape_v1(table):
    props = fm.properties_for(None, table)
    assert (props.stiffness_multiplier, props.areal_density_kg_m2) == (1.0, 0.18)
    assert props.warnings == []
    base = json.loads(DRAPE_V1.read_text(encoding="utf-8"))
    assert fm.derive_config(base, "drape_v1.json", props, table) is None


def test_stretchier_fabric_is_softer(table):
    order = ["none at all", "none", "contain little", "contain", "contain a lot"]
    values = [fm.properties_for(FabricInfo(stretch=s), table).stiffness_multiplier for s in order]
    assert values == sorted(values, reverse=True)


def test_unknown_values_use_defaults_with_warnings(table):
    props = fm.properties_for(FabricInfo(fibers=["mystery"], stretch="bouncy"), table)
    assert props.stiffness_multiplier == 1.0 and props.areal_density_kg_m2 == 0.18
    assert len(props.warnings) == 2


def test_first_fiber_without_composition(table):
    props = fm.properties_for(FabricInfo(fibers=["polyester", "cotton"]), table)
    assert props.areal_density_kg_m2 == 0.15 and props.density_source == "first_fiber"


def test_composition_is_weighted(table):
    info = FabricInfo(fibers=["cotton", "polyester"], composition={"cotton": 60, "polyester": 40})
    props = fm.properties_for(info, table)
    assert props.areal_density_kg_m2 == pytest.approx(0.6 * 0.18 + 0.4 * 0.15)
    assert props.density_source == "composition"


def test_derived_config_changes_only_material(table, tmp_path):
    base = json.loads(DRAPE_V1.read_text(encoding="utf-8"))
    props = fm.properties_for(FabricInfo(fibers=["nylon"], stretch="contain a lot"), table)
    cfg = fm.derive_config(base, "drape_v1.json", props, table)
    assert cfg["parent"] == {"name": "drape_v1", "version": "1.0.0", "file": "drape_v1.json"}
    assert cfg["cloth"]["shear_stiffness"] == pytest.approx(9.0)
    assert cfg["weld"]["areal_density_kg_m2"] == 0.14
    for block in ("thresholds", "time_limits_s", "schedule", "cloth_collision", "body_collision"):
        assert cfg[block] == base[block]
    assert base["cloth"]["tension_stiffness"] == 15.0  # 원본 dict를 바꾸지 않는다
    sha = fm.write_config(cfg, tmp_path / "derived.json")
    assert len(sha) == 64
    from fitting.adapters.blender.drape_config import load_config

    loaded, rec = load_config(tmp_path / "derived.json")
    assert rec["sha256"] == sha and loaded["name"] == "drape_v1_fabric"


@pytest.mark.parametrize("bad", ["stretch_stiffness_multiplier: {none: 1}", "version: 3"])
def test_broken_table_is_rejected(tmp_path, bad):
    p = tmp_path / "map.yaml"
    p.write_text("version: x\nfiber_areal_density_kg_m2: {default: 0.18}\n" + bad, encoding="utf-8")
    with pytest.raises(fm.FabricMapError):
        fm.load_fabric_map(p)
