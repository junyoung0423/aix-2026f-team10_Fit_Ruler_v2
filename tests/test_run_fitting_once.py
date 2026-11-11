"""피팅 1회 실행 스크립트 (#12 #13): python -m tools.run_fitting_once.

기준 사례(신체 1 × 티셔츠 1)의 기대 핏 판정은 `docs/decisions.md` "1주차 기준 [제안]" 1절과 같다.
`time.sleep`으로 타이밍을 맞추지 않는다.
"""

import json
import re
import subprocess
import sys

import pytest
import yaml

import tools.run_fitting_once as tool
from fitting.domain import FitReport, FittingRequest, FittingStatus, JobStatus, Stage, StageStatus
from server.wiring import PORT_NAMES

from . import stub_adapters
from .conftest import EXAMPLES, REPO_ROOT

STUB = "tests.stub_adapters"
ALL_STUBS = {
    "body": f"{STUB}:make_body",
    "drape": f"{STUB}:make_drape",
    "render": f"{STUB}:make_render",
    "realize": f"{STUB}:make_realize",
    "validate": f"{STUB}:make_validate",
}
ALL_FAKE = dict.fromkeys(PORT_NAMES, "fake")

# 기준 사례 기대값: 부위 → (여유량, 판정)
EXPECTED_REGIONS = {
    "chest": (8.0, "REGULAR"),
    "shoulder": (6.0, "DROPPED"),
    "waist": (16.0, "LOOSE"),
    "sleeve": (None, "NOT_EVALUATED"),
    "body_length": (None, "NOT_EVALUATED"),
}
PROGRESS_LINE = re.compile(r"^\[([A-Z_]+)\] ([A-Z_]+)(?: \d+\.\ds)?$")


@pytest.fixture(autouse=True)
def _reset_stubs():
    stub_adapters.reset()


def _run(capsys, *args):
    code = tool.main([str(a) for a in args])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _config(tmp_path, ports: dict, name: str = "adapters.yaml"):
    path = tmp_path / name
    path.write_text(
        yaml.safe_dump({"version": "adapters_v0", "ports": {**ALL_FAKE, **ports}}),
        encoding="utf-8",
    )
    return path


def _progress(stdout: str) -> list[tuple[str, str]]:
    return [m.groups() for line in stdout.splitlines() if (m := PROGRESS_LINE.match(line))]


def _load(out, name, model):
    return model.model_validate_json((out / name).read_text(encoding="utf-8"))


# --- 기본 실행 (가짜 어댑터) ---


def test_default_run_with_fake_adapters(tmp_path, capsys):
    out = tmp_path / "run"

    code, stdout, stderr = _run(capsys, "--adapters", "fake", "--out", out)

    assert code == 0, stderr
    assert sorted(p.name for p in out.iterdir() if p.is_file()) == [
        "report.json",
        "request.json",
        "status.json",
        "summary.txt",
    ]
    status = _load(out, "status.json", FittingStatus)
    report = _load(out, "report.json", FitReport)
    request = _load(out, "request.json", FittingRequest)
    assert status.status is JobStatus.SUCCEEDED
    assert status.report.regions == report.regions
    assert request.garment_variant_id == "tee_regular_m_navy_v1"
    assert request.realize_consent is False  # 기본은 미동의
    assert status.visual.stage is Stage.RENDER  # 렌더까지만
    assert status.visual.skip_reason.value == "CONSENT_NOT_GIVEN"
    assert stderr == ""


def test_the_reference_case_matches_the_team_document(tmp_path, capsys):
    """기준 신체 1 × 티셔츠 1: 가슴 +8.0 기본 여유, 어깨 +6.0 어깨선 내려옴, 허리 +16.0 넉넉."""
    out = tmp_path / "run"
    _run(capsys, "--adapters", "fake", "--out", out)

    report = _load(out, "report.json", FitReport)
    request = _load(out, "request.json", FittingRequest)

    assert request.measurements.model_dump() == {
        "height_cm": 175.0,
        "chest_cm": 100.0,
        "waist_cm": 80.0,
        "shoulder_cm": 40.0,
        "arm_length_cm": 56.0,
    }
    assert report.rules_version == "fit_rules_v0"
    assert {r.region.value: (r.ease_cm, r.label.value) for r in report.regions} == EXPECTED_REGIONS
    assert report.summary == "가슴 기본 여유 · 어깨 어깨선 내려옴 예상 · 허리 넉넉"


def test_summary_is_printed_and_written_to_summary_txt(tmp_path, capsys):
    out = tmp_path / "run"

    _, stdout, _ = _run(capsys, "--adapters", "fake", "--out", out)

    summary = (out / "summary.txt").read_text(encoding="utf-8")
    assert stdout.endswith(summary)  # 같은 내용을 출력하고 파일에도 쓴다
    assert summary.splitlines()[0] == "결과: 성공 — 렌더까지 완료 (실사화는 동의하지 않아 건너뜀)"
    assert "어댑터: body=fake · drape=fake · render=fake · realize=fake · validate=fake" in summary
    assert "가슴: +8.0 cm · REGULAR (몸 100, 옷 108)" in summary
    assert "어깨: +6.0 cm · DROPPED (몸 40, 옷 46)" in summary
    assert "허리: +16.0 cm · LOOSE (몸 80, 옷 96)" in summary
    assert "소매: 판정 안 함 · NOT_EVALUATED" in summary
    assert "총장: 판정 안 함 · NOT_EVALUATED" in summary
    assert "REALIZE        SKIPPED" in summary
    assert "건너뜀 이유 CONSENT_NOT_GIVEN" in summary


def test_fake_artifacts_are_reported_as_not_existing(tmp_path, capsys):
    """가짜는 파일이 없다. 산출물 ID와 위치는 보여 주되 "없음(가짜)"로 표시한다."""
    out = tmp_path / "run"

    _, stdout, _ = _run(capsys, "--adapters", "fake", "--consent", "--out", out)

    status = _load(out, "status.json", FittingStatus)
    for artifact_id in (
        status.visual.drape_preview_artifact_id,
        status.visual.render_artifact_id,
        status.visual.video_artifact_id,
        status.visual.frame_artifact_id,
        status.visual.render_frame_artifact_id,
    ):
        assert artifact_id in stdout
    assert stdout.count("없음(가짜)") == 5
    assert "있음" not in stdout
    assert list((out / "artifacts").iterdir()) == []


def test_consent_with_fake_adapters_passes_the_validation(tmp_path, capsys):
    out = tmp_path / "run"

    code, stdout, _ = _run(capsys, "--adapters", "fake", "--consent", "--out", out)

    status = _load(out, "status.json", FittingStatus)
    assert code == 0
    assert status.visual.quality_status.value == "PASS"
    assert status.visual.stage is Stage.VALIDATE
    assert "결과: 성공 — 실사화·검증까지 완료" in stdout
    assert "검증 PASS" in stdout


def test_report_only_skips_the_video_path(tmp_path, capsys):
    out = tmp_path / "run"

    code, stdout, _ = _run(capsys, "--adapters", "fake", "--mode", "report_only", "--out", out)

    status = _load(out, "status.json", FittingStatus)
    assert code == 0
    assert status.status is JobStatus.SUCCEEDED
    assert status.visual.status is StageStatus.SKIPPED
    assert status.visual.skip_reason.value == "REPORT_ONLY"
    assert stdout.splitlines()[0].startswith("피팅 시작")
    assert "핏 리포트만 완료" in (out / "summary.txt").read_text(encoding="utf-8")
    assert _load(out, "report.json", FitReport).regions  # 리포트는 있다


def test_default_adapters_also_run(tmp_path, capsys, monkeypatch):
    """`--adapters`도 `FIT_ADAPTERS`도 없으면 `default` 설정(지금은 전부 가짜)으로 돈다."""
    monkeypatch.delenv("FIT_ADAPTERS")

    code, stdout, _ = _run(capsys, "--out", tmp_path / "run")

    assert code == 0
    assert "어댑터: body=fake" in stdout


def test_default_out_folder_is_artifacts_runs_job_id(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(tool, "DEFAULT_RUNS_DIR", tmp_path / "runs")

    code, stdout, _ = _run(capsys, "--adapters", "fake")

    job_id = re.search(r"피팅 시작: (fit_[0-9a-f]{12})", stdout).group(1)
    assert code == 0
    assert (tmp_path / "runs" / job_id / "status.json").is_file()
    assert _load(tmp_path / "runs" / job_id, "status.json", FittingStatus).job_id == job_id


def test_default_runs_folder_is_ignored_by_git():
    assert tool.DEFAULT_RUNS_DIR == REPO_ROOT / "artifacts" / "runs"
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "artifacts/runs/x/summary.txt"],
        cwd=REPO_ROOT,
        check=False,
    )
    assert ignored.returncode == 0


# --- 진행 줄 ---


def test_progress_lines_follow_the_stage_order_without_consent(tmp_path, capsys):
    _, stdout, _ = _run(capsys, "--adapters", "fake", "--out", tmp_path / "run")

    assert _progress(stdout) == [
        ("FIT_CALCULATE", "SUCCEEDED"),
        ("FIT_REPORT", "SUCCEEDED"),
        ("REALIZE", "SKIPPED"),  # 미동의라 처음부터 건너뜀
        ("VALIDATE", "SKIPPED"),
        ("BODY_FIT", "RUNNING"),
        ("BODY_FIT", "SUCCEEDED"),
        ("DRAPE", "RUNNING"),
        ("DRAPE", "SUCCEEDED"),
        ("RENDER", "RUNNING"),
        ("RENDER", "SUCCEEDED"),
    ]


def test_progress_lines_follow_the_stage_order_with_consent(tmp_path, capsys):
    _, stdout, _ = _run(capsys, "--adapters", "fake", "--consent", "--out", tmp_path / "run")

    assert _progress(stdout) == [
        ("FIT_CALCULATE", "SUCCEEDED"),
        ("FIT_REPORT", "SUCCEEDED"),
        *[
            (stage, status)
            for stage in ("BODY_FIT", "DRAPE", "RENDER", "REALIZE", "VALIDATE")
            for status in ("RUNNING", "SUCCEEDED")
        ],
    ]


def test_progress_lines_show_the_elapsed_time_of_finished_stages(tmp_path, capsys):
    _, stdout, _ = _run(capsys, "--adapters", "fake", "--out", tmp_path / "run")

    assert re.search(r"^\[DRAPE\] RUNNING$", stdout, re.M)
    assert re.search(r"^\[DRAPE\] SUCCEEDED \d+\.\ds$", stdout, re.M)  # 걸린 시간
    assert re.search(r"^\[REALIZE\] SKIPPED$", stdout, re.M)  # 건너뜀은 시간이 없다


def test_progress_comes_before_the_summary(tmp_path, capsys):
    _, stdout, _ = _run(capsys, "--adapters", "fake", "--out", tmp_path / "run")

    assert stdout.index("[RENDER] SUCCEEDED") < stdout.index("결과: ")


# --- 스텁(실제 어댑터 경로) ---


def test_stub_adapters_write_real_files_that_the_summary_points_to(tmp_path, capsys):
    out = tmp_path / "run"

    code, stdout, stderr = _run(
        capsys, "--adapters", _config(tmp_path, ALL_STUBS), "--consent", "--yes", "--out", out
    )

    assert code == 0, stderr
    status = _load(out, "status.json", FittingStatus)
    job_id = status.job_id
    for name in ("drape_preview", "render_clip", "video", "frame", "render_frame"):
        extension = "png" if name in ("drape_preview", "frame", "render_frame") else "mp4"
        path = out / "artifacts" / f"{job_id}_{name}.{extension}"
        assert path.is_file()
        assert f"{path} — 있음 (" in stdout  # 실제 파일 경로와 있는지
    assert "없음" not in stdout
    assert [port for port, _ in stub_adapters.CALLS] == [
        "body",
        "drape",
        "render",
        "realize",
        "validate",
    ]
    assert "body=tests.stub_adapters:make_body" in stdout  # 사용한 어댑터가 출력에 나온다


def test_stub_artifacts_go_inside_the_run_folder(tmp_path, capsys):
    out = tmp_path / "run"

    _run(capsys, "--adapters", _config(tmp_path, ALL_STUBS), "--out", out)

    assert stub_adapters.SEEN_SETTINGS[0].artifacts_dir == out / "artifacts"
    assert any((out / "artifacts").iterdir())


def test_a_mix_of_stub_and_fake_ports(tmp_path, capsys):
    out = tmp_path / "run"

    code, stdout, _ = _run(
        capsys, "--adapters", _config(tmp_path, {"drape": ALL_STUBS["drape"]}), "--out", out
    )

    assert code == 0
    assert stdout.count("있음 (") == 1  # 착장 스텁의 미리보기뿐
    assert stdout.count("없음(가짜)") == 1  # 렌더 클립은 가짜라 파일이 없다


@pytest.mark.parametrize(
    ("port", "factory", "stage", "code_name"),
    [
        ("drape", "make_failing_drape", "DRAPE", "DRAPE_INVALID"),
        ("body", "make_failing_body", "BODY_FIT", "BODY_FIT_NOT_CONVERGED"),
    ],
)
def test_a_failing_stage_gives_exit_code_3_and_says_so_first(
    tmp_path, capsys, port, factory, stage, code_name
):
    out = tmp_path / "run"
    adapters = _config(tmp_path, {**ALL_STUBS, port: f"{STUB}:{factory}"})

    code, stdout, _ = _run(capsys, "--adapters", adapters, "--out", out)

    status = _load(out, "status.json", FittingStatus)
    summary = (out / "summary.txt").read_text(encoding="utf-8")
    assert code == 3
    assert status.status is JobStatus.PARTIAL_SUCCEEDED
    first = summary.splitlines()[0]
    assert first.startswith("결과: 영상 경로 실패")  # 대체 결과를 성공으로 보이지 않는다
    assert stage in first and code_name in first
    assert f"[{stage}] FAILED" in stdout
    assert f"{stage:<14} FAILED" in summary
    assert "SKIPPED" in summary  # 뒤 단계는 건너뜀
    assert "상태 FAILED" in summary and f"오류 코드 {code_name}" in summary
    assert _load(out, "report.json", FitReport).regions  # 핏 리포트는 남는다
    assert "가슴: +8.0 cm · REGULAR" in summary


def test_a_validation_fail_verdict_is_also_a_video_path_failure(tmp_path, capsys, monkeypatch):
    out = tmp_path / "run"
    monkeypatch.setattr(stub_adapters.StubValidator, "metrics", {"quality_status": "FAIL"})

    code, stdout, _ = _run(
        capsys, "--adapters", _config(tmp_path, ALL_STUBS), "--consent", "--yes", "--out", out
    )

    assert code == 3
    assert stdout.index("결과: 영상 경로 실패") < stdout.index("[핏 리포트]")
    assert "VALIDATION_FAILED" in stdout


def test_an_unexpected_exception_from_an_adapter_is_exit_code_2(tmp_path, capsys):
    out = tmp_path / "run"
    adapters = _config(tmp_path, {**ALL_STUBS, "render": f"{STUB}:make_exploding_render"})

    code, stdout, stderr = _run(capsys, "--adapters", adapters, "--out", out)

    assert code == 2
    assert "예상하지 못한 예외" in stderr and "RuntimeError" in stderr
    assert (out / "request.json").is_file()  # 입력은 남는다
    assert not (out / "status.json").exists()  # 끝나지 않았으니 성공처럼 보이는 결과는 없다
    assert "결과: 성공" not in stdout


def test_a_garment_that_is_not_ready_fails_without_a_report(tmp_path, capsys):
    garments = tmp_path / "garments"
    garments.mkdir()
    demo = json.loads((EXAMPLES / "garment.json").read_text(encoding="utf-8"))
    (garments / "draft.json").write_text(json.dumps({**demo, "status": "DRAFT"}), encoding="utf-8")
    out = tmp_path / "run"

    code, stdout, _ = _run(
        capsys,
        *("--garments-dir", garments, "--garment-id", demo["id"]),
        *("--adapters", "fake", "--out", out),
    )

    summary = (out / "summary.txt").read_text(encoding="utf-8")
    assert code == 2
    assert summary.splitlines()[0].startswith("결과: 피팅 실패")
    assert "GARMENT_NOT_READY" in summary
    assert "[핏 리포트] 없음" in summary
    assert not (out / "report.json").exists()
    assert _load(out, "status.json", FittingStatus).status is JobStatus.FAILED


# --- 비용이 드는 실사화 ---


def test_real_realize_with_consent_needs_yes_and_runs_nothing_without_it(tmp_path, capsys):
    out = tmp_path / "run"
    adapters = _config(tmp_path, {"realize": ALL_STUBS["realize"]})

    code, stdout, stderr = _run(capsys, "--adapters", adapters, "--consent", "--out", out)

    assert code == 1
    assert "--yes" in stderr and "비용" in stderr
    assert stdout == ""
    assert stub_adapters.CALLS == []  # 포트를 부르지 않았다
    assert stub_adapters.SEEN_SETTINGS == []  # 어댑터를 만들지도 않았다
    assert not out.exists()  # 결과 폴더도 만들지 않았다


def test_yes_lets_the_real_realize_run(tmp_path, capsys):
    adapters = _config(tmp_path, {"realize": ALL_STUBS["realize"]})

    code, _, _ = _run(capsys, "--adapters", adapters, "--consent", "--yes", "--out", tmp_path / "r")

    assert code == 0
    assert ("realize", "realize") in stub_adapters.CALLS


@pytest.mark.parametrize(
    "extra",
    [[], ["--mode", "report_only", "--consent"]],
    ids=["no consent", "report only"],
)
def test_yes_is_not_needed_when_the_real_realize_will_not_run(tmp_path, capsys, extra):
    adapters = _config(tmp_path, {"realize": ALL_STUBS["realize"]})

    code, _, stderr = _run(capsys, "--adapters", adapters, *extra, "--out", tmp_path / "run")

    assert code == 0, stderr
    assert ("realize", "realize") not in stub_adapters.CALLS


def test_fake_realize_with_consent_never_needs_yes(tmp_path, capsys):
    code, _, _ = _run(capsys, "--adapters", "fake", "--consent", "--out", tmp_path / "run")

    assert code == 0


# --- 어댑터 고르기 ---


def test_the_environment_variable_picks_the_adapters(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("FIT_ADAPTERS", str(_config(tmp_path, ALL_STUBS)))

    code, stdout, _ = _run(capsys, "--out", tmp_path / "run")

    assert code == 0
    assert "drape=tests.stub_adapters:make_drape" in stdout


def test_the_argument_beats_the_environment_variable(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("FIT_ADAPTERS", str(_config(tmp_path, ALL_STUBS)))

    code, stdout, _ = _run(capsys, "--adapters", "fake", "--out", tmp_path / "run")

    assert code == 0
    assert "tests.stub_adapters" not in stdout
    assert stub_adapters.CALLS == []


# --- 입력 ---


def test_body_and_garment_can_be_given(tmp_path, capsys):
    out = tmp_path / "run"

    code, _, _ = _run(
        capsys,
        *("--garment-id", "tee_regular_m_navy_v1", "--body", "166,97.5,81,36,52"),
        *("--adapters", "fake", "--out", out),
    )

    request = _load(out, "request.json", FittingRequest)
    report = _load(out, "report.json", FitReport)
    assert code == 0
    assert request.measurements.chest_cm == 97.5  # 기준 신체 2
    # 신체 2 × 티셔츠 1: 가슴 +10.5 기본 여유, 어깨 +10.0 어깨선 내려옴, 허리 +15.0 넉넉
    assert {r.region.value: r.ease_cm for r in report.regions}["chest"] == 10.5
    assert {r.region.value: r.ease_cm for r in report.regions}["waist"] == 15.0


def test_a_request_file_can_replace_the_options(tmp_path, capsys):
    out = tmp_path / "run"

    code, _, _ = _run(
        capsys,
        *("--request", EXAMPLES / "fitting_request.json"),
        *("--adapters", "fake", "--out", out),
    )

    request = _load(out, "request.json", FittingRequest)
    assert code == 0
    assert request.realize_consent is True  # 예시 요청은 동의가 켜져 있다
    assert _load(out, "status.json", FittingStatus).visual.quality_status.value == "PASS"


def test_garments_dir_comes_from_the_environment_too(tmp_path, capsys, monkeypatch):
    garments = tmp_path / "garments"
    garments.mkdir()
    demo = json.loads((EXAMPLES / "garment.json").read_text(encoding="utf-8"))
    (garments / "custom.json").write_text(json.dumps({**demo, "id": "tee_custom"}), "utf-8")
    monkeypatch.setenv("FIT_GARMENTS_DIR", str(garments))

    code, stdout, _ = _run(
        capsys, "--garment-id", "tee_custom", "--adapters", "fake", "--out", tmp_path / "run"
    )

    assert code == 0
    assert "의류: tee_custom" in stdout


@pytest.mark.parametrize(
    ("args", "fragment"),
    [
        (["--garment-id", "tee_nope"], "의류를 찾을 수 없습니다: tee_nope"),
        (["--body", "175,300,80,40,56"], "가슴둘레 값은 60~160 cm 사이로 입력해 주세요."),
        (["--body", "100,100,80,40,56"], "키 값은 140~210 cm 사이로 입력해 주세요."),
        (["--body", "175,100,80"], "다섯 숫자"),
        (["--body", "a,b,c,d,e"], "다섯 숫자"),
        (["--body", ""], "다섯 숫자"),  # 빈 값을 일부러 줬으면 기본값으로 바꾸지 않고 오류다
        (["--garment-id", ""], "의류"),
        (["--body", "175,100,80,40,56,1"], "다섯 숫자"),
        (["--request", "없는파일.json"], "요청 파일을 읽을 수 없습니다"),
        (["--request", "{EXAMPLE}", "--body", "175,100,80,40,56"], "--body와 함께 쓸 수 없습니다"),
        (["--request", "{EXAMPLE}", "--consent"], "--consent와 함께 쓸 수 없습니다"),
        (["--request", "{EXAMPLE}", "--garment-id", "x"], "--garment-id와 함께"),
        (["--adapters", "/없는/파일.yaml"], "어댑터 설정 파일을 찾을 수 없습니다"),
        (["--adapters", "no_such_name"], "no_such_name.yaml"),
    ],
)
def test_input_and_config_errors_are_exit_code_1_with_a_korean_line(
    tmp_path, capsys, args, fragment
):
    args = [str(EXAMPLES / "fitting_request.json") if a == "{EXAMPLE}" else a for a in args]
    adapters = [] if "--adapters" in args else ["--adapters", "fake"]

    code, stdout, stderr = _run(capsys, *args, *adapters, "--out", tmp_path / "run")

    assert code == 1
    assert fragment in stderr
    assert stderr.startswith("오류: ")
    assert stdout == ""
    assert not (tmp_path / "run").exists()  # 아무것도 쓰지 않았다


def test_a_request_file_that_is_not_json_is_exit_code_1(tmp_path, capsys):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")

    code, _, stderr = _run(capsys, "--request", broken, "--adapters", "fake")

    assert code == 1
    assert "JSON" in stderr


def test_a_wrong_adapter_config_is_exit_code_1_and_never_falls_back(tmp_path, capsys):
    adapters = _config(tmp_path, {"drape": "no.such.module:make"})

    code, stdout, stderr = _run(capsys, "--adapters", adapters, "--out", tmp_path / "run")

    assert code == 1
    assert "drape" in stderr and "no.such.module" in stderr
    assert stdout == ""  # 가짜로 대신 돌지 않았다
    assert stub_adapters.CALLS == []


def test_several_wrong_ports_are_all_in_the_error(tmp_path, capsys):
    adapters = _config(tmp_path, {"drape": "a.b:c", "validate": "d.e:f"})

    code, _, stderr = _run(capsys, "--adapters", adapters)

    assert code == 1
    assert "a.b:c" in stderr and "d.e:f" in stderr


@pytest.mark.parametrize("args", [["--mode", "fast"], ["--bogus"], ["--out"]])
def test_usage_errors_are_exit_code_1(capsys, args):
    code, stdout, stderr = _run(capsys, *args)

    assert code == 1
    assert stdout == ""
    assert "오류:" in stderr


def test_help_exits_with_zero(capsys):
    code, stdout, _ = _run(capsys, "--help")

    assert code == 0
    assert "--adapters" in stdout and "--yes" in stdout


# --- 실제 명령 ---


def _module(*args, env_extra=None):
    import os

    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(
        [sys.executable, "-m", "tools.run_fitting_once", *map(str, args)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )


def test_runs_as_a_module(tmp_path):
    done = _module("--adapters", "fake", "--out", tmp_path / "run")

    assert done.returncode == 0, done.stderr
    assert "결과: 성공" in done.stdout
    assert (tmp_path / "run" / "summary.txt").is_file()


def test_module_exit_code_1_with_a_korean_line_for_a_missing_config_file():
    done = _module(env_extra={"FIT_ADAPTERS": "/없는/파일.yaml"})

    assert done.returncode == 1
    assert done.stdout == ""
    assert done.stderr.startswith("오류: 어댑터 설정 파일을 찾을 수 없습니다")
    assert done.stderr.count("\n") == 1  # 한 줄


def test_module_exit_code_3_when_the_video_path_fails(tmp_path):
    adapters = _config(tmp_path, {**ALL_STUBS, "drape": f"{STUB}:make_failing_drape"})

    done = _module("--adapters", adapters, "--out", tmp_path / "run")

    assert done.returncode == 3
    assert done.stdout.count("영상 경로 실패") == 1
    assert "영상 경로 실패" in (tmp_path / "run" / "summary.txt").read_text(encoding="utf-8")
