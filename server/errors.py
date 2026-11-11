"""API 오류를 `ApiError` 한 형식으로 바꾼다 (#2).

FastAPI 기본 422 응답(`{"detail": [...]}`)은 쓰지 않는다. 검증 라이브러리의 원문 메시지와
계산기의 내부 사유(`FitEngineError.reason`)는 응답에 넣지 않고 서버 로그에만 남긴다.
"""

import logging
from collections.abc import Sequence
from typing import Any

from annotated_types import Ge, Le
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from fitting.domain import ApiError, ErrorCode, FieldError, FieldErrorCode, Measurements
from fitting.fit import FitEngineError
from fitting.services.jobs import IdempotencyConflict

logger = logging.getLogger(__name__)

INPUT_INVALID_MESSAGE = "입력한 값을 확인해 주세요."
GARMENT_NOT_FOUND_MESSAGE = "선택한 의류를 찾을 수 없어요. 목록에서 다시 선택해 주세요."
JOB_NOT_FOUND_MESSAGE = "작업을 찾을 수 없어요. 서버가 다시 시작됐거나 오래된 작업일 수 있어요."
ARTIFACT_NOT_FOUND_MESSAGE = "요청한 파일을 찾을 수 없어요."
IDEMPOTENCY_CONFLICT_MESSAGE = "같은 Idempotency-Key로 다른 내용의 요청을 보낼 수 없어요."

# 작업 요청의 Idempotency-Key 헤더 이름. 오류의 fields[].field에 이 이름을 쓴다.
IDEMPOTENCY_HEADER = "Idempotency-Key"

# 계산할 수 없는 의류(FitEngineError) → HTTP 상태. [제안] 명세서에 상태 코드가 없어서
# "요청 형식은 맞지만 지금 의류 상태로는 처리할 수 없음"이라는 뜻으로 409를 쓴다.
GARMENT_NOT_READY_STATUS = 409

# 화면 문구에 쓰는 필드 이름
_FIELD_NAMES = {
    "garment_variant_id": "의류",
    "measurements": "신체 치수",
    "measurement_protocol_version": "측정 방법 버전",
    "height_cm": "키",
    "chest_cm": "가슴둘레",
    "waist_cm": "허리둘레",
    "shoulder_cm": "어깨너비",
    "arm_length_cm": "팔길이",
}

_NOT_A_NUMBER_TYPES = {"float_type", "float_parsing", "finite_number", "int_type", "int_parsing"}
_RANGE_TYPES = {"greater_than_equal", "less_than_equal", "greater_than", "less_than"}


class GarmentNotFound(Exception):
    def __init__(self, garment_id: str) -> None:
        super().__init__(garment_id)
        self.garment_id = garment_id


class JobNotFound(Exception):
    def __init__(self, job_id: str) -> None:
        super().__init__(job_id)
        self.job_id = job_id


class ArtifactNotFound(Exception):
    def __init__(self, artifact_id: str) -> None:
        super().__init__(artifact_id)
        self.artifact_id = artifact_id


def _error_response(status: int, error: ApiError) -> JSONResponse:
    return JSONResponse(status_code=status, content=error.model_dump(mode="json"))


def _measurement_range(name: str) -> tuple[float | None, float | None]:
    """신체 치수 필드의 지원 범위(경계 포함). 모델에 적힌 값을 그대로 쓴다."""
    info = Measurements.model_fields.get(name)
    low = high = None
    for item in info.metadata if info else ():
        if isinstance(item, Ge):
            low = float(item.ge)
        elif isinstance(item, Le):
            high = float(item.le)
    return low, high


def _field_error(error: dict[str, Any]) -> FieldError:
    loc: Sequence[Any] = error.get("loc", ())
    if loc and loc[0] == "body":
        loc = loc[1:]
    field = ".".join(str(part) for part in loc)
    name = str(loc[-1]) if loc else ""
    label = _FIELD_NAMES.get(name, field or "요청 본문")
    kind = error.get("type", "")

    if kind == "missing":
        return FieldError(
            field=field, code=FieldErrorCode.MISSING, message=f"{label} 값을 입력해 주세요."
        )
    if kind == "extra_forbidden":
        return FieldError(
            field=field,
            code=FieldErrorCode.UNKNOWN_FIELD,
            message=f"알 수 없는 항목입니다: {field}",
        )
    if kind in _NOT_A_NUMBER_TYPES:
        return FieldError(
            field=field,
            code=FieldErrorCode.NOT_A_NUMBER,
            message=f"{label} 값은 숫자로 입력해 주세요.",
        )
    if kind in _RANGE_TYPES:
        low, high = _measurement_range(name)
        if low is not None and high is not None:
            message = f"{label} 값은 {low:g}~{high:g} cm 사이로 입력해 주세요."
        else:
            message = f"{label} 값이 지원 범위를 벗어났습니다."
        return FieldError(
            field=field, code=FieldErrorCode.OUT_OF_RANGE, message=message, min=low, max=high
        )
    if kind == "value_error" and name == "measurement_protocol_version":
        return FieldError(
            field=field,
            code=FieldErrorCode.UNSUPPORTED_VALUE,
            message="지원하지 않는 측정 방법 버전입니다.",
        )
    if kind == "json_invalid":
        return FieldError(
            field="", code=FieldErrorCode.INVALID, message="요청 본문이 올바른 JSON이 아닙니다."
        )
    return FieldError(
        field=field, code=FieldErrorCode.INVALID, message=f"{label} 값의 형식이 올바르지 않습니다."
    )


def input_invalid_error(errors: Sequence[dict[str, Any]]) -> ApiError:
    """검증 오류 목록(FastAPI·pydantic의 `errors()`) → 422 INPUT_INVALID 응답 본문."""
    return ApiError(
        code=ErrorCode.INPUT_INVALID,
        safe_message=INPUT_INVALID_MESSAGE,
        fields=[_field_error(e) for e in errors],
    )


def garment_not_found_error() -> ApiError:
    """404 GARMENT_NOT_FOUND 응답 본문."""
    return ApiError(code=ErrorCode.GARMENT_NOT_FOUND, safe_message=GARMENT_NOT_FOUND_MESSAGE)


def job_not_found_error() -> ApiError:
    """404 JOB_NOT_FOUND 응답 본문."""
    return ApiError(code=ErrorCode.JOB_NOT_FOUND, safe_message=JOB_NOT_FOUND_MESSAGE)


def artifact_not_found_error() -> ApiError:
    """404 ARTIFACT_NOT_FOUND 응답 본문. 없는 ID와 형식이 틀린 ID를 구분하지 않는다."""
    return ApiError(code=ErrorCode.ARTIFACT_NOT_FOUND, safe_message=ARTIFACT_NOT_FOUND_MESSAGE)


def idempotency_conflict_error() -> ApiError:
    """422 INPUT_INVALID 응답 본문. 같은 키에 다른 요청 본문이 왔다."""
    return ApiError(
        code=ErrorCode.INPUT_INVALID,
        safe_message=INPUT_INVALID_MESSAGE,
        fields=[
            FieldError(
                field=IDEMPOTENCY_HEADER,
                code=FieldErrorCode.INVALID,
                message=IDEMPOTENCY_CONFLICT_MESSAGE,
            )
        ],
    )


def garment_not_ready_error(exc: FitEngineError) -> ApiError:
    """계산기 오류 → 409 응답 본문. 코드와 화면 문구만 쓰고 내부 사유는 넣지 않는다."""
    port_error = exc.to_port_error()
    return ApiError(code=port_error.code, safe_message=port_error.safe_message)


async def _on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _error_response(422, input_invalid_error(exc.errors()))


async def _on_garment_not_found(request: Request, exc: GarmentNotFound) -> JSONResponse:
    return _error_response(404, garment_not_found_error())


async def _on_job_not_found(request: Request, exc: JobNotFound) -> JSONResponse:
    return _error_response(404, job_not_found_error())


async def _on_artifact_not_found(request: Request, exc: ArtifactNotFound) -> JSONResponse:
    return _error_response(404, artifact_not_found_error())


async def _on_idempotency_conflict(request: Request, exc: IdempotencyConflict) -> JSONResponse:
    logger.info("Idempotency-Key 충돌: %s", exc.key)  # 키 값은 로그에만
    return _error_response(422, idempotency_conflict_error())


async def _on_fit_engine_error(request: Request, exc: FitEngineError) -> JSONResponse:
    logger.info("핏 계산 불가 %s: %s", exc.code.value, exc.reason)  # 내부 사유는 로그에만
    return _error_response(GARMENT_NOT_READY_STATUS, garment_not_ready_error(exc))


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _on_validation_error)
    app.add_exception_handler(GarmentNotFound, _on_garment_not_found)
    app.add_exception_handler(JobNotFound, _on_job_not_found)
    app.add_exception_handler(ArtifactNotFound, _on_artifact_not_found)
    app.add_exception_handler(IdempotencyConflict, _on_idempotency_conflict)
    app.add_exception_handler(FitEngineError, _on_fit_engine_error)
