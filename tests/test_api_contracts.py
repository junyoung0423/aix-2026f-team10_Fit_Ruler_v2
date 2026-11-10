"""핏 API 계약(#2): FitRequest·GarmentList·ApiError의 스키마와 예시."""

import json

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from fitting.domain import ApiError, FitRequest, FittingRequest, GarmentList

from .conftest import EXAMPLES, REPO_ROOT

SCHEMA_DIR = REPO_ROOT / "docs" / "api" / "schemas"

API_EXAMPLES = {
    "fit_request.json": ("fit_request", FitRequest),
    "garment_list.json": ("garment_list", GarmentList),
    "api_error_input_invalid.json": ("api_error", ApiError),
    "api_error_garment_not_found.json": ("api_error", ApiError),
    "api_error_garment_not_ready.json": ("api_error", ApiError),
}


def _schema(name: str) -> Draft202012Validator:
    return Draft202012Validator(
        json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
    )


def _sample(filename: str) -> dict:
    return json.loads((EXAMPLES / filename).read_text(encoding="utf-8"))


@pytest.mark.parametrize("filename", list(API_EXAMPLES))
def test_api_examples_pass_schema_and_model(filename):
    schema_name, model = API_EXAMPLES[filename]
    sample = _sample(filename)
    assert not list(_schema(schema_name).iter_errors(sample))
    model.model_validate(sample)


def test_fit_request_is_fitting_request_without_mode():
    fit = _sample("fit_request.json")
    fitting = _sample("fitting_request.json")
    fitting.pop("mode")
    assert fit == fitting
    assert set(FitRequest.model_fields) == set(FittingRequest.model_fields) - {"mode"}


def test_error_examples_cover_the_three_codes():
    codes = {name: _sample(name)["code"] for name in API_EXAMPLES if name.startswith("api_error_")}
    assert codes == {
        "api_error_input_invalid.json": "INPUT_INVALID",
        "api_error_garment_not_found.json": "GARMENT_NOT_FOUND",
        "api_error_garment_not_ready.json": "GARMENT_NOT_READY",
    }
    fields = _sample("api_error_input_invalid.json")["fields"]
    assert {(f["field"], f["code"]) for f in fields} == {
        ("measurements.chest_cm", "OUT_OF_RANGE"),
        ("measurements.height_cm", "NOT_A_NUMBER"),
    }


def _changed(path: list[str], value) -> dict:
    data = _sample("fit_request.json")
    target = data
    for key in path[:-1]:
        target = target[key]
    if value is KeyError:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return data


INVALID_FIT_REQUESTS = {
    "범위 아래": _changed(["measurements", "chest_cm"], 59.9),
    "범위 위": _changed(["measurements", "height_cm"], 210.1),
    "문자열 숫자": _changed(["measurements", "height_cm"], "175"),
    "불리언": _changed(["measurements", "height_cm"], True),
    "필수 치수 누락": _changed(["measurements", "waist_cm"], KeyError),
    "모르는 필드": _changed(["note"], "x"),
    "mode는 없다": _changed(["mode"], "full"),
    "의류 id 비어 있음": _changed(["garment_variant_id"], ""),
    "지원하지 않는 프로토콜": _changed(["measurement_protocol_version"], "measure_v0"),
}


@pytest.mark.parametrize("case", list(INVALID_FIT_REQUESTS))
def test_schema_and_model_reject_the_same_fit_requests(case):
    payload = INVALID_FIT_REQUESTS[case]
    assert not _schema("fit_request").is_valid(payload)
    with pytest.raises(ValidationError):
        FitRequest.model_validate(payload)
