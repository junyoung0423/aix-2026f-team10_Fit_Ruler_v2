"""포트: 서비스와 어댑터 사이의 요청·결과·오류와 인터페이스 (#1)."""

from fitting.ports.dto import (
    ARTIFACT_PRODUCING_STAGES,
    PortError,
    PortRequest,
    PortResult,
    PortResultInvalid,
    require_output_artifacts,
)
from fitting.ports.interfaces import (
    BODY_OUTPUTS,
    DRAPE_OUTPUTS,
    REALIZE_OUTPUTS,
    RENDER_OUTPUTS,
    VALIDATE_OUTPUTS,
    BodyBuilder,
    Draper,
    FitCalculator,
    PortFailure,
    Realizer,
    Renderer,
    Validator,
    output_artifact,
)

__all__ = [
    "ARTIFACT_PRODUCING_STAGES",
    "BODY_OUTPUTS",
    "DRAPE_OUTPUTS",
    "REALIZE_OUTPUTS",
    "RENDER_OUTPUTS",
    "VALIDATE_OUTPUTS",
    "BodyBuilder",
    "Draper",
    "FitCalculator",
    "PortError",
    "PortFailure",
    "PortRequest",
    "PortResult",
    "PortResultInvalid",
    "Realizer",
    "Renderer",
    "Validator",
    "output_artifact",
    "require_output_artifacts",
]
