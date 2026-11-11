"""팀이 공유하는 열거형 계약.

명세서(v0.1)에 이름이 직접 나온 값은 그대로 쓰고, 명세서에 없어서 이 구현에서 정한 값은
`[제안]`으로 표시한다. 이름을 바꾸면 계약 변경이므로 CONTRIBUTING.md의 절차를 따른다.
"""

from enum import Enum


class Mode(str, Enum):
    """실행 방식 (명세서 3장)."""

    REPORT_ONLY = "report_only"
    FULL = "full"


class GarmentStatus(str, Enum):
    """의류 준비 상태 (명세서 2장 S04). READY만 일반 목록에 표시한다."""

    DRAFT = "DRAFT"
    PREPARING = "PREPARING"
    READY = "READY"
    FAILED = "FAILED"


class Region(str, Enum):
    """핏 리포트의 부위. 리포트에는 아래 순서로 모두 포함한다 (명세서 11장)."""

    CHEST = "chest"
    SHOULDER = "shoulder"
    WAIST = "waist"
    SLEEVE = "sleeve"
    BODY_LENGTH = "body_length"


class FitLabel(str, Enum):
    """부위별 판정. 가슴·허리는 네 단계, 어깨는 세 단계 (명세서 4장).

    REGULAR, UNKNOWN은 명세서 예시에 나온 이름이고 나머지 이름은 [제안]이다.
    """

    INSUFFICIENT = "INSUFFICIENT"  # 여유 부족 예상
    SNUG = "SNUG"  # 밀착
    REGULAR = "REGULAR"  # 기본 여유 / 기본 범위
    LOOSE = "LOOSE"  # 넉넉
    NARROW = "NARROW"  # 어깨 좁음 예상
    DROPPED = "DROPPED"  # 어깨선 내려옴 예상
    UNKNOWN = "UNKNOWN"  # 비교할 수 없어 판정하지 않음
    NOT_EVALUATED = "NOT_EVALUATED"  # [제안] 소매·기장처럼 적정 여부를 계산하지 않는 부위


class ReasonCode(str, Enum):
    """판정하지 않은 이유. MEASUREMENT_NOT_COMPARABLE만 명세서에 이름이 있다."""

    MEASUREMENT_NOT_COMPARABLE = "MEASUREMENT_NOT_COMPARABLE"
    MEASUREMENT_MISSING = "MEASUREMENT_MISSING"  # [제안] 의류 실측이 null
    NO_DIRECT_BODY_COUNTERPART = "NO_DIRECT_BODY_COUNTERPART"  # [제안] 소매
    BODY_INPUT_NOT_PROVIDED = "BODY_INPUT_NOT_PROVIDED"  # [제안] 기장


class ErrorCode(str, Enum):
    """오류 코드 (명세서 12장)."""

    INPUT_INVALID = "INPUT_INVALID"
    GARMENT_NOT_READY = "GARMENT_NOT_READY"
    GARMENT_NOT_FOUND = "GARMENT_NOT_FOUND"  # [제안] 없는 의류 ID (핏 API 404). 명세서에 없다
    JOB_NOT_FOUND = "JOB_NOT_FOUND"  # [제안] 없는 작업 ID (작업 API 404). 명세서에 없다
    # [제안] 없는 산출물 ID나 형식이 틀린 ID (산출물 API 404). 명세서에 없다
    ARTIFACT_NOT_FOUND = "ARTIFACT_NOT_FOUND"
    BODY_FIT_NOT_CONVERGED = "BODY_FIT_NOT_CONVERGED"
    DRAPE_INVALID = "DRAPE_INVALID"
    RENDER_FAILED = "RENDER_FAILED"
    REALIZE_TIMEOUT = "REALIZE_TIMEOUT"
    # 외부 제출 결과를 알 수 없다. PortError.external_request_id가 있으면 다시 제출하지 말고 같은
    # 요청 ID로 결과를 다시 조회한다. 이때 retryable=true는 같은 요청 ID 재조회를 뜻한다(B5·D4).
    EXTERNAL_SUBMIT_UNKNOWN = "EXTERNAL_SUBMIT_UNKNOWN"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    VALIDATION_UNVERIFIABLE = "VALIDATION_UNVERIFIABLE"
    BUDGET_LIMIT = "BUDGET_LIMIT"


class Stage(str, Enum):
    """작업 단계. 핏 경로 2개, 영상 경로 5개.

    명세서 11장의 수동 검수(REVIEW)·게시(PUBLISH) 단계는 활동지 범위에 없어 뺐다
    (docs/decisions.md 2026-11-10). 실사 영상의 품질은 VALIDATE(#11)의 자동 검증으로만 판정한다.
    """

    FIT_CALCULATE = "FIT_CALCULATE"
    FIT_REPORT = "FIT_REPORT"
    BODY_FIT = "BODY_FIT"
    DRAPE = "DRAPE"
    RENDER = "RENDER"
    REALIZE = "REALIZE"
    VALIDATE = "VALIDATE"


class StageStatus(str, Enum):
    """단계와 경로의 상태 (명세서 11장)."""

    WAITING = "WAITING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class JobStatus(str, Enum):
    """작업 전체 상태 (명세서 11장)."""

    ACCEPTED = "ACCEPTED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL_SUCCEEDED = "PARTIAL_SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class VisualSkipReason(str, Enum):
    """영상 경로(또는 그 일부)를 건너뛴 이유. 실패가 아니라 사용자가 고른 것이다 (계획 A5·B3)."""

    CONSENT_NOT_GIVEN = "CONSENT_NOT_GIVEN"  # 실사화(외부 전송)에 동의하지 않아 렌더까지만 했다
    REPORT_ONLY = "REPORT_ONLY"  # 핏 리포트만 요청했다


class QualityStatus(str, Enum):
    """실사 영상 자동 검증 결과. PENDING은 명세서 예시, 나머지는 7장의 PASS/FAIL/UNVERIFIABLE."""

    PENDING = "PENDING"
    PASS = "PASS"
    FAIL = "FAIL"
    UNVERIFIABLE = "UNVERIFIABLE"

