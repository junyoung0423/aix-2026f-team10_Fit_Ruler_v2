"""run_fitting의 진행 알림 `on_progress` (#12)."""

import logging
from datetime import datetime, timezone

import pytest

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
    GarmentVariant,
    JobStatus,
    Measurements,
    Mode,
    QualityStatus,
    Stage,
    StageStatus,
    VisualSkipReason,
)
from fitting.fit import RuleFitCalculator, compute_report
from fitting.services.fitting import (
    VISUAL_STAGES,
    in_flight_status,
    pending_progress,
    report_part_of,
    run_fitting,
)
from tools.export_contracts import EXAMPLE_CREATED_AT, EXAMPLE_JOB_ID, build_running_status

from .conftest import EXAMPLES

JOB = "job_001"
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

S = StageStatus
W, R, OK, FAIL, SKIP = S.WAITING, S.RUNNING, S.SUCCEEDED, S.FAILED, S.SKIPPED


@pytest.fixture
def request_for(body):
    def _make(mode: Mode = Mode.FULL, consent: bool = True) -> FittingRequest:
        return FittingRequest(
            garment_variant_id="tee_test",
            mode=mode,
            realize_consent=consent,
            measurements=Measurements(**body.model_dump(exclude={"measurement_protocol_version"})),
            measurement_protocol_version=body.measurement_protocol_version,
        )

    return _make


def _run(request, garment, ports, on_progress=None):
    return run_fitting(request, garment, ports, job_id=JOB, created_at=NOW, on_progress=on_progress)


def _collect(request, garment, ports):
    seen: list[FittingStatus] = []
    outcome = _run(request, garment, ports, seen.append)
    return outcome, seen


def _stages(status: FittingStatus) -> list[StageStatus]:
    """영상 경로 다섯 단계의 상태."""
    by_stage = {p.stage: p.status for p in status.stages}
    return [by_stage[stage] for stage in VISUAL_STAGES]


# --- 호출 순서 ----------------------------------------------------------------------------------


def test_full_flow_reports_each_stage_start_and_success(request_for, make_garment):
    outcome, seen = _collect(request_for(), make_garment(), fake_ports())

    # (작업, 영상 경로, 지금 단계, 다섯 단계의 상태)
    assert [(s.status, s.visual.status, s.visual.stage, _stages(s)) for s in seen] == [
        (JobStatus.RUNNING, R, None, [W, W, W, W, W]),
        (JobStatus.RUNNING, R, Stage.BODY_FIT, [R, W, W, W, W]),
        (JobStatus.RUNNING, R, Stage.BODY_FIT, [OK, W, W, W, W]),
        (JobStatus.RUNNING, R, Stage.DRAPE, [OK, R, W, W, W]),
        (JobStatus.RUNNING, R, Stage.DRAPE, [OK, OK, W, W, W]),
        (JobStatus.RUNNING, R, Stage.RENDER, [OK, OK, R, W, W]),
        (JobStatus.RUNNING, R, Stage.RENDER, [OK, OK, OK, W, W]),
        (JobStatus.RUNNING, R, Stage.REALIZE, [OK, OK, OK, R, W]),
        (JobStatus.RUNNING, R, Stage.REALIZE, [OK, OK, OK, OK, W]),
        (JobStatus.RUNNING, R, Stage.VALIDATE, [OK, OK, OK, OK, R]),
        (JobStatus.SUCCEEDED, OK, Stage.VALIDATE, [OK, OK, OK, OK, OK]),
    ]
    assert seen[-1] == outcome.status


def test_report_is_in_every_status_from_the_first_call(request_for, make_garment):
    outcome, seen = _collect(request_for(), make_garment(), fake_ports())

    assert outcome.report is not None
    for status in seen:
        assert status.report.status is OK
        assert status.report.regions == outcome.report.regions
        assert [p.status for p in status.stages[:2]] == [OK, OK]  # FIT_CALCULATE, FIT_REPORT


def test_artifacts_appear_as_soon_as_their_stage_finishes(request_for, make_garment):
    _, seen = _collect(request_for(), make_garment(), fake_ports())
    ids = [
        (
            s.visual.drape_preview_artifact_id,
            s.visual.render_artifact_id,
            s.visual.video_artifact_id,
        )
        for s in seen
    ]

    assert ids[0] == (None, None, None)
    assert ids[3] == (None, None, None)  # 착장이 실행 중일 때는 아직 없다
    assert ids[4] == (f"{JOB}_drape_preview", None, None)
    assert ids[6] == (f"{JOB}_drape_preview", f"{JOB}_render_clip", None)
    assert ids[8] == (f"{JOB}_drape_preview", f"{JOB}_render_clip", f"{JOB}_video")
    # 검증이 끝나기 전에는 판정이 없다: 실행 중에는 PENDING, 끝나면 PASS
    assert {s.visual.quality_status for s in seen[:-1]} == {QualityStatus.PENDING}
    assert seen[-1].visual.quality_status is QualityStatus.PASS


def test_a_status_is_not_changed_by_later_progress(request_for, make_garment):
    """알림으로 받은 상태는 스냅샷이다. 뒤 단계가 진행돼도 앞에서 받은 상태는 그대로다."""
    _, seen = _collect(request_for(), make_garment(), fake_ports())

    assert _stages(seen[0]) == [W, W, W, W, W]
    assert seen[0].visual.drape_preview_artifact_id is None
    assert len({id(s) for s in seen}) == len(seen)


def test_the_port_runs_while_its_stage_is_announced_as_running(request_for, make_garment):
    seen: list[FittingStatus] = []
    during: dict[str, FittingStatus] = {}

    class Spy(FakeDraper):
        def drape(self, request, garment):
            during["status"] = seen[-1]
            return super().drape(request, garment)

    _run(request_for(), make_garment(), fake_ports(drape=Spy()), seen.append)

    assert during["status"].visual.stage is Stage.DRAPE
    assert _stages(during["status"]) == [OK, R, W, W, W]


# --- 최종 상태 ----------------------------------------------------------------------------------


def _failing_cases():
    return [
        ("body", FakeBodyBuilder(fail_with=ErrorCode.BODY_FIT_NOT_CONVERGED), 0),
        ("drape", FakeDraper(fail_with=ErrorCode.DRAPE_INVALID), 1),
        ("render", FakeRenderer(fail_with=ErrorCode.RENDER_FAILED), 2),
        ("realize", FakeRealizer(fail_with=ErrorCode.REALIZE_TIMEOUT), 3),
        ("validate", FakeValidator(fail_with=ErrorCode.VALIDATION_UNVERIFIABLE), 4),
    ]


@pytest.mark.parametrize(("port", "fake", "index"), _failing_cases())
def test_failure_is_announced_by_the_last_call(request_for, make_garment, port, fake, index):
    outcome, seen = _collect(request_for(), make_garment(), fake_ports(**{port: fake}))

    final = seen[-1]
    assert final == outcome.status
    assert final.status is JobStatus.PARTIAL_SUCCEEDED
    assert final.visual.status is FAIL
    # 실패한 단계 앞은 성공, 실패한 단계는 FAILED, 뒤는 SKIPPED
    expected = [OK] * index + [FAIL] + [SKIP] * (4 - index)
    assert _stages(final) == expected
    # 실패 직전까지는 진행 중으로 알렸고, 그 단계가 RUNNING인 상태도 있었다
    assert all(s.status is JobStatus.RUNNING for s in seen[:-1])
    assert any(_stages(s)[index] is R for s in seen[:-1])
    assert final.report.regions == outcome.report.regions


def test_validation_fail_verdict_is_announced_as_final(request_for, make_garment):
    ports = fake_ports(validate=FakeValidator(quality=QualityStatus.FAIL))

    outcome, seen = _collect(request_for(), make_garment(), ports)

    assert seen[-1] == outcome.status
    assert seen[-1].visual.quality_status is QualityStatus.FAIL
    assert seen[-1].visual.error_code is ErrorCode.VALIDATION_FAILED


def test_no_consent_skips_realize_and_validate_from_the_start(request_for, make_garment):
    outcome, seen = _collect(request_for(consent=False), make_garment(), fake_ports())

    for status in seen:
        assert _stages(status)[3:] == [SKIP, SKIP]
        assert status.visual.skip_reason is VisualSkipReason.CONSENT_NOT_GIVEN
        assert status.visual.quality_status is None
    assert [(s.status, s.visual.stage, _stages(s)[:3]) for s in seen] == [
        (JobStatus.RUNNING, None, [W, W, W]),
        (JobStatus.RUNNING, Stage.BODY_FIT, [R, W, W]),
        (JobStatus.RUNNING, Stage.BODY_FIT, [OK, W, W]),
        (JobStatus.RUNNING, Stage.DRAPE, [OK, R, W]),
        (JobStatus.RUNNING, Stage.DRAPE, [OK, OK, W]),
        (JobStatus.RUNNING, Stage.RENDER, [OK, OK, R]),
        (JobStatus.SUCCEEDED, Stage.RENDER, [OK, OK, OK]),  # 렌더가 끝이라 최종 상태로 알린다
    ]
    assert seen[-1] == outcome.status
    assert outcome.status.visual.status is OK


def test_report_only_announces_the_final_status_once(request_for, make_garment):
    outcome, seen = _collect(request_for(mode=Mode.REPORT_ONLY), make_garment(), fake_ports())

    assert seen == [outcome.status]
    assert outcome.status.status is JobStatus.SUCCEEDED
    assert outcome.status.visual.status is SKIP
    assert outcome.status.visual.skip_reason is VisualSkipReason.REPORT_ONLY


def test_garment_not_ready_announces_the_failed_status_once(request_for, make_garment):
    garment = make_garment(status=GarmentStatus.DRAFT)

    outcome, seen = _collect(request_for(), garment, fake_ports())

    assert seen == [outcome.status]
    assert outcome.status.status is JobStatus.FAILED
    assert outcome.report is None


def test_fit_engine_error_announces_the_failed_status_once(request_for, make_garment):
    garment = make_garment(family="hoodie")  # 핏 기준표가 적용되지 않는 의류군
    ports = fake_ports(fit=RuleFitCalculator())

    outcome, seen = _collect(request_for(), garment, ports)

    assert seen == [outcome.status]
    assert outcome.status.report.status is FAIL
    assert outcome.status.report.error_code is ErrorCode.GARMENT_NOT_READY


# --- on_progress가 없거나 고장 나도 -------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "consent"),
    [(Mode.FULL, True), (Mode.FULL, False), (Mode.REPORT_ONLY, True)],
)
def test_result_is_the_same_with_and_without_a_callback(request_for, make_garment, mode, consent):
    request = request_for(mode=mode, consent=consent)

    without = _run(request, make_garment(), fake_ports())
    with_callback = _run(request, make_garment(), fake_ports(), lambda status: None)

    assert with_callback == without


def test_callback_exception_does_not_stop_the_fitting(request_for, make_garment, caplog):
    calls = []

    def broken(status):
        calls.append(status)
        raise RuntimeError("받는 쪽이 고장 났다")

    expected = _run(request_for(), make_garment(), fake_ports())
    with caplog.at_level(logging.ERROR, logger="fitting.services.fitting"):
        outcome = _run(request_for(), make_garment(), fake_ports(), broken)

    assert outcome == expected
    assert len(calls) == 11  # 첫 호출이 실패해도 계속 알린다
    assert any("진행 상태를 전달하지 못했습니다" in record.message for record in caplog.records)


# --- 상태를 만드는 보조 함수 ---------------------------------------------------------------------


def test_running_status_has_the_shape_of_the_contract_example():
    """실사화 실행 중 상태가 계약 예시 `fitting_status_running.json`과 같다."""
    request = FittingRequest.model_validate_json(
        (EXAMPLES / "fitting_request.json").read_text(encoding="utf-8")
    )
    garment = GarmentVariant.model_validate_json(
        (EXAMPLES / "garment.json").read_text(encoding="utf-8")
    )
    seen: list[FittingStatus] = []
    outcome = run_fitting(
        request,
        garment,
        fake_ports(fit=RuleFitCalculator()),
        job_id=EXAMPLE_JOB_ID,
        created_at=EXAMPLE_CREATED_AT,
        on_progress=seen.append,
    )
    assert outcome.report is not None

    realize_running = next(
        s for s in seen if s.visual.stage is Stage.REALIZE and _stages(s)[3] is R
    )

    assert realize_running == build_running_status(outcome.report)


def test_queued_status_for_a_job_that_has_not_started(body, make_garment):
    report = compute_report(body, make_garment(), job_id=JOB, created_at=NOW)
    part = report_part_of(report)

    queued = in_flight_status(
        JOB, part, pending_progress(realize_consent=True), realize_consent=True, queued=True
    )

    assert queued.status is JobStatus.ACCEPTED
    assert queued.visual.status is S.QUEUED
    assert queued.visual.stage is None
    assert queued.visual.quality_status is QualityStatus.PENDING
    assert _stages(queued) == [W, W, W, W, W]
    assert queued.report.regions == report.regions


def test_pending_progress_without_consent_skips_realize_and_validate():
    assert list(pending_progress(realize_consent=False).values()) == [OK, OK, W, W, W, SKIP, SKIP]
    assert list(pending_progress(realize_consent=True).values()) == [OK, OK, W, W, W, W, W]
