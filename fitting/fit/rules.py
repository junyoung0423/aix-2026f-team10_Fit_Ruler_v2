"""핏 기준표의 구조: 부위별 판정 구간, 경계 포함 여부, 화면 문구, 적용 의류군 (명세서 4장).

구간 값과 문구는 코드에 두지 않고 `config/fit_rules_v0.yaml`에 둔다. 이 파일을 읽어 `FitRules`를
만드는 것은 `fitting.fit.loader`다.

계산(구간 판정)과 문구 생성을 같은 규칙 객체에서 수행해, 설명 문구와 수치가 어긋나지 않게 한다.
기준표 값을 바꾸면 YAML의 `version`을 올려 이전 리포트를 소급 변경하지 않고 재현할 수 있게 한다.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from fitting.domain.enums import FitLabel, Region

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
