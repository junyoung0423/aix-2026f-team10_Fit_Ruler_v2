"""외부 기능 포트의 공통 요청·결과·오류 (명세서 10장).

어댑터의 반환값을 검증한 뒤 도메인 상태를 바꾼다. 화면에는 safe_message만 보이고,
내부 파일 경로와 외부 서비스 응답 원문은 internal_details_ref가 가리키는 운영 기록에만 둔다.

숫자와 불리언은 모델과 JSON 스키마가 같은 판정을 하도록 `strict`로 받는다. 모델만 `"30"`이나
`true`를 정수로 바꿔 받으면, 스키마만 보는 쪽(화면 등)과 서버의 판정이 어긋난다.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr

from fitting.domain.enums import ErrorCode, Stage
from fitting.domain.versions import SCHEMA_VERSION

# 산출물(파일·영상·메시 등)을 만들어 내는 단계. 이 단계의 성공 결과에는 산출물 ID가 있어야 한다.
# [제안] 명세서에 단계별 산출물 목록이 없어서, 생성 단계(신체 맞춤·드레이프·렌더·실사화)만 넣었다.
# 핏 계산·리포트는 엔진이 직접 만들고, 검증은 판정이 결과여서 뺐다.
ARTIFACT_PRODUCING_STAGES = frozenset({Stage.BODY_FIT, Stage.DRAPE, Stage.RENDER, Stage.REALIZE})

ArtifactId = Annotated[str, Field(min_length=1)]

# 지표 값은 숫자 또는 문자열이다. 불리언(`true`)은 숫자로 바꿔 받지 않고 거절한다.
MetricValue = StrictFloat | StrictInt | StrictStr


class PortRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = SCHEMA_VERSION
    job_id: str
    attempt_id: str
    input_artifact_ids: list[str] = Field(default_factory=list)
    config_version: str
    timeout_seconds: int = Field(strict=True, gt=0)
    trace_id: str


class PortError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ErrorCode
    stage: Stage | None = None
    retryable: bool = Field(strict=True)
    safe_message: str
    internal_details_ref: str | None = None
    # 외부 생성 요청은 성공했는데 결과를 받지 못했을 때의 외부 요청 ID (EXTERNAL_SUBMIT_UNKNOWN).
    # 있으면 다시 제출하지 말고 같은 ID로 결과를 다시 조회한다. 성공 결과에는
    # metrics["external_request_id"]로 남는다.
    external_request_id: str | None = None


class PortResult(BaseModel):
    """어댑터가 돌려주는 성공 결과. 네 필드 모두 응답에 반드시 있어야 한다.

    기본값이 없으므로 빈 응답(`{}`)이나 일부만 담은 응답은 검증에서 거절된다.
    `output_artifact_ids`는 키는 필수이고 값은 빈 목록일 수 있다. 산출물을 만드는 단계에서
    목록이 비어 있지 않은지는 `require_output_artifacts`로 확인한다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    output_artifact_ids: list[ArtifactId]
    metrics: dict[str, MetricValue]
    tool_versions: dict[str, str]


class PortResultInvalid(ValueError):
    """형식은 맞지만 해당 단계의 결과로는 받아들일 수 없는 어댑터 응답."""

    def __init__(self, stage: Stage, reason: str) -> None:
        super().__init__(f"{stage.value}: {reason}")
        self.stage = stage
        self.reason = reason  # 운영 기록용. 화면에 보이지 않는다


def require_output_artifacts(result: PortResult, stage: Stage) -> PortResult:
    """산출물을 만드는 단계의 결과에 산출물 ID가 하나 이상 있는지 확인한다.

    어댑터 응답을 `PortResult`로 검증한 뒤, 도메인 상태를 바꾸기 전에 호출한다.

    Raises:
        PortResultInvalid: 산출물 단계인데 `output_artifact_ids`가 비어 있다.
    """
    if stage in ARTIFACT_PRODUCING_STAGES and not result.output_artifact_ids:
        raise PortResultInvalid(stage, "산출물 단계의 결과에 output_artifact_ids가 없습니다")
    return result
