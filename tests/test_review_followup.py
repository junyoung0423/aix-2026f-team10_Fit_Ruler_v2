"""B1 리뷰 반영 보완 (#1): 검증 전 실패의 quality_status null, 가짜 지표 키, 허용 오차 항목."""

import json
import re
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
    JobStatus,
    Measurements,
    QualityStatus,
    Stage,
    StageStatus,
)
from fitting.ports import BodyBuilder, Draper, PortRequest, Realizer, Renderer, Validator
from fitting.services.fitting import run_fitting

from .conftest import EXAMPLES, REPO_ROOT

JOB = "job_001"
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _status(body, garment, ports):
    request = FittingRequest(
        garment_variant_id="tee_test",
        realize_consent=True,
        measurements=Measurements(**body.model_dump(exclude={"measurement_protocol_version"})),
        measurement_protocol_version=body.measurement_protocol_version,
    )
    return run_fitting(request, garment, ports, job_id=JOB, created_at=NOW).status


# --- (1) 검증 판정 전에 끝나면 quality_status는 null ----------------------------------------

BEFORE_VERDICT = [
    ("body", FakeBodyBuilder(fail_with=ErrorCode.BODY_FIT_NOT_CONVERGED), Stage.BODY_FIT),
    ("drape", FakeDraper(fail_with=ErrorCode.DRAPE_INVALID), Stage.DRAPE),
    ("render", FakeRenderer(fail_with=ErrorCode.RENDER_FAILED), Stage.RENDER),
    ("realize", FakeRealizer(fail_with=ErrorCode.REALIZE_TIMEOUT), Stage.REALIZE),
    ("realize", FakeRealizer(no_artifacts=True), Stage.REALIZE),
    # Validator 포트 자체의 실패(PortFailure). VALIDATE는 산출물 필수 단계가 아니라서
    # 산출물이 빈 결과는 잘못된 결과(PortResultInvalid)가 되지 않는다.
    ("validate", FakeValidator(fail_with=ErrorCode.VALIDATION_UNVERIFIABLE), Stage.VALIDATE),
]


@pytest.mark.parametrize(("port", "fake", "stage"), BEFORE_VERDICT)
def test_failure_before_verdict_has_null_quality(body, make_garment, port, fake, stage):
    status = _status(body, make_garment(), fake_ports(**{port: fake}))
    assert status.status is JobStatus.PARTIAL_SUCCEEDED
    assert status.visual.status is StageStatus.FAILED
    assert status.visual.stage is stage
    assert status.visual.error_code is not None
    assert status.visual.quality_status is None
    assert status.visual.quality_metrics is None


@pytest.mark.parametrize(
    ("quality", "expected", "code"),
    [
        (QualityStatus.FAIL, QualityStatus.FAIL, ErrorCode.VALIDATION_FAILED),
        (QualityStatus.UNVERIFIABLE, QualityStatus.UNVERIFIABLE, ErrorCode.VALIDATION_UNVERIFIABLE),
        # PENDING을 돌려주면 판정이 없는 것으로 보고 UNVERIFIABLE (지금과 같다)
        (QualityStatus.PENDING, QualityStatus.UNVERIFIABLE, ErrorCode.VALIDATION_UNVERIFIABLE),
    ],
)
def test_validator_verdicts_are_unchanged(body, make_garment, quality, expected, code):
    status = _status(body, make_garment(), fake_ports(validate=FakeValidator(quality=quality)))
    assert status.visual.quality_status is expected
    assert status.visual.error_code is code
    assert status.visual.quality_metrics == {"color_delta_e": 1.5, "silhouette_iou": 0.95}


class _NoVerdictValidator(FakeValidator):
    """판정 키(quality_status)를 빼고 돌려주는 검증기."""

    def validate(self, request: PortRequest, garment):
        return self._run(request, {"color_delta_e": 1.0})


def test_missing_verdict_key_is_unverifiable(body, make_garment):
    status = _status(body, make_garment(), fake_ports(validate=_NoVerdictValidator()))
    assert status.visual.quality_status is QualityStatus.UNVERIFIABLE
    assert status.visual.error_code is ErrorCode.VALIDATION_UNVERIFIABLE


def test_pending_appears_only_in_the_running_example():
    pending = sorted(
        path.name
        for path in EXAMPLES.glob("fitting_status_*.json")
        if json.loads(path.read_text(encoding="utf-8"))["visual"]["quality_status"] == "PENDING"
    )
    assert pending == ["fitting_status_running.json"]


# --- (2) 가짜 어댑터 지표 키 = 포트 docstring의 지표 키 -----------------------------------


def _documented_keys(port) -> set[str]:
    """docstring "지표:" 단락에 백틱으로 적은 소문자 키."""
    paragraph = port.__doc__.split("지표:", 1)[1].split("\n\n", 1)[0]
    return set(re.findall(r"`([a-z_]+)`", paragraph))


def _request() -> PortRequest:
    return PortRequest(
        job_id=JOB, attempt_id="a1", config_version="cfg_v1", timeout_seconds=60, trace_id=JOB
    )


def test_fake_body_builder_metrics_match_the_docs(body):
    result = FakeBodyBuilder().build_body(_request(), body)
    assert set(result.metrics) == _documented_keys(BodyBuilder)
    assert result.metrics["fitted_chest_cm"] == body.chest_cm  # 입력 그대로
    assert result.metrics["max_abs_error_cm"] == 0.0


def test_fake_realizer_metrics_match_the_docs(make_garment):
    result = FakeRealizer().realize(_request(), make_garment())
    assert set(result.metrics) == _documented_keys(Realizer)
    assert result.metrics["external_request_id"] == f"fake_{JOB}"
    assert "audio" not in result.metrics


def test_fake_validator_metrics_match_the_docs(make_garment):
    result = FakeValidator().validate(_request(), make_garment())
    assert set(result.metrics) == _documented_keys(Validator)


@pytest.mark.parametrize(("port", "owner"), [(Draper, "C4"), (Renderer, "C5")])
def test_undecided_metric_keys_are_marked(port, owner):
    assert f"지표 키는 {owner}에서 정한다" in port.__doc__


# --- (3) 허용 오차 항목 ------------------------------------------------------------------


def test_tolerance_item_exists_and_docstring_points_to_it():
    text = (REPO_ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
    open_items = text.split("## 아직 정할 것", 1)[1]
    assert re.search(
        r"^\| 허용 오차 \| .*max_abs_error_cm.* \| D3·C4 전 \| #6 #7 \|$", open_items, re.M
    )
    assert '"허용 오차"' in BodyBuilder.__doc__
