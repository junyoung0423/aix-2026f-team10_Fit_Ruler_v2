"""피팅 작업 실행기 JobRunner (#12).

타이밍은 `time.sleep`이 아니라 `threading.Event`·`Barrier`로 맞춘다. 영상 경로의 한 단계를 Event로
멈춰 세워 두고 그 사이의 상태를 확인한다.
"""

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

import fitting.services.jobs as jobs_module
from fitting.adapters.fake import (
    FakeBodyBuilder,
    FakeDraper,
    FakeRealizer,
    FakeRenderer,
    FakeValidator,
    fake_ports,
)
from fitting.domain import (
    ErrorCode,
    FittingRequest,
    FittingStatus,
    GarmentStatus,
    JobStatus,
    Measurements,
    Mode,
    QualityStatus,
    Stage,
    StageStatus,
    VisualSkipReason,
)
from fitting.fit import FitEngineError, RuleFitCalculator
from fitting.services.fitting import (
    INVALID_RESULT_CODE,
    RunSettings,
    in_flight_status,
    pending_progress,
    report_part_of,
    run_fitting,
)
from fitting.services.jobs import IdempotencyConflict, JobRunner

S = StageStatus
W, R, OK, FAIL, SKIP = S.WAITING, S.RUNNING, S.SUCCEEDED, S.FAILED, S.SKIPPED
WAIT = 5  # 초. 기다리다 멈추는 상한일 뿐이고, 정상이면 바로 끝난다

VISUAL = (Stage.BODY_FIT, Stage.DRAPE, Stage.RENDER, Stage.REALIZE, Stage.VALIDATE)


@pytest.fixture
def request_for(body):
    def _make(
        garment_id: str = "tee_test",
        mode: Mode = Mode.FULL,
        consent: bool = True,
        height: float | None = None,
    ) -> FittingRequest:
        measurements = body.model_dump(exclude={"measurement_protocol_version"})
        if height is not None:
            measurements["height_cm"] = height
        return FittingRequest(
            garment_variant_id=garment_id,
            mode=mode,
            realize_consent=consent,
            measurements=Measurements(**measurements),
            measurement_protocol_version=body.measurement_protocol_version,
        )

    return _make


@pytest.fixture
def runners():
    """테스트가 만든 실행기를 끝에 모두 정리한다."""
    created: list[JobRunner] = []

    def _make(ports, **kwargs) -> JobRunner:
        runner = JobRunner(ports, **kwargs)
        created.append(runner)
        return runner

    yield _make
    for runner in created:
        runner.shutdown(wait=True, timeout=WAIT)


def _stages(status: FittingStatus) -> list[StageStatus]:
    by_stage = {p.stage: p.status for p in status.stages}
    return [by_stage[stage] for stage in VISUAL]


@dataclass
class GatedDraper(FakeDraper):
    """착장 단계에 들어오면 알리고, 풀어 줄 때까지 멈춰 선다."""

    entered: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)

    def drape(self, request, garment):
        self.entered.set()
        assert self.release.wait(WAIT), "테스트가 단계를 풀어 주지 않았다"
        return super().drape(request, garment)


@pytest.fixture
def gate():
    draper = GatedDraper()
    yield draper
    draper.release.set()  # 테스트가 실패해도 작업 스레드가 남지 않게 한다


# --- 정상 흐름 ----------------------------------------------------------------------------------


def test_submit_returns_the_report_and_a_queued_visual_path(request_for, make_garment, runners):
    runner = runners(fake_ports(fit=RuleFitCalculator()))

    first = runner.submit(request_for(), make_garment())

    assert first.job_id.startswith("fit_") and len(first.job_id) == len("fit_") + 12
    assert first.status is JobStatus.ACCEPTED
    assert first.report.status is OK
    assert [r.region.value for r in first.report.regions] == [
        "chest",
        "shoulder",
        "waist",
        "sleeve",
        "body_length",
    ]
    assert first.visual.status is S.QUEUED
    assert first.visual.quality_status is QualityStatus.PENDING
    assert _stages(first) == [W, W, W, W, W]
    runner.wait(first.job_id, WAIT)


@pytest.mark.parametrize(
    ("mode", "consent"),
    [(Mode.FULL, True), (Mode.FULL, False), (Mode.REPORT_ONLY, True)],
)
def test_final_status_equals_run_fitting(request_for, make_garment, runners, mode, consent):
    request = request_for(mode=mode, consent=consent)
    runner = runners(fake_ports())

    first = runner.submit(request, make_garment())
    final = runner.wait(first.job_id, WAIT)

    direct = run_fitting(
        request,
        make_garment(),
        fake_ports(),
        job_id=first.job_id,
        created_at=datetime.now(timezone.utc),
    )
    assert final == direct.status
    assert runner.get(first.job_id) == final


def test_no_consent_job_is_queued_with_realize_and_validate_already_skipped(
    request_for, make_garment, runners
):
    runner = runners(fake_ports())

    first = runner.submit(request_for(consent=False), make_garment())

    assert _stages(first) == [W, W, W, SKIP, SKIP]
    assert first.visual.skip_reason is VisualSkipReason.CONSENT_NOT_GIVEN
    assert first.visual.quality_status is None
    final = runner.wait(first.job_id, WAIT)
    assert final.status is JobStatus.SUCCEEDED
    assert final.visual.stage is Stage.RENDER


def test_report_only_finishes_inside_submit_without_a_thread(request_for, make_garment, runners):
    ports = fake_ports()
    runner = runners(ports)
    before = _worker_threads()

    status = runner.submit(request_for(mode=Mode.REPORT_ONLY), make_garment())

    assert status.status is JobStatus.SUCCEEDED
    assert status.visual.status is SKIP
    assert status.visual.skip_reason is VisualSkipReason.REPORT_ONLY
    assert _stages(status) == [SKIP] * 5
    assert runner.get(status.job_id) == status
    assert runner.wait(status.job_id, 0) == status  # 이미 끝났다
    assert _worker_threads() == before  # 스레드를 만들지 않았다
    for step in (ports.body, ports.drape, ports.render, ports.realize, ports.validate):
        assert step.calls == []


def test_get_returns_none_for_an_unknown_job(runners):
    runner = runners(fake_ports())

    assert runner.get("fit_nope") is None
    with pytest.raises(KeyError):
        runner.wait("fit_nope", 0)


def test_settings_reach_the_ports(request_for, make_garment, runners):
    ports = fake_ports()
    runner = runners(ports, settings=RunSettings(config_version="cfg_x", timeout_seconds=7))

    runner.wait(runner.submit(request_for(), make_garment()).job_id, WAIT)

    sent = ports.body.calls[0]
    assert (sent.config_version, sent.timeout_seconds) == ("cfg_x", 7)


# --- 진행 중 상태 -------------------------------------------------------------------------------


def test_a_stage_in_progress_is_visible_through_get(request_for, make_garment, runners, gate):
    runner = runners(fake_ports(drape=gate))
    job_id = runner.submit(request_for(), make_garment()).job_id

    assert gate.entered.wait(WAIT)
    running = runner.get(job_id)

    assert running.status is JobStatus.RUNNING
    assert running.visual.status is R
    assert running.visual.stage is Stage.DRAPE
    assert _stages(running) == [OK, R, W, W, W]  # 앞은 성공, 지금 단계 실행 중, 뒤는 대기
    assert running.visual.quality_status is QualityStatus.PENDING
    assert running.report.status is OK  # 리포트는 처음부터 있다
    assert running.visual.drape_preview_artifact_id is None  # 착장이 끝나기 전이다

    gate.release.set()
    final = runner.wait(job_id, WAIT)
    assert final.status is JobStatus.SUCCEEDED
    assert final.visual.drape_preview_artifact_id == f"{job_id}_drape_preview"


def test_report_is_the_same_in_every_state_of_the_job(request_for, make_garment, runners, gate):
    runner = runners(fake_ports(fit=RuleFitCalculator(), drape=gate))
    first = runner.submit(request_for(), make_garment())
    assert gate.entered.wait(WAIT)
    running = runner.get(first.job_id)
    gate.release.set()
    final = runner.wait(first.job_id, WAIT)

    assert first.report.regions == running.report.regions == final.report.regions
    assert first.report.summary == final.report.summary


def test_second_job_waits_while_the_first_runs(request_for, make_garment, runners, gate):
    runner = runners(fake_ports(drape=gate), max_workers=1)
    first = runner.submit(request_for(), make_garment())
    assert gate.entered.wait(WAIT)

    second = runner.submit(request_for(height=170.0), make_garment())
    waiting = runner.get(second.job_id)

    assert first.job_id != second.job_id
    assert waiting.status is JobStatus.ACCEPTED
    assert waiting.visual.status is S.QUEUED
    assert _stages(waiting) == [W, W, W, W, W]
    assert waiting.report.status is OK  # 대기 중에도 리포트는 이미 있다

    gate.release.set()
    assert runner.wait(first.job_id, WAIT).status is JobStatus.SUCCEEDED
    assert runner.wait(second.job_id, WAIT).status is JobStatus.SUCCEEDED


def test_two_workers_run_two_jobs_at_the_same_time(request_for, make_garment, runners):
    barrier = threading.Barrier(2, timeout=WAIT)

    class Meeting(FakeDraper):
        def drape(self, request, garment):
            barrier.wait()  # 두 작업이 동시에 여기 와야 지나간다
            return super().drape(request, garment)

    runner = runners(fake_ports(drape=Meeting()), max_workers=2)
    ids = [runner.submit(request_for(height=h), make_garment()).job_id for h in (170.0, 171.0)]

    assert [runner.wait(job_id, WAIT).status for job_id in ids] == [JobStatus.SUCCEEDED] * 2


def test_many_jobs_from_many_threads(request_for, make_garment, runners):
    runner = runners(fake_ports(), max_workers=4, max_jobs=500)
    ids: list[str] = []
    lock = threading.Lock()

    def submit_some() -> None:
        for _ in range(5):
            job_id = runner.submit(request_for(), make_garment()).job_id
            with lock:
                ids.append(job_id)

    threads = [threading.Thread(target=submit_some) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(WAIT)

    assert len(set(ids)) == 30
    assert {runner.wait(job_id, WAIT).status for job_id in ids} == {JobStatus.SUCCEEDED}


# --- 실패 ---------------------------------------------------------------------------------------

PORT_CASES = [
    ("body", FakeBodyBuilder, ErrorCode.BODY_FIT_NOT_CONVERGED, Stage.BODY_FIT),
    ("drape", FakeDraper, ErrorCode.DRAPE_INVALID, Stage.DRAPE),
    ("render", FakeRenderer, ErrorCode.RENDER_FAILED, Stage.RENDER),
    ("realize", FakeRealizer, ErrorCode.REALIZE_TIMEOUT, Stage.REALIZE),
    ("validate", FakeValidator, ErrorCode.VALIDATION_UNVERIFIABLE, Stage.VALIDATE),
]


@pytest.mark.parametrize(("port", "fake", "code", "stage"), PORT_CASES)
def test_failed_stage_keeps_the_report(request_for, make_garment, runners, port, fake, code, stage):
    runner = runners(fake_ports(fit=RuleFitCalculator(), **{port: fake(fail_with=code)}))

    first = runner.submit(request_for(), make_garment())
    final = runner.wait(first.job_id, WAIT)

    index = VISUAL.index(stage)
    assert final.status is JobStatus.PARTIAL_SUCCEEDED
    assert final.visual.status is FAIL
    assert final.visual.stage is stage
    assert final.visual.error_code is code
    assert _stages(final) == [OK] * index + [FAIL] + [SKIP] * (4 - index)
    assert final.report == first.report  # 핏 리포트는 처음 받은 그대로다
    assert final.report.regions == first.report.regions


@pytest.mark.parametrize(("port", "fake", "code", "stage"), PORT_CASES)
def test_unexpected_exception_fails_only_that_stage(
    request_for, make_garment, runners, caplog, port, fake, code, stage
):
    method = {"body": "build_body"}.get(port, port)
    broken = fake()

    def boom(*args, **kwargs):
        raise RuntimeError("내부 사정 비밀 문구")

    setattr(broken, method, boom)
    runner = runners(fake_ports(fit=RuleFitCalculator(), **{port: broken}))

    with caplog.at_level(logging.ERROR, logger="fitting.services.jobs"):
        first = runner.submit(request_for(), make_garment())
        final = runner.wait(first.job_id, WAIT)

    index = VISUAL.index(stage)
    assert final.status is JobStatus.PARTIAL_SUCCEEDED
    assert final.visual.status is FAIL
    assert final.visual.stage is stage
    assert final.visual.error_code is INVALID_RESULT_CODE[stage]
    assert _stages(final) == [OK] * index + [FAIL] + [SKIP] * (4 - index)
    assert final.report == first.report
    assert "비밀" not in final.model_dump_json()  # 예외 내용은 상태에 나가지 않는다
    logged = [str(r.exc_info[1]) for r in caplog.records if r.exc_info]
    assert "내부 사정 비밀 문구" in logged  # 로그에는 남는다


def test_a_validation_fail_verdict_is_a_partial_success(request_for, make_garment, runners):
    runner = runners(fake_ports(validate=FakeValidator(quality=QualityStatus.FAIL)))

    final = runner.wait(runner.submit(request_for(), make_garment()).job_id, WAIT)

    assert final.status is JobStatus.PARTIAL_SUCCEEDED
    assert final.visual.quality_status is QualityStatus.FAIL
    assert final.visual.error_code is ErrorCode.VALIDATION_FAILED


def test_an_error_outside_the_ports_still_ends_the_job(
    request_for, make_garment, runners, monkeypatch
):
    """run_fitting 자체가 예상 밖으로 죽어도 화면이 영원히 진행 중으로 남지 않는다."""

    def dies_while_draping(request, garment, ports, *, job_id, created_at, settings, on_progress):
        report = ports.fit.calculate(None, None, job_id=job_id, created_at=None)  # 저장한 리포트
        progress = pending_progress(realize_consent=True)
        progress.update({Stage.BODY_FIT: OK, Stage.DRAPE: R})
        running = in_flight_status(
            job_id,
            report_part_of(report),
            progress,
            realize_consent=True,
            stage=Stage.DRAPE,
        )
        on_progress(running)
        raise RuntimeError("run_fitting이 죽었다")

    monkeypatch.setattr(jobs_module, "run_fitting", dies_while_draping)
    runner = runners(fake_ports())

    first = runner.submit(request_for(), make_garment())
    final = runner.wait(first.job_id, WAIT)

    assert final.status is JobStatus.PARTIAL_SUCCEEDED
    assert final.visual.stage is Stage.DRAPE
    assert final.visual.error_code is ErrorCode.DRAPE_INVALID
    assert _stages(final) == [OK, FAIL, SKIP, SKIP, SKIP]
    assert final.report == first.report


def test_an_error_before_any_progress_fails_the_first_stage(
    request_for, make_garment, runners, monkeypatch
):
    def broken(*args, **kwargs):
        raise RuntimeError("시작하자마자 죽었다")

    monkeypatch.setattr(jobs_module, "run_fitting", broken)
    runner = runners(fake_ports())

    final = runner.wait(runner.submit(request_for(), make_garment()).job_id, WAIT)

    assert final.visual.stage is Stage.BODY_FIT
    assert final.visual.error_code is ErrorCode.BODY_FIT_NOT_CONVERGED
    assert _stages(final) == [FAIL, SKIP, SKIP, SKIP, SKIP]


# --- 핏 계산 ------------------------------------------------------------------------------------


def test_the_fit_calculator_runs_once_per_job(request_for, make_garment, runners):
    ports = fake_ports()
    runner = runners(ports)

    for mode in (Mode.FULL, Mode.REPORT_ONLY, Mode.FULL):
        runner.wait(
            runner.submit(request_for(mode=mode, height=170.0), make_garment()).job_id, WAIT
        )

    assert ports.fit.calls == 3


def test_a_queued_job_does_not_compute_the_report_again(request_for, make_garment, runners, gate):
    ports = fake_ports(drape=gate)
    runner = runners(ports)
    runner.submit(request_for(), make_garment())
    assert gate.entered.wait(WAIT)

    second = runner.submit(request_for(height=170.0), make_garment())

    assert ports.fit.calls == 2  # 둘 다 요청 스레드에서 바로 계산했다
    gate.release.set()
    runner.wait(second.job_id, WAIT)
    assert ports.fit.calls == 2  # 백그라운드에서는 다시 계산하지 않았다


@pytest.mark.parametrize(
    "status", [GarmentStatus.DRAFT, GarmentStatus.PREPARING, GarmentStatus.FAILED]
)
def test_garment_that_is_not_ready_creates_no_job(request_for, make_garment, runners, status):
    ports = fake_ports(fit=RuleFitCalculator())
    runner = runners(ports)

    with pytest.raises(FitEngineError) as raised:
        runner.submit(request_for(), make_garment(status=status))

    assert raised.value.code is ErrorCode.GARMENT_NOT_READY
    assert len(runner) == 0


def test_garment_that_cannot_be_computed_creates_no_job(request_for, make_garment, runners):
    runner = runners(fake_ports(fit=RuleFitCalculator()))

    with pytest.raises(FitEngineError) as raised:
        runner.submit(request_for(), make_garment(family="hoodie"))

    assert raised.value.code is ErrorCode.GARMENT_NOT_READY
    assert len(runner) == 0


def test_not_ready_garment_does_not_reach_the_calculator(request_for, make_garment, runners):
    ports = fake_ports()  # 가짜 계산기는 상태를 가리지 않지만, 실행기가 먼저 막는다
    runner = runners(ports)

    with pytest.raises(FitEngineError):
        runner.submit(request_for(), make_garment(status=GarmentStatus.DRAFT))

    assert ports.fit.calls == 0


def test_request_and_garment_must_match(request_for, make_garment, runners):
    runner = runners(fake_ports())

    with pytest.raises(ValueError, match="다릅니다"):
        runner.submit(request_for(garment_id="other"), make_garment())

    assert len(runner) == 0


# --- Idempotency-Key ----------------------------------------------------------------------------


def test_same_key_returns_the_existing_job(request_for, make_garment, runners, gate):
    ports = fake_ports(drape=gate)
    runner = runners(ports)
    first = runner.submit_job(request_for(), make_garment(), idempotency_key="k1")
    assert gate.entered.wait(WAIT)

    again = runner.submit_job(request_for(), make_garment(), idempotency_key="k1")

    assert first.created is True
    assert again.created is False
    assert again.status.job_id == first.status.job_id
    assert again.status.visual.stage is Stage.DRAPE  # 돌려주는 것은 지금 상태다
    assert len(runner) == 1
    assert ports.fit.calls == 1  # 다시 계산하지 않는다
    gate.release.set()
    runner.wait(first.status.job_id, WAIT)


def test_submit_with_a_repeated_key_returns_the_same_job(request_for, make_garment, runners):
    runner = runners(fake_ports())

    first = runner.submit(request_for(), make_garment(), idempotency_key="k1")
    again = runner.submit(request_for(), make_garment(), idempotency_key="k1")

    assert again.job_id == first.job_id
    runner.wait(first.job_id, WAIT)


def test_same_key_with_a_different_body_conflicts(request_for, make_garment, runners):
    ports = fake_ports()
    runner = runners(ports)
    runner.submit(request_for(), make_garment(), idempotency_key="k1")

    with pytest.raises(IdempotencyConflict) as raised:
        runner.submit(request_for(height=171.0), make_garment(), idempotency_key="k1")
    with pytest.raises(IdempotencyConflict):
        runner.submit(request_for(consent=False), make_garment(), idempotency_key="k1")

    assert raised.value.key == "k1"
    assert len(runner) == 1
    assert ports.fit.calls == 1


def test_different_keys_and_no_key_create_new_jobs(request_for, make_garment, runners):
    runner = runners(fake_ports())

    ids = {
        runner.submit(request_for(), make_garment(), idempotency_key="a").job_id,
        runner.submit(request_for(), make_garment(), idempotency_key="b").job_id,
        runner.submit(request_for(), make_garment()).job_id,
        runner.submit(request_for(), make_garment()).job_id,
    }

    assert len(ids) == 4


def test_a_report_only_job_can_be_replayed_by_key(request_for, make_garment, runners):
    runner = runners(fake_ports())
    request = request_for(mode=Mode.REPORT_ONLY)

    first = runner.submit_job(request, make_garment(), idempotency_key="r")
    again = runner.submit_job(request, make_garment(), idempotency_key="r")

    assert (first.created, again.created) == (True, False)
    assert again.status == first.status


def test_not_ready_garment_does_not_reserve_the_key(request_for, make_garment, runners):
    runner = runners(fake_ports(fit=RuleFitCalculator()))
    with pytest.raises(FitEngineError):
        runner.submit(request_for(), make_garment(status=GarmentStatus.DRAFT), idempotency_key="k")

    status = runner.submit(request_for(), make_garment(), idempotency_key="k")  # 이제 된다

    assert status.job_id.startswith("fit_")
    runner.wait(status.job_id, WAIT)


# --- 보관 개수 ----------------------------------------------------------------------------------


def test_oldest_finished_jobs_are_dropped_beyond_max_jobs(request_for, make_garment, runners):
    runner = runners(fake_ports(), max_jobs=2)

    ids = [
        runner.submit(request_for(mode=Mode.REPORT_ONLY, height=170.0 + i), make_garment()).job_id
        for i in range(4)
    ]

    assert [runner.get(job_id) is not None for job_id in ids] == [False, False, True, True]
    assert len(runner) == 2


def test_running_jobs_are_never_dropped(request_for, make_garment, runners, gate):
    runner = runners(fake_ports(drape=gate), max_jobs=2)
    running = runner.submit(request_for(), make_garment()).job_id
    assert gate.entered.wait(WAIT)

    done = [
        runner.submit(request_for(mode=Mode.REPORT_ONLY, height=170.0 + i), make_garment()).job_id
        for i in range(3)
    ]

    assert runner.get(running) is not None  # 진행 중이라 남는다
    assert [runner.get(job_id) is not None for job_id in done] == [False, False, True]
    gate.release.set()
    assert runner.wait(running, WAIT).status is JobStatus.SUCCEEDED
    assert runner.get(running) is not None


def test_a_job_that_finishes_over_the_limit_is_dropped_before_the_next_one_starts(
    request_for, make_garment, runners, gate
):
    runner = runners(fake_ports(drape=gate), max_jobs=1, max_workers=1)
    first = runner.submit(request_for(), make_garment()).job_id
    assert gate.entered.wait(WAIT)
    second = runner.submit(request_for(height=170.0), make_garment()).job_id
    assert len(runner) == 2  # 둘 다 끝나지 않아서 아무것도 지우지 않는다

    gate.release.set()
    final = runner.wait(second, WAIT)  # 첫 작업이 끝난 뒤에야 둘째가 시작한다

    assert runner.get(first) is None  # 한도를 넘겨서 끝난 오래된 작업이 지워졌다
    assert runner.get(second) == final
    assert len(runner) == 1


def test_dropping_a_job_frees_its_idempotency_key(request_for, make_garment, runners):
    runner = runners(fake_ports(), max_jobs=1)
    request = request_for(mode=Mode.REPORT_ONLY)
    old = runner.submit(request, make_garment(), idempotency_key="k").job_id
    runner.submit(request_for(mode=Mode.REPORT_ONLY, height=170.0), make_garment())
    assert runner.get(old) is None

    new = runner.submit(request, make_garment(), idempotency_key="k")

    assert new.job_id != old  # 지워진 작업은 키로도 찾을 수 없다


# --- 시작과 종료 --------------------------------------------------------------------------------


def _worker_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith("fit-job-")]


def test_workers_start_lazily_and_are_daemons(request_for, make_garment, runners):
    before = _worker_threads()
    runner = runners(fake_ports(), max_workers=3)
    assert _worker_threads() == before  # 실행기를 만든 것만으로는 스레드가 없다

    runner.wait(runner.submit(request_for(), make_garment()).job_id, WAIT)

    started = [t for t in _worker_threads() if t not in before]
    assert len(started) == 3
    assert all(t.daemon for t in started)  # 서버를 끌 때 프로세스를 붙잡지 않는다


def test_shutdown_cancels_waiting_jobs_and_lets_the_running_one_finish(
    request_for, make_garment, runners, gate
):
    runner = runners(fake_ports(drape=gate), max_workers=1)
    running = runner.submit(request_for(), make_garment()).job_id
    assert gate.entered.wait(WAIT)
    waiting = runner.submit(request_for(height=170.0), make_garment()).job_id

    runner.shutdown()

    cancelled = runner.wait(waiting, 0)  # 기다리던 작업은 바로 끝난다
    assert cancelled.status is JobStatus.CANCELLED
    assert cancelled.visual.status is S.CANCELLED
    assert _stages(cancelled) == [S.CANCELLED] * 5
    assert cancelled.report.status is OK  # 리포트는 남는다
    gate.release.set()
    assert (
        runner.wait(running, WAIT).status is JobStatus.SUCCEEDED
    )  # 실행 중이던 작업은 끝까지 간다


def test_shutdown_ends_idle_workers_and_is_repeatable(request_for, make_garment, runners):
    before = _worker_threads()
    runner = runners(fake_ports(), max_workers=2)
    runner.wait(runner.submit(request_for(), make_garment()).job_id, WAIT)

    runner.shutdown(wait=True, timeout=WAIT)
    runner.shutdown(wait=True, timeout=WAIT)

    assert _worker_threads() == before


def test_no_new_job_after_shutdown(request_for, make_garment, runners):
    runner = runners(fake_ports())
    runner.shutdown()

    with pytest.raises(RuntimeError, match="종료"):
        runner.submit(request_for(), make_garment())


def test_wait_times_out_while_the_job_is_running(request_for, make_garment, runners, gate):
    runner = runners(fake_ports(drape=gate))
    job_id = runner.submit(request_for(), make_garment()).job_id
    assert gate.entered.wait(WAIT)

    with pytest.raises(TimeoutError):
        runner.wait(job_id, 0.01)

    gate.release.set()
    runner.wait(job_id, WAIT)


@pytest.mark.parametrize(("workers", "jobs"), [(0, 1), (-1, 1), (1, 0), (1, -5)])
def test_limits_must_be_positive(workers, jobs):
    with pytest.raises(ValueError):
        JobRunner(fake_ports(), max_workers=workers, max_jobs=jobs)
