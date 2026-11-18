"""영상 경로 실패 처리 (B5, #12): 단계 × 실패 종류, 감시 시간, 같은 요청 ID 재조회.

실제 시간을 기다리지 않는다. 멈추는 포트는 `threading.Event`로 막고, 감시 시간 기다리기(`wait`)와
재조회 사이 쉬기(`sleep`)는 테스트가 바꿔 끼운다.
"""

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fitting.adapters.fake import FakeValidator, fake_ports
from fitting.domain import (
    SCHEMA_VERSION,
    ErrorCode,
    FittingRequest,
    FittingStatus,
    JobStatus,
    Measurements,
    QualityStatus,
    Stage,
    StageStatus,
    VisualSkipReason,
)
from fitting.ports import PortError, PortFailure, PortRequest, PortResult
from fitting.services.fitting import (
    INVALID_RESULT_CODE,
    VISUAL_STAGES,
    FittingPorts,
    RunSettings,
    run_fitting,
)
from fitting.services.jobs import JobRunner
from server.app import create_app

from .conftest import EXAMPLES, REPO_ROOT

JOB = "job_b5"
NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)
WAIT = 5
SECRET = "내부 경로 /secret/blender.exe 가 죽었다"  # 화면에 새면 안 되는 예외 내용

S = StageStatus
# 단계 → (포트 이름, 메서드 이름)
PORT_OF = {
    Stage.BODY_FIT: ("body", "build_body"),
    Stage.DRAPE: ("drape", "drape"),
    Stage.RENDER: ("render", "render"),
    Stage.REALIZE: ("realize", "realize"),
    Stage.VALIDATE: ("validate", "validate"),
}
# 그 단계 앞에서 이미 나온 산출물 필드
PRESERVED = {
    Stage.BODY_FIT: (),
    Stage.DRAPE: (),
    Stage.RENDER: ("drape_preview_artifact_id",),
    Stage.REALIZE: ("drape_preview_artifact_id", "render_artifact_id"),
    Stage.VALIDATE: ("drape_preview_artifact_id", "render_artifact_id", "video_artifact_id"),
}


# --- 시나리오 포트 -----------------------------------------------------------------------------


def ok(request: PortRequest, call: Callable[[], PortResult]) -> PortResult:
    return call()


def fail(
    code: ErrorCode, *, retryable: bool = False, request_id: str | None = None
) -> Callable[..., PortResult]:
    def action(request: PortRequest, call: Callable[[], PortResult]) -> PortResult:
        raise PortFailure(
            PortError(
                code=code,
                retryable=retryable,
                safe_message="시나리오 실패",
                external_request_id=request_id,
            )
        )

    return action


def boom(request: PortRequest, call: Callable[[], PortResult]) -> PortResult:
    raise RuntimeError(SECRET)


def empty_result(request: PortRequest, call: Callable[[], PortResult]) -> PortResult:
    return PortResult(
        schema_version=SCHEMA_VERSION, output_artifact_ids=[], metrics={}, tool_versions={}
    )


class Scripted:
    """진짜(가짜 어댑터) 포트를 감싸고, 호출마다 `script`의 동작을 하나씩 꺼내 실행한다.

    동작은 `(요청, 진짜 호출) -> 결과`다. 마지막 동작은 계속 쓴다. `ok`면 진짜 포트를 부른다.
    """

    def __init__(self, inner: Any, method: str, script: list[Callable[..., PortResult]]) -> None:
        self._inner = inner
        self._method = method
        self._script = list(script)
        self.calls: list[PortRequest] = []

    def __getattr__(self, name: str) -> Any:
        if name != self._method:
            return getattr(self._inner, name)
        real = getattr(self._inner, name)

        def invoke(request: PortRequest, *args: Any) -> PortResult:
            self.calls.append(request)
            action = self._script.pop(0) if len(self._script) > 1 else self._script[0]
            return action(request, lambda: real(request, *args))

        return invoke


class Hang:
    """호출이 들어오면 알리고, 풀어 줄 때까지 멈춘다. 풀리면 평소처럼 결과를 돌려준다."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()

    def __call__(self, request: PortRequest, call: Callable[[], PortResult]) -> PortResult:
        self.entered.set()
        assert self.release.wait(WAIT), "테스트가 호출을 풀어 주지 않았다"
        try:
            return call()
        finally:
            self.finished.set()


def fast_wait(hang: Hang | None = None) -> Callable[[threading.Event, float], bool]:
    """감시 시간 기다리기. 멈춘 호출이 들어왔으면 바로 시간 초과로, 아니면 끝날 때까지 기다린다."""

    def wait(done: threading.Event, seconds: float) -> bool:
        while True:
            if done.is_set():
                return True
            if hang is not None and hang.entered.is_set():
                return False
            time.sleep(0.0005)

    return wait


def scripted_ports(
    stage: Stage, script: list[Callable[..., PortResult]]
) -> tuple[FittingPorts, Scripted]:
    ports = fake_ports()
    attr, method = PORT_OF[stage]
    scripted = Scripted(getattr(ports, attr), method, script)
    return replace(ports, **{attr: scripted}), scripted


@pytest.fixture
def request_for(body):
    def _make(consent: bool = True) -> FittingRequest:
        return FittingRequest(
            garment_variant_id="tee_test",
            realize_consent=consent,
            measurements=Measurements(**body.model_dump(exclude={"measurement_protocol_version"})),
            measurement_protocol_version=body.measurement_protocol_version,
        )

    return _make


def run(request, garment, ports, *, settings=None, wait=None, sleep=None, on_progress=None):
    return run_fitting(
        request,
        garment,
        ports,
        job_id=JOB,
        created_at=NOW,
        settings=settings or RunSettings(requery_delay_s=0.0),
        on_progress=on_progress,
        sleep=sleep or (lambda seconds: None),
        wait=wait,
    )


def by_stage(status: FittingStatus) -> dict[Stage, StageStatus]:
    return {p.stage: p.status for p in status.stages}


def assert_failed_at(outcome, baseline, stage: Stage, code: ErrorCode) -> None:
    """공통 확인: 그 단계만 FAILED, 앞은 SUCCEEDED, 뒤는 SKIPPED, 핏 리포트와 앞 산출물은 그대로."""
    status = outcome.status
    assert status.status is JobStatus.PARTIAL_SUCCEEDED
    assert status.report == baseline.status.report
    assert status.report.status is S.SUCCEEDED
    assert status.visual.status is S.FAILED
    assert status.visual.stage is stage
    assert status.visual.error_code is code
    assert status.visual.quality_status is not QualityStatus.PASS
    states = by_stage(status)
    index = VISUAL_STAGES.index(stage)
    for before in VISUAL_STAGES[:index]:
        assert states[before] is S.SUCCEEDED
    assert states[stage] is S.FAILED
    for after in VISUAL_STAGES[index + 1 :]:
        assert states[after] is S.SKIPPED
    for field in PRESERVED[stage]:
        assert getattr(status.visual, field) is not None
    assert status.visual.video_artifact_id is None or stage is Stage.VALIDATE
    assert outcome.report == baseline.report


@pytest.fixture
def baseline(request_for, make_garment):
    return run(request_for(), make_garment(), fake_ports())


# --- 단계 × 실패 종류 ---------------------------------------------------------------------------


@pytest.mark.parametrize("stage", VISUAL_STAGES)
def test_port_failure_fails_that_stage_with_its_code_and_is_called_once(
    stage, request_for, make_garment, baseline
):
    ports, scripted = scripted_ports(stage, [fail(ErrorCode.BUDGET_LIMIT)])

    outcome = run(request_for(), make_garment(), ports)

    assert_failed_at(outcome, baseline, stage, ErrorCode.BUDGET_LIMIT)
    assert len(scripted.calls) == 1


@pytest.mark.parametrize("stage", VISUAL_STAGES)
def test_unexpected_exception_becomes_the_stage_failure_and_stays_out_of_the_status(
    stage, request_for, make_garment, baseline, caplog
):
    ports, _ = scripted_ports(stage, [boom])

    with caplog.at_level(logging.ERROR):
        outcome = run(request_for(), make_garment(), ports)

    assert_failed_at(outcome, baseline, stage, INVALID_RESULT_CODE[stage])
    assert SECRET not in outcome.status.model_dump_json()  # 화면(API)에는 예외 내용이 없다
    assert any(SECRET in str(r.exc_info[1]) for r in caplog.records if r.exc_info)  # 로그에는 있다


@pytest.mark.parametrize("stage", VISUAL_STAGES)
def test_result_without_artifacts_fails_the_stage(stage, request_for, make_garment, baseline):
    ports, _ = scripted_ports(stage, [empty_result])

    outcome = run(request_for(), make_garment(), ports)

    assert_failed_at(outcome, baseline, stage, INVALID_RESULT_CODE[stage])


@pytest.mark.parametrize("stage", VISUAL_STAGES)
def test_a_hung_port_is_ended_by_the_watchdog_and_the_late_result_is_discarded(
    stage, request_for, make_garment, baseline, caplog
):
    hang = Hang()
    ports, scripted = scripted_ports(stage, [hang])
    expected = ErrorCode.REALIZE_TIMEOUT if stage is Stage.REALIZE else INVALID_RESULT_CODE[stage]

    with caplog.at_level(logging.WARNING):
        outcome = run(request_for(), make_garment(), ports, wait=fast_wait(hang))

    assert_failed_at(outcome, baseline, stage, expected)
    assert len(scripted.calls) == 1  # 감시 시간 초과는 다시 부르지 않는다
    assert "감시 여유" in caplog.text and stage.value in caplog.text

    hang.release.set()  # 늦게 돌아와도 이미 끝난 상태를 바꾸지 못한다
    assert hang.finished.wait(WAIT)
    for thread in threading.enumerate():
        if thread.name.startswith("watchdog-"):
            thread.join(WAIT)
    assert outcome.status.visual.stage is stage
    assert outcome.status.status is JobStatus.PARTIAL_SUCCEEDED
    assert "늦게 끝나" in caplog.text


def test_the_watchdog_waits_for_the_time_limit_plus_the_grace(request_for, make_garment):
    waited: list[float] = []
    hang = Hang()
    ports, _ = scripted_ports(Stage.RENDER, [hang])

    def wait(done: threading.Event, seconds: float) -> bool:
        waited.append(seconds)
        return fast_wait(hang)(done, seconds)

    settings = RunSettings(
        stage_timeouts_s={s: 10.0 for s in VISUAL_STAGES},
        watchdog_grace_s=7.0,
        requery_delay_s=0.0,
    )
    run(request_for(), make_garment(), ports, settings=settings, wait=wait)
    hang.release.set()

    assert waited[-1] == 17.0  # 렌더: 시간 제한 10초 + 여유 7초
    assert set(waited[:-1]) == {17.0}  # 앞 단계도 같은 규칙


# --- 같은 요청 ID 재조회 ------------------------------------------------------------------------


def retryable(request_id: str | None, code: ErrorCode = ErrorCode.REALIZE_TIMEOUT):
    return fail(code, retryable=True, request_id=request_id)


def test_retryable_failure_with_a_request_id_is_requeried_and_can_succeed(
    request_for, make_garment
):
    ports, scripted = scripted_ports(Stage.REALIZE, [retryable("req-1"), ok])
    slept: list[float] = []
    seen: list[FittingStatus] = []

    outcome = run(
        request_for(),
        make_garment(),
        ports,
        settings=RunSettings(requery_delay_s=5.0),
        sleep=slept.append,
        on_progress=seen.append,
    )

    status = outcome.status
    assert status.status is JobStatus.SUCCEEDED
    assert by_stage(status)[Stage.REALIZE] is S.SUCCEEDED
    attempts = {p.stage: p.attempt_no for p in status.stages}
    assert attempts[Stage.REALIZE] == 2
    assert attempts[Stage.RENDER] == 1
    first, second = scripted.calls
    assert first.job_id == second.job_id == JOB  # 같은 작업의 같은 외부 요청을 다시 조회한다
    assert first.attempt_id == f"{JOB}_realize_1" and second.attempt_id == f"{JOB}_realize_2"
    assert slept == [5.0]
    # 시도가 바뀔 때마다 진행 상태가 나간다
    realize_attempts = [
        p.attempt_no for s in seen for p in s.stages if p.stage is Stage.REALIZE and p.attempt_no
    ]
    assert 2 in realize_attempts


def test_requery_gives_up_after_the_limit_and_fails_with_the_last_code(
    request_for, make_garment, baseline
):
    ports, scripted = scripted_ports(
        Stage.REALIZE,
        [
            retryable("req-1", ErrorCode.EXTERNAL_SUBMIT_UNKNOWN),
            retryable("req-1", ErrorCode.EXTERNAL_SUBMIT_UNKNOWN),
            retryable("req-1", ErrorCode.REALIZE_TIMEOUT),
        ],
    )

    outcome = run(request_for(), make_garment(), ports)

    assert len(scripted.calls) == 3  # 첫 호출 + 재조회 2번
    assert_failed_at(outcome, baseline, Stage.REALIZE, ErrorCode.REALIZE_TIMEOUT)
    assert {p.stage: p.attempt_no for p in outcome.status.stages}[Stage.REALIZE] == 3


def test_requery_is_off_when_max_attempts_is_zero(request_for, make_garment, baseline):
    ports, scripted = scripted_ports(Stage.REALIZE, [retryable("req-1"), ok])

    outcome = run(
        request_for(), make_garment(), ports, settings=RunSettings(requery_max_attempts=0)
    )

    assert len(scripted.calls) == 1
    assert_failed_at(outcome, baseline, Stage.REALIZE, ErrorCode.REALIZE_TIMEOUT)


def test_retryable_without_a_request_id_is_not_requeried(
    request_for, make_garment, baseline, caplog
):
    ports, scripted = scripted_ports(Stage.REALIZE, [retryable(None), ok])

    with caplog.at_level(logging.WARNING):
        outcome = run(request_for(), make_garment(), ports)

    assert len(scripted.calls) == 1
    assert_failed_at(outcome, baseline, Stage.REALIZE, ErrorCode.REALIZE_TIMEOUT)
    assert "external_request_id가 없습니다" in caplog.text


def test_a_request_id_without_retryable_is_not_requeried(request_for, make_garment):
    ports, scripted = scripted_ports(
        Stage.REALIZE, [fail(ErrorCode.EXTERNAL_SUBMIT_UNKNOWN, request_id="req-1"), ok]
    )

    outcome = run(request_for(), make_garment(), ports)

    assert len(scripted.calls) == 1
    assert outcome.status.visual.error_code is ErrorCode.EXTERNAL_SUBMIT_UNKNOWN


def test_requery_does_not_depend_on_the_error_code_name(request_for, make_garment):
    """재조회 여부는 retryable·요청 ID만 본다. 다른 단계·다른 코드도 같은 규칙이다."""
    ports, scripted = scripted_ports(
        Stage.RENDER, [retryable("job-9", ErrorCode.RENDER_FAILED), ok]
    )

    outcome = run(request_for(), make_garment(), ports)

    assert len(scripted.calls) == 2
    assert outcome.status.status is JobStatus.SUCCEEDED


def test_a_failure_that_asks_for_a_requery_after_a_timeout_is_not_requeried_twice(
    request_for, make_garment
):
    """감시 시간 초과 뒤에는 어댑터가 아직 돌고 있을 수 있어 다시 부르지 않는다."""
    hang = Hang()
    ports, scripted = scripted_ports(Stage.REALIZE, [hang, ok])

    outcome = run(request_for(), make_garment(), ports, wait=fast_wait(hang))
    hang.release.set()

    assert len(scripted.calls) == 1
    assert outcome.status.visual.error_code is ErrorCode.REALIZE_TIMEOUT


# --- 대체 결과를 성공으로 처리하지 않는다 -------------------------------------------------------


def test_realize_failure_keeps_the_3d_preview_as_a_substitute_not_a_success(
    request_for, make_garment
):
    ports, _ = scripted_ports(Stage.REALIZE, [fail(ErrorCode.EXTERNAL_SUBMIT_UNKNOWN)])

    visual = run(request_for(), make_garment(), ports).status.visual

    assert visual.status is S.FAILED
    assert visual.video_artifact_id is None and visual.frame_artifact_id is None
    assert visual.quality_status is not QualityStatus.PASS
    assert visual.drape_preview_artifact_id and visual.render_artifact_id


@pytest.mark.parametrize("quality", [QualityStatus.FAIL, QualityStatus.UNVERIFIABLE])
def test_a_validation_that_does_not_pass_is_not_a_succeeded_job(quality, request_for, make_garment):
    ports = fake_ports(validate=FakeValidator(quality=quality))

    status = run(request_for(), make_garment(), ports).status

    assert status.status is not JobStatus.SUCCEEDED
    assert status.visual.status is S.FAILED
    assert status.visual.quality_status is quality


def test_without_consent_the_job_still_succeeds_and_does_not_touch_realize(
    request_for, make_garment
):
    ports, scripted = scripted_ports(Stage.REALIZE, [boom])

    status = run(request_for(consent=False), make_garment(), ports).status

    assert status.status is JobStatus.SUCCEEDED
    assert status.visual.skip_reason is VisualSkipReason.CONSENT_NOT_GIVEN
    assert scripted.calls == []
    assert by_stage(status)[Stage.REALIZE] is S.SKIPPED


# --- 시간 제한 전달 -----------------------------------------------------------------------------


def test_each_stage_gets_its_own_time_limit_rounded_up(request_for, make_garment):
    ports = fake_ports()
    settings = RunSettings(
        stage_timeouts_s={
            Stage.BODY_FIT: 11,
            Stage.DRAPE: 22.5,
            Stage.RENDER: 33,
            Stage.REALIZE: 44.1,
            Stage.VALIDATE: 55,
        },
        requery_delay_s=0.0,
    )

    run(request_for(), make_garment(), ports, settings=settings)

    sent = [
        ports.body.calls[0].timeout_seconds,
        ports.drape.calls[0].timeout_seconds,
        ports.render.calls[0].timeout_seconds,
        ports.realize.calls[0].timeout_seconds,
        ports.validate.calls[0].timeout_seconds,
    ]
    assert sent == [11, 23, 33, 45, 55]


def test_default_settings_match_the_pipeline_file(request_for, make_garment):
    from fitting.services.pipeline import load_run_settings

    loaded = load_run_settings()
    ports = fake_ports()
    run(request_for(), make_garment(), ports, settings=RunSettings())

    assert RunSettings().stage_timeouts_s == {}  # 비어 있으면 아래 기본값을 쓴다
    assert {s: RunSettings().timeout_for(s) for s in VISUAL_STAGES} == dict(loaded.stage_timeouts_s)
    assert dict(RunSettings().watchdog_grace_s) == dict(loaded.watchdog_grace_s)
    assert RunSettings().requery_max_attempts == loaded.requery_max_attempts
    assert RunSettings().requery_delay_s == loaded.requery_delay_s
    assert ports.body.calls[0].timeout_seconds == 180


def test_timeout_seconds_stays_as_the_value_for_every_stage_without_a_stage_value(
    request_for, make_garment
):
    ports = fake_ports()

    run(request_for(), make_garment(), ports, settings=RunSettings(timeout_seconds=7))

    assert {c.timeout_seconds for c in (ports.body.calls + ports.validate.calls)} == {7}


# --- 작업 실행기·API 경로 -------------------------------------------------------------------------


def test_job_runner_ends_a_hung_job_and_starts_the_next_one(request_for, make_garment):
    hang = Hang()
    ports, scripted = scripted_ports(Stage.DRAPE, [hang, ok])
    runner = JobRunner(ports, settings=RunSettings(requery_delay_s=0.0), wait=fast_wait(hang))
    try:
        first = runner.submit(request_for(), make_garment())
        final = runner.wait(first.job_id, WAIT)
        second = runner.submit(request_for(), make_garment())
        second_final = runner.wait(second.job_id, WAIT)
    finally:
        hang.release.set()
        runner.shutdown(wait=True, timeout=WAIT)

    assert final.status is JobStatus.PARTIAL_SUCCEEDED
    assert final.visual.stage is Stage.DRAPE
    assert final.visual.error_code is ErrorCode.DRAPE_INVALID
    assert final.report == first.report  # 핏 리포트는 첫 응답 그대로
    assert second_final.status is JobStatus.SUCCEEDED  # 멈춘 작업이 뒤 작업을 막지 않는다


def test_api_shows_attempt_no_for_a_requeried_stage(request_for, make_garment, tmp_path):
    ports, _ = scripted_ports(Stage.REALIZE, [retryable("req-1"), ok])
    runner = JobRunner(ports, settings=RunSettings(requery_delay_s=0.0), sleep=lambda s: None)
    garments = tmp_path / "garments"
    garments.mkdir()
    garment = json.loads((EXAMPLES / "garment.json").read_text(encoding="utf-8"))
    (garments / "g.json").write_text(json.dumps(garment), encoding="utf-8")
    request = json.loads((EXAMPLES / "fitting_request.json").read_text(encoding="utf-8"))
    request["realize_consent"] = True

    app = create_app(garments_dir=garments, artifacts_dir=tmp_path, runner=runner)
    with TestClient(app) as client:
        job_id = client.post("/api/fittings", json=request).json()["job_id"]
        runner.wait(job_id, WAIT)
        data = client.get(f"/api/fittings/{job_id}").json()

    assert data["status"] == "SUCCEEDED"
    attempts = {item["stage"]: item["attempt_no"] for item in data["stages"]}
    assert attempts["REALIZE"] == 2
    assert attempts["RENDER"] == 1


# --- 단계별 감시 여유 ---------------------------------------------------------------------------


@pytest.mark.parametrize("stage", VISUAL_STAGES)
def test_the_watchdog_waits_for_the_stage_limit_plus_that_stages_grace(
    stage, request_for, make_garment
):
    waited: dict[str, float] = {}
    hang = Hang()
    ports, _ = scripted_ports(stage, [hang])
    settings = RunSettings(requery_delay_s=0.0)  # 시간 제한·감시 여유 모두 기본값

    def wait(done: threading.Event, seconds: float) -> bool:
        waited.setdefault(f"{len(waited)}", seconds)
        return fast_wait(hang)(done, seconds)

    run(request_for(), make_garment(), ports, settings=settings, wait=wait)
    hang.release.set()

    expected = {s: settings.timeout_for(s) + settings.grace_for(s) for s in VISUAL_STAGES}
    assert list(waited.values()) == [expected[s] for s in VISUAL_STAGES[: len(waited)]]
    assert expected[Stage.DRAPE] == 1260  # 착장: 600 + 660


def test_drape_grace_covers_the_adapters_fresh_analysis_limit():
    """착장 어댑터(`blender/draper.py`)는 Blender 착장에 시간 제한을 주고, 착장이 끝나면 분석 단계에
    같은 길이의 한도를 새로 준다. 정상 착장이 최대 2 × 한도까지 걸리므로 감시 시간이 그보다 커야
    정상 실행을 실패로 끊지 않는다. 어댑터 코드는 import하지 않고 설정 파일만 읽는다.
    """
    drape_json = json.loads((REPO_ROOT / "config" / "drape" / "drape_v1.json").read_text("utf-8"))
    adapter_limit = float(drape_json["time_limits_s"]["drape"])
    settings = RunSettings()
    stage_limit = settings.timeout_for(Stage.DRAPE)
    watchdog = stage_limit + settings.grace_for(Stage.DRAPE)

    longest_normal_run = 2 * min(stage_limit, adapter_limit)
    assert watchdog >= longest_normal_run + 60, (
        f"착장 어댑터는 분석 단계에 한도를 새로 준다. 둘 중 하나를 바꾸면 이 값도 맞춘다: "
        f"감시 시간 {watchdog:g}초 < 2 × min({stage_limit:g}, {adapter_limit:g}) + 60"
    )
