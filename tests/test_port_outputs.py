"""포트 산출물 순서 상수, 검증 표시 필드, 지표 키 문서, 외부 요청 ID (#1 리뷰 반영)."""

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

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
    Measurements,
    Mode,
    QualityStatus,
    Stage,
)
from fitting.ports import (
    BODY_OUTPUTS,
    DRAPE_OUTPUTS,
    REALIZE_OUTPUTS,
    RENDER_OUTPUTS,
    VALIDATE_OUTPUTS,
    BodyBuilder,
    PortError,
    PortFailure,
    Realizer,
    Validator,
    output_artifact,
)
from fitting.services.fitting import run_fitting

from .conftest import EXAMPLES, REPO_ROOT

JOB = "job_001"
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _request(body, consent: bool = True, mode: Mode = Mode.FULL) -> FittingRequest:
    return FittingRequest(
        garment_variant_id="tee_test",
        mode=mode,
        realize_consent=consent,
        measurements=Measurements(**body.model_dump(exclude={"measurement_protocol_version"})),
        measurement_protocol_version=body.measurement_protocol_version,
    )


def _visual(body, garment, ports, **kwargs):
    return run_fitting(
        _request(body, **kwargs), garment, ports, job_id=JOB, created_at=NOW
    ).status.visual


# --- (5) 산출물 순서 상수 ------------------------------------------------------------------


def test_output_roles_and_order():
    assert BODY_OUTPUTS == ("body_mesh",)
    assert DRAPE_OUTPUTS == ("drape_result", "drape_preview")
    assert RENDER_OUTPUTS == ("render_clip",)
    assert REALIZE_OUTPUTS == ("video",)
    assert VALIDATE_OUTPUTS == ("frame", "render_frame")


def test_output_artifact_finds_by_role():
    ids = ["a_result", "a_preview"]
    assert output_artifact(ids, DRAPE_OUTPUTS, "drape_result") == "a_result"
    assert output_artifact(ids, DRAPE_OUTPUTS, "drape_preview") == "a_preview"
    assert output_artifact(ids[:1], DRAPE_OUTPUTS, "drape_preview") is None
    with pytest.raises(ValueError):
        output_artifact(ids, DRAPE_OUTPUTS, "preview")


@pytest.mark.parametrize(
    ("fake", "outputs"),
    [
        (FakeBodyBuilder, BODY_OUTPUTS),
        (FakeDraper, DRAPE_OUTPUTS),
        (FakeRenderer, RENDER_OUTPUTS),
        (FakeRealizer, REALIZE_OUTPUTS),
        (FakeValidator, VALIDATE_OUTPUTS),
    ],
)
def test_fake_adapters_use_the_constants(fake, outputs):
    assert fake().outputs is outputs


def test_flow_finds_artifacts_by_role_not_position(body, make_garment):
    """흐름은 상수의 역할 이름으로 산출물을 찾는다: 미리보기·착장 결과·대표·렌더 프레임."""
    ports = fake_ports()
    visual = _visual(body, make_garment(), ports)
    assert visual.drape_preview_artifact_id == f"{JOB}_drape_preview"
    assert ports.render.calls[0].input_artifact_ids == [f"{JOB}_drape_result"]
    assert visual.frame_artifact_id == f"{JOB}_frame"
    assert visual.render_frame_artifact_id == f"{JOB}_render_frame"


# --- (4) 검증 결과 표시용 필드 -----------------------------------------------------------


def test_success_has_render_frame_and_numeric_metrics(body, make_garment):
    visual = _visual(body, make_garment(), fake_ports())
    assert visual.quality_status is QualityStatus.PASS
    assert visual.render_frame_artifact_id == f"{JOB}_render_frame"
    assert visual.quality_metrics == {"color_delta_e": 1.5, "silhouette_iou": 0.95}
    assert "quality_status" not in visual.quality_metrics  # 숫자 지표만


def test_validation_fail_keeps_frames_and_metrics(body, make_garment):
    validator = FakeValidator(quality=QualityStatus.FAIL, color_delta_e=12.0, silhouette_iou=0.4)
    visual = _visual(body, make_garment(), fake_ports(validate=validator))
    assert visual.error_code is ErrorCode.VALIDATION_FAILED
    assert visual.frame_artifact_id == f"{JOB}_frame"
    assert visual.render_frame_artifact_id == f"{JOB}_render_frame"
    assert visual.quality_metrics == {"color_delta_e": 12.0, "silhouette_iou": 0.4}


def test_integer_metric_becomes_float(body, make_garment):
    visual = _visual(body, make_garment(), fake_ports(validate=FakeValidator(color_delta_e=2)))
    assert type(visual.quality_metrics["color_delta_e"]) is float


def test_missing_render_frame_is_null(body, make_garment):
    validator = FakeValidator(outputs=("frame",))
    visual = _visual(body, make_garment(), fake_ports(validate=validator))
    assert visual.frame_artifact_id == f"{JOB}_frame"
    assert visual.render_frame_artifact_id is None


@pytest.mark.parametrize(
    "kwargs",
    [{"consent": False}, {"mode": Mode.REPORT_ONLY}],
)
def test_no_validation_means_no_metrics(body, make_garment, kwargs):
    visual = _visual(body, make_garment(), fake_ports(), **kwargs)
    assert visual.render_frame_artifact_id is None
    assert visual.quality_metrics is None


def test_realize_failure_has_no_metrics(body, make_garment):
    realizer = FakeRealizer(fail_with=ErrorCode.REALIZE_TIMEOUT)
    visual = _visual(body, make_garment(), fake_ports(realize=realizer))
    assert visual.quality_metrics is None
    assert visual.render_frame_artifact_id is None


def test_succeeded_example_shows_the_new_fields():
    visual = json.loads((EXAMPLES / "fitting_status_succeeded.json").read_text(encoding="utf-8"))[
        "visual"
    ]
    assert visual["render_frame_artifact_id"] == "fit_sample_001_render_frame"
    assert visual["quality_metrics"] == {"color_delta_e": 1.5, "silhouette_iou": 0.95}


# --- (6) 포트별 지표 키 문서 -------------------------------------------------------------


@pytest.mark.parametrize(
    ("port", "keys"),
    [
        (
            BodyBuilder,
            [
                "fitted_height_cm",
                "fitted_chest_cm",
                "fitted_waist_cm",
                "fitted_shoulder_cm",
                "fitted_arm_length_cm",
                "max_abs_error_cm",
                "BODY_FIT_NOT_CONVERGED",
            ],
        ),
        (Realizer, ["external_request_id", "cost_usd", "queue_s", "processing_s"]),
        (Validator, ["quality_status", "color_delta_e", "silhouette_iou"]),
    ],
)
def test_metric_keys_are_documented(port, keys):
    for key in keys:
        assert key in port.__doc__


# --- (7) 외부 요청 ID --------------------------------------------------------------------


def _error(**extra) -> PortError:
    return PortError(
        code=ErrorCode.EXTERNAL_SUBMIT_UNKNOWN,
        stage=Stage.REALIZE,
        retryable=True,
        safe_message="실사 영상 결과를 받지 못했어요.",
        **extra,
    )


def test_port_error_external_request_id_defaults_to_none():
    assert _error().external_request_id is None


def test_port_error_keeps_external_request_id():
    error = _error(external_request_id="fal_req_123")
    assert error.external_request_id == "fal_req_123"
    assert PortError.model_validate_json(error.model_dump_json()) == error
    assert PortFailure(error).error.external_request_id == "fal_req_123"


def test_port_error_external_request_id_must_be_text():
    with pytest.raises(ValidationError):
        _error(external_request_id=123)


def test_port_error_schema_has_external_request_id():
    schema = json.loads(
        (REPO_ROOT / "docs/api/schemas/port_error.schema.json").read_text(encoding="utf-8")
    )
    assert "external_request_id" in schema["properties"]
    assert "external_request_id" not in schema.get("required", [])


def test_resubmit_rule_is_documented():
    assert "다시 제출하지 말고" in Realizer.__doc__
    assert "retryable=true" in Realizer.__doc__
