"""pattern_fit의 GarmentCode 없이 도는 부분 (#4): 설정 경로·맞춤 설정 값·디자인 값 쓰기."""

from pathlib import Path

import pytest

pytest.importorskip("numpy", reason="garment 선택 의존성 필요: pip install -e .[garment]")
pytest.importorskip("scipy", reason="garment 선택 의존성 필요: pip install -e .[garment]")
pytest.importorskip("svgpathtools", reason="garment 선택 의존성 필요: pip install -e .[garment]")

import yaml  # noqa: E402

from fitting.adapters.garmentcode import pattern_fit as pf  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_relative_paths_resolve_against_gc_root(tmp_path):
    cfg = {"body_yaml": "assets/bodies/mean_all.yaml", "design_yaml": str(tmp_path / "d.yaml")}
    out = pf.resolve_config(cfg, gc_root=tmp_path / "gc")
    assert Path(out["body_yaml"]) == (tmp_path / "gc" / "assets" / "bodies" / "mean_all.yaml")
    assert Path(out["design_yaml"]) == tmp_path / "d.yaml"  # 절대경로는 그대로


@pytest.mark.parametrize("key", ["body_yaml", "design_yaml"])
def test_missing_path_is_an_error(key):
    cfg = {"body_yaml": "a.yaml", "design_yaml": "b.yaml"}
    cfg[key] = ""
    with pytest.raises(ValueError, match=key):
        pf.resolve_config(cfg)


def test_committed_fit_config_keeps_previous_values(tmp_path):
    """옮기면서 맞춤 값을 바꾸지 않았다(이전 pattern_fit_v0 0.1.2와 같다)."""
    cfg, rec = pf.load_fit_config(pf.DEFAULT_FIT_CONFIG, gc_root=tmp_path)
    assert rec["path"] == "config/pattern/pattern_fit_v0.json"
    assert (rec["name"], rec["version"]) == ("pattern_fit_v0", "0.2.0")
    assert cfg["tolerance_cm"] == 1.0
    assert cfg["fit_params"] == {
        "shirt.width": "chest_girth_cm",
        "shirt.length": "body_length_cm",
        "sleeve.length": "sleeve_length_cm",
    }
    assert cfg["diff_step"] == 1e-4
    assert cfg["max_nfev"] == 60
    assert "targets_json" not in cfg  # 이전 저장소에만 있는 경로는 쓰지 않는다
    assert Path(cfg["body_yaml"]) == tmp_path / "assets" / "bodies" / "mean_all.yaml"


def test_set_value_keeps_type():
    design = {"shirt": {"width": {"v": 1.05, "type": "float"}}, "n": {"k": {"v": 2, "type": "int"}}}
    pf.set_value(design, "shirt.width", 1.1)
    pf.set_value(design, "n.k", 2.6)
    assert pf.get_param(design, "shirt.width")["v"] == 1.1
    assert pf.get_param(design, "n.k")["v"] == 3


def test_design_yaml_round_trip_is_exact():
    """design.yaml로 새 프로세스에서 다시 만들 때 값이 비트 단위로 같아야 한다."""
    design = {"shirt": {"width": {"v": 1.0817236315022316, "range": [1.0, 1.3], "type": "float"}}}
    back = yaml.safe_load(pf.design_yaml_text(design))["design"]
    assert back == design


def test_normalize_targets_fills_missing_with_none():
    assert pf.normalize_targets({"chest_girth_cm": 100.0}) == {
        "chest_girth_cm": 100.0,
        "shoulder_width_cm": None,
        "sleeve_length_cm": None,
        "body_length_cm": None,
    }
