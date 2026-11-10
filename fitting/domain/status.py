"""GET /api/fittings/{id} 작업 상태 응답 (명세서 11장, 경로는 3주 계획 B3).

핏 경로(`report`)와 영상 경로(`visual`)는 서로 독립이다. 한 경로의 예외가 다른 경로의
상태를 덮어쓰지 않는다. 응답 필드는 명세서의 예시를 따르고, `stages`와 `error_code`는
화면의 진행 표시(S02)를 위해 이 구현에서 더한 [제안] 필드다.
"""

from pydantic import BaseModel, ConfigDict, Field

from fitting.domain.enums import (
    ErrorCode,
    JobStatus,
    QualityStatus,
    Stage,
    StageStatus,
    VisualSkipReason,
)
from fitting.domain.report import RegionResult
from fitting.domain.versions import SCHEMA_VERSION


class StageProgress(BaseModel):
    """[제안] 단계별 진행. 화면이 단계명과 대기 여부를 표시하는 데 쓴다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: Stage
    status: StageStatus
    attempt_no: int = Field(ge=1)


class ReportPart(BaseModel):
    """핏 경로 상태. 완료하면 리포트의 부위별 결과를 함께 돌려준다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: StageStatus
    rules_version: str | None = None
    regions: list[RegionResult] | None = None
    error_code: ErrorCode | None = None
    summary: str | None = None  # FitReport.summary. 화면의 요약 줄
    warnings: list[str] = Field(default_factory=list)  # FitReport.warnings. 예: DEMO_GARMENT


class VisualPart(BaseModel):
    """영상 경로 상태. report_only 작업은 status가 SKIPPED다.

    3D 미리보기(`drape_preview_artifact_id`)는 실사화가 실패해도 남아 있으면 보여 준다(#12).
    검증에서 기준을 넘으면 `quality_status`가 FAIL이고 `error_code`가 VALIDATION_FAILED다(#11).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: StageStatus
    stage: Stage | None = None
    # 검증을 건너뛰면(영상 경로 SKIPPED, 실사화 미동의) null이다. "검증 대기 중"과 구분한다.
    quality_status: QualityStatus | None = QualityStatus.PENDING
    skip_reason: VisualSkipReason | None = None  # 사용자가 골라서 건너뛴 경우의 이유
    drape_preview_artifact_id: str | None = None  # 3D 착장 미리보기 (#8)
    render_artifact_id: str | None = None  # 정면 렌더 클립 (#10)
    video_artifact_id: str | None = None  # 실사 영상 (#10)
    frame_artifact_id: str | None = None  # 대표 프레임 (#11)
    error_code: ErrorCode | None = None


class FittingStatus(BaseModel):
    """작업 전체 상태와 두 경로의 상태."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = SCHEMA_VERSION
    job_id: str
    status: JobStatus
    report: ReportPart
    visual: VisualPart
    stages: list[StageProgress] = Field(default_factory=list)
