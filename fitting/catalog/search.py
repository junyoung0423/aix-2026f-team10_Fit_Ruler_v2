"""치수가 비슷한 견본 검색 (#5). 의류 치수표와 가장 가까운 견본 1개를 고른다.

순수 계산이다. 견본집 파일·네트워크·외부 도구를 모르고, 견본 목록을 받아 결과만 돌려준다.
규칙(비교 치수·가중치·필수 여부)은 코드에 두지 않고 `config/sample_search_v0.yaml`에서 읽는다.

- 비교 치수와 단위는 `GarmentMeasurements`와 같다. 모두 cm이고 가슴·허리는 둘레다. 검색은 단위나
  단면·둘레를 바꾸지 않으므로, 단면 값은 부르는 쪽이 2배 해서 넘긴다.
- 점수 = Σ(가중치 × |견본 − 의류|) ÷ Σ(가중치). 의류와 견본 둘 다 값이 있는 치수만 더한다.
  작을수록 비슷하다. 계산은 핏 계산기처럼 10진수(`Decimal`)로 하고, 점수는 반올림해서 비교한다.
- 순위: 반올림한 점수가 작은 순. 같으면 ① 비교한 치수 개수가 많은 쪽 ② 어깨 차이 절댓값이 작은
  쪽 ③ 가슴 차이 절댓값이 작은 쪽 ④ `sample_id` 오름차순. 입력 순서와 상관없이 결과가 같다.
"""

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from fitting.domain import SCHEMA_VERSION, GarmentMeasurements
from fitting.fit import to_decimal

DEFAULT_SEARCH_RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "sample_search_v0.yaml"

# 동률일 때 차이를 보는 치수 (순위 규칙 ②③). 설정 파일에 없는 치수면 가장 뒤로 보낸다.
_TIE_SHOULDER = "shoulder_width_cm"
_TIE_CHEST = "chest_girth_cm"
_NOT_COMPARED = Decimal("Infinity")

_TOP_KEYS = {"version", "score_decimals", "dimensions"}
_DIMENSION_KEYS = {"name", "weight", "required"}
_MAX_SCORE_DECIMALS = 6


# --- 오류·이유 코드 -----------------------------------------------------------------------------


class SampleSearchErrorCode(str, Enum):
    """견본을 고르지 못한 이유."""

    GARMENT_REQUIRED_MISSING = "GARMENT_REQUIRED_MISSING"  # 의류에 필수 치수가 없다
    NO_CANDIDATES = "NO_CANDIDATES"  # 견본집이 비었거나 모든 견본이 제외됐다
    DUPLICATE_SAMPLE_ID = "DUPLICATE_SAMPLE_ID"  # 견본 ID가 겹친다


class SkipReason(str, Enum):
    """선택 치수를 비교하지 않은 이유."""

    GARMENT_VALUE_MISSING = "GARMENT_VALUE_MISSING"
    SAMPLE_VALUE_MISSING = "SAMPLE_VALUE_MISSING"


class ExcludeReason(str, Enum):
    """견본을 후보에서 뺀 이유."""

    SAMPLE_REQUIRED_MISSING = "SAMPLE_REQUIRED_MISSING"


class SampleSearchError(Exception):
    """견본을 고를 수 없다. `code`로 구분하고, 메시지는 사람이 읽는 한국어 한 줄이다."""

    def __init__(
        self,
        code: SampleSearchErrorCode | str,
        message: str,
        *,
        missing: Sequence[str] = (),
        sample_ids: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.code = SampleSearchErrorCode(code)
        self.message = message
        self.missing = tuple(missing)  # GARMENT_REQUIRED_MISSING: 빠진 의류 치수 필드 이름
        self.sample_ids = tuple(sample_ids)  # DUPLICATE_SAMPLE_ID: 겹친 견본 ID


class SearchRulesError(ValueError):
    """검색 규칙 파일이 형식에 맞지 않는다. 메시지에 문제 위치(예: dimensions[2].weight)가 있다."""


# --- 입력·결과 모델 -----------------------------------------------------------------------------
# 이름은 C3·C4·A4가 받아 쓴다. 바꾸면 계약 변경이다.


class SampleCandidate(BaseModel):
    """검색에 넣는 견본 한 개."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_id: str = Field(min_length=1)  # 형식은 C(#4)가 정한다
    measurements: GarmentMeasurements  # 견본 패턴을 잰 치수 (cm, 가슴·허리는 둘레)


class DimensionComparison(BaseModel):
    """비교한 치수 하나."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension: str  # 예: "chest_girth_cm"
    garment_cm: float
    sample_cm: float
    diff_cm: float  # 견본 − 의류 (부호 있음)
    weight: float


class SkippedDimension(BaseModel):
    """한쪽에 값이 없어 비교하지 않은 선택 치수."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension: str
    reason: SkipReason


class ExcludedSample(BaseModel):
    """필수 치수가 없어 후보에서 뺀 견본."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_id: str
    reason: ExcludeReason
    missing: list[str]  # 견본에 없는 필수 치수 필드 이름


class RankedSample(BaseModel):
    """순위가 매겨진 후보 한 개."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rank: int = Field(ge=1)  # 1부터. 동률이어도 순서대로 매긴다
    sample_id: str
    score_cm: float
    compared: list[DimensionComparison]  # 설정 파일의 치수 순서
    skipped: list[SkippedDimension]


class SampleSearchResult(BaseModel):
    """검색 결과. `ranking[0]`이 선택 견본이다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = SCHEMA_VERSION
    rules_version: str
    selected_sample_id: str
    selected_measurements: GarmentMeasurements
    score_cm: float
    exact_match: bool  # 비교한 모든 치수의 차이가 0인가
    tie_broken: bool  # 1위와 반올림 점수가 같은 다른 후보가 있어 동률 규칙으로 골랐는가
    ranking: list[RankedSample]  # 후보 전체, 순위순
    excluded: list[ExcludedSample]


# --- 규칙 ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DimensionRule:
    name: str  # GarmentMeasurements 필드 이름
    weight: Decimal
    required: bool


@dataclass(frozen=True)
class SearchRules:
    version: str
    dimensions: tuple[DimensionRule, ...]
    score_decimals: int


def load_search_rules(path: Path | str | None = None) -> SearchRules:
    """검색 규칙 YAML을 읽는다. `path`가 없으면 `DEFAULT_SEARCH_RULES_PATH`를 읽는다.

    Raises:
        SearchRulesError: 파일이 없거나, YAML이 아니거나, 규칙 형식에 맞지 않는다.
    """
    path = Path(path) if path is not None else DEFAULT_SEARCH_RULES_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SearchRulesError(f"검색 규칙 파일을 읽을 수 없습니다: {path} ({error})") from error
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        message = f"검색 규칙 파일이 YAML 형식이 아닙니다: {path} ({error})"
        raise SearchRulesError(message) from error
    try:
        return parse_search_rules(data)
    except SearchRulesError as error:
        raise SearchRulesError(f"{path}: {error}") from error


@lru_cache(maxsize=1)
def default_search_rules() -> SearchRules:
    """기본 규칙. 한 번 읽은 뒤에는 같은 객체를 돌려준다."""
    return load_search_rules(DEFAULT_SEARCH_RULES_PATH)


def parse_search_rules(data: Any) -> SearchRules:
    """YAML에서 읽은 값(dict)을 검사해 `SearchRules`로 바꾼다."""
    top = _mapping(data, "검색 규칙", required=_TOP_KEYS, allowed=_TOP_KEYS)
    version = top["version"]
    if not isinstance(version, str) or not version.strip():
        raise SearchRulesError("version: 비어 있지 않은 문자열이어야 합니다")
    decimals = top["score_decimals"]
    if isinstance(decimals, bool) or not isinstance(decimals, int):
        raise SearchRulesError("score_decimals: 정수여야 합니다")
    if not 0 <= decimals <= _MAX_SCORE_DECIMALS:
        raise SearchRulesError(f"score_decimals: 0~{_MAX_SCORE_DECIMALS} 사이여야 합니다")
    raw_dimensions = top["dimensions"]
    if not isinstance(raw_dimensions, list) or not raw_dimensions:
        raise SearchRulesError("dimensions: 비교 치수를 하나 이상 목록으로 적어야 합니다")
    dimensions = tuple(_dimension(d, f"dimensions[{i}]") for i, d in enumerate(raw_dimensions))
    names = [d.name for d in dimensions]
    duplicated = sorted({n for n, count in Counter(names).items() if count > 1})
    if duplicated:
        raise SearchRulesError(f"dimensions: 같은 치수가 두 번 있습니다: {', '.join(duplicated)}")
    if not any(d.required for d in dimensions):
        raise SearchRulesError("dimensions: required가 true인 치수가 하나 이상 있어야 합니다")
    return SearchRules(version=version, dimensions=dimensions, score_decimals=decimals)


def _dimension(data: Any, where: str) -> DimensionRule:
    raw = _mapping(data, where, required=_DIMENSION_KEYS, allowed=_DIMENSION_KEYS)
    name = raw["name"]
    if name not in GarmentMeasurements.model_fields:
        known = ", ".join(GarmentMeasurements.model_fields)
        raise SearchRulesError(f"{where}.name: 모르는 치수입니다: {name!r} (가능: {known})")
    weight = raw["weight"]
    if isinstance(weight, bool) or not isinstance(weight, int | float):
        raise SearchRulesError(f"{where}.weight: 숫자여야 합니다")
    if isinstance(weight, float) and not math.isfinite(weight):
        raise SearchRulesError(f"{where}.weight: 유한한 숫자여야 합니다")
    if weight <= 0:
        raise SearchRulesError(f"{where}.weight: 0보다 커야 합니다")
    required = raw["required"]
    if not isinstance(required, bool):
        raise SearchRulesError(f"{where}.required: true 또는 false여야 합니다")
    decimal_weight = Decimal(repr(weight)) if isinstance(weight, float) else Decimal(weight)
    return DimensionRule(name=name, weight=decimal_weight, required=required)


def _mapping(data: Any, where: str, *, required: set[str], allowed: set[str]) -> Mapping:
    if not isinstance(data, Mapping):
        raise SearchRulesError(f"{where}: 키-값 묶음이어야 합니다")
    keys = set(data)
    missing = sorted(required - keys)
    if missing:
        raise SearchRulesError(f"{where}: 필요한 키가 없습니다: {', '.join(missing)}")
    unknown = sorted(str(k) for k in keys - allowed)
    if unknown:
        raise SearchRulesError(f"{where}: 모르는 키가 있습니다: {', '.join(unknown)}")
    return data


# --- 검색 ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Scored:
    """순위를 매기기 전 후보 한 개. 반올림하지 않은 차이는 동률 규칙에만 쓴다."""

    sample: SampleCandidate
    score: Decimal  # 반올림한 점수
    diffs: dict[str, Decimal]  # 비교한 치수 → 견본 − 의류 (반올림 없음)
    compared: list[DimensionComparison]
    skipped: list[SkippedDimension]

    def sort_key(self) -> tuple:
        shoulder = abs(self.diffs[_TIE_SHOULDER]) if _TIE_SHOULDER in self.diffs else _NOT_COMPARED
        chest = abs(self.diffs[_TIE_CHEST]) if _TIE_CHEST in self.diffs else _NOT_COMPARED
        return (self.score, -len(self.compared), shoulder, chest, self.sample.sample_id)


def search_sample(
    garment: GarmentMeasurements,
    samples: Sequence[SampleCandidate],
    rules: SearchRules | None = None,
) -> SampleSearchResult:
    """의류 치수와 가장 비슷한 견본 1개와 비교 결과를 돌려준다.

    입력을 바꾸지 않고, 같은 입력이면 입력 순서와 상관없이 항상 같은 결과를 낸다.

    Raises:
        SampleSearchError: 의류에 필수 치수가 없다(GARMENT_REQUIRED_MISSING), 견본 ID가 겹친다
            (DUPLICATE_SAMPLE_ID), 후보가 하나도 없다(NO_CANDIDATES). 이 순서로 확인한다.
    """
    rules = rules or default_search_rules()

    missing = [d.name for d in rules.dimensions if d.required and _value(garment, d.name) is None]
    if missing:
        raise SampleSearchError(
            SampleSearchErrorCode.GARMENT_REQUIRED_MISSING,
            f"의류에 필수 치수가 없어 견본을 고를 수 없습니다: {', '.join(missing)}",
            missing=missing,
        )

    duplicated = sorted(
        sample_id for sample_id, count in Counter(s.sample_id for s in samples).items() if count > 1
    )
    if duplicated:
        raise SampleSearchError(
            SampleSearchErrorCode.DUPLICATE_SAMPLE_ID,
            f"견본 ID가 겹칩니다: {', '.join(duplicated)}",
            sample_ids=duplicated,
        )

    excluded: list[ExcludedSample] = []
    candidates: list[_Scored] = []
    for sample in sorted(samples, key=lambda s: s.sample_id):
        absent = [d.name for d in rules.dimensions if d.required and _value(sample, d.name) is None]
        if absent:
            excluded.append(
                ExcludedSample(
                    sample_id=sample.sample_id,
                    reason=ExcludeReason.SAMPLE_REQUIRED_MISSING,
                    missing=absent,
                )
            )
            continue
        candidates.append(_score(garment, sample, rules))

    if not candidates:
        raise SampleSearchError(
            SampleSearchErrorCode.NO_CANDIDATES,
            "비교할 견본이 없습니다. 견본집이 비었거나 모든 견본에 필수 치수가 없습니다.",
        )

    ordered = sorted(candidates, key=_Scored.sort_key)
    best = ordered[0]
    return SampleSearchResult(
        rules_version=rules.version,
        selected_sample_id=best.sample.sample_id,
        selected_measurements=best.sample.measurements,
        score_cm=float(best.score),
        exact_match=all(diff == 0 for diff in best.diffs.values()),
        tie_broken=any(other.score == best.score for other in ordered[1:]),
        ranking=[
            RankedSample(
                rank=rank,
                sample_id=item.sample.sample_id,
                score_cm=float(item.score),
                compared=item.compared,
                skipped=item.skipped,
            )
            for rank, item in enumerate(ordered, start=1)
        ],
        excluded=excluded,
    )


def _value(owner: GarmentMeasurements | SampleCandidate, name: str) -> float | None:
    measurements = owner.measurements if isinstance(owner, SampleCandidate) else owner
    return getattr(measurements, name)


def _score(garment: GarmentMeasurements, sample: SampleCandidate, rules: SearchRules) -> _Scored:
    compared: list[DimensionComparison] = []
    skipped: list[SkippedDimension] = []
    diffs: dict[str, Decimal] = {}
    weighted = Decimal(0)
    total_weight = Decimal(0)
    for rule in rules.dimensions:
        garment_cm = _value(garment, rule.name)
        sample_cm = _value(sample, rule.name)
        if garment_cm is None:
            skipped.append(
                SkippedDimension(dimension=rule.name, reason=SkipReason.GARMENT_VALUE_MISSING)
            )
            continue
        if sample_cm is None:
            skipped.append(
                SkippedDimension(dimension=rule.name, reason=SkipReason.SAMPLE_VALUE_MISSING)
            )
            continue
        diff = to_decimal(sample_cm) - to_decimal(garment_cm)
        diffs[rule.name] = diff
        weighted += rule.weight * abs(diff)
        total_weight += rule.weight
        compared.append(
            DimensionComparison(
                dimension=rule.name,
                garment_cm=garment_cm,
                sample_cm=sample_cm,
                diff_cm=float(diff),
                weight=float(rule.weight),
            )
        )
    # 필수 치수가 양쪽에 있으므로 비교한 치수가 하나 이상이고 total_weight는 0보다 크다.
    step = Decimal(1).scaleb(-rules.score_decimals)
    score = (weighted / total_weight).quantize(step, rounding=ROUND_HALF_UP)
    return _Scored(sample=sample, score=score, diffs=diffs, compared=compared, skipped=skipped)
