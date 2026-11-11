"""run_drape의 출력 루트 결정: --out-root > 환경변수 FIT_RULER_RUNS_DIR > 없으면 멈춤.

Blender·GarmentCode는 부르지 않는다. run_drape는 표준 라이브러리만 import하므로
garment 의존성도 필요 없다.
"""
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
