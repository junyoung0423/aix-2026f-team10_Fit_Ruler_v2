"""견본집 생성 명령 (#4) 중 GarmentCode 없이 확인할 수 있는 부분: 출력 폴더 보호, 설정 오류."""

from pathlib import Path

import pytest
import yaml

pytest.importorskip("numpy", reason="garment 선택 의존성 필요: pip install -e .[garment]")
pytest.importorskip("scipy", reason="garment 선택 의존성 필요: pip install -e .[garment]")
pytest.importorskip("svgpathtools", reason="garment 선택 의존성 필요: pip install -e .[garment]")

from tools.build_sample_catalog import (  # noqa: E402
    DEFAULT_CONFIG,
    EXIT_CONFIG,
    EXIT_FAILED,
    failure_reason,
    main,
)


def test_existing_output_is_not_overwritten(tmp_path, capsys):
    out = tmp_path / "tee_gc_v1"
    out.mkdir()
    (out / "keep.txt").write_text("지우면 안 된다", encoding="utf-8")
    assert main(["--out", str(out)]) == EXIT_FAILED
    assert "덮어쓰지 않습니다" in capsys.readouterr().err
    assert (out / "keep.txt").read_text(encoding="utf-8") == "지우면 안 된다"


def test_config_error_stops_before_generation(tmp_path, capsys):
    data = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    data["samples"][0]["style"] = "regular"  # 가슴 100 cm는 slim 구간
    config = tmp_path / "bad.yaml"
    config.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    out = tmp_path / "out"
    assert main(["--config", str(config), "--out", str(out)]) == EXIT_CONFIG
    assert "설정 오류" in capsys.readouterr().err
    assert not out.exists()


def test_failure_reason_lists_fields_out_of_tolerance():
    res = {
        "measurements": {
            "chest_girth_cm": {"target": 140.0, "pattern": 130.2, "error": -9.8, "ok": False},
            "shoulder_width_cm": {"target": None, "pattern": 34.5, "error": None, "ok": None},
            "sleeve_length_cm": {"target": 22.0, "pattern": 22.0, "error": 0.0, "ok": True},
        },
        "self_intersecting": False,
        "params_at_bound": {"shirt.width": True, "sleeve.length": False},
    }
    reason = failure_reason(res)
    assert "chest_girth_cm 130.2 (목표 140.0, 오차 -9.800)" in reason
    assert "sleeve" not in reason
    assert "범위 끝에 닿은 파라미터: shirt.width" in reason


def test_default_config_path_exists():
    assert Path(DEFAULT_CONFIG).is_file()
