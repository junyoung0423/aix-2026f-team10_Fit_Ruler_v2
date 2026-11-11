"""등록 의류(SKU)와 의류 실측 (명세서 3장, 9장)."""

from datetime import date
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fitting.domain.enums import GarmentStatus
from fitting.domain.versions import SCHEMA_VERSION

# 첫 버전이 지원하는 의류군 (명세서 1장 R01: 성인용 기본 반팔 라운드넥, 무지)
FAMILY_BASIC_SHORT_SLEEVE_CREW = "basic_short_sleeve_crew"

# 의류 실측의 지원 범위(cm): 0 초과 300 이하. 상한은 가장 큰 신체 입력(가슴 160 cm)에 여유를
# 더한 둘레도 넉넉히 담으면서, 비현실적인 값(예: 1e30)이 핏 계산의 10진수 반올림에서
# 예외를 일으키기 전에 입력 단계에서 거절하기 위한 제안값이다. 경계값은 포함한다.
# 모델과 JSON 스키마가 같은 범위를 쓰도록 이 한 곳에서만 정한다.
MAX_GARMENT_CM = 300.0

# 의류 실측은 0보다 크고 MAX_GARMENT_CM 이하인 유한한 cm 값이다.
# 없는 값은 0이 아니라 null로 둔다 (명세서 3장).
GarmentCm = Annotated[float, Field(strict=True, allow_inf_nan=False, gt=0, le=MAX_GARMENT_CM)]


class GarmentMeasurements(BaseModel):
    """의류 실측 (명세서 3장). 단면과 둘레, 소매와 팔길이를 서로 대체하지 않는다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chest_girth_cm: GarmentCm | None = None  # 가슴 측정선의 전체 둘레
    waist_girth_cm: GarmentCm | None = None  # 신체 자연 허리와 대응하는 위치의 전체 둘레
    shoulder_width_cm: GarmentCm | None = None  # 좌우 어깨 봉제선 끝 기준점 사이 거리
    sleeve_length_cm: GarmentCm | None = None  # 어깨 봉제선부터 소매 끝까지
    body_length_cm: GarmentCm | None = None  # 목 옆 높은 어깨점(HPS)부터 앞 밑단까지


class MeasurementMeta(BaseModel):
    """의류 실측의 측정 위치·방법·출처 기록 (명세서 3장 measurement_meta)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    measurement_protocol_version: str = Field(min_length=1)
    source: str = Field(min_length=1)  # 원출처 설명
    is_measured: bool  # 실제 의류를 실측했는지. false면 화면에 "시연용 의류"로 표시한다
    chest_from_flat_width: bool = False  # 단면 너비를 2배 해서 둘레로 변환했는지
    waist_from_flat_width: bool = False
    waist_position_matched: bool = False  # 허리 위치가 신체 자연 허리와 대응하는지 확인했는지
    measured_by: str | None = None
    measured_on: date | None = None
    notes: str | None = None


# 화면 색 견본 값: `#rrggbb` (소문자 16진수 6자리)
HEX_COLOR_PATTERN = r"^#[0-9a-f]{6}$"

# 섬유 비율(%): 0 초과 100 이하
FiberPercent = Annotated[float, Field(strict=True, allow_inf_nan=False, gt=0, le=100)]


class FabricInfo(BaseModel):
    """원단 정보 (#3). 출처 값을 옮겨 두는 곳이고, 천 물성 매핑은 C4(#8)에서 한다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # 섬유 키(영어 소문자·밑줄, 예: cotton, polyester). 출처에 적힌 순서다.
    fibers: list[str] = Field(default_factory=list)
    # 섬유 키 -> 비율(%). 출처에 비율이 없으면(AI Hub 71501) null이다.
    composition: dict[str, FiberPercent] | None = None
    # 신축성. 출처 값을 그대로 둔다 (AI Hub 71501: none at all, none, contain little, contain,
    # contain a lot).
    stretch: str | None = None
    # 실사화(D5) 프롬프트용 영어 한 줄. 예: "plain navy cotton jersey"
    description_en: str | None = None


# JSON 스키마용 규칙: status가 READY이면 가슴·어깨 실측이 null이 아닌 숫자여야 한다.
# 모델의 `_ready_requires_chest_and_shoulder`와 같은 규칙이며, 스키마만 보는 쪽(화면 등)도
# 같은 판정을 하게 한다.
_READY_REQUIRES_CHEST_AND_SHOULDER = {
    "if": {"properties": {"status": {"const": "READY"}}, "required": ["status"]},
    "then": {
        "properties": {
            "measurements": {
                "properties": {
                    "chest_girth_cm": {"type": "number"},
                    "shoulder_width_cm": {"type": "number"},
                },
                "required": ["chest_girth_cm", "shoulder_width_cm"],
            }
        }
    },
}


class GarmentVariant(BaseModel):
    """핏형·사이즈·색상이 하나로 확정된 선택 단위(SKU) (명세서 1장, 9장)."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra=_READY_REQUIRES_CHEST_AND_SHOULDER,
    )

    schema_version: str = SCHEMA_VERSION
    id: str = Field(min_length=1)
    style: str = Field(min_length=1)  # 핏형. 예: regular(기본핏), relaxed(여유핏)
    size: str = Field(min_length=1)  # 예: S, M, L
    color: str = Field(min_length=1)  # 영어 소문자 키. 예: navy, black, white
    # 화면 색 견본용 `#rrggbb`. 출처에 없으면 색 키의 대표값을 쓴다 (#3)
    color_hex: str | None = Field(default=None, pattern=HEX_COLOR_PATTERN)
    fabric_preset: str = Field(min_length=1)
    fabric: FabricInfo | None = None  # 원단 정보 (#3)
    garment_family: str = FAMILY_BASIC_SHORT_SLEEVE_CREW
    status: GarmentStatus
    measurements: GarmentMeasurements
    measurement_meta: MeasurementMeta
    asset_version: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)

    @model_validator(mode="after")
    def _ready_requires_chest_and_shoulder(self) -> "GarmentVariant":
        """필수 가슴·어깨 실측이 없으면 READY로 전환할 수 없다 (명세서 3장)."""
        if self.status is GarmentStatus.READY:
            missing = [
                name
                for name, value in (
                    ("chest_girth_cm", self.measurements.chest_girth_cm),
                    ("shoulder_width_cm", self.measurements.shoulder_width_cm),
                )
                if value is None
            ]
            if missing:
                raise ValueError(f"READY 의류에는 필수 실측이 필요합니다: {', '.join(missing)}")
        return self

    @property
    def is_demo(self) -> bool:
        """실제 의류와 대조되지 않은 시연용 의류인지 여부."""
        return not self.measurement_meta.is_measured
