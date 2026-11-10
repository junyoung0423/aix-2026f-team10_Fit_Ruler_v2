"""실사화 동의, 건너뛴 경로의 검증 상태, 상태 응답의 리포트 요약·경고 (#1 리뷰 반영)."""

import json
from datetime import datetime, timezone

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from fitting.adapters.fake import FakeDraper, FakeRenderer, fake_ports
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
from fitting.services.fitting import run_fitting

from .conftest import EXAMPLES, REPO_ROOT

JOB = "job_001"
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _request(body, *, consent: bool | None, mode: Mode = Mode.FULL) -> FittingRequest:
    extra = {} if consent is None else {"realize_consent": consent}
    return FittingRequest(
        garment_variant_id="tee_test",
        mode=mode,
        measurements=Measurements(**body.model_dump(exclude={"measurement_protocol_version"})),
        measurement_protocol_version=body.measurement_protocol_version,
        **extra,
    )


def _run(request, garment, ports):
    return run_fitting(request, garment, ports, job_id=JOB, created_at=NOW)


def _stages(status):
    return {p.stage: p.status for p in status.stages}


# --- (1) 실사화 동의 --------------------------------------------------------------------------


def test_consent_defaults_to_false_and_is_strict(body):
    assert _request(body, consent=None).realize_consent is False
    for bad in ("true", 1, 0.0):
        with pytest.raises(ValidationError):
            _request(body, consent=bad)


def test_schema_rejects_non_boolean_consent():
    schema = json.loads(
        (REPO_ROOT / "docs/api/schemas/fitting_request.schema.json").read_text(encoding="utf-8")
    )
    sample = json.loads((EXAMPLES / "fitting_request.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    assert sample["realize_consent"] is True
    assert validator.is_valid(sample)
    assert not validator.is_valid({**sample, "realize_consent": "true"})
    without = dict(sample)
    del without["realize_consent"]
    assert validator.is_valid(without)  # 없으면 기본값(동의 안 함)


def test_with_consent_the_full_visual_path_runs(body, make_garment):
    ports = fake_ports()
    status = _run(_request(body, consent=True), make_garment(), ports).status
    assert status.status is JobStatus.SUCCEEDED
    assert status.visual.status is StageStatus.SUCCEEDED
    assert status.visual.stage is Stage.VALIDATE
    assert status.visual.skip_reason is None
    assert status.visual.quality_status is QualityStatus.PASS
    assert len(ports.realize.calls) == 1
    assert len(ports.validate.calls) == 1


def test_without_consent_stops_after_render_and_succeeds(body, make_garment):
    ports = fake_ports()
    outcome = _run(_request(body, consent=False), make_garment(), ports)
    status = outcome.status

    assert status.status is JobStatus.SUCCEEDED  # 사용자가 고른 것이라 실패가 아니다
    assert status.report.status is StageStatus.SUCCEEDED
    assert status.report.regions == outcome.report.regions
    visual = status.visual
    assert visual.status is StageStatus.SUCCEEDED
    assert visual.stage is Stage.RENDER
    assert visual.skip_reason is VisualSkipReason.CONSENT_NOT_GIVEN
    assert visual.quality_status is None
    assert visual.error_code is None
    assert visual.drape_preview_artifact_id == f"{JOB}_drape_preview"
    assert visual.render_artifact_id == f"{JOB}_render_clip"
    assert visual.video_artifact_id is None
    assert visual.frame_artifact_id is None

    stages = _stages(status)
    for stage in (Stage.BODY_FIT, Stage.DRAPE, Stage.RENDER):
        assert stages[stage] is StageStatus.SUCCEEDED
    assert stages[Stage.REALIZE] is StageStatus.SKIPPED
    assert stages[Stage.VALIDATE] is StageStatus.SKIPPED
    # 실사화(외부 전송)와 검증 어댑터는 부르지 않는다
    assert ports.realize.calls == []
    assert ports.validate.calls == []
    assert len(ports.render.calls) == 1


@pytest.mark.parametrize(
    ("port", "fake", "stage", "code"),
    [
        ("drape", FakeDraper, Stage.DRAPE, ErrorCode.DRAPE_INVALID),
        ("render", FakeRenderer, Stage.RENDER, ErrorCode.RENDER_FAILED),
    ],
)
def test_without_consent_an_earlier_failure_is_still_a_failure(
    body, make_garment, port, fake, stage, code
):
    ports = fake_ports(**{port: fake(fail_with=code)})
    status = _run(_request(body, consent=False), make_garment(), ports).status
    assert status.status is JobStatus.PARTIAL_SUCCEEDED
    assert status.report.status is StageStatus.SUCCEEDED
    assert status.visual.status is StageStatus.FAILED
    assert status.visual.stage is stage
    assert status.visual.error_code is code
    assert status.visual.skip_reason is None  # 동의 여부와 관계없이 지금과 같은 실패 처리
    assert status.visual.quality_status is None  # 검증 판정 전에 끝났다
    assert ports.realize.calls == []


def test_no_consent_example_matches_the_flow():
    status = FittingStatus.model_validate_json(
        (EXAMPLES / "fitting_status_no_consent.json").read_text(encoding="utf-8")
    )
    assert status.status is JobStatus.SUCCEEDED
    assert status.visual.skip_reason is VisualSkipReason.CONSENT_NOT_GIVEN
    assert status.visual.quality_status is None
    assert status.visual.stage is Stage.RENDER


# --- (2) 건너뛴 경로의 검증 상태 ------------------------------------------------------------


def test_report_only_has_skip_reason_and_no_quality_status(body, make_garment):
    ports = fake_ports()
    status = _run(_request(body, consent=True, mode=Mode.REPORT_ONLY), make_garment(), ports).status
    assert status.status is JobStatus.SUCCEEDED
    assert status.visual.status is StageStatus.SKIPPED
    assert status.visual.skip_reason is VisualSkipReason.REPORT_ONLY
    assert status.visual.quality_status is None
    assert ports.body.calls == []


def test_garment_not_ready_has_no_quality_status(body, make_garment):
    garment = make_garment(status=GarmentStatus.DRAFT, chest=None)
    status = _run(_request(body, consent=True), garment, fake_ports()).status
    assert status.visual.status is StageStatus.SKIPPED
    assert status.visual.quality_status is None
    assert status.visual.skip_reason is None


def test_examples_use_null_quality_only_for_skipped_validation():
    """판정이 있는 예시만 PASS·FAIL이고, 진행 중 예시만 PENDING, 나머지는 null이다."""
    expected = {
        "fitting_status_running.json": "PENDING",
        "fitting_status_succeeded.json": "PASS",
        "fitting_status_validation_failed.json": "FAIL",
        "fitting_status_no_consent.json": None,
        "fitting_status_report_only.json": None,
        "fitting_status_drape_failed.json": None,
        "fitting_status_realize_failed.json": None,
    }
    found = {
        path.name: json.loads(path.read_text(encoding="utf-8"))["visual"]["quality_status"]
        for path in EXAMPLES.glob("fitting_status_*.json")
    }
    assert found == expected


# --- (3) 상태 응답의 리포트 요약·경고 -------------------------------------------------------


@pytest.mark.parametrize(
    ("consent", "mode"), [(True, Mode.FULL), (False, Mode.FULL), (True, Mode.REPORT_ONLY)]
)
def test_report_summary_and_warnings_are_in_the_status(body, make_garment, consent, mode):
    outcome = _run(_request(body, consent=consent, mode=mode), make_garment(), fake_ports())
    assert outcome.status.report.summary == outcome.report.summary
    assert outcome.status.report.warnings == outcome.report.warnings
    assert outcome.status.report.warnings  # 가짜 계산기는 경고를 남긴다


def test_failed_report_has_no_summary(body, make_garment):
    garment = make_garment(status=GarmentStatus.DRAFT, chest=None)
    report = _run(_request(body, consent=True), garment, fake_ports()).status.report
    assert report.summary is None
    assert report.warnings == []


def test_status_examples_carry_summary_and_demo_warning():
    for path in EXAMPLES.glob("fitting_status_*.json"):
        report = json.loads(path.read_text(encoding="utf-8"))["report"]
        assert report["summary"] == "가슴 기본 여유 · 어깨 기본 범위 · 허리 넉넉", path.name
        assert report["warnings"] == ["DEMO_GARMENT"], path.name
