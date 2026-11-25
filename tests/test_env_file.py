"""`.env` 읽기(#13, B6)와 명령 출력 UTF-8(설치 점검 보고서 3번)."""

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

import server.app as app_module
from server.env_file import (
    DEFAULT_ENV_FILE,
    ENV_FILE_ENV,
    EnvFileResult,
    env_file_path,
    load_env_file,
    parse_env_lines,
)
from tools.console import use_utf8_output

REPO_ROOT = Path(__file__).resolve().parents[1]
SECRET = "fal-SECRET-do-not-print-5678"


def _write(tmp_path, text: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


# --- 읽기 규칙 -----------------------------------------------------------------------------------


def test_fills_only_missing_or_empty_variables(tmp_path):
    path = _write(tmp_path, "A=from_file\nB=from_file\nC=from_file\n")
    environ = {"A": "from_env", "B": "  "}

    result = load_env_file(path, environ=environ)

    assert environ == {"A": "from_env", "B": "from_file", "C": "from_file"}  # 환경변수가 우선
    assert result.loaded == ("B", "C")
    assert result.kept == ("A",)
    assert result.found and not result.disabled


def test_empty_values_are_not_set(tmp_path):
    """`.env.example`을 그대로 복사해도 아무것도 바뀌지 않는다."""
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    path = _write(tmp_path, example)
    environ: dict[str, str] = {}

    result = load_env_file(path, environ=environ)

    assert environ == {}
    assert result.loaded == ()
    assert result.ignored_lines == ()  # 예시 파일은 형식이 맞다


def test_comments_quotes_export_and_bad_lines(tmp_path):
    text = (
        "# 주석\n"
        "\n"
        'BLENDER_PATH="C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe"\n'
        "export SMPL_MODEL_DIR='models/smpl'\n"
        "FIT_WORK_DIR = C:\\dev\\work \n"
        "이건 틀린 줄\n"
        "1BAD=x\n"
        "URL=http://a/b?c=d\n"
    )
    environ: dict[str, str] = {}

    result = load_env_file(_write(tmp_path, text), environ=environ)

    assert environ == {
        "BLENDER_PATH": r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
        "SMPL_MODEL_DIR": "models/smpl",
        "FIT_WORK_DIR": r"C:\dev\work",
        "URL": "http://a/b?c=d",  # 첫 '='에서만 나눈다
    }
    assert result.ignored_lines == (6, 7)


def test_inline_comments_are_dropped_from_unquoted_values(tmp_path):
    text = (
        "FIT_MAX_WORKERS=2  # 동시 작업 수\n"
        "FIT_RULER_GC_ROOT=  # 비우면 기본값\n"
        "FIT_NOTE=a#b\n"
        'FIT_QUOTED="x #y"\n'
    )
    environ: dict[str, str] = {}

    load_env_file(_write(tmp_path, text), environ=environ)

    assert environ == {"FIT_MAX_WORKERS": "2", "FIT_NOTE": "a#b", "FIT_QUOTED": "x #y"}


def test_utf8_bom_is_ignored(tmp_path):
    path = tmp_path / ".env"
    path.write_bytes("\ufeffFIT_X=1\n".encode())  # 메모장이 BOM을 붙여 저장한 경우
    environ: dict[str, str] = {}

    load_env_file(path, environ=environ)

    assert environ == {"FIT_X": "1"}


def test_missing_file_changes_nothing(tmp_path):
    environ = {"A": "1"}
    result = load_env_file(tmp_path / "없음.env", environ=environ)

    assert environ == {"A": "1"}
    assert not result.found and not result.disabled


def test_result_has_names_but_never_values(tmp_path):
    result = load_env_file(_write(tmp_path, f"FAL_KEY={SECRET}\n"), environ={})

    assert result.loaded == ("FAL_KEY",)
    assert SECRET not in repr(result)


def test_parse_keeps_later_duplicates_out(tmp_path):
    environ: dict[str, str] = {}
    result = load_env_file(_write(tmp_path, "A=first\nA=second\n"), environ=environ)

    assert environ == {"A": "first"}
    assert result.kept == ("A",)
    assert parse_env_lines("A=1\nA=2")[0] == [("A", "1"), ("A", "2")]


# --- 어느 파일을 읽나 ------------------------------------------------------------------------


def test_env_file_path_choice(tmp_path):
    assert env_file_path({}) == DEFAULT_ENV_FILE
    assert DEFAULT_ENV_FILE == REPO_ROOT / ".env"
    assert env_file_path({ENV_FILE_ENV: " "}) == DEFAULT_ENV_FILE
    assert env_file_path({ENV_FILE_ENV: str(tmp_path / "x.env")}) == tmp_path / "x.env"
    assert env_file_path({ENV_FILE_ENV: "OFF"}) is None


def test_off_disables_reading(tmp_path):
    environ = {ENV_FILE_ENV: "off"}
    result = load_env_file(environ=environ)

    assert result == EnvFileResult(path=None, found=False)
    assert result.disabled


def test_tests_never_read_the_developer_env_file():
    """conftest가 `FIT_ENV_FILE=off`로 둔다. 개발자 `.env`가 테스트 결과를 바꾸지 않는다."""
    assert os.environ[ENV_FILE_ENV] == "off"
    assert load_env_file().disabled


# --- 서버와 명령이 읽는다 --------------------------------------------------------------------


def test_server_app_reads_env_file_before_creating_the_app(tmp_path, monkeypatch):
    path = _write(tmp_path, "FIT_B6_ENV_PROBE=from_env_file\n")
    monkeypatch.setenv(ENV_FILE_ENV, str(path))
    monkeypatch.setenv("FIT_B6_ENV_PROBE", "")  # 비어 있으면 .env 값을 쓴다(끝나면 되돌린다)
    seen = []
    monkeypatch.setattr(
        app_module, "create_app", lambda: seen.append(os.environ["FIT_B6_ENV_PROBE"]) or object()
    )
    app_module.__dict__.pop("app", None)
    try:
        app_module.app  # noqa: B018
    finally:
        app_module.__dict__.pop("app", None)

    assert seen == ["from_env_file"]


def test_create_app_alone_does_not_read_env_file(tmp_path, monkeypatch):
    path = _write(tmp_path, "FIT_B6_ENV_PROBE=from_env_file\n")
    monkeypatch.setenv(ENV_FILE_ENV, str(path))
    monkeypatch.setenv("FIT_B6_ENV_PROBE", "")

    app_module.create_app(garments_dir=tmp_path, artifacts_dir=tmp_path / "files")

    assert os.environ["FIT_B6_ENV_PROBE"] == ""


def test_run_fitting_once_command_reads_env_file(tmp_path):
    """명령으로 실행하면 `.env`를 읽는다. `.env`의 틀린 실패 처리 설정으로 시작을 멈추는지 본다."""
    path = _write(tmp_path, f"FIT_PIPELINE_CONFIG={tmp_path / 'no_such_pipeline.yaml'}\n")
    env = {k: v for k, v in os.environ.items() if k != "FIT_PIPELINE_CONFIG"}
    env[ENV_FILE_ENV] = str(path)
    done = subprocess.run(
        [sys.executable, "-m", "tools.run_fitting_once", "--out", str(tmp_path / "run")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )

    assert done.returncode == 1, done.stdout + done.stderr
    assert "no_such_pipeline.yaml" in done.stderr


# --- 출력 인코딩 ---------------------------------------------------------------------------------


def _stream(encoding: str) -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding=encoding, newline="\n")


def test_cp949_stream_becomes_utf8():
    stream = _stream("cp949")
    with pytest.raises(UnicodeEncodeError):
        stream.write("—")
        stream.flush()

    stream = _stream("cp949")
    use_utf8_output([stream])
    stream.write("핏 리포트 — 통과 × 1\n")
    stream.flush()

    assert stream.encoding == "utf-8"
    assert stream.buffer.getvalue().decode("utf-8") == "핏 리포트 — 통과 × 1\n"


def test_utf8_and_odd_streams_are_left_alone():
    utf8 = _stream("UTF-8")
    plain = io.StringIO()  # reconfigure 없음
    use_utf8_output([utf8, plain, None])

    assert utf8.encoding == "UTF-8"


@pytest.mark.parametrize(
    "module", ["tools.run_fitting_once", "tools.search_sample", "tools.prepare_garments"]
)
def test_tools_write_utf8_when_the_pipe_is_cp949(module):
    """`--help`(한국어 설명)를 cp949 파이프로 받아도 UTF-8로 나온다."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env.update(PYTHONIOENCODING="cp949", PYTHONUTF8="0")
    done = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        env=env,
        check=False,
    )

    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    done.stdout.decode("utf-8")  # cp949였다면 한국어에서 UnicodeDecodeError
    assert any(ord(ch) > 0xAC00 for ch in done.stdout.decode("utf-8"))  # 한국어가 있다
