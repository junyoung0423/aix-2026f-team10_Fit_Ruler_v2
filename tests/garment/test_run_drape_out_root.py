"""run_drape의 출력 루트 결정: --out-root > 환경변수 FIT_RULER_RUNS_DIR > 없으면 멈춤.

Blender·GarmentCode는 부르지 않는다. run_drape는 표준 라이브러리만 import하므로
garment 의존성도 필요 없다.
"""
import json
import sys
from pathlib import Path

import pytest

from fitting.adapters.blender import run_drape


def test_arg_wins_over_env(tmp_path):
    env = {run_drape.RUNS_DIR_ENV: str(tmp_path / "from_env")}
    assert run_drape.resolve_out_root(str(tmp_path / "from_arg"), env) == tmp_path / "from_arg"


def test_env_used_when_no_arg(tmp_path):
    env = {run_drape.RUNS_DIR_ENV: str(tmp_path / "from_env")}
    assert run_drape.resolve_out_root(None, env) == tmp_path / "from_env"


@pytest.mark.parametrize("env", [{}, {"FIT_RULER_RUNS_DIR": ""}])
def test_none_when_neither(env):
    assert run_drape.resolve_out_root(None, env) is None


def test_default_reads_os_environ(tmp_path, monkeypatch):
    monkeypatch.setenv(run_drape.RUNS_DIR_ENV, str(tmp_path))
    assert run_drape.resolve_out_root(None) == Path(tmp_path)
    monkeypatch.delenv(run_drape.RUNS_DIR_ENV)
    assert run_drape.resolve_out_root(None) is None


def test_main_stops_without_out_root(tmp_path, monkeypatch):
    """둘 다 없으면 출력 폴더를 만들거나 하위 프로세스를 부르기 전에 0이 아닌 종료로 멈춘다."""
    monkeypatch.delenv(run_drape.RUNS_DIR_ENV, raising=False)
    monkeypatch.chdir(tmp_path)

    def fail(*args, **kwargs):
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(run_drape.subprocess, "run", fail)
    monkeypatch.setattr(sys, "argv", [
        "run_drape", "--spec", "spec.json", "--body", "body.obj",
        "--config", "cfg.json", "--name", "x",
    ])
    with pytest.raises(SystemExit) as exc:
        run_drape.main()
    assert exc.value.code not in (0, None)
    assert run_drape.RUNS_DIR_ENV in str(exc.value.code)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("skip", [True, False])
def test_skip_analyze_runs_export_and_drape_only(tmp_path, monkeypatch, skip):
    """--skip-analyze면 analyze를 부르지 않고, 판정은 그대로 Blender 지표로 한다."""
    repo = Path(run_drape.REPO_ROOT)
    spec, body = tmp_path / "spec.json", tmp_path / "body.obj"
    spec.write_text("{}", encoding="utf-8")
    body.write_text("v 0 0 0\n", encoding="utf-8")
    called = []

    def fake_step(name, cmd, log_path, timeout=None, **kw):
        called.append(name)
        out = Path(log_path).parent
        if name == "export":
            (out / "drape_input.json").write_text("{}", encoding="utf-8")
        if name == "drape":
            (out / "drape_metrics.json").write_text(
                '{"drape_ok": true, "failed_checks": []}', encoding="utf-8"
            )
        if name == "analyze":
            (out / "analysis.json").write_text('{"matches_blender_metrics": true}', "utf-8")
        return {"step": name, "returncode": 0, "timed_out": False}

    monkeypatch.setattr(run_drape, "run_step", fake_step)
    monkeypatch.setattr(run_drape, "git_head", lambda path: None)
    cfg = repo / "config" / "drape" / "drape_v1.json"
    argv = ["run_drape", "--spec", str(spec), "--body", str(body), "--config", str(cfg)]
    argv += ["--name", "x", "--out-root", str(tmp_path / "runs")]
    monkeypatch.setattr(sys, "argv", argv + (["--skip-analyze"] if skip else []))
    with pytest.raises(SystemExit) as exc:
        run_drape.main()
    assert exc.value.code == 0
    assert called == (["export", "drape"] if skip else ["export", "drape", "analyze"])
    summary = json.loads((tmp_path / "runs" / "x" / "run_summary.json").read_text("utf-8"))
    assert summary["status"] == "SUCCEEDED" and summary["analyze_skipped"] is skip
    assert ("offline_matches_blender" in summary) is not skip
