"""견본집 폴더를 다시 읽어 검사한다 (#4, C3). 저장소 `.venv`에서 GarmentCode 없이 돈다.

    python -m tools.check_sample_catalog data/samples/tee_gc_v1 [--min-samples 10]

검사 항목
- 견본 수가 `--min-samples`(기본 10) 이상이다.
- `catalog.json`의 각 항목이 견본 검색 형식(`SampleCandidate`, 다른 키 없음)이다.
- ID 형식(`tee_gc_{style}_{두 자리 번호}`)이 맞고 겹치지 않는다.
- 네 치수(가슴·어깨·소매·기장)가 모두 있고 단위 범위(`UNIT_RANGES_CM`) 안이다. 허리는 null이다.
- 가슴·소매·기장 잔여 오차(잰 값 − 목표)가 `PATTERN_TOLERANCE_CM`(1.0 cm) 이하다.
- `style`이 가슴 구간(catalog.json의 `style_bands`, 생성용 몸 가슴 기준)과 ID의 style과 맞는다.
- 견본 폴더에 design.yaml·specification.json·sample.json이 모두 있고, 목록에 없는 견본 폴더가 없다.
- specification.json을 다시 읽어 정규형 sha256과 치수를 다시 계산하면 sample.json 기록과 같다.
- catalog.json과 각 sample.json의 치수가 같다.

실패하면 틀린 곳을 모두 출력하고 종료 코드 1로 끝난다. 폴더·catalog.json을 읽을 수 없으면 2.
"""

import argparse
import json
import math
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from fitting.catalog.sample_plan import (
    TARGET_FIELDS,
    SamplePlanError,
    check_sample_id,
    parse_style_bands,
    style_for_chest,
)
from fitting.catalog.search import SampleCandidate
from tools.console import use_utf8_output

EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_UNREADABLE = 2

MIN_SAMPLES = 10  # 3주 계획 C3 완료 조건: 견본 10개 이상

# 패턴 잔여 오차 허용치(cm). decisions.md "1주차 기준 [제안]" 5절(명세서 6장 초기 목표).
PATTERN_TOLERANCE_CM = 1.0

# 견본 치수의 단위 범위(cm, 경계 포함). AI Hub 71501 변환 의류를 받는 범위와 같다
# (decisions.md 2026-11-11, fitting/adapters/aihub/tables.py의 MEASUREMENT_RANGES_CM.
# 기장은 앞길이 범위).
# 견본은 이 의류들과 비교되므로 범위 밖이면 mm 단위·단면 너비·다른 필드가 들어간 것으로 본다.
UNIT_RANGES_CM = {
    "chest_girth_cm": (60.0, 200.0),
    "shoulder_width_cm": (25.0, 80.0),
    "sleeve_length_cm": (5.0, 45.0),
    "body_length_cm": (35.0, 100.0),
}
REQUIRED_FIELDS = tuple(UNIT_RANGES_CM)

# specification.json을 다시 잰 값과 기록(소수 셋째 자리 반올림)의 허용 차이(cm).
# 반올림(0.0005)과 numpy·svgpathtools 버전 차이(GarmentCode용 3.9 환경 ↔ .venv)를 담는 값이다.
RECOMPUTE_TOLERANCE_CM = 0.01
# 잔여 오차 기록과 (잰 값 − 목표)의 허용 차이(cm).
# 둘 다 소수 셋째 자리 반올림이라 0.001까지 다를 수 있다.
RESIDUAL_RECORD_TOLERANCE_CM = 0.0015

SAMPLE_FILES = ("design.yaml", "specification.json", "sample.json")


class CatalogUnreadable(Exception):
    pass


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        raise CatalogUnreadable(f"{path}: 읽을 수 없습니다 ({e.strerror})") from e
    except ValueError as e:
        raise CatalogUnreadable(f"{path}: JSON 형식이 아닙니다 ({e})") from e


def _num(v):
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def _check_measurements(sid, m, problems):
    for f in REQUIRED_FIELDS:
        v = m.get(f)
        if v is None:
            problems.append(f"{sid}: {f} 값이 없습니다")
            continue
        lo, hi = UNIT_RANGES_CM[f]
        if not _num(v) or not lo <= v <= hi:
            problems.append(f"{sid}: {f} = {v}가 단위 범위 {lo}~{hi} cm 밖입니다")
    if m.get("waist_girth_cm") is not None:
        problems.append(f"{sid}: 견본 허리는 측정 정의가 없어 null이어야 합니다")


def _check_sample_dir(root, sid, item_measurements, catalog, bands, problems):
    """견본 폴더 하나. 문제를 problems에 더한다."""
    d = root / sid
    missing = [n for n in SAMPLE_FILES if not (d / n).is_file()]
    if missing:
        problems.append(f"{sid}: 파일이 없습니다: {', '.join(missing)}")
        if "sample.json" in missing:
            return
    try:
        sample = _read_json(d / "sample.json")
    except CatalogUnreadable as e:
        problems.append(str(e))
        return
    if sample.get("sample_id") != sid:
        problems.append(f"{sid}: sample.json의 sample_id가 {sample.get('sample_id')!r}입니다")
    measured = sample.get("measurements_cm") or {}
    if measured != item_measurements:
        problems.append(
            f"{sid}: catalog.json과 sample.json의 치수가 다릅니다 "
            f"({item_measurements} != {measured})"
        )
    style = sample.get("style")
    problems.extend(check_sample_id(sid, style))
    chest = measured.get("chest_girth_cm")
    if bands is not None and _num(chest):
        got = style_for_chest(chest, catalog["generation_body_chest_cm"], bands)
        if got != style:
            problems.append(
                f"{sid}: 가슴 {chest} cm의 style 구간은 {got}인데 style이 {style}입니다"
            )

    targets = sample.get("targets_cm") or {}
    residuals = sample.get("residuals_cm") or {}
    for f in TARGET_FIELDS:
        t, m, r = targets.get(f), measured.get(f), residuals.get(f)
        if not (_num(t) and _num(m) and _num(r)):
            problems.append(f"{sid}: {f} 목표·치수·잔여 오차가 모두 있어야 합니다")
            continue
        if abs(r) > PATTERN_TOLERANCE_CM:
            problems.append(
                f"{sid}: {f} 잔여 오차 {r:+.3f} cm가 허용 오차 {PATTERN_TOLERANCE_CM} cm를 넘습니다"
            )
        if abs((m - t) - r) > RESIDUAL_RECORD_TOLERANCE_CM:
            problems.append(f"{sid}: {f} 잔여 오차 기록 {r}가 치수 − 목표({m - t:.3f})와 다릅니다")

    if (d / "design.yaml").is_file():
        try:
            design = yaml.safe_load((d / "design.yaml").read_text(encoding="utf-8"))
            if not isinstance(design, dict) or "design" not in design:
                problems.append(f"{sid}: design.yaml에 design 키가 없습니다")
        except yaml.YAMLError as e:
            problems.append(f"{sid}: design.yaml을 읽을 수 없습니다 ({e})")
    if (d / "specification.json").is_file():
        _check_specification(sid, d / "specification.json", sample, measured, problems)


def _check_specification(sid, path, sample, measured, problems):
    from fitting.adapters.garmentcode import pattern_measure as pm

    try:
        spec = _read_json(path)
        m, _ = pm.measure(spec)
        sha = pm.canonical_sha256(spec)
    except (CatalogUnreadable, pm.PatternMeasureError, KeyError, TypeError, ValueError) as e:
        problems.append(f"{sid}: specification.json을 잴 수 없습니다 ({e})")
        return
    if sha != sample.get("pattern_sha256"):
        problems.append(
            f"{sid}: 패턴 해시가 기록과 다릅니다 (다시 계산 {sha[:12]}…, "
            f"기록 {str(sample.get('pattern_sha256'))[:12]}…)"
        )
    for f in REQUIRED_FIELDS:
        rec = measured.get(f)
        if _num(rec) and abs(m[f] - rec) > RECOMPUTE_TOLERANCE_CM:
            problems.append(f"{sid}: {f}를 다시 재면 {m[f]:.3f} cm인데 기록은 {rec} cm입니다")


def check_catalog(root, min_samples=MIN_SAMPLES):
    """견본집 폴더를 검사해 (견본 수, 문제 목록)을 돌려준다.

    catalog.json을 못 읽으면 CatalogUnreadable을 낸다.
    """
    root = Path(root)
    if not root.is_dir():
        raise CatalogUnreadable(f"{root}: 폴더가 없습니다")
    catalog = _read_json(root / "catalog.json")
    problems = []
    if not isinstance(catalog, dict) or not isinstance(catalog.get("samples"), list):
        raise CatalogUnreadable(f'{root / "catalog.json"}: "samples" 목록이 없습니다')
    for k in ("generation_body_chest_cm", "style_bands"):
        if k not in catalog:
            problems.append(f"catalog.json: {k} 키가 없습니다")
    bands = None
    if "style_bands" in catalog:
        try:
            bands = parse_style_bands(catalog["style_bands"])
        except SamplePlanError as e:
            problems.extend(f"catalog.json: {p}" for p in e.problems)
    if "generation_body_chest_cm" in catalog and not _num(catalog["generation_body_chest_cm"]):
        problems.append("catalog.json: generation_body_chest_cm가 숫자가 아닙니다")
        bands = None
    if "generation_body_chest_cm" not in catalog:
        bands = None

    items = catalog["samples"]
    if len(items) < min_samples:
        problems.append(f"견본이 {len(items)}개로 {min_samples}개보다 적습니다")
    ids = []
    for i, item in enumerate(items):
        try:
            cand = SampleCandidate.model_validate(item)
        except ValidationError as e:
            msg = "; ".join(f"{'.'.join(str(p) for p in x['loc'])}: {x['msg']}" for x in e.errors())
            problems.append(f"catalog.json samples[{i}]: 견본 검색 형식이 아닙니다 ({msg})")
            continue
        sid = cand.sample_id
        ids.append(sid)
        m = cand.measurements.model_dump()
        _check_measurements(sid, m, problems)
        _check_sample_dir(root, sid, item.get("measurements"), catalog, bands, problems)
    dup = sorted({x for x in ids if ids.count(x) > 1})
    if dup:
        problems.append(f"겹치는 ID: {', '.join(dup)}")
    extra = sorted(p.name for p in root.iterdir() if p.is_dir() and p.name not in ids)
    if extra:
        problems.append(f"catalog.json에 없는 견본 폴더: {', '.join(extra)}")
    return len(items), problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("catalog_dir", type=Path, help="견본집 폴더 (catalog.json이 있는 곳)")
    ap.add_argument("--min-samples", type=int, default=MIN_SAMPLES)
    a = ap.parse_args(argv)
    try:
        n, problems = check_catalog(a.catalog_dir, a.min_samples)
    except CatalogUnreadable as e:
        print(f"오류: {e}", file=sys.stderr)
        return EXIT_UNREADABLE
    if problems:
        print(f"견본집 검사 실패: {a.catalog_dir} (견본 {n}개, 문제 {len(problems)}개)")
        for p in problems:
            print(f"  - {p}")
        return EXIT_PROBLEMS
    print(f"견본집 검사 통과: {a.catalog_dir} (견본 {n}개)")
    return EXIT_OK


if __name__ == "__main__":
    use_utf8_output()
    sys.exit(main())
