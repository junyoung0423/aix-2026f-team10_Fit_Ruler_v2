"""피팅 흐름(run_fitting)에 실제 핏 계산기를 넣었을 때 (#2).

계산기가 `FitEngineError`를 던지면 리포트 경로 FAILED + 오류 코드, 영상 경로 SKIPPED,
작업 FAILED로 끝나고 영상 어댑터는 부르지 않는다. 실사화 동의 여부와 상관없이 같다.
B1 리뷰 계약: 판정이 없으므로 quality_status·quality_metrics는 null, 사용자가 고른 건너뜀이
아니므로 skip_reason은 null, 리포트가 없으므로 summary는 null·warnings는 [].
"""

from datetime import datetime, timezone

import pytest

from fitting.adapters.fake import fake_ports
from fitting.domain import (
    ErrorCode,
    FittingRequest,
    JobStatus,
    Mode,
    Stage,
    StageStatus,
)
from fitting.fit import RuleFitCalculator
from fitting.services.fitting import run_fitting

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _request(body, mode: Mode = Mode.FULL, consent: bool = True) -> FittingRequest:
    return FittingRequest(
        garment_variant_id="tee_test",
        mode=mode,
        realize_consent=consent,
        measurements=body.model_dump(exclude={"measurement_protocol_version"}),
        measurement_protocol_version=body.measurement_protocol_version,
    )


def _run(body, garment, mode=Mode.FULL, consent=True):
    ports = fake_ports(fit=RuleFitCalculator())
    request = _request(body, mode, consent)
    outcome = run_fitting(request, garment, ports, job_id="job_1", created_at=NOW)
    return outcome, ports


def test_real_calculator_runs_in_the_flow(body, make_garment):
    outcome, _ = _run(body, make_garment())
    assert outcome.status.status is JobStatus.SUCCEEDED
    assert outcome.report is not None
    assert outcome.report.rules_version == "fit_rules_v0"
    assert outcome.status.report.regions == outcome.report.regions
    assert outcome.status.report.summary == outcome.report.summary
    assert outcome.status.report.warnings == outcome.report.warnings


@pytest.mark.parametrize(
    "garment_args",
    [
        {"family": "hoodie"},  # 기준표가 적용되지 않는 의류군
        {"protocol": "measure_v0"},  # 신체와 의류의 측정 프로토콜이 다름
    ],
)
@pytest.mark.parametrize(
    ("mode", "consent"),
    [(Mode.FULL, True), (Mode.FULL, False), (Mode.REPORT_ONLY, True)],
)
def test_fit_engine_error_fails_the_job_and_skips_visual(
    body, make_garment, garment_args, mode, consent
):
    outcome, ports = _run(body, make_garment(**garment_args), mode, consent)
    status = outcome.status
    assert outcome.report is None
    assert status.status is JobStatus.FAILED
    assert status.report.status is StageStatus.FAILED
    assert status.report.error_code is ErrorCode.GARMENT_NOT_READY
    assert status.report.regions is None
    assert status.report.summary is None
    assert status.report.warnings == []
    assert status.visual.status is StageStatus.SKIPPED
    assert status.visual.error_code is None
    assert status.visual.quality_status is None  # 검증 판정 없음 (B1 규칙)
    assert status.visual.skip_reason is None  # 사용자가 고른 건너뜀이 아니다
    assert status.visual.quality_metrics is None
    assert status.visual.render_frame_artifact_id is None
    stages = {s.stage: s.status for s in status.stages}
    assert stages[Stage.FIT_CALCULATE] is StageStatus.FAILED
    assert all(
        st is StageStatus.SKIPPED
        for stage, st in stages.items()
        if stage is not Stage.FIT_CALCULATE
    )
    # 영상 어댑터는 부르지 않는다
    for port in (ports.body, ports.drape, ports.render, ports.realize, ports.validate):
        assert port.calls == []


def test_status_does_not_carry_the_internal_reason(body, make_garment):
    outcome, _ = _run(body, make_garment(protocol="measure_v0"))
    text = outcome.status.model_dump_json()
    assert "measure_v0" not in text
    assert "프로토콜" not in text
