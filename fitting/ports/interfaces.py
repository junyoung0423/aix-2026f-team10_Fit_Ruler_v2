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

- BodyBuilder: 입력 없음 → [몸 메시]
- Draper: [몸 메시] → [착장 결과, 3D 미리보기]
- Renderer: [착장 결과] → [정면 렌더 클립]
- Realizer: [정면 렌더 클립] → [실사 영상]
- Validator: [정면 렌더 클립, 실사 영상] → [대표 프레임], metrics["quality_status"]
"""

from datetime import datetime
from typing import Protocol

from fitting.domain import BodyInput, FitReport, GarmentVariant
from fitting.ports.dto import PortError, PortRequest, PortResult


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
    def build_body(self, request: PortRequest, body: BodyInput) -> PortResult: ...


class Draper(Protocol):
    def drape(self, request: PortRequest, garment: GarmentVariant) -> PortResult: ...


class Renderer(Protocol):
    def render(self, request: PortRequest, garment: GarmentVariant) -> PortResult: ...


class Realizer(Protocol):
    def realize(self, request: PortRequest, garment: GarmentVariant) -> PortResult: ...


class Validator(Protocol):
    def validate(self, request: PortRequest, garment: GarmentVariant) -> PortResult: ...
