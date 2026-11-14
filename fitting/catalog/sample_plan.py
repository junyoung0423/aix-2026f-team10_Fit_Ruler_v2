"""견본집 생성 계획 (#4, C3). 견본 ID·style 구간·목표 치수 규칙을 검사한다.

순수 계산이다. GarmentCode·파일 형식을 모른다. 견본집 생성 명령(`tools/build_sample_catalog.py`,
GarmentCode용 Python 3.9)과 검사 명령(`tools/check_sample_catalog.py`, 저장소 `.venv`)이 함께 쓴다.
그래서 Python 3.9에서도 돌아야 하고 pydantic·`fitting.domain`을 import하지 않는다.

규칙은 팀 문서 `docs/decisions.md`의 견본집 결정(2026-11-14)을 따른다.
- ID: `tee_gc_{style}_{두 자리 번호}`. 번호는 가슴 오름차순, 그다음 기장 오름차순으로 01부터.
- style: 견본 가슴둘레 − 생성용 몸 가슴둘레(cm)가 어느 구간에 드는지로 정한다. 구간은 설정
  (`config/samples/*.yaml`의 `style_bands`)에서 읽는다.
- 목표 치수: 가슴둘레·기장·소매(cm). 어깨는 맞추지 않는다(GarmentCode가 어깨를 바꾸지 못함).
"""

from __future__ import annotations

import math
import re

# 견본 ID. style 부분은 아래 구간 설정의 style과 같아야 한다.
SAMPLE_ID_PATTERN = re.compile(r"^tee_gc_(?P<style>[a-z]+)_(?P<number>\d{2})$")

# 목표로 맞추는 치수(cm). 순서는 결과 파일의 순서다.
TARGET_FIELDS = ("chest_girth_cm", "body_length_cm", "sleeve_length_cm")

_BAND_KEYS = {"style", "lower", "upper", "include_lower", "include_upper"}
_SAMPLE_KEYS = {"id", "style"} | set(TARGET_FIELDS)
_PLAN_KEYS = {
    "name",
    "version",
    "pattern_fit_config",
    "generation_body",
    "generation_body_chest_cm",
    "style_bands",
    "samples",
}


class SamplePlanError(ValueError):
    """견본집 계획이 규칙에 맞지 않는다. problems에 틀린 곳이 모두 있다."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def parse_style_bands(data):
    """style 구간 목록을 검사해 그대로(복사본) 돌려준다. 틀리면 SamplePlanError."""
    problems = []
    if not isinstance(data, list) or not data:
        raise SamplePlanError(["style_bands: 비어 있지 않은 목록이어야 합니다"])
    bands = []
    for i, band in enumerate(data):
        where = f"style_bands[{i}]"
        if not isinstance(band, dict) or set(band) != _BAND_KEYS:
            problems.append(f"{where}: 키는 {sorted(_BAND_KEYS)}여야 합니다")
            continue
        if not isinstance(band["style"], str) or not re.fullmatch(r"[a-z]+", band["style"]):
            problems.append(f"{where}.style: 영어 소문자여야 합니다")
        for k in ("lower", "upper"):
            if band[k] is not None and not _is_number(band[k]):
                problems.append(f"{where}.{k}: 숫자나 null이어야 합니다")
        for k in ("include_lower", "include_upper"):
            if not isinstance(band[k], bool):
                problems.append(f"{where}.{k}: true/false여야 합니다")
        bands.append(dict(band))
    styles = [b.get("style") for b in bands]
    if len(set(styles)) != len(styles):
        problems.append("style_bands: style이 겹칩니다")
    if problems:
        raise SamplePlanError(problems)
    return bands


def _in_band(ease, band):
    lo, hi = band["lower"], band["upper"]
    if lo is not None and (ease < lo or (ease == lo and not band["include_lower"])):
        return False
    if hi is not None and (ease > hi or (ease == hi and not band["include_upper"])):
        return False
    return True


def style_for_chest(chest_girth_cm, body_chest_cm, bands):
    """견본 가슴둘레의 style. 어느 구간에도 들지 않거나 둘 이상이면 None."""
    ease = round(chest_girth_cm - body_chest_cm, 6)  # 뺄셈의 부동소수 꼬리로 경계가 흔들리지 않게
    hits = [b["style"] for b in bands if _in_band(ease, b)]
    return hits[0] if len(hits) == 1 else None


def check_sample_id(sample_id, style):
    """ID 형식과 ID 안의 style을 검사해 문제 목록을 돌려준다(없으면 빈 목록)."""
    m = SAMPLE_ID_PATTERN.match(sample_id) if isinstance(sample_id, str) else None
    if m is None:
        return [f"{sample_id!r}: ID 형식이 tee_gc_{{style}}_{{두 자리 번호}}가 아닙니다"]
    if m.group("style") != style:
        return [f"{sample_id}: ID의 style({m.group('style')})이 style 값({style})과 다릅니다"]
    return []


def expected_numbers(samples):
    """{ID: 기대 번호}. 가슴 오름차순, 그다음 기장 오름차순으로 1부터."""
    order = sorted(samples, key=lambda s: (s["chest_girth_cm"], s["body_length_cm"]))
    return {s["id"]: i + 1 for i, s in enumerate(order)}


def parse_sample_plan(data):
    """견본집 설정(yaml을 읽은 dict)을 검사해 돌려준다.

    틀린 곳이 있으면 모두 모아 SamplePlanError를 낸다.
    """
    if not isinstance(data, dict):
        raise SamplePlanError(["설정이 mapping이 아닙니다"])
    problems = []
    missing = _PLAN_KEYS - set(data)
    extra = set(data) - _PLAN_KEYS
    if missing:
        problems.append(f"빠진 키: {sorted(missing)}")
    if extra:
        problems.append(f"모르는 키: {sorted(extra)}")
    if problems:
        raise SamplePlanError(problems)
    for k in ("name", "version", "pattern_fit_config", "generation_body"):
        if not isinstance(data[k], str) or not data[k]:
            problems.append(f"{k}: 비어 있지 않은 문자열이어야 합니다")
    body_chest = data["generation_body_chest_cm"]
    if not _is_number(body_chest) or body_chest <= 0:
        problems.append("generation_body_chest_cm: 양수여야 합니다")
    try:
        bands = parse_style_bands(data["style_bands"])
    except SamplePlanError as e:
        problems.extend(e.problems)
        bands = None
    samples = data["samples"]
    if not isinstance(samples, list) or not samples:
        problems.append("samples: 비어 있지 않은 목록이어야 합니다")
        raise SamplePlanError(problems)
    good = []
    for i, s in enumerate(samples):
        where = f"samples[{i}]"
        if not isinstance(s, dict) or set(s) != _SAMPLE_KEYS:
            problems.append(f"{where}: 키는 {sorted(_SAMPLE_KEYS)}여야 합니다")
            continue
        bad = [f for f in TARGET_FIELDS if not _is_number(s[f]) or s[f] <= 0]
        if bad:
            problems.append(f"{where} {s['id']}: 양수 cm가 아닌 목표 {bad}")
            continue
        problems.extend(check_sample_id(s["id"], s["style"]))
        if bands is not None and _is_number(body_chest):
            got = style_for_chest(s["chest_girth_cm"], body_chest, bands)
            if got != s["style"]:
                problems.append(
                    f"{s['id']}: 가슴 {s['chest_girth_cm']} cm − {body_chest} cm의 style은 "
                    f"{got}인데 {s['style']}로 적혀 있습니다"
                )
        good.append(s)
    ids = [s["id"] for s in good]
    dup = sorted({x for x in ids if ids.count(x) > 1})
    if dup:
        problems.append(f"겹치는 ID: {dup}")
    else:
        for sid, n in expected_numbers(good).items():
            m = SAMPLE_ID_PATTERN.match(sid)
            if m and int(m.group("number")) != n:
                problems.append(f"{sid}: 번호는 가슴·기장 오름차순으로 {n:02d}이어야 합니다")
    keys = [(s["chest_girth_cm"], s["body_length_cm"]) for s in good]
    if len(set(keys)) != len(keys):
        problems.append("가슴·기장이 같은 견본이 있습니다")
    if problems:
        raise SamplePlanError(problems)
    out = dict(data)
    out["style_bands"] = bands
    out["samples"] = [dict(s) for s in samples]
    return out
