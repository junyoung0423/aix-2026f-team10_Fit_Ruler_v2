"""fit_rules_v0: 부위별 판정 구간, 경계 포함 여부, 화면 문구, 적용 의류군 (명세서 4장).

계산(구간 판정)과 문구 생성을 같은 규칙 객체에서 수행해, 설명 문구와 수치가 어긋나지 않게 한다.
구간 값은 기본 반팔 티셔츠용 초기 검증값이다. 신축성과 착용 취향을 반영한 정답이 아니며,
실제 착용 대조 후 조정한다. 값을 바꾸면 `FIT_RULES_VERSION`을 올려 이전 리포트를 소급
변경하지 않고 재현할 수 있게 한다.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from fitting.domain.enums import FitLabel, Region
from fitting.domain.garment import FAMILY_BASIC_SHORT_SLEEVE_CREW

FIT_RULES_VERSION = "fit_rules_v0"

_ONE_DECIMAL = Decimal("0.1")


def display_cm(value: Decimal) -> str:
    """화면 표시용으로만 소수점 한 자리로 반올림한다. 계산에는 쓰지 않는다."""
    return f"{value.quantize(_ONE_DECIMAL, rounding=ROUND_HALF_UP):.1f}"


@dataclass(frozen=True)
class Band:
    """판정 구간 하나. 여유량이 `upper`보다 작으면(포함이면 이하이면) 이 구간이다.

    `upper`가 None이면 마지막 구간으로, 위 구간에 해당하지 않는 모든 값을 받는다.
    """

    upper: Decimal | None
    upper_inclusive: bool
    label: FitLabel
    label_ko: str

    def contains_upper(self, ease: Decimal) -> bool:
        if self.upper is None:
            return True
        return ease <= self.upper if self.upper_inclusive else ease < self.upper


@dataclass(frozen=True)
class RegionRule:
    region: Region
    short_ko: str  # 종합 문구용 부위명. 예: 가슴
    noun_ko: str  # 설명 문구용 측정 항목명. 예: 가슴둘레
    bands: tuple[Band, ...]
    negative_note: str = ""  # 여유량이 음수일 때 덧붙이는 문구
    note: str = ""  # 항상 덧붙이는 문구

    def classify(self, ease: Decimal) -> Band:
        """여유량을 구간에 대응시킨다. 반올림하지 않은 값으로 판정한다."""
        for band in self.bands:
            if band.contains_upper(ease):
                return band
        raise ValueError(f"{self.region.value}의 마지막 구간은 upper가 None이어야 합니다.")

    def explain(self, ease: Decimal, band: Band) -> str:
        """판정 근거 문장. 산식과 같은 값을 쓰며, 음수 여유를 '착용 불가'로 단정하지 않는다."""
        shown = display_cm(abs(ease))
        if ease == 0:
            relation = "몸과 같아서"
        elif shown == "0.0":
            relation = "몸과 거의 같아서"
        elif ease > 0:
            relation = f"몸보다 {shown} cm 커서"
        else:
            relation = f"몸보다 {shown} cm 작아서"
        text = f"{self.noun_ko}가 {relation} 초기 기준상 {band.label_ko}에 해당합니다."
        if ease < 0 and self.negative_note:
            text += f" {self.negative_note}"
        if self.note:
            text += f" {self.note}"
        return text


@dataclass(frozen=True)
class FitRules:
    version: str
    applicable_families: frozenset[str]
    chest: RegionRule
    shoulder: RegionRule
    waist: RegionRule


_STRETCH_NOTE = "신축성에 따라 실제 착용감은 달라질 수 있어 착용 가능 여부는 판단하지 않습니다."

# 가슴 여유: 0 미만 / 0 이상 6 미만 / 6 이상 14 이하 / 14 초과 (cm)
_CHEST = RegionRule(
    region=Region.CHEST,
    short_ko="가슴",
    noun_ko="가슴둘레",
    bands=(
        Band(Decimal("0"), False, FitLabel.INSUFFICIENT, "여유 부족 예상"),
        Band(Decimal("6"), False, FitLabel.SNUG, "밀착"),
        Band(Decimal("14"), True, FitLabel.REGULAR, "기본 여유"),
        Band(None, False, FitLabel.LOOSE, "넉넉"),
    ),
    negative_note=_STRETCH_NOTE,
)

# 허리 여유: 0 미만 / 0 이상 4 미만 / 4 이상 12 이하 / 12 초과 (cm)
# 위치 대응이 검증된 의류만 적용한다.
_WAIST = RegionRule(
    region=Region.WAIST,
    short_ko="허리",
    noun_ko="허리둘레",
    bands=(
        Band(Decimal("0"), False, FitLabel.INSUFFICIENT, "여유 부족 예상"),
        Band(Decimal("4"), False, FitLabel.SNUG, "밀착"),
        Band(Decimal("12"), True, FitLabel.REGULAR, "기본 여유"),
        Band(None, False, FitLabel.LOOSE, "넉넉"),
    ),
    negative_note=_STRETCH_NOTE,
)

# 어깨 여유: -1 미만 / -1 이상 2 이하 / 2 초과 (cm). 양쪽 합계이며 한쪽 어깨당 값이 아니다.
_SHOULDER = RegionRule(
    region=Region.SHOULDER,
    short_ko="어깨",
    noun_ko="어깨너비",
    bands=(
        Band(Decimal("-1"), False, FitLabel.NARROW, "좁음 예상"),
        Band(Decimal("2"), True, FitLabel.REGULAR, "기본 범위"),
        Band(None, False, FitLabel.DROPPED, "어깨선 내려옴 예상"),
    ),
    note="어깨 여유는 양쪽을 합한 값이며 한쪽 어깨당 값이 아닙니다.",
)

FIT_RULES_V0 = FitRules(
    version=FIT_RULES_VERSION,
    applicable_families=frozenset({FAMILY_BASIC_SHORT_SLEEVE_CREW}),
    chest=_CHEST,
    shoulder=_SHOULDER,
    waist=_WAIST,
)
