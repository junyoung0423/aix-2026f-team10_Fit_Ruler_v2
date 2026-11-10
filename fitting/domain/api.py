"""핏 API의 응답 계약: 의류 목록과 오류 응답 (#2).

모든 API 오류는 `ApiError` 한 형식으로 돌려준다. 화면에는 `safe_message`와 `fields`만 쓰고,
계산기의 내부 사유(`FitEngineError.reason`)나 검증 라이브러리의 원문 메시지는 넣지 않는다.
"""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from fitting.domain.enums import ErrorCode
from fitting.domain.garment import GarmentVariant
from fitting.domain.versions import SCHEMA_VERSION


class FieldErrorCode(str, Enum):
    """[제안] 입력 필드 하나가 틀린 이유. 화면이 필드별 안내 문구를 고르는 데 쓴다."""

    MISSING = "MISSING"  # 필수 필드가 없다
    UNKNOWN_FIELD = "UNKNOWN_FIELD"  # 모르는 필드가 있다
    NOT_A_NUMBER = "NOT_A_NUMBER"  # 숫자가 아니다(문자열 숫자 "100", 불리언, NaN 포함)
    OUT_OF_RANGE = "OUT_OF_RANGE"  # 지원 범위 밖이다
    UNSUPPORTED_VALUE = "UNSUPPORTED_VALUE"  # 지원하지 않는 값이다(예: 측정 프로토콜 버전)
    INVALID = "INVALID"  # 그 밖의 형식 오류(빈 문자열, 잘못된 JSON 등)


class FieldError(BaseModel):
    """[제안] 틀린 입력 필드 하나."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str  # 요청 본문 안의 위치. 예: "measurements.chest_cm". 본문 전체면 ""
    code: FieldErrorCode
    message: str  # 화면에 그대로 보여 줄 수 있는 한국어 문장
    min: float | None = None  # OUT_OF_RANGE일 때 지원 범위 (경계 포함, cm)
    max: float | None = None


class ApiError(BaseModel):
    """API 오류 응답. HTTP 상태와 함께 돌려준다.

    | 상황 | HTTP | code |
    |---|---|---|
    | 입력 범위·형식 오류 | 422 | INPUT_INVALID (`fields`에 필드별 이유) |
    | 없는 의류 ID | 404 | GARMENT_NOT_FOUND |
    | 준비 안 됨·의류군·프로토콜 불일치·필수 실측 누락 | 409 | GARMENT_NOT_READY |
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = SCHEMA_VERSION
    code: ErrorCode
    safe_message: str
    fields: list[FieldError] | None = None


class GarmentListItem(BaseModel):
    """의류 목록의 한 벌. `is_demo`가 true면 화면에 "시연용 의류"로 표시한다.

    `is_demo`는 `garment.measurement_meta.is_measured`의 반대이고, 화면이 따로 계산하지 않게
    함께 준다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    garment: GarmentVariant
    is_demo: bool


class GarmentList(BaseModel):
    """GET /api/garments 응답. READY 의류만 담는다 (명세서 S04)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = SCHEMA_VERSION
    garments: list[GarmentListItem] = Field(default_factory=list)
