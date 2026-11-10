"""피팅 흐름이 쓰는 기능 포트 (#1).

서비스(`fitting/services/`)는 이 인터페이스만 알고, 실제 구현(어댑터)은 실행할 때 넣어 준다.
CI와 테스트는 `fitting/adapters/fake/`의 가짜 어댑터로 전체 흐름을 돌린다.

| 포트 | 단계 | 실제 구현 | 이슈 |
|---|---|---|---|
| FitCalculator | FIT_CALCULATE·FIT_REPORT | `fitting/fit/` (B2) | #2 |
| BodyBuilder | BODY_FIT | `adapters/smpl/` (D3) | #7 |
| Draper | DRAPE | `adapters/blender/` (C4) | #8 |
| Renderer | RENDER | `adapters/blender/` (C5) | #10 |
| Realizer | REALIZE | `adapters/fal_ltx/` (D4) | #10 |
| Validator | VALIDATE | `adapters/media/`·`verify/` (D5·D6) | #11 |

핏 계산은 외부 도구가 없는 순수 로직이지만, B2 전에도 흐름을 돌릴 수 있도록 포트로 둔다.

외부 기능 포트는 성공하면 `PortResult`를 돌려주고, 실패하면 `PortFailure`를 던진다.
산출물 ID는 `PortRequest.input_artifact_ids`로 다음 단계에 넘긴다. 단계별로 넘기는 ID는 아래와 같다.
결과의 `output_artifact_ids` 순서는 아래 `*_OUTPUTS` 상수가 정하고, 서비스와 가짜 어댑터는 숫자
인덱스 대신 `output_artifact(ids, 상수, 역할)`로 위치를 찾는다.

- BodyBuilder: 입력 없음 → `BODY_OUTPUTS` [몸 메시]
- Draper: [몸 메시] → `DRAPE_OUTPUTS` [착장 결과, 3D 미리보기]
- Renderer: [착장 결과] → `RENDER_OUTPUTS` [정면 렌더 클립]
- Realizer: [정면 렌더 클립] → `REALIZE_OUTPUTS` [실사 영상]
- Validator: [정면 렌더 클립, 실사 영상] → `VALIDATE_OUTPUTS` [대표 프레임, 대응 렌더 프레임]

지표(`PortResult.metrics`) 키는 각 포트 설명에 적는다. 키 이름만 약속하고 값은 검증하지 않는다.
"""

from datetime import datetime
from typing import Protocol

from fitting.domain import BodyInput, FitReport, GarmentVariant
from fitting.ports.dto import PortError, PortRequest, PortResult

# 포트별 산출물 역할 이름과 순서. `output_artifact_ids`는 이 순서를 따른다.
BODY_OUTPUTS = ("body_mesh",)
DRAPE_OUTPUTS = ("drape_result", "drape_preview")
RENDER_OUTPUTS = ("render_clip",)
REALIZE_OUTPUTS = ("video",)
VALIDATE_OUTPUTS = ("frame", "render_frame")


def output_artifact(ids: list[str], outputs: tuple[str, ...], role: str) -> str | None:
    """역할 이름으로 산출물 ID를 찾는다. 결과에 그 위치가 없으면 None이다.

    Raises:
        ValueError: `outputs`에 없는 역할 이름이다(프로그래밍 오류).
    """
    index = outputs.index(role)
    return ids[index] if len(ids) > index else None


class PortFailure(Exception):
    """어댑터가 단계를 끝내지 못했을 때 던진다. 화면에는 `error.safe_message`만 보인다."""

    def __init__(self, error: PortError) -> None:
        super().__init__(f"{error.code.value}: {error.safe_message}")
        self.error = error


class FitCalculator(Protocol):
    def calculate(
        self,
        body: BodyInput,
        garment: GarmentVariant,
        *,
        job_id: str,
        created_at: datetime,
    ) -> FitReport: ...


class BodyBuilder(Protocol):
    """입력 치수에 맞춘 몸 메시를 만든다 (D3). 산출물은 `BODY_OUTPUTS`.

    지표: `fitted_height_cm`, `fitted_chest_cm`, `fitted_waist_cm`, `fitted_shoulder_cm`,
    `fitted_arm_length_cm`(만든 몸을 다시 잰 치수), `max_abs_error_cm`(입력과의 최대 절대 오차).
    허용 오차를 넘으면 `BODY_FIT_NOT_CONVERGED`로 실패한다. 허용 오차 값은 docs/decisions.md의
    "허용 오차"에서 정한다.
    """

    def build_body(self, request: PortRequest, body: BodyInput) -> PortResult: ...


class Draper(Protocol):
    """몸에 옷을 입힌다 (C4). 산출물은 `DRAPE_OUTPUTS`.

    지표 키는 C4에서 정한다.
    """

    def drape(self, request: PortRequest, garment: GarmentVariant) -> PortResult: ...


class Renderer(Protocol):
    """정면 고정 카메라 클립을 렌더한다 (C5). 산출물은 `RENDER_OUTPUTS`.

    지표 키는 C5에서 정한다.
    """

    def render(self, request: PortRequest, garment: GarmentVariant) -> PortResult: ...


class Realizer(Protocol):
    """렌더 클립을 외부 서비스로 실사화한다 (D4). 산출물은 `REALIZE_OUTPUTS`.

    지표: `external_request_id`(외부 요청 ID), `cost_usd`, `queue_s`(대기), `processing_s`(생성).

    외부 요청 재조회 규칙: 외부 생성 요청은 성공했는데 결과를 받지 못하면
    `EXTERNAL_SUBMIT_UNKNOWN`으로 실패하고 `PortError.external_request_id`에 요청 ID를 남긴다.
    요청 ID가 있으면 다시 제출하지 말고 같은 ID로 결과를 다시 조회한다(생성 비용이 다시 나가지
    않게). 이때 `retryable=true`는 같은 요청 ID 재조회를 뜻한다. 구현은 D4(fal 어댑터)·B5(재시도).
    """

    def realize(self, request: PortRequest, garment: GarmentVariant) -> PortResult: ...


class Validator(Protocol):
    """실사 영상을 렌더와 비교해 자동 검증한다 (D5·D6). 산출물은 `VALIDATE_OUTPUTS`
    [대표 프레임, 대응 렌더 프레임].

    지표: `quality_status`(문자열 PASS·FAIL·UNVERIFIABLE), `color_delta_e`(색 차이),
    `silhouette_iou`(실루엣 겹침). 숫자 지표는 `VisualPart.quality_metrics`로 화면에 간다.
    """

    def validate(self, request: PortRequest, garment: GarmentVariant) -> PortResult: ...
