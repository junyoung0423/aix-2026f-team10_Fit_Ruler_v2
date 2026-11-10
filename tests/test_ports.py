"""포트 DTO 검증: 필수 필드, strict 타입, 산출물 단계의 결과 ID 확인 (명세서 10장).

모델과 JSON 스키마가 같은 판정을 하는지는 `test_schemas.py`가 확인한다.
여기서는 모델 쪽 동작과 JSON 본문 경로, 단계별 산출물 확인을 본다.
"""

import pytest
from pydantic import ValidationError

from fitting.domain import Stage
from fitting.ports import (
    ARTIFACT_PRODUCING_STAGES,
    PortError,
    PortRequest,
    PortResult,
    PortResultInvalid,
    require_output_artifacts,
)

REQUEST = {
    "job_id": "fit_sample_001",
    "attempt_id": "att_001",
    "config_version": "cfg_v1",
    "timeout_seconds": 30,
    "trace_id": "trace_001",
}


def _result(ids: list[str]) -> PortResult:
    return PortResult(
        schema_version="v1", output_artifact_ids=ids, metrics={}, tool_versions={}
    )


# --- PortResult: 빈 응답과 일부만 담은 응답은 거절한다 -----------------------------------------


def test_empty_adapter_response_is_rejected():
    with pytest.raises(ValidationError):
        PortResult.model_validate({})
    with pytest.raises(ValidationError):
        PortResult.model_validate_json("{}")


@pytest.mark.parametrize(
    "missing", ["schema_version", "output_artifact_ids", "metrics", "tool_versions"]
)
def test_every_port_result_field_is_required(missing):
    full = {
        "schema_version": "v1",
        "output_artifact_ids": ["art_001"],
        "metrics": {},
        "tool_versions": {},
    }
    del full[missing]
    with pytest.raises(ValidationError) as info:
        PortResult.model_validate(full)
    assert missing in str(info.value)


def test_complete_port_result_is_accepted():
    result = PortResult.model_validate(
        {
            "schema_version": "v1",
            "output_artifact_ids": ["art_001"],
            "metrics": {"frames": 120, "fps": 24.0, "codec": "h264"},
            "tool_versions": {"renderer": "0.1.0"},
        }
    )
    assert result.output_artifact_ids == ["art_001"]
    # 지표 값은 들어온 타입 그대로 보존한다 (정수가 실수로 바뀌지 않는다)
    assert type(result.metrics["frames"]) is int
    assert type(result.metrics["fps"]) is float


def test_empty_artifact_id_and_boolean_metric_are_rejected():
    base = {"schema_version": "v1", "metrics": {}, "tool_versions": {}}
    with pytest.raises(ValidationError):
        PortResult.model_validate({**base, "output_artifact_ids": [""]})
    with pytest.raises(ValidationError):
        PortResult.model_validate({**base, "output_artifact_ids": ["a"], "metrics": {"ok": True}})


# --- 산출물을 만드는 단계는 결과 ID가 있어야 한다 -----------------------------------------------


@pytest.mark.parametrize("stage", sorted(ARTIFACT_PRODUCING_STAGES, key=lambda s: s.value))
def test_artifact_stage_requires_an_output_artifact_id(stage):
    with pytest.raises(PortResultInvalid) as info:
        require_output_artifacts(_result([]), stage)
    assert info.value.stage is stage
    assert stage.value in str(info.value)


@pytest.mark.parametrize("stage", sorted(ARTIFACT_PRODUCING_STAGES, key=lambda s: s.value))
def test_artifact_stage_accepts_a_result_with_ids_and_returns_it(stage):
    result = _result(["art_001"])
    assert require_output_artifacts(result, stage) is result


@pytest.mark.parametrize(
    "stage", sorted(set(Stage) - ARTIFACT_PRODUCING_STAGES, key=lambda s: s.value)
)
def test_other_stages_may_have_no_output_artifacts(stage):
    result = _result([])
    assert require_output_artifacts(result, stage) is result


def test_artifact_producing_stages_are_the_generation_stages():
    assert ARTIFACT_PRODUCING_STAGES == {Stage.BODY_FIT, Stage.DRAPE, Stage.RENDER, Stage.REALIZE}


def test_port_result_invalid_is_a_value_error_and_keeps_reason_for_ops_only():
    with pytest.raises(ValueError) as info:
        require_output_artifacts(_result([]), Stage.RENDER)
    assert isinstance(info.value, PortResultInvalid)
    assert "output_artifact_ids" in info.value.reason


# --- timeout_seconds와 retryable은 strict ------------------------------------------------------


@pytest.mark.parametrize("bad", ["30", True, False, 0, -1, None, "", 30.5])
def test_timeout_seconds_rejects_non_integers_and_non_positive(bad):
    with pytest.raises(ValidationError):
        PortRequest.model_validate({**REQUEST, "timeout_seconds": bad})


def test_timeout_seconds_accepts_a_positive_integer():
    assert PortRequest.model_validate({**REQUEST, "timeout_seconds": 1}).timeout_seconds == 1
    assert PortRequest.model_validate({**REQUEST, "timeout_seconds": 30}).timeout_seconds == 30


@pytest.mark.parametrize("literal", ['"30"', "true", "0", "-5", "30.5"])
def test_timeout_seconds_in_json_body_is_strict_too(literal):
    raw = (
        '{"job_id": "j", "attempt_id": "a", "config_version": "c", "trace_id": "t", '
        f'"timeout_seconds": {literal}}}'
    )
    with pytest.raises(ValidationError):
        PortRequest.model_validate_json(raw)


@pytest.mark.parametrize("bad", ["true", "false", "yes", 1, 0, None])
def test_retryable_rejects_non_booleans(bad):
    payload = {"code": "RENDER_FAILED", "retryable": bad, "safe_message": "x"}
    with pytest.raises(ValidationError):
        PortError.model_validate(payload)


@pytest.mark.parametrize("value", [True, False])
def test_retryable_accepts_booleans(value):
    payload = {"code": "RENDER_FAILED", "retryable": value, "safe_message": "x"}
    assert PortError.model_validate(payload).retryable is value
