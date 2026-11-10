"""사용자 신체 입력과 작업 생성 요청 (명세서 3장, 11장)."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from fitting.domain.enums import Mode

# 명세서에는 `measurement_protocol_version`의 값이 "measure_v1"(API 예시)과
# "measurement_protocol_v1"(5장)로 두 가지로 적혀 있다. 이 구현은 API 예시의 "measure_v1"을 쓴다.
SUPPORTED_PROTOCOL_VERSIONS = frozenset({"measure_v1"})


def _body_cm(low: float, high: float):
    """cm 단위 유한한 숫자. 문자열·불리언·NaN·무한대는 거절하고 범위 경계는 포함한다."""
    return Annotated[float, Field(strict=True, allow_inf_nan=False, ge=low, le=high)]


# 초기 지원 범위는 UI와 모델의 지원 범위를 정하기 위한 제안값이다 (명세서 3장).
HeightCm = _body_cm(140.0, 210.0)
ChestCm = _body_cm(60.0, 160.0)
WaistCm = _body_cm(50.0, 160.0)
ShoulderCm = _body_cm(28.0, 60.0)
ArmLengthCm = _body_cm(40.0, 85.0)


def _protocol_field():
    """스키마에도 지원하는 프로토콜 버전만 허용하도록 enum을 넣는다."""
    return Field(json_schema_extra={"enum": sorted(SUPPORTED_PROTOCOL_VERSIONS)})


def _check_protocol(value: str) -> str:
    if value not in SUPPORTED_PROTOCOL_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_PROTOCOL_VERSIONS))
        raise ValueError(f"지원하지 않는 측정 프로토콜 버전입니다: {value} (지원: {supported})")
    return value


class Measurements(BaseModel):
    """사용자가 입력하는 다섯 신체 치수. 단위는 cm이다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # 측정 위치(measure_v1)는 명세서 3장 표 원문이다. docs/decisions.md "신체 측정 위치"와 같다.
    height_cm: HeightCm = Field(description="맨발로 선 상태의 바닥부터 정수리까지 수직 높이")
    chest_cm: ChestCm = Field(
        description="가슴이 가장 돌출된 수평 위치의 둘레. 줄자를 당기지 않음"
    )
    waist_cm: WaistCm = Field(description="자연 허리 위치의 수평 둘레. 배꼽 둘레와 구분")
    shoulder_cm: ShoulderCm = Field(
        description="좌우 어깨끝 기준점 사이의 직선 거리. 몸 표면을 따른 곡선 길이와 구분"
    )
    arm_length_cm: ArmLengthCm = Field(
        description=(
            "오른쪽 어깨끝에서 팔꿈치를 거쳐 손목 기준점까지. 약하게 굽힌 표준 측정 자세 사용"
        )
    )


class BodyInput(Measurements):
    """작업 스냅샷에 고정하는 신체 입력 (명세서 9장). 다섯 치수와 측정 프로토콜 버전."""

    measurement_protocol_version: str = _protocol_field()

    @field_validator("measurement_protocol_version")
    @classmethod
    def _protocol_supported(cls, value: str) -> str:
        return _check_protocol(value)


class FittingRequest(BaseModel):
    """POST /api/fittings 요청 본문 (명세서 11장). Idempotency-Key는 헤더로 받는다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    garment_variant_id: str = Field(min_length=1)
    mode: Mode = Mode.FULL
    # 실사화(외부 서비스로 렌더 클립 전송)에 동의했는지. 기본은 동의하지 않음이다.
    # false면 full이어도 렌더까지만 하고 실사화·검증은 건너뛴다(VisualSkipReason.CONSENT_NOT_GIVEN).
    realize_consent: bool = Field(default=False, strict=True)
    measurements: Measurements
    measurement_protocol_version: str = _protocol_field()

    @field_validator("measurement_protocol_version")
    @classmethod
    def _protocol_supported(cls, value: str) -> str:
        return _check_protocol(value)

    def to_body_input(self) -> BodyInput:
        return BodyInput(
            **self.measurements.model_dump(),
            measurement_protocol_version=self.measurement_protocol_version,
        )
