"""가짜 영상 단계 지연 `with_stage_delay` (#12). 실제로 기다리지 않고 `sleep`을 바꿔 확인한다."""

from datetime import datetime, timezone

import pytest

from fitting.adapters.fake import fake_ports, with_stage_delay
from fitting.domain import FittingRequest, FittingStatus, Measurements, Stage, StageStatus
from fitting.services.fitting import run_fitting

JOB = "job_001"
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


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


def _run(request, garment, ports, on_progress=None):
    return run_fitting(request, garment, ports, job_id=JOB, created_at=NOW, on_progress=on_progress)


def test_zero_seconds_returns_the_same_ports():
    ports = fake_ports()

    assert with_stage_delay(ports, 0) is ports
    assert with_stage_delay(ports, 0.0) is ports


@pytest.mark.parametrize("seconds", [-1, -0.001, float("nan"), float("inf")])
def test_invalid_seconds_are_rejected(seconds):
    with pytest.raises(ValueError, match="지연 시간"):
        with_stage_delay(fake_ports(), seconds)


def test_each_video_stage_sleeps_once_before_its_call(request_for, make_garment):
    ports = fake_ports()
    naps: list[tuple[float, int]] = []

    def sleep(seconds: float) -> None:
        # 쉬는 시점에 몇 단계가 이미 불렸는지 기록한다
        done = sum(len(step.calls) for step in (ports.body, ports.drape, ports.render))
        done += len(ports.realize.calls) + len(ports.validate.calls)
        naps.append((seconds, done))

    outcome = _run(request_for(), make_garment(), with_stage_delay(ports, 1.5, sleep=sleep))

    # 단계마다 한 번, 그 단계가 불리기 직전에 (0, 1, 2, 3, 4개가 끝난 시점)
    assert naps == [(1.5, 0), (1.5, 1), (1.5, 2), (1.5, 3), (1.5, 4)]
    assert outcome.status.visual.status is StageStatus.SUCCEEDED


def test_stage_is_running_while_it_waits(request_for, make_garment):
    """쉬는 동안 화면에는 그 단계가 RUNNING으로 보인다."""
    seen: list[FittingStatus] = []
    during: list[tuple[Stage | None, StageStatus]] = []

    def sleep(_: float) -> None:
        last = seen[-1]
        stage = last.visual.stage
        during.append((stage, {p.stage: p.status for p in last.stages}[stage]))

    _run(request_for(), make_garment(), with_stage_delay(fake_ports(), 2, sleep=sleep), seen.append)

    assert during == [
        (Stage.BODY_FIT, StageStatus.RUNNING),
        (Stage.DRAPE, StageStatus.RUNNING),
        (Stage.RENDER, StageStatus.RUNNING),
        (Stage.REALIZE, StageStatus.RUNNING),
        (Stage.VALIDATE, StageStatus.RUNNING),
    ]


def test_stages_that_are_skipped_do_not_sleep(request_for, make_garment):
    naps: list[float] = []

    _run(
        request_for(consent=False),
        make_garment(),
        with_stage_delay(fake_ports(), 1, sleep=naps.append),
    )

    assert naps == [1, 1, 1]  # 몸·착장·렌더만. 실사화·검증은 건너뛴다


def test_results_do_not_change(request_for, make_garment):
    plain = _run(request_for(), make_garment(), fake_ports())

    delayed = _run(
        request_for(), make_garment(), with_stage_delay(fake_ports(), 3, sleep=lambda s: None)
    )

    assert delayed == plain


def test_the_fit_calculator_is_not_delayed_and_attributes_pass_through(request_for, make_garment):
    naps: list[float] = []
    ports = fake_ports()
    delayed = with_stage_delay(ports, 1, sleep=naps.append)

    assert delayed.fit is ports.fit
    _run(request_for(), make_garment(), delayed)

    assert ports.fit.calls == 1  # 핏 계산은 한 번, 지연 없음
    assert len(naps) == 5
    assert delayed.drape.calls is ports.drape.calls  # 가짜 어댑터의 기록 속성이 그대로 보인다
    assert len(delayed.drape.calls) == 1
