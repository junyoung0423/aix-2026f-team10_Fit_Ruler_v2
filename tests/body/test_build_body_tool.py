"""tools.build_body를 가짜 몸 모델로 돌려 본다. 실제 SMPL 모델은 쓰지 않는다."""

import argparse
import json
from pathlib import Path

import pytest

pytest.importorskip("numpy")  # 선택 의존성 body가 없으면 건너뛴다
pytest.importorskip("scipy")

from fitting.adapters.smpl import SmplBodyBuilder  # noqa: E402
from tests.adapters.test_smpl_builder import FakePosedBody, unreachable_arm  # noqa: E402
from tools import build_body  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def test_cases_are_the_reference_bodies_in_decisions():
    decisions = (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
    for name in ("body1", "body2", "team_example"):
        values = [f"{value:g}" for value in build_body.CASES[name]]
        as_text, as_row = "/".join(values), "| " + " | ".join(values) + " |"
        assert as_text in decisions or as_row in decisions, f"{name} {as_text}가 1절에 없다"


def test_parse_body():
    assert build_body.parse_body("166, 97.5, 81, 36, 52") == (166.0, 97.5, 81.0, 36.0, 52.0)
    with pytest.raises(argparse.ArgumentTypeError):
        build_body.parse_body("175,100,80")
    with pytest.raises(argparse.ArgumentTypeError):
        build_body.parse_body("175,100,80,40,abc")


def test_missing_model_dir_is_a_setup_error(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SMPL_MODEL_DIR", raising=False)
    code = build_body.main(["--case", "body1", "--out-dir", str(tmp_path)])
    assert code == build_body.EXIT_SETUP
    assert "SMPL_MODEL_DIR" in capsys.readouterr().err


def _fake_factory(model):
    def make(settings):
        return SmplBodyBuilder(
            body_model=model,
            artifacts_dir=settings.artifacts_dir,
            work_dir=settings.work_dir,
            model_name="fake_neutral.npz",
        )

    return make


def test_passing_body_writes_mesh_and_record(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(build_body, "make_body_builder", _fake_factory(FakePosedBody()))
    code = build_body.main(["--case", "body1", "--out-dir", str(tmp_path), "--no-image"])

    assert code == build_body.EXIT_PASSED
    out = tmp_path / "body1"
    assert (out / "body1_body_mesh.obj").is_file()
    record = json.loads((out / "body_fit.json").read_text(encoding="utf-8"))
    assert record["passed"] is True
    assert record["input_cm"]["shoulder_cm"] == 40.0
    printed = capsys.readouterr().out
    assert "통과" in printed and "팔길이" in printed


def test_failing_body_exits_3_and_keeps_the_record(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(build_body, "make_body_builder", _fake_factory(unreachable_arm()))
    args = ["--body", "175,100,80,40,70", "--name", "long_arm", "--out-dir", str(tmp_path)]
    code = build_body.main([*args, "--no-image"])

    assert code == build_body.EXIT_NOT_CONVERGED
    out = tmp_path / "long_arm"
    assert not (out / "long_arm_body_mesh.obj").exists()
    assert json.loads((out / "body_fit.json").read_text(encoding="utf-8"))["passed"] is False
    assert "BODY_FIT_NOT_CONVERGED" in capsys.readouterr().out


def test_name_must_be_safe_for_file_names(tmp_path, capsys):
    code = build_body.main(["--case", "body1", "--name", "../x", "--out-dir", str(tmp_path)])
    assert code == build_body.EXIT_SETUP
