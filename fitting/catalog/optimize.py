"""의류별 견본 최적화 기록 (#6, C4). 순수 계산이다.

등록 의류 1벌 → 견본 검색(#5) → 고른 견본의 **복사본** 디자인을 의류 치수에 맞춤 → 다시 재기.
GarmentCode를 부르는 부분은 `fitting/adapters/garmentcode/prep_fit.py`(Python 3.9 하위
프로세스)이고, 이 모듈은 그 결과로 준비 성공·실패를 판정하고 준비 기록(`prep.json`)에 쓸 값을
만든다. 외부 도구·어댑터를 import하지 않는다.

판정 (docs/decisions.md "1주차 기준 [제안]" 5절 패턴 허용 오차)
- 맞추는 치수: 가슴둘레·소매·기장. 다시 잰 값 − 목표의 절댓값이 허용 오차(1.0 cm) 이하면 통과.
  허용 오차는 패턴 맞춤 설정(`tolerance_cm`)에서 읽고 늘리지 않는다.
- 의류에 기장이 없으면(AI Hub) 견본 기장을 그대로 두고 "기장 목표 없음"을 기록한다
  (판정하지 않음).
- 어깨는 맞추지 않고 판정하지 않는다. 의류 − 견본 어깨 차이만 기록한다.
- 패턴이 자기 교차하면 준비 실패다.

준비 실패는 3D 착장만 못 한다는 뜻이다. 의류 파일의 `status`는 바꾸지 않고, 핏 리포트는
패턴 없이도 된다.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
PREPARED_DIR_ENV = "FIT_PREPARED_DIR"
DEFAULT_PREPARED_DIR = REPO_ROOT / "data" / "prepared"

PREP_SCHEMA = "garment_prep_v0"
PREP_FILE = "prep.json"
DESIGN_FILE = "design.yaml"
SPEC_FILE = "specification.json"

READY = "READY"
FAILED = "FAILED"

# 맞추는 치수 (pattern_fit_v0의 fit_params와 같은 측정 항목)
FIT_FIELDS = ("chest_girth_cm", "sleeve_length_cm", "body_length_cm")
SHOULDER = "shoulder_width_cm"
MEASURE_FIELDS = ("chest_girth_cm", "shoulder_width_cm", "sleeve_length_cm", "body_length_cm")


class FailureReason:
    """준비 실패 이유 코드 (`prep.json`의 `failure_reason`)."""

    GARMENT_INVALID = "GARMENT_INVALID"  # 의류 파일을 계약으로 읽지 못함
    SAMPLE_SEARCH_FAILED = "SAMPLE_SEARCH_FAILED"  # 견본을 고르지 못함 (가슴·어깨 없음 등)
    FIT_ERROR = "FIT_ERROR"  # 맞춤·생성 하위 프로세스가 실패함
    OUT_OF_TOLERANCE = "OUT_OF_TOLERANCE"  # 가슴·소매·기장 중 잔여 오차가 허용 오차를 넘음
    SELF_INTERSECTING = "SELF_INTERSECTING"  # 맞춘 패턴이 자기 교차함


def prepared_dir(environ: Mapping[str, str] | None = None) -> Path:
    """준비 폴더: 환경변수 `FIT_PREPARED_DIR` > 저장소 `data/prepared/` (의류 폴더와 같은 방식)."""
    env = os.environ if environ is None else environ
    value = (env.get(PREPARED_DIR_ENV) or "").strip()
    return Path(value) if value else DEFAULT_PREPARED_DIR


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path | str) -> str:
    return sha256_bytes(Path(path).read_bytes())


def fit_targets(measurements: Mapping[str, Any]) -> dict[str, float | None]:
    """의류 실측 → 맞출 목표(가슴·소매·기장). 없는 값은 None(맞추지 않음, 0으로 채우지 않는다)."""
    return {
        f: (None if measurements.get(f) is None else float(measurements[f])) for f in FIT_FIELDS
    }


def _r3(x: float) -> float:
    return round(float(x), 3) + 0.0  # + 0.0: -0.0을 0.0으로


def residuals(
    targets: Mapping[str, float | None], measured: Mapping[str, float]
) -> dict[str, float | None]:
    """잔여 오차 = 다시 잰 값 − 목표 (cm, 소수 셋째 자리). 목표가 없으면 None."""
    return {
        f: (None if targets.get(f) is None else _r3(measured[f] - targets[f])) for f in FIT_FIELDS
    }


def judge(
    targets: Mapping[str, float | None],
    measured: Mapping[str, float],
    tolerance_cm: float,
    *,
    self_intersecting: bool = False,
) -> tuple[str, str | None, str | None]:
    """준비 성공·실패. 반환: (status, failure_reason, failure_detail).

    목표가 있는 가슴·소매·기장의 |다시 잰 값 − 목표|가 모두 `tolerance_cm` 이하(경계 포함)여야
    성공이다.
    비교는 반올림하지 않은 값으로 한다.
    """
    if not math.isfinite(tolerance_cm) or tolerance_cm <= 0:
        raise ValueError(f"tolerance_cm must be a positive number: {tolerance_cm}")
    over = []
    for f in FIT_FIELDS:
        t = targets.get(f)
        if t is None:
            continue
        err = float(measured[f]) - float(t)
        if not math.isfinite(err) or abs(err) > tolerance_cm:
            over.append(f"{f} {err:+.3f} cm")
    if over:
        return FAILED, FailureReason.OUT_OF_TOLERANCE, "허용 오차 밖: " + ", ".join(over)
    if self_intersecting:
        return FAILED, FailureReason.SELF_INTERSECTING, "맞춘 패턴이 자기 교차합니다"
    return READY, None, None


def shoulder_record(garment_cm: float | None, sample_cm: float | None) -> dict[str, Any]:
    """어깨: 맞추지 않고 판정하지 않는다. 차이 = 의류 − 견본(cm). 한쪽이 없으면 None."""
    diff = None if garment_cm is None or sample_cm is None else _r3(garment_cm - sample_cm)
    return {
        "judged": False,
        "garment_cm": garment_cm,
        "sample_cm": None if sample_cm is None else _r3(sample_cm),
        "diff_cm": diff,
    }


def notes_for(targets: Mapping[str, float | None]) -> list[str]:
    notes = []
    if targets.get("body_length_cm") is None:
        notes.append("기장 목표 없음: 견본 기장을 그대로 둠(판정 안 함)")
    if targets.get("sleeve_length_cm") is None:
        notes.append("소매 목표 없음: 견본 소매를 그대로 둠(판정 안 함)")
    if targets.get("chest_girth_cm") is None:
        notes.append("가슴 목표 없음")
    return notes


def inputs_key(inputs: Mapping[str, Any]) -> str:
    """건너뛰기 판단용 입력 해시. 의류·견본·설정 해시와 기록 형식이 같으면 같은 값이다."""
    return sha256_bytes(json.dumps(dict(inputs), sort_keys=True).encode("utf-8"))


def is_up_to_date(existing: Mapping[str, Any] | None, key: str) -> bool:
    """기존 준비 기록이 같은 입력으로 만들어졌는지. 파일이 빠졌으면 다시 만든다."""
    return bool(existing) and existing.get("inputs_key") == key


def read_prep(folder: Path) -> dict[str, Any] | None:
    """`{folder}/prep.json`. 없거나 읽을 수 없으면 None."""
    path = Path(folder) / PREP_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
