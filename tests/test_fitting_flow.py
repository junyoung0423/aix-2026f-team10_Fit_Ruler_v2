"""가짜 어댑터로 피팅 전체 흐름을 돌린다 (#1 완료 조건, #12 실패 처리).

핏 리포트 → 몸 → 착장 → 렌더 → 실사화 → 검증 순서로 산출물이 넘어가는지,
어느 영상 단계가 실패해도 핏 리포트와 이미 만든 3D 미리보기가 남는지 확인한다.
"""

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
    FitLabel,
    FittingRequest,
    GarmentStatus,
    JobStatus,
    Measurements,
    Mode,
    QualityStatus,
    Region,
    Stage,
    StageStatus,
)
from fitting.services.fitting import VISUAL_STAGES, run_fitting

JOB = "job_001"
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def request_for(body):
    def _make(
        garment_id: str = "tee_test", mode: Mode = Mode.FULL, consent: bool = True
    ) -> FittingRequest:
        return FittingRequest(
            garment_variant_id=garment_id,
            mode=mode,
            realize_consent=consent,
            measurements=Measurements(**body.model_dump(exclude={"measurement_protocol_version"})),
            measurement_protocol_version=body.measurement_protocol_version,
        )

    return _make


def _run(request, garment, ports):
    return run_fitting(request, garment, ports, job_id=JOB, created_at=NOW)


def _stage_map(status):
    return {p.stage: p.status for p in status.stages}


# --- 정상 흐름 ---------------------------------------------------------------------------------


def test_full_flow_succeeds_with_fake_adapters(request_for, make_garment):
    ports = fake_ports()
    outcome = _run(request_for(), make_garment(), ports)
    status = outcome.status

    assert status.status is JobStatus.SUCCEEDED
    assert status.report.status is StageStatus.SUCCEEDED
    assert status.visual.status is StageStatus.SUCCEEDED
    assert status.visual.quality_status is QualityStatus.PASS
    assert set(_stage_map(status).values()) == {StageStatus.SUCCEEDED}
    assert [p.stage for p in status.stages] == [
        Stage.FIT_CALCULATE,
        Stage.FIT_REPORT,
        *VISUAL_STAGES,
    ]
    assert status.visual.drape_preview_artifact_id == f"{JOB}_drape_preview"
    assert status.visual.render_artifact_id == f"{JOB}_render_clip"
    assert status.visual.video_artifact_id == f"{JOB}_video"
    assert status.visual.frame_artifact_id == f"{JOB}_frame"
    assert outcome.report is not None
    assert status.report.regions == outcome.report.regions


def test_artifacts_are_passed_to_the_next_stage(request_for, make_garment):
    ports = fake_ports()
    _run(request_for(), make_garment(), ports)

    assert ports.body.calls[0].input_artifact_ids == []
    assert ports.drape.calls[0].input_artifact_ids == [f"{JOB}_body_mesh"]
    assert ports.render.calls[0].input_artifact_ids == [f"{JOB}_drape"]
    assert ports.realize.calls[0].input_artifact_ids == [f"{JOB}_render_clip"]
    assert ports.validate.calls[0].input_artifact_ids == [f"{JOB}_render_clip", f"{JOB}_video"]
    for step in (ports.body, ports.drape, ports.render, ports.realize, ports.validate):
        assert step.calls[0].job_id == JOB
        assert step.calls[0].trace_id == JOB


def test_fake_report_has_every_region_and_ease(request_for, make_garment):
    report = _run(request_for(), make_garment(), fake_ports()).report
    assert [r.region for r in report.regions] == list(Region)
    chest = report.regions[0]
    assert chest.ease_cm == pytest.approx(8.0)  # 옷 108 - 몸 100
    assert report.warnings  # 가짜 결과라는 표시


def test_report_only_skips_the_visual_path(request_for, make_garment):
    ports = fake_ports()
    status = _run(request_for(mode=Mode.REPORT_ONLY), make_garment(), ports).status

    assert status.status is JobStatus.SUCCEEDED
    assert status.report.status is StageStatus.SUCCEEDED
    assert status.visual.status is StageStatus.SKIPPED
    assert all(_stage_map(status)[s] is StageStatus.SKIPPED for s in VISUAL_STAGES)
    assert ports.body.calls == []


# --- 영상 단계가 실패해도 핏 리포트는 남는다 (#12) ---------------------------------------------

FAILURES = [
    ("body", FakeBodyBuilder, Stage.BODY_FIT, ErrorCode.BODY_FIT_NOT_CONVERGED),
    ("drape", FakeDraper, Stage.DRAPE, ErrorCode.DRAPE_INVALID),
    ("render", FakeRenderer, Stage.RENDER, ErrorCode.RENDER_FAILED),
    ("realize", FakeRealizer, Stage.REALIZE, ErrorCode.REALIZE_TIMEOUT),
    ("validate", FakeValidator, Stage.VALIDATE, ErrorCode.VALIDATION_UNVERIFIABLE),
]


@pytest.mark.parametrize(("port", "fake", "stage", "code"), FAILURES)
def test_visual_failure_keeps_the_fit_report(request_for, make_garment, port, fake, stage, code):
    ports = fake_ports(**{port: fake(fail_with=code)})
    outcome = _run(request_for(), make_garment(), ports)
    status = outcome.status

    assert status.status is JobStatus.PARTIAL_SUCCEEDED
    assert status.report.status is StageStatus.SUCCEEDED
    assert status.report.regions == outcome.report.regions
    assert status.visual.status is StageStatus.FAILED
    assert status.visual.stage is stage
    assert status.visual.error_code is code

    stages = _stage_map(status)
    assert stages[stage] is StageStatus.FAILED
    after = VISUAL_STAGES[VISUAL_STAGES.index(stage) + 1 :]
    assert all(stages[s] is StageStatus.SKIPPED for s in after)
    # 실패한 단계 뒤의 어댑터는 부르지 않는다
    for later in after:
        name = {
            Stage.DRAPE: "drape",
            Stage.RENDER: "render",
            Stage.REALIZE: "realize",
            Stage.VALIDATE: "validate",
        }[later]
        assert getattr(ports, name).calls == []


def test_realize_failure_keeps_3d_preview_and_render(request_for, make_garment):
    ports = fake_ports(realize=FakeRealizer(fail_with=ErrorCode.REALIZE_TIMEOUT))
    visual = _run(request_for(), make_garment(), ports).status.visual
    assert visual.drape_preview_artifact_id == f"{JOB}_drape_preview"
    assert visual.render_artifact_id == f"{JOB}_render_clip"
    assert visual.video_artifact_id is None
    # 실사화가 실패한 결과를 실사화 성공으로 보이지 않는다
    assert visual.quality_status is QualityStatus.PENDING


@pytest.mark.parametrize(
    ("port", "fake", "stage", "code"),
    [
        ("body", FakeBodyBuilder, Stage.BODY_FIT, ErrorCode.BODY_FIT_NOT_CONVERGED),
        ("drape", FakeDraper, Stage.DRAPE, ErrorCode.DRAPE_INVALID),
        ("render", FakeRenderer, Stage.RENDER, ErrorCode.RENDER_FAILED),
        ("realize", FakeRealizer, Stage.REALIZE, ErrorCode.EXTERNAL_SUBMIT_UNKNOWN),
    ],
)
def test_result_without_artifacts_is_a_stage_failure(
    request_for, make_garment, port, fake, stage, code
):
    ports = fake_ports(**{port: fake(no_artifacts=True)})
    status = _run(request_for(), make_garment(), ports).status
    assert status.status is JobStatus.PARTIAL_SUCCEEDED
    assert status.visual.stage is stage
    assert status.visual.error_code is code


# --- 자동 검증 (#11) --------------------------------------------------------------------------


def test_validation_fail_is_marked_apart_from_success(request_for, make_garment):
    ports = fake_ports(validate=FakeValidator(quality=QualityStatus.FAIL))
    status = _run(request_for(), make_garment(), ports).status

    assert status.status is JobStatus.PARTIAL_SUCCEEDED
    assert status.visual.status is StageStatus.FAILED
    assert status.visual.quality_status is QualityStatus.FAIL
    assert status.visual.error_code is ErrorCode.VALIDATION_FAILED
    assert _stage_map(status)[Stage.VALIDATE] is StageStatus.FAILED
    # 영상과 대표 프레임은 남겨 두어 왜 실패했는지 볼 수 있다
    assert status.visual.video_artifact_id == f"{JOB}_video"
    assert status.visual.frame_artifact_id == f"{JOB}_frame"


@pytest.mark.parametrize("quality", [QualityStatus.UNVERIFIABLE, QualityStatus.PENDING])
def test_unverifiable_validation_is_not_a_pass(request_for, make_garment, quality):
    ports = fake_ports(validate=FakeValidator(quality=quality))
    status = _run(request_for(), make_garment(), ports).status
    assert status.visual.status is StageStatus.FAILED
    assert status.visual.quality_status is QualityStatus.UNVERIFIABLE
    assert status.visual.error_code is ErrorCode.VALIDATION_UNVERIFIABLE


# --- 의류 준비 상태와 요청 확인 -----------------------------------------------------------------


@pytest.mark.parametrize("garment_status", [GarmentStatus.DRAFT, GarmentStatus.FAILED])
def test_garment_not_ready_fails_without_calling_adapters(
    request_for, make_garment, garment_status
):
    ports = fake_ports()
    outcome = _run(request_for(), make_garment(status=garment_status, chest=None), ports)

    assert outcome.report is None
    assert outcome.status.status is JobStatus.FAILED
    assert outcome.status.report.error_code is ErrorCode.GARMENT_NOT_READY
    assert outcome.status.visual.status is StageStatus.SKIPPED
    assert ports.fit.calls == 0
    assert ports.body.calls == []


def test_mismatched_garment_is_a_programming_error(request_for, make_garment):
    with pytest.raises(ValueError):
        _run(request_for(garment_id="other"), make_garment(), fake_ports())


def test_unknown_fake_port_name_is_rejected():
    with pytest.raises(TypeError):
        fake_ports(drap=FakeDraper())


def test_fake_report_does_not_judge(request_for, make_garment):
    report = _run(request_for(), make_garment(waist=None), fake_ports()).report
    labels = {r.region: r.label for r in report.regions}
    assert labels[Region.WAIST] is FitLabel.UNKNOWN
    assert labels[Region.SLEEVE] is FitLabel.NOT_EVALUATED
