"""수치 기반 핏 리포트 (명세서 4장, 9장)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from fitting.domain.enums import FitLabel, ReasonCode, Region
from fitting.domain.versions import SCHEMA_VERSION


class RegionResult(BaseModel):
    """한 부위의 비교 결과.

    비교하지 않은 부위는 ease_cm을 null로 두고 reason_code에 이유를 넣는다.
    body_cm, garment_cm은 입력 원래 값이고, ease_cm은 반올림하지 않은 차이다.
    화면에 보일 때만 소수점 한 자리로 반올림한다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    region: Region
    body_cm: float | None
    garment_cm: float | None
    ease_cm: float | None
    label: FitLabel
    reason_code: ReasonCode | None = None
    explanation: str
    # 입력에 없는 신체 위치를 추정해 비교했는지 (#2, 활동지 61행). true면 body_cm은 추정값이고
    # 화면에 추정임을 표시한다. 지금은 총장(body_length)에만 쓰고, 기준표에서 끄면 항상 false다.
    is_estimated: bool = False


class FitReport(BaseModel):
    """완료한 수치 기반 핏 리포트. 규칙 버전을 함께 저장해 나중에 재현할 수 있게 한다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = SCHEMA_VERSION
    job_id: str
    rules_version: str
    regions: list[RegionResult]  # 가슴·어깨·허리·소매·기장을 모두 포함한다
    summary: str  # 예: "가슴 기본 여유 · 어깨 기본 범위". 단일 적합 점수나 확률은 만들지 않는다
    warnings: list[str]
    created_at: datetime
