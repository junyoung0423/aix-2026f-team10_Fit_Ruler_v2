"""핏 기준표 YAML을 읽어 `FitRules`를 만든다 (#2).

기본 기준표는 저장소의 `config/fit_rules_v0.yaml`이다. 다른 기준표는 경로를 넘겨 읽는다.
잘못된 기준표는 판정에 쓰이기 전에, 읽을 때 `FitRulesError`로 거절한다.
"""

import math
from collections.abc import Mapping
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from fitting.domain.enums import FitLabel, Region
from fitting.fit.rules import Band, BodyLengthEstimate, FitRules, RegionRule

DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "fit_rules_v0.yaml"

# 구간에 쓸 수 있는 판정. UNKNOWN·NOT_EVALUATED는 비교하지 않은 부위에만 쓴다.
BAND_LABELS = frozenset(
    {
        FitLabel.INSUFFICIENT,
        FitLabel.SNUG,
        FitLabel.REGULAR,
        FitLabel.LOOSE,
        FitLabel.NARROW,
        FitLabel.DROPPED,
    }
)

_TOP_KEYS = {"version", "applicable_families", "regions"}
_OPTIONAL_TOP_KEYS = {"body_length_estimate"}
_ESTIMATE_KEYS = {"enabled", "method", "ratio"}
# 총장 추정 방법. 지금은 키 × 비율 하나만 있다.
BODY_LENGTH_ESTIMATE_METHODS = frozenset({"height_ratio"})
_REGION_KEYS = {"short_ko", "noun_ko", "negative_note", "note", "bands"}
_BAND_KEYS = {"upper", "upper_inclusive", "label", "label_ko"}
_RULE_REGIONS = (Region.CHEST, Region.SHOULDER, Region.WAIST)


class FitRulesError(ValueError):
    """기준표 파일이 형식에 맞지 않는다. 메시지에 문제 위치(예: regions.chest.bands[3])를 담는다."""


def load_fit_rules(path: Path | str | None = None) -> FitRules:
    """기준표 YAML을 읽는다. `path`가 없으면 `DEFAULT_RULES_PATH`를 읽는다.

    Raises:
        FitRulesError: 파일이 없거나, YAML이 아니거나, 기준표 형식에 맞지 않는다.
    """
    path = Path(path) if path is not None else DEFAULT_RULES_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise FitRulesError(f"기준표 파일을 읽을 수 없습니다: {path} ({error})") from error
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise FitRulesError(f"기준표 파일이 YAML 형식이 아닙니다: {path} ({error})") from error
    try:
        return parse_fit_rules(data)
    except FitRulesError as error:
        raise FitRulesError(f"{path}: {error}") from error


@lru_cache(maxsize=1)
def default_fit_rules() -> FitRules:
    """기본 기준표. 한 번 읽은 뒤에는 같은 객체를 돌려준다."""
    return load_fit_rules(DEFAULT_RULES_PATH)


def parse_fit_rules(data: Any) -> FitRules:
    """YAML에서 읽은 값(dict)을 검사해 `FitRules`로 바꾼다."""
    top = _mapping(data, "기준표", required=_TOP_KEYS, allowed=_TOP_KEYS | _OPTIONAL_TOP_KEYS)
    version = _text(top["version"], "version")
    families = top["applicable_families"]
    if not isinstance(families, list) or not families:
        raise FitRulesError("applicable_families: 의류군을 하나 이상 목록으로 적어야 합니다")
    regions = _mapping(
        top["regions"],
        "regions",
        required={r.value for r in _RULE_REGIONS},
        allowed={r.value for r in _RULE_REGIONS},
    )
    rules = {
        region: _region_rule(region, regions[region.value], f"regions.{region.value}")
        for region in _RULE_REGIONS
    }
    return FitRules(
        version=version,
        applicable_families=frozenset(
            _text(f, f"applicable_families[{i}]") for i, f in enumerate(families)
        ),
        chest=rules[Region.CHEST],
        shoulder=rules[Region.SHOULDER],
        waist=rules[Region.WAIST],
        body_length_estimate=_body_length_estimate(
            top.get("body_length_estimate"), "body_length_estimate"
        ),
    )


def _body_length_estimate(data: Any, where: str) -> BodyLengthEstimate:
    """총장 추정 설정. 없으면 꺼져 있다. 켜면 method와 0 초과 1 미만의 ratio가 있어야 한다."""
    if data is None:
        return BodyLengthEstimate()
    raw = _mapping(data, where, required={"enabled"}, allowed=_ESTIMATE_KEYS)
    enabled = raw["enabled"]
    if not isinstance(enabled, bool):
        raise FitRulesError(f"{where}.enabled: true 또는 false여야 합니다")
    method = _text(raw.get("method", "height_ratio"), f"{where}.method")
    if method not in BODY_LENGTH_ESTIMATE_METHODS:
        allowed = ", ".join(sorted(BODY_LENGTH_ESTIMATE_METHODS))
        raise FitRulesError(f"{where}.method: 모르는 추정 방법입니다: {method!r} (가능: {allowed})")
    ratio = _decimal_or_none(raw.get("ratio"), f"{where}.ratio")
    if ratio is not None and not Decimal(0) < ratio < Decimal(1):
        raise FitRulesError(f"{where}.ratio: 0보다 크고 1보다 작아야 합니다")
    if enabled and ratio is None:
        raise FitRulesError(f"{where}.ratio: 추정을 켜면 비율이 있어야 합니다")
    return BodyLengthEstimate(enabled=enabled, method=method, ratio=ratio)


def _region_rule(region: Region, data: Any, where: str) -> RegionRule:
    raw = _mapping(data, where, required={"short_ko", "noun_ko", "bands"}, allowed=_REGION_KEYS)
    raw_bands = raw["bands"]
    if not isinstance(raw_bands, list) or not raw_bands:
        raise FitRulesError(f"{where}.bands: 구간을 하나 이상 목록으로 적어야 합니다")
    bands = tuple(_band(b, f"{where}.bands[{i}]") for i, b in enumerate(raw_bands))
    _check_bands(bands, f"{where}.bands")
    return RegionRule(
        region=region,
        short_ko=_text(raw["short_ko"], f"{where}.short_ko"),
        noun_ko=_text(raw["noun_ko"], f"{where}.noun_ko"),
        bands=bands,
        negative_note=_text(raw.get("negative_note", ""), f"{where}.negative_note", empty=True),
        note=_text(raw.get("note", ""), f"{where}.note", empty=True),
    )


def _band(data: Any, where: str) -> Band:
    raw = _mapping(data, where, required={"upper", "label", "label_ko"}, allowed=_BAND_KEYS)
    label_name = raw["label"]
    try:
        label = FitLabel(label_name)
    except ValueError:
        label = None
    if label not in BAND_LABELS:
        allowed = ", ".join(sorted(lb.value for lb in BAND_LABELS))
        raise FitRulesError(f"{where}.label: 모르는 라벨입니다: {label_name!r} (가능: {allowed})")
    inclusive = raw.get("upper_inclusive", False)
    if not isinstance(inclusive, bool):
        raise FitRulesError(f"{where}.upper_inclusive: true 또는 false여야 합니다")
    return Band(
        upper=_decimal_or_none(raw["upper"], f"{where}.upper"),
        upper_inclusive=inclusive,
        label=label,
        label_ko=_text(raw["label_ko"], f"{where}.label_ko"),
    )


def _check_bands(bands: tuple[Band, ...], where: str) -> None:
    """마지막 구간만 upper가 null이고, 나머지 upper는 엄격한 오름차순이며, 라벨이 겹치지 않는다."""
    *body, last = bands
    if last.upper is not None:
        last_at = f"{where}[{len(bands) - 1}].upper"
        raise FitRulesError(f"{last_at}: 마지막 구간은 upper가 null이어야 합니다")
    for i, band in enumerate(body):
        if band.upper is None:
            raise FitRulesError(f"{where}[{i}].upper: 마지막이 아닌 구간에는 upper가 있어야 합니다")
    uppers = [b.upper for b in body]
    for i in range(1, len(uppers)):
        if not uppers[i - 1] < uppers[i]:
            raise FitRulesError(
                f"{where}[{i}].upper: 구간은 upper 오름차순이어야 합니다 "
                f"({uppers[i - 1]} 다음에 {uppers[i]})"
            )
    labels = [b.label for b in bands]
    duplicated = sorted({lb.value for lb in labels if labels.count(lb) > 1})
    if duplicated:
        raise FitRulesError(f"{where}: 같은 라벨이 두 구간에 있습니다: {', '.join(duplicated)}")


def _mapping(data: Any, where: str, *, required: set[str], allowed: set[str]) -> Mapping:
    if not isinstance(data, Mapping):
        raise FitRulesError(f"{where}: 키-값 묶음이어야 합니다")
    keys = set(data)
    missing = sorted(required - keys)
    if missing:
        raise FitRulesError(f"{where}: 필요한 키가 없습니다: {', '.join(missing)}")
    unknown = sorted(str(k) for k in keys - allowed)
    if unknown:
        raise FitRulesError(f"{where}: 모르는 키가 있습니다: {', '.join(unknown)}")
    return data


def _text(value: Any, where: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise FitRulesError(f"{where}: 비어 있지 않은 문자열이어야 합니다")
    return value


def _decimal_or_none(value: Any, where: str) -> Decimal | None:
    """구간 경계(cm). 숫자만 받는다. float는 적힌 자릿수 그대로(repr) 10진수로 바꾼다."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise FitRulesError(f"{where}: 숫자 또는 null이어야 합니다")
    if isinstance(value, float) and not math.isfinite(value):
        raise FitRulesError(f"{where}: 유한한 숫자여야 합니다")
    return Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
