"""팀 전체가 공유하는 데이터 계약 (#1).

이 패키지의 모델이 JSON 스키마(`docs/api/schemas/`)와 API 응답 예시(`docs/api/examples/`)의
원본이다. 모델을 바꾸면 `python -m tools.export_contracts`로 스키마와 예시를 다시 만들고,
CONTRIBUTING.md의 계약 변경 규칙을 따른다.
"""

from fitting.domain.enums import (
    ErrorCode,
    FitLabel,
    GarmentStatus,
    JobStatus,
    Mode,
    QualityStatus,
    ReasonCode,
    Region,
    Stage,
    StageStatus,
)
from fitting.domain.garment import (
    FAMILY_BASIC_SHORT_SLEEVE_CREW,
    MAX_GARMENT_CM,
    GarmentMeasurements,
    GarmentVariant,
    MeasurementMeta,
)
from fitting.domain.measurements import (
    SUPPORTED_PROTOCOL_VERSIONS,
    BodyInput,
    FittingRequest,
    Measurements,
)
from fitting.domain.report import FitReport, RegionResult
from fitting.domain.status import FittingStatus, ReportPart, StageProgress, VisualPart
from fitting.domain.versions import SCHEMA_VERSION

__all__ = [
    "FAMILY_BASIC_SHORT_SLEEVE_CREW",
    "MAX_GARMENT_CM",
    "SCHEMA_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "BodyInput",
    "ErrorCode",
    "FitLabel",
    "FitReport",
    "FittingRequest",
    "FittingStatus",
    "GarmentMeasurements",
    "GarmentStatus",
    "GarmentVariant",
    "JobStatus",
    "MeasurementMeta",
    "Measurements",
    "Mode",
    "QualityStatus",
    "ReasonCode",
    "Region",
    "RegionResult",
    "ReportPart",
    "Stage",
    "StageProgress",
    "StageStatus",
    "VisualPart",
]
