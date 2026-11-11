"""API 응답 예시와 JSON 스키마가 계약 모델과 어긋나지 않는지 확인한다."""

import json

import pytest

from fitting.catalog.search import SampleSearchResult
from fitting.domain import (
    ApiError,
    ErrorCode,
    FitReport,
    FittingRequest,
    FittingStatus,
    GarmentVariant,
)
from tools.export_contracts import SCHEMA_MODELS, build_artifacts, main

from .conftest import EXAMPLES, REPO_ROOT

STATUS_EXAMPLES = sorted(p.name for p in EXAMPLES.glob("fitting_status_*.json"))


def _read(name: str) -> str:
    return (EXAMPLES / name).read_text(encoding="utf-8")


def test_hand_written_examples_validate():
    request = FittingRequest.model_validate_json(_read("fitting_request.json"))
    garment = GarmentVariant.model_validate_json(_read("garment.json"))
    assert request.garment_variant_id == garment.id


def test_examples_are_marked_as_samples():
    garment = GarmentVariant.model_validate_json(_read("garment.json"))
    assert garment.is_demo
    assert garment.source_ref.startswith("SAMPLE")
    assert "SAMPLE" in (garment.measurement_meta.notes or "")


def test_status_examples_cover_success_and_failure_cases():
    assert STATUS_EXAMPLES == [
        "fitting_status_drape_failed.json",
        "fitting_status_no_consent.json",
        "fitting_status_realize_failed.json",
        "fitting_status_report_only.json",
        "fitting_status_running.json",
        "fitting_status_succeeded.json",
        "fitting_status_validation_failed.json",
    ]


@pytest.mark.parametrize("name", STATUS_EXAMPLES)
def test_status_examples_carry_the_example_report(name):
    report = FitReport.model_validate_json(_read("fit_report.json"))
    status = FittingStatus.model_validate_json(_read(name))
    assert status.job_id == report.job_id
    # 어떤 영상 단계가 실패해도 핏 리포트는 그대로 들어 있다 (#12).
    assert status.report.regions == report.regions


def test_committed_schemas_and_examples_are_up_to_date():
    """계약 모델을 바꿨다면 `python -m tools.export_contracts`로 다시 만들어야 한다."""
    stale = [
        path.relative_to(REPO_ROOT).as_posix()
        for path, content in build_artifacts(REPO_ROOT).items()
        if not path.exists() or path.read_text(encoding="utf-8") != content
    ]
    assert not stale, f"최신이 아닌 파일: {stale}"


def test_export_check_command_passes():
    assert main(["--check", "--root", str(REPO_ROOT)]) == 0


@pytest.mark.parametrize("name", list(SCHEMA_MODELS))
def test_schema_file_is_valid_json_schema_document(name):
    path = REPO_ROOT / "docs" / "api" / "schemas" / f"{name}.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    assert schema["title"] == SCHEMA_MODELS[name].__name__
    assert schema["type"] == "object"


def test_search_example_is_reference_case_1():
    """견본 검색 예시는 예시 의류로 실제 검색을 돌린 결과다 (기준 사례 1)."""
    result = SampleSearchResult.model_validate_json(_read("sample_search_result.json"))

    assert result.selected_sample_id == "tee_gc_regular_04"
    assert result.score_cm == 3.85
    assert result.exact_match is False
    assert [item.sample_id for item in result.ranking][:2] == [
        "tee_gc_regular_04",
        "tee_gc_regular_03",
    ]


def test_job_not_found_example_is_an_api_error():
    error = ApiError.model_validate_json(_read("api_error_job_not_found.json"))

    assert error.code is ErrorCode.JOB_NOT_FOUND
    assert error.fields is None


def test_error_codes_only_grew_by_the_two_job_api_codes():
    """계약 변경은 값 추가뿐이다: 기존 코드는 그대로고 JOB_NOT_FOUND·ARTIFACT_NOT_FOUND가 늘었다."""
    assert {code.value for code in ErrorCode} == {
        "INPUT_INVALID",
        "GARMENT_NOT_READY",
        "GARMENT_NOT_FOUND",
        "JOB_NOT_FOUND",
        "ARTIFACT_NOT_FOUND",
        "BODY_FIT_NOT_CONVERGED",
        "DRAPE_INVALID",
        "RENDER_FAILED",
        "REALIZE_TIMEOUT",
        "EXTERNAL_SUBMIT_UNKNOWN",
        "VALIDATION_FAILED",
        "VALIDATION_UNVERIFIABLE",
        "BUDGET_LIMIT",
    }
    schema = json.loads((REPO_ROOT / "docs/api/schemas/api_error.schema.json").read_text("utf-8"))
    assert {"JOB_NOT_FOUND", "ARTIFACT_NOT_FOUND"} <= set(schema["$defs"]["ErrorCode"]["enum"])
