"""JSON 스키마 검증.

`docs/api/schemas/`의 스키마는 A(TypeScript 화면)처럼 Python 모델을 쓰지 않는 쪽이 계약으로
읽는 파일이다. 그래서 스키마가 (1) 올바른 JSON Schema이고, (2) 공통 샘플을 통과시키고,
(3) 모델이 거절하는 입력을 똑같이 거절하는지 확인한다.
"""

import copy
import json

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ValidationError

from fitting.domain import FittingRequest, GarmentVariant
from fitting.domain.garment import MAX_GARMENT_CM
from fitting.ports import PortError, PortRequest, PortResult
from tools.export_contracts import SCHEMA_MODELS

from .conftest import EXAMPLES, REPO_ROOT

SCHEMA_DIR = REPO_ROOT / "docs" / "api" / "schemas"

# 샘플 파일 -> 그 파일이 따라야 하는 스키마
SAMPLE_SCHEMA = {
    "fitting_request.json": "fitting_request",
    "garment.json": "garment_variant",
    "fit_report.json": "fit_report",
    "fitting_status_running.json": "fitting_status",
    "fitting_status_succeeded.json": "fitting_status",
    "fitting_status_report_only.json": "fitting_status",
    "fitting_status_realize_failed.json": "fitting_status",
    "fitting_status_drape_failed.json": "fitting_status",
    "fitting_status_validation_failed.json": "fitting_status",
}


def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def load_sample(filename: str) -> dict:
    return json.loads((EXAMPLES / filename).read_text(encoding="utf-8"))


def schema_accepts(name: str, payload: dict) -> bool:
    return Draft202012Validator(load_schema(name)).is_valid(payload)


def model_accepts(model: type[BaseModel], payload: dict) -> bool:
    try:
        model.model_validate(payload)
    except ValidationError:
        return False
    return True


def modified(payload: dict, path: list[str], value) -> dict:
    """payload를 복사해 path가 가리키는 값만 바꾼다. value가 KeyError면 그 키를 지운다."""
    result = copy.deepcopy(payload)
    target = result
    for key in path[:-1]:
        target = target[key]
    if value is KeyError:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return result


@pytest.mark.parametrize("name", list(SCHEMA_MODELS))
def test_schema_is_a_valid_json_schema(name):
    Draft202012Validator.check_schema(load_schema(name))


@pytest.mark.parametrize(("filename", "schema_name"), list(SAMPLE_SCHEMA.items()))
def test_samples_pass_their_schema(filename, schema_name):
    errors = list(Draft202012Validator(load_schema(schema_name)).iter_errors(load_sample(filename)))
    assert not errors, [e.message for e in errors]


# --- 모델이 거절하는 입력은 스키마도 거절한다 ---------------------------------------------------

REQUEST = load_sample("fitting_request.json")
GARMENT = load_sample("garment.json")

INVALID_REQUESTS = {
    "키가 범위 아래": modified(REQUEST, ["measurements", "height_cm"], 139.9),
    "키가 범위 위": modified(REQUEST, ["measurements", "height_cm"], 210.1),
    "가슴이 범위 아래": modified(REQUEST, ["measurements", "chest_cm"], 59.9),
    "허리가 범위 위": modified(REQUEST, ["measurements", "waist_cm"], 160.1),
    "어깨가 범위 아래": modified(REQUEST, ["measurements", "shoulder_cm"], 27.9),
    "팔길이가 범위 위": modified(REQUEST, ["measurements", "arm_length_cm"], 85.1),
    "음수": modified(REQUEST, ["measurements", "height_cm"], -175.0),
    "문자열 숫자": modified(REQUEST, ["measurements", "height_cm"], "175.0"),
    "단위가 붙은 문자열": modified(REQUEST, ["measurements", "height_cm"], "175 cm"),
    "null": modified(REQUEST, ["measurements", "height_cm"], None),
    "불리언": modified(REQUEST, ["measurements", "height_cm"], True),
    "필수 치수 누락": modified(REQUEST, ["measurements", "waist_cm"], KeyError),
    "모르는 신체 필드": modified(REQUEST, ["measurements", "height_in"], 68.9),
    "모르는 최상위 필드": modified(REQUEST, ["note"], "x"),
    "실행 방식 오류": modified(REQUEST, ["mode"], "fast"),
    "의류 id 비어 있음": modified(REQUEST, ["garment_variant_id"], ""),
    "지원하지 않는 프로토콜": modified(REQUEST, ["measurement_protocol_version"], "measure_v0"),
    "프로토콜 누락": modified(REQUEST, ["measurement_protocol_version"], KeyError),
}

VALID_REQUESTS = {
    "샘플 그대로": REQUEST,
    "범위 경계(최솟값)": {
        **REQUEST,
        "measurements": {
            "height_cm": 140.0,
            "chest_cm": 60.0,
            "waist_cm": 50.0,
            "shoulder_cm": 28.0,
            "arm_length_cm": 40.0,
        },
    },
    "범위 경계(최댓값)": {
        **REQUEST,
        "measurements": {
            "height_cm": 210.0,
            "chest_cm": 160.0,
            "waist_cm": 160.0,
            "shoulder_cm": 60.0,
            "arm_length_cm": 85.0,
        },
    },
    "정수 입력": modified(REQUEST, ["measurements", "height_cm"], 175),
    "report_only": modified(REQUEST, ["mode"], "report_only"),
    "mode 생략(기본값 full)": modified(REQUEST, ["mode"], KeyError),
}


@pytest.mark.parametrize("case", list(INVALID_REQUESTS))
def test_schema_rejects_what_the_model_rejects_for_requests(case):
    payload = INVALID_REQUESTS[case]
    assert not model_accepts(FittingRequest, payload), "모델이 거절해야 하는 케이스"
    assert not schema_accepts("fitting_request", payload)


@pytest.mark.parametrize("case", list(VALID_REQUESTS))
def test_schema_accepts_what_the_model_accepts_for_requests(case):
    payload = VALID_REQUESTS[case]
    assert model_accepts(FittingRequest, payload), "모델이 받아들여야 하는 케이스"
    assert schema_accepts("fitting_request", payload)


INVALID_GARMENTS = {
    "READY인데 가슴 실측 null": modified(GARMENT, ["measurements", "chest_girth_cm"], None),
    "READY인데 어깨 실측 null": modified(GARMENT, ["measurements", "shoulder_width_cm"], None),
    "READY인데 가슴 실측 키 없음": modified(GARMENT, ["measurements", "chest_girth_cm"], KeyError),
    "실측 0": modified(GARMENT, ["measurements", "waist_girth_cm"], 0),
    "실측 음수": modified(GARMENT, ["measurements", "sleeve_length_cm"], -1.0),
    "실측 문자열": modified(GARMENT, ["measurements", "chest_girth_cm"], "108"),
    "상태 오류": modified(GARMENT, ["status"], "DONE"),
    "모르는 실측 필드": modified(GARMENT, ["measurements", "hem_cm"], 50.0),
    "meta 실측 여부 누락": modified(GARMENT, ["measurement_meta", "is_measured"], KeyError),
    "id 비어 있음": modified(GARMENT, ["id"], ""),
    "실측이 지원 상한 바로 위": modified(
        GARMENT, ["measurements", "chest_girth_cm"], MAX_GARMENT_CM + 0.1
    ),
    "실측 1e30": modified(GARMENT, ["measurements", "chest_girth_cm"], 1e30),
    "허리 실측 1e30": modified(GARMENT, ["measurements", "waist_girth_cm"], 1e30),
}

VALID_GARMENTS = {
    "샘플 그대로": GARMENT,
    "실측이 지원 상한 경계": modified(GARMENT, ["measurements", "chest_girth_cm"], MAX_GARMENT_CM),
    "DRAFT는 가슴·어깨 없어도 됨": modified(
        modified(modified(GARMENT, ["status"], "DRAFT"), ["measurements", "chest_girth_cm"], None),
        ["measurements", "shoulder_width_cm"],
        None,
    ),
    "READY도 허리·소매·기장은 null 가능": modified(
        modified(
            modified(GARMENT, ["measurements", "waist_girth_cm"], None),
            ["measurements", "sleeve_length_cm"],
            None,
        ),
        ["measurements", "body_length_cm"],
        None,
    ),
    "다른 프로토콜 버전의 의류(차단은 엔진이 한다)": modified(
        GARMENT, ["measurement_meta", "measurement_protocol_version"], "measure_v0"
    ),
}


@pytest.mark.parametrize("case", list(INVALID_GARMENTS))
def test_schema_rejects_what_the_model_rejects_for_garments(case):
    payload = INVALID_GARMENTS[case]
    assert not model_accepts(GarmentVariant, payload), "모델이 거절해야 하는 케이스"
    assert not schema_accepts("garment_variant", payload)


@pytest.mark.parametrize("case", list(VALID_GARMENTS))
def test_schema_accepts_what_the_model_accepts_for_garments(case):
    payload = VALID_GARMENTS[case]
    assert model_accepts(GarmentVariant, payload), "모델이 받아들여야 하는 케이스"
    assert schema_accepts("garment_variant", payload)


# --- 포트 DTO: 모델과 스키마가 같은 판정을 한다 -------------------------------------------------

PORT_REQUEST = {
    "schema_version": "v1",
    "job_id": "fit_sample_001",
    "attempt_id": "att_001",
    "input_artifact_ids": ["art_in_001"],
    "config_version": "cfg_v1",
    "timeout_seconds": 30,
    "trace_id": "trace_001",
}

PORT_RESULT = {
    "schema_version": "v1",
    "output_artifact_ids": ["art_render_001"],
    "metrics": {"frames": 120, "fps": 24.0, "codec": "h264"},
    "tool_versions": {"renderer": "0.1.0"},
}

PORT_ERROR = {
    "code": "RENDER_FAILED",
    "stage": "RENDER",
    "retryable": True,
    "safe_message": "잠시 후 다시 시도해 주세요.",
    "internal_details_ref": "ops/log/001",
}

# (스키마 이름, 모델, 기준 payload)
PORT_CASES = {
    "port_request": (PortRequest, PORT_REQUEST),
    "port_result": (PortResult, PORT_RESULT),
    "port_error": (PortError, PORT_ERROR),
}

INVALID_PORT_PAYLOADS = {
    "빈 결과 {}": ("port_result", {}),
    "결과에 schema_version 없음": (
        "port_result",
        modified(PORT_RESULT, ["schema_version"], KeyError),
    ),
    "결과에 output_artifact_ids 없음": (
        "port_result",
        modified(PORT_RESULT, ["output_artifact_ids"], KeyError),
    ),
    "결과에 metrics 없음": ("port_result", modified(PORT_RESULT, ["metrics"], KeyError)),
    "결과에 tool_versions 없음": (
        "port_result",
        modified(PORT_RESULT, ["tool_versions"], KeyError),
    ),
    "결과에 모르는 필드": ("port_result", modified(PORT_RESULT, ["extra"], 1)),
    "산출물 ID가 빈 문자열": (
        "port_result",
        modified(PORT_RESULT, ["output_artifact_ids"], [""]),
    ),
    "지표 값이 불리언": ("port_result", modified(PORT_RESULT, ["metrics", "frames"], True)),
    "timeout이 숫자 문자열": ("port_request", modified(PORT_REQUEST, ["timeout_seconds"], "30")),
    "timeout이 불리언": ("port_request", modified(PORT_REQUEST, ["timeout_seconds"], True)),
    "timeout 0": ("port_request", modified(PORT_REQUEST, ["timeout_seconds"], 0)),
    "timeout 음수": ("port_request", modified(PORT_REQUEST, ["timeout_seconds"], -1)),
    "timeout 누락": ("port_request", modified(PORT_REQUEST, ["timeout_seconds"], KeyError)),
    "retryable이 문자열": ("port_error", modified(PORT_ERROR, ["retryable"], "true")),
    "retryable이 숫자": ("port_error", modified(PORT_ERROR, ["retryable"], 1)),
    "오류 코드 오류": ("port_error", modified(PORT_ERROR, ["code"], "BOOM")),
}

VALID_PORT_PAYLOADS = {
    "요청 그대로": ("port_request", PORT_REQUEST),
    "요청 timeout 1": ("port_request", modified(PORT_REQUEST, ["timeout_seconds"], 1)),
    "요청 입력 산출물 생략": (
        "port_request",
        modified(PORT_REQUEST, ["input_artifact_ids"], KeyError),
    ),
    "결과 그대로": ("port_result", PORT_RESULT),
    "결과 산출물 목록이 비어 있음(키는 필수)": (
        "port_result",
        modified(PORT_RESULT, ["output_artifact_ids"], []),
    ),
    "결과 지표·도구 버전이 비어 있음(키는 필수)": (
        "port_result",
        modified(modified(PORT_RESULT, ["metrics"], {}), ["tool_versions"], {}),
    ),
    "오류 그대로": ("port_error", PORT_ERROR),
    "오류 retryable false": ("port_error", modified(PORT_ERROR, ["retryable"], False)),
    "오류 stage 생략": ("port_error", modified(PORT_ERROR, ["stage"], KeyError)),
}


@pytest.mark.parametrize("case", list(INVALID_PORT_PAYLOADS))
def test_schema_rejects_what_the_model_rejects_for_ports(case):
    name, payload = INVALID_PORT_PAYLOADS[case]
    model = PORT_CASES[name][0]
    assert not model_accepts(model, payload), "모델이 거절해야 하는 케이스"
    assert not schema_accepts(name, payload)


@pytest.mark.parametrize("case", list(VALID_PORT_PAYLOADS))
def test_schema_accepts_what_the_model_accepts_for_ports(case):
    name, payload = VALID_PORT_PAYLOADS[case]
    model = PORT_CASES[name][0]
    assert model_accepts(model, payload), "모델이 받아들여야 하는 케이스"
    assert schema_accepts(name, payload)


def test_port_result_schema_requires_every_field():
    schema = load_schema("port_result")
    assert set(schema["required"]) == {
        "schema_version",
        "output_artifact_ids",
        "metrics",
        "tool_versions",
    }


def test_body_input_schema_rejects_unsupported_protocol():
    body = {**REQUEST["measurements"], "measurement_protocol_version": "measure_v1"}
    assert schema_accepts("body_input", body)
    assert not schema_accepts("body_input", {**body, "measurement_protocol_version": "x"})


# --- 열거형과 상태 응답 ------------------------------------------------------------------------


def test_report_schema_enums_match_python_enums():
    from fitting.domain import FitLabel, ReasonCode, Region

    defs = load_schema("fit_report")["$defs"]
    assert set(defs["FitLabel"]["enum"]) == {e.value for e in FitLabel}
    assert set(defs["ReasonCode"]["enum"]) == {e.value for e in ReasonCode}
    assert set(defs["Region"]["enum"]) == {e.value for e in Region}


def test_status_schema_rejects_unknown_enum_values():
    status = load_sample("fitting_status_running.json")
    assert schema_accepts("fitting_status", status)
    assert not schema_accepts("fitting_status", modified(status, ["status"], "DONE"))
    assert not schema_accepts("fitting_status", modified(status, ["visual", "status"], "BUSY"))
    assert not schema_accepts(
        "fitting_status", modified(status, ["visual", "quality_status"], "MAYBE")
    )


def test_report_schema_rejects_unknown_label():
    report = load_sample("fit_report.json")
    assert schema_accepts("fit_report", report)
    assert not schema_accepts("fit_report", modified(report, ["regions", 0, "label"], "FINE"))
