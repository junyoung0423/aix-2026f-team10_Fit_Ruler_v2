"""환경 점검 명령 (#13, B6): 고른 어댑터 설정에 필요한 것만 보고, 빠지면 종료 코드 1.

실제 Blender·GarmentCode·SMPL·fal 없이, 임시 폴더와 가짜 환경(사전)·가짜 버전 확인으로 시험한다.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from server.env_file import EnvFileResult
from server.wiring import AdapterConfig
from tools import check_env as ce

REPO_ROOT = Path(__file__).resolve().parents[1]
SECRET = "fal-SECRET-do-not-print-1234"
GARMENT = "tee_regular_m_navy_v1"
ALL_FAKE = {name: "fake" for name in ("body", "drape", "render", "realize", "validate")}
NO_ENV_FILE = EnvFileResult(path=None, found=False)


def _versions(blender="Blender 5.2.0", gc_python="Python 3.9.13", node="v24.17.0"):
    """명령별로 정해 둔 `--version` 첫 줄을 돌려주는 가짜 확인기."""

    def probe(command):
        exe = Path(command[0]).name.lower()
        if "blender" in exe:
            return blender
        if "node" in exe:
            return node
        return gc_python

    return probe


def _config(tmp_path, ports: dict, name="adapters.yaml") -> str:
    path = tmp_path / name
    data = {"version": "adapters_v0", "ports": {**ALL_FAKE, **ports}}
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return str(path)


def _run(adapters, environ, tmp_path, **overrides):
    """임시 저장소 루트(웹 패키지 있음)와 가짜 확인기로 점검한다."""
    repo = tmp_path / "repo"
    (repo / "web" / "node_modules").mkdir(parents=True, exist_ok=True)
    options = dict(
        env_file=NO_ENV_FILE,
        environ=environ,
        repo_root=repo,
        probe=_versions(),
        which=lambda name: f"/usr/bin/{name}",
        has_module=lambda name: True,
        version_info=(3, 12, 1),
        temp_dir="/tmp",
    )
    options.update(overrides)
    return ce.run_checks(adapters, **options)


def _row(report, item):
    rows = [check for check in report.checks if check.item == item]
    assert rows, f"{item} 행이 없습니다: {[c.item for c in report.checks]}"
    return rows[0]


def _items(report):
    return [check.item for check in report.checks]


def _base_env(tmp_path) -> dict:
    """착장·렌더·몸·실사화가 필요로 하는 것을 모두 갖춘 가짜 환경."""
    blender = tmp_path / "Blender 5.2" / "blender.exe"
    blender.parent.mkdir(parents=True)
    blender.write_text("")
    gc_python = tmp_path / "gcvenv" / "python.exe"
    gc_python.parent.mkdir()
    gc_python.write_text("")
    gc_root = tmp_path / "GarmentCode"
    (gc_root / "assets" / "garment_programs").mkdir(parents=True)
    prepared = tmp_path / "prepared"
    (prepared / GARMENT).mkdir(parents=True)
    (prepared / GARMENT / "prep.json").write_text(
        json.dumps({"status": "READY", "sample_id": "tee_gc_regular_06"}), encoding="utf-8"
    )
    smpl = tmp_path / "smpl"
    smpl.mkdir()
    (smpl / "basicmodel_neutral_lbs_10_207_0_v1.1.0.pkl").write_bytes(b"")
    body = tmp_path / "mean_all.obj"
    body.write_text("")
    return {
        "BLENDER_PATH": str(blender),
        "FIT_GC_PYTHON": str(gc_python),
        "FIT_RULER_GC_ROOT": str(gc_root),
        "FIT_PREPARED_DIR": str(prepared),
        "SMPL_MODEL_DIR": str(smpl),
        "FAL_KEY": SECRET,
        "FIT_DRAPE_BODY_OBJ": str(body),
    }


@pytest.fixture
def no_factories(monkeypatch):
    """팩토리를 부르지 않고 받은 설정만 기록한다(빈 SMPL 파일 등을 실제로 읽지 않게)."""
    calls = []

    def fake_build(config, settings, *, fit):
        calls.append((config, settings))

    monkeypatch.setattr(ce, "build_ports", fake_build)
    return calls


# --- 가짜만 ----------------------------------------------------------------------------------


def test_all_fake_needs_no_external_tools(tmp_path):
    report = _run("fake", {}, tmp_path)

    assert report.missing == ()
    items = _items(report)
    for tool in ("Blender", "SMPL 모델", "FAL_KEY", "GarmentCode 루트", "선택 의존성 body"):
        assert tool not in items  # 고른 설정에 필요한 것만 본다
    assert _row(report, "어댑터 연결").status == ce.OK
    assert "모두 가짜" in _row(report, "어댑터 연결").detail
    assert _row(report, f"의류 {GARMENT}").status == ce.OK


# --- 영상 경로 전부 실제(visual_real) ----------------------------------------------------------


def test_visual_real_on_empty_pc_lists_every_missing_piece(tmp_path):
    environ = {
        "FIT_GC_PYTHON": str(tmp_path / "없음" / "python.exe"),
        "FIT_RULER_GC_ROOT": str(tmp_path / "없음"),
        "FIT_PREPARED_DIR": str(tmp_path / "없음"),
    }
    report = _run("visual_real", environ, tmp_path)

    missing = {check.item for check in report.missing}
    assert missing == {
        "Blender",
        "GarmentCode용 Python",
        "GarmentCode 루트",
        f"준비된 의류 {GARMENT}",
        "SMPL 모델",
        "FAL_KEY",
    }
    for check in report.missing:
        assert check.fix, f"{check.item}에 고치는 방법이 없습니다"
    # 빠진 것이 있으면 팩토리를 부르지 않는다
    assert _row(report, "어댑터 연결").status == ce.INFO
    assert "개발용 몸 OBJ" not in _items(report)  # 몸이 실제면 개발용 몸은 필요 없다


def test_visual_fitroom_needs_the_same_as_visual_real(tmp_path):
    """실사화만 피팅룸 어댑터로 바꾼 설정도 빈 PC에서 FAL_KEY까지 같은 것을 알려 준다."""
    environ = {
        "FIT_GC_PYTHON": str(tmp_path / "없음" / "python.exe"),
        "FIT_RULER_GC_ROOT": str(tmp_path / "없음"),
        "FIT_PREPARED_DIR": str(tmp_path / "없음"),
    }
    real = _run("visual_real", environ, tmp_path)
    fitroom = _run("visual_fitroom", environ, tmp_path)

    assert {c.item for c in fitroom.missing} == {c.item for c in real.missing}
    assert "FAL_KEY" in {c.item for c in fitroom.missing}


def test_visual_real_with_everything_runs_the_factories(tmp_path, no_factories):
    environ = _base_env(tmp_path)
    del environ["FIT_DRAPE_BODY_OBJ"]
    report = _run("visual_real", environ, tmp_path)

    assert report.missing == (), [(c.item, c.detail) for c in report.missing]
    for item in ("Blender", "GarmentCode용 Python", "SMPL 모델", "FAL_KEY", "어댑터 연결"):
        assert _row(report, item).status == ce.OK
    assert "Blender 5.2.0" in _row(report, "Blender").detail
    assert "basicmodel_neutral" in _row(report, "SMPL 모델").detail
    # 팩토리는 서버와 같은 설정(환경의 Blender·SMPL·FAL_KEY 있음)으로 불린다
    [(config, settings)] = no_factories
    assert config.ports["realize"] == "fitting.adapters.fal_ltx.factory:make_realizer"
    assert settings.blender_path == Path(environ["BLENDER_PATH"])
    assert settings.fal_key_present is True


def test_fal_key_value_is_never_printed(tmp_path, no_factories):
    report = _run("visual_real", _base_env(tmp_path), tmp_path)

    text = ce.render(report)
    assert SECRET not in text
    assert SECRET not in repr(report)
    assert "| FAL_KEY | 통과 | 있음 (값은 출력하지 않음" in text


def test_factory_error_is_reported_as_missing(tmp_path):
    config = _config(tmp_path, {"drape": "tests.stub_adapters:make_config_error"})
    report = _run(config, {}, tmp_path)

    row = _row(report, "어댑터 연결")
    assert row.status == ce.MISSING
    assert "BLENDER_PATH를 정해 주세요" in row.detail
    assert report.missing == (row,)


def test_stub_factories_connect(tmp_path):
    config = _config(tmp_path, {"drape": "tests.stub_adapters:make_drape"})
    report = _run(config, {}, tmp_path)

    assert _row(report, "어댑터 연결").status == ce.OK
    assert "drape" in _row(report, "어댑터 연결").detail


# --- 착장(Blender) ------------------------------------------------------------------------------


def test_drape_with_fake_body_needs_dev_body_obj(tmp_path, no_factories):
    environ = _base_env(tmp_path)
    del environ["FIT_DRAPE_BODY_OBJ"]
    report = _run("drape_render_blender", environ, tmp_path)

    row = _row(report, "개발용 몸 OBJ")
    assert row.status == ce.MISSING
    assert "FIT_DRAPE_BODY_OBJ" in row.fix
    assert "SMPL 모델" not in _items(report)
    assert "FAL_KEY" not in _items(report)


def test_drape_render_blender_ready(tmp_path, no_factories):
    report = _run("drape_render_blender", _base_env(tmp_path), tmp_path)

    assert report.missing == ()
    assert _row(report, "개발용 몸 OBJ").status == ce.OK
    assert "사용자 몸이 아님" in _row(report, "개발용 몸 OBJ").detail
    assert _row(report, f"준비된 의류 {GARMENT}").status == ce.OK
    assert "선택 의존성 realize" not in _items(report)


def test_dev_body_with_real_body_is_a_warning(tmp_path, no_factories):
    report = _run("visual_real", _base_env(tmp_path), tmp_path)

    row = _row(report, "개발용 몸 OBJ")
    assert row.status == ce.WARN
    assert report.missing == ()


def test_dev_body_path_that_does_not_exist_is_missing(tmp_path, no_factories):
    environ = {**_base_env(tmp_path), "FIT_DRAPE_BODY_OBJ": str(tmp_path / "없는.obj")}
    report = _run("drape_blender", environ, tmp_path)

    assert _row(report, "개발용 몸 OBJ").status == ce.MISSING


def test_render_only_needs_blender(tmp_path):
    config = _config(tmp_path, {"render": "fitting.adapters.blender.renderer:make_renderer"})
    report = _run(config, {}, tmp_path)

    assert _row(report, "Blender").status == ce.MISSING
    for item in ("GarmentCode용 Python", f"준비된 의류 {GARMENT}", "개발용 몸 OBJ"):
        assert item not in _items(report)


def test_blender_exe_fallback_is_a_warning(tmp_path, no_factories):
    environ = _base_env(tmp_path)
    environ["BLENDER_EXE"] = environ.pop("BLENDER_PATH")
    report = _run("drape_render_blender", environ, tmp_path)

    row = _row(report, "Blender")
    assert row.status == ce.WARN
    assert "BLENDER_PATH" in row.fix
    assert report.missing == ()


def test_blender_path_that_is_not_a_file_is_missing(tmp_path):
    report = _run("drape_render_blender", {"BLENDER_PATH": str(tmp_path)}, tmp_path)

    assert _row(report, "Blender").status == ce.MISSING
    assert "실행 파일이 없음" in _row(report, "Blender").detail


@pytest.mark.parametrize(
    ("blender", "status"),
    [
        ("Blender 5.2.0", ce.OK),
        ("Blender 5.0.1", ce.OK),
        ("Blender 4.2.3", ce.WARN),
        (None, ce.WARN),
    ],
)
def test_blender_version(tmp_path, no_factories, blender, status):
    report = _run(
        "drape_render_blender", _base_env(tmp_path), tmp_path, probe=_versions(blender=blender)
    )

    assert _row(report, "Blender").status == status
    assert report.missing == ()  # 버전이 달라도 돌 수는 있다


@pytest.mark.parametrize(
    ("version", "status"),
    [("Python 3.9.13", ce.OK), ("Python 3.9.0", ce.OK), ("Python 3.10.0", ce.WARN)],
)
def test_garmentcode_python_version(tmp_path, no_factories, version, status):
    report = _run(
        "drape_blender", _base_env(tmp_path), tmp_path, probe=_versions(gc_python=version)
    )

    assert _row(report, "GarmentCode용 Python").status == status


def test_failed_preparation_is_missing_with_reason(tmp_path, no_factories):
    environ = _base_env(tmp_path)
    record = Path(environ["FIT_PREPARED_DIR"]) / GARMENT / "prep.json"
    record.write_text(
        json.dumps({"status": "FAILED", "failure_reason": "OUT_OF_TOLERANCE"}), encoding="utf-8"
    )
    report = _run("drape_blender", environ, tmp_path)

    row = _row(report, f"준비된 의류 {GARMENT}")
    assert row.status == ce.MISSING
    assert "OUT_OF_TOLERANCE" in row.detail
    assert f"tools.prepare_garments --only {GARMENT}" in row.fix


def test_prepared_garment_follows_garment_id(tmp_path, no_factories):
    report = _run(
        "drape_blender", _base_env(tmp_path), tmp_path, garment_id="tee_relaxed_l_black_v1"
    )

    assert _row(report, "준비된 의류 tee_relaxed_l_black_v1").status == ce.MISSING
    assert _row(report, "의류 tee_relaxed_l_black_v1").status == ce.OK


# --- 몸(SMPL) ----------------------------------------------------------------------------------


def test_smpl_folder_without_neutral_model_is_missing(tmp_path):
    folder = tmp_path / "smpl"
    folder.mkdir()
    (folder / "basicmodel_m_lbs_10_207_0_v1.1.0.pkl").write_bytes(b"")
    (folder / "neutral_notes.txt").write_text("")
    report = _run("smpl_body", {"SMPL_MODEL_DIR": str(folder)}, tmp_path)

    row = _row(report, "SMPL 모델")
    assert row.status == ce.MISSING
    assert "중성 모델" in row.detail
    assert "smpl-body-measure.md" in row.fix


def test_smpl_relative_folder_is_found_under_repo_root(tmp_path, no_factories, monkeypatch):
    monkeypatch.chdir(tmp_path)  # 지금 폴더에는 models/smpl이 없다
    report = _run("smpl_body", {"SMPL_MODEL_DIR": "models/smpl"}, tmp_path)
    assert _row(report, "SMPL 모델").status == ce.MISSING

    folder = tmp_path / "repo" / "models" / "smpl"
    folder.mkdir(parents=True)
    (folder / "SMPL_NEUTRAL.npz").write_bytes(b"")
    report = _run("smpl_body", {"SMPL_MODEL_DIR": "models/smpl"}, tmp_path)
    assert _row(report, "SMPL 모델").status == ce.OK


def test_missing_optional_dependency_is_reported(tmp_path):
    report = _run("smpl_body", {}, tmp_path, has_module=lambda name: name != "scipy")

    row = _row(report, "선택 의존성 body")
    assert row.status == ce.MISSING
    assert "scipy" in row.detail
    assert ".[dev,garment,realize,body]" in row.fix


# --- 항상 보는 것 -------------------------------------------------------------------------------


def test_old_python_is_missing(tmp_path):
    report = _run("fake", {}, tmp_path, version_info=(3, 9, 13))

    assert _row(report, "Python").status == ce.MISSING


def test_unknown_adapter_config_stops_early(tmp_path):
    report = _run("없는설정", {}, tmp_path)

    row = _row(report, "어댑터 설정")
    assert row.status == ce.MISSING
    assert "찾을 수 없습니다" in row.detail
    assert "어댑터 연결" not in _items(report)


def test_adapters_follow_environment_variable(tmp_path):
    report = _run(None, {"FIT_ADAPTERS": "smpl_body"}, tmp_path)

    assert "smpl_body.yaml" in _row(report, "어댑터 설정").detail
    assert "SMPL 모델" in _items(report)


def test_unknown_garment_is_missing(tmp_path):
    report = _run("fake", {}, tmp_path, garment_id="tee_none")

    row = _row(report, "의류 tee_none")
    assert row.status == ce.MISSING
    assert "--garment-id" in row.fix


def test_broken_pipeline_config_is_missing(tmp_path):
    report = _run("fake", {"FIT_PIPELINE_CONFIG": str(tmp_path / "없음.yaml")}, tmp_path)

    assert _row(report, "실패 처리 설정").status == ce.MISSING


def test_non_ascii_temp_folder_is_a_warning(tmp_path):
    report = _run("fake", {}, tmp_path, temp_dir=r"C:\Users\정준영\AppData\Local\Temp")

    row = _row(report, "임시 폴더")
    assert row.status == ce.WARN
    assert "--basetemp" in row.fix
    assert report.missing == ()


def test_node_is_only_a_warning(tmp_path):
    report = _run("fake", {}, tmp_path, which=lambda name: None)
    assert _row(report, "Node (웹)").status == ce.WARN
    assert report.missing == ()

    report = _run("fake", {}, tmp_path, probe=_versions(node="v18.19.0"))
    assert _row(report, "Node (웹)").status == ce.WARN


def test_missing_web_packages_is_a_warning(tmp_path):
    report = _run("fake", {}, tmp_path, repo_root=tmp_path / "empty")

    assert _row(report, "웹 패키지").status == ce.WARN


def test_env_file_row_shows_names_not_values(tmp_path):
    result = EnvFileResult(
        path=tmp_path / ".env", found=True, loaded=("FAL_KEY", "BLENDER_PATH"), kept=("FIT_X",)
    )
    report = _run("fake", {}, tmp_path, env_file=result)

    row = _row(report, ".env")
    assert row.status == ce.OK
    assert "2개 읽음: FAL_KEY, BLENDER_PATH" in row.detail
    assert "환경변수가 우선: FIT_X" in row.detail


def test_env_file_with_bad_lines_is_a_warning(tmp_path):
    result = EnvFileResult(path=tmp_path / ".env", found=True, ignored_lines=(3,))
    report = _run("fake", {}, tmp_path, env_file=result)

    assert _row(report, ".env").status == ce.WARN


def test_needs_follow_adapter_modules():
    def needs(**ports):
        config = AdapterConfig(version="adapters_v0", ports={**ALL_FAKE, **ports}, source="x")
        return ce.needs_for(config)

    assert needs() == set()
    assert needs(drape="tests.stub_adapters:make_drape") == set()  # 모르는 모듈은 팩토리 검사만
    assert ce.NEED_FAL_KEY in needs(realize="fitting.adapters.fal_ltx.factory:make_realizer")
    assert needs(realize="fitting.adapters.fal_fitroom.factory:make_fitroom_realizer") == {
        "extra:realize",
        ce.NEED_FAL_KEY,
        ce.NEED_BLENDER,  # 회전 영상 맨 위 자르기
    }
    assert needs(validate="fitting.adapters.media.factory:make_validator") == {"extra:realize"}


def test_render_shortens_paths_inside_the_repo(tmp_path):
    report = ce.Report(
        adapters=str(tmp_path / "config" / "adapters" / "x.yaml"),
        ports={},
        checks=(ce.Check("가", ce.OK, f"{tmp_path / 'data' / 'garments'} (READY)"),),
        repo_root=tmp_path,
    )
    text = ce.render(report)

    assert str(tmp_path) not in text
    assert "data" in text and "garments" in text


# --- 명령 --------------------------------------------------------------------------------------


def test_main_exit_code_follows_missing():
    assert ce.main(["--adapters", "없는설정"]) == ce.EXIT_MISSING
    assert ce.main(["--adapters", "fake"]) == ce.EXIT_OK


def _module(*args, **env_extra):
    """`python -m tools.check_env`. 출력은 cp949로 열리게 강제한다(한국어 Windows 파이프)."""
    drop = ("FAL_KEY", "PYTHONUTF8", "PYTHONIOENCODING")
    env = {key: value for key, value in os.environ.items() if key not in drop}
    env.update(PYTHONIOENCODING="cp949", PYTHONUTF8="0", **env_extra)
    return subprocess.run(
        [sys.executable, "-m", "tools.check_env", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        env=env,
        check=False,
    )


def test_module_output_is_utf8_even_when_the_pipe_is_cp949():
    """cp949에 없는 "—"를 찍어도 멈추지 않고, 한국어가 UTF-8로 나온다(설치 점검 보고서 3번)."""
    done = _module("--adapters", "fake")

    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    out = done.stdout.decode("utf-8")  # cp949로 나왔다면 한국어에서 실패한다
    assert "모두 가짜 — 외부 도구 필요 없음" in out
    assert "결과: 이 설정으로 피팅을 돌리는 데 필요한 것이 모두 있습니다" in out


def test_module_reads_env_file_and_hides_the_key(tmp_path):
    """명령은 `.env`(FIT_ENV_FILE로 고른 파일)를 읽고, 키 값은 출력하지 않는다."""
    env_file = tmp_path / "test.env"
    env_file.write_text(f'# 테스트\nFAL_KEY="{SECRET}"\nBLENDER_PATH=\n', encoding="utf-8")
    config = _config(tmp_path, {"realize": "fitting.adapters.fal_ltx.factory:make_realizer"})
    done = _module("--adapters", config, FIT_ENV_FILE=str(env_file))
    out = done.stdout.decode("utf-8")
    err = done.stderr.decode("utf-8", "replace")

    assert done.returncode in (0, 1), err  # fal_client가 없는 PC에서는 1(어댑터 연결 없음)
    assert SECRET not in out
    assert SECRET not in err
    assert "| .env | 통과 | 1개 읽음: FAL_KEY |" in out  # 빈 값(BLENDER_PATH=)은 넣지 않는다
    assert "| FAL_KEY | 통과 | 있음" in out
