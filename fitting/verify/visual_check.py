"""실사화 결과의 색·실루엣 검증 판정 (D6, #11). numpy만 쓰고 외부 SDK·어댑터를 import하지 않는다.

기준은 명세서 7장 "실사화와 대표 프레임 검증"([추천안 R08])을 따른다. 수치는 설정 파일
(`config/validate/visual_check_v1.json`)에서 읽고 여기에 두지 않는다.

- 프레임: 실사 영상 길이의 0, 10, …, 100 % 위치 11프레임. 대응 렌더 프레임은 시각으로 찾는다
  (`fitting.verify.frame_match`, `time`). 두 영상 길이 차이가 0.5초를 넘으면 검증할 수 없다.
- 프레임마다 (렌더 옷 마스크 R, 실사 옷 마스크 S, 같은 크기)
  - 면적 비율 |S| / |R|이 0.5~1.5 밖이면 그 프레임은 측정 불가
  - 실루엣 겹침: IoU = |R ∩ S| / |R ∪ S| ≥ 0.85
  - 가슴·밑단 폭: R의 상자 위에서 45 %, 90 % 높이, 주변 5행의 가로폭 중앙값. 원본 대비 변화 ≤ 5 %
  - 대표색: 두 마스크를 안쪽으로 5 px 줄인 공통 영역의 Lab 중앙값끼리 CIEDE2000 ≤ 8.
    공통 영역이 R의 20 % 미만이면 색은 측정 불가
- 영상: 11프레임 중 9프레임 이상이 모든 기준을 통과하면 PASS. 측정 불가 프레임은 통과로 세지 않는다.
  - 측정된 위반만으로 9프레임을 채울 수 없게 되면(실패 프레임 ≥ 11 − 9 + 1) FAIL
  - 그 밖에 통과 프레임이 모자라면(측정 불가 때문에) UNVERIFIABLE
- 대표 프레임: PASS면 통과 프레임 중 시간상 가운데(50 %)에 가장 가까운 프레임, 같으면 이른 프레임.
  PASS가 아니면 가운데 프레임을 사람이 볼 수 있게 남긴다(통과 프레임이 아님을 기록).
- 중대한 변형(신체 부위 추가, 소매·넥라인 소실 등)은 자동으로 찾지 않는다. 명세서대로 PASS에도
  사람 확인이 필요하다는 표시(`human_review_required`)를 기록에 남긴다.

판정에는 기록에 남기는 반올림 값(IoU·폭 변화 소수 셋째 자리, 색 차이 둘째 자리)을 쓴다. 화면에
보이는 숫자와 판정이 어긋나지 않게 하기 위해서다.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

CHECK_VERSION = "visual_check_v1"

PASS, FAIL, UNVERIFIABLE = "PASS", "FAIL", "UNVERIFIABLE"

# 프레임 판정 사유 코드 → 기록용 설명
FAIL_REASONS = {
    "silhouette_iou": "실루엣 겹침이 기준보다 낮음",
    "chest_width": "가슴 폭 변화가 기준을 넘음",
    "hem_width": "밑단 폭 변화가 기준을 넘음",
    "color_delta_e": "대표색 차이가 기준을 넘음",
}
UNVERIFIABLE_REASONS = {
    "no_garment": "실사 프레임에서 옷 영역을 찾지 못함",
    "area_ratio": "실사 옷 면적이 원본의 허용 배율 밖",
    "width_rows": "가슴·밑단 높이에서 폭을 잴 수 없음",
    "color_area": "색 비교 공통 영역이 너무 작음",
}

_EPS = 1e-9


def _round_half_up(value: float) -> int:
    return math.floor(value + 0.5 + _EPS)


# ---------------------------------------------------------------------------
# 기준
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VisualCriteria:
    """색·실루엣 검증 기준. 값은 설정 파일에서 읽는다."""

    sample_fractions: tuple[float, ...]  # 실사 영상 길이 비율(0~1)
    max_duration_gap_s: float  # 두 영상 길이 차이 상한
    max_aspect_gap: float  # 두 영상 화면비 차이 상한(넘으면 늘리지 않고는 겹칠 수 없다)
    silhouette_iou_min: float
    width_change_max: float  # 가슴·밑단 폭 변화 비율 상한(0.05 = 5 %)
    color_delta_e_max: float  # CIEDE2000 상한
    min_frames_passed: int
    chest_height: float  # 렌더 옷 상자 위에서부터 비율
    hem_height: float
    width_rows: int  # 폭을 재는 주변 행 수(홀수)
    area_ratio_min: float
    area_ratio_max: float
    color_erode_px: int  # 색 비교 전에 두 마스크를 안쪽으로 줄이는 픽셀
    color_min_common_ratio: float  # 색 비교 공통 영역 / 렌더 마스크 하한
    source: str = ""  # 기준을 읽은 설정 이름과 버전(기록용)

    @classmethod
    def from_config(cls, config: Mapping[str, Any], source: str = "") -> VisualCriteria:
        """설정 dict(`config/validate/visual_check_v*.json` 전체)에서 읽는다.

        Raises:
            ValueError: 필요한 값이 없거나 범위를 벗어났다.
        """
        blocks = {name: config.get(name) for name in ("sampling", "thresholds", "measurement")}
        missing_blocks = [name for name, block in blocks.items() if not isinstance(block, Mapping)]
        if missing_blocks:
            raise ValueError(f"검증 설정에 블록이 없습니다: {', '.join(missing_blocks)}")
        sampling, thresholds, measurement = (blocks[n] for n in blocks)
        keys = {
            "sampling": ("fractions", "max_duration_gap_s"),
            "thresholds": (
                "silhouette_iou_min",
                "width_change_max",
                "color_delta_e_max",
                "min_frames_passed",
            ),
            "measurement": (
                "max_aspect_gap",
                "chest_height",
                "hem_height",
                "width_rows",
                "area_ratio_min",
                "area_ratio_max",
                "color_erode_px",
                "color_min_common_ratio",
            ),
        }
        missing = [f"{b}.{k}" for b, names in keys.items() for k in names if k not in blocks[b]]
        if missing:
            raise ValueError(f"검증 설정에 기준값이 없습니다: {', '.join(missing)}")
        criteria = cls(
            sample_fractions=tuple(float(f) for f in sampling["fractions"]),
            max_duration_gap_s=float(sampling["max_duration_gap_s"]),
            max_aspect_gap=float(measurement["max_aspect_gap"]),
            silhouette_iou_min=float(thresholds["silhouette_iou_min"]),
            width_change_max=float(thresholds["width_change_max"]),
            color_delta_e_max=float(thresholds["color_delta_e_max"]),
            min_frames_passed=int(thresholds["min_frames_passed"]),
            chest_height=float(measurement["chest_height"]),
            hem_height=float(measurement["hem_height"]),
            width_rows=int(measurement["width_rows"]),
            area_ratio_min=float(measurement["area_ratio_min"]),
            area_ratio_max=float(measurement["area_ratio_max"]),
            color_erode_px=int(measurement["color_erode_px"]),
            color_min_common_ratio=float(measurement["color_min_common_ratio"]),
            source=source,
        )
        criteria._check()
        return criteria

    def _check(self) -> None:
        problems = []
        fractions = self.sample_fractions
        if not fractions or any(not 0.0 <= f <= 1.0 for f in fractions):
            problems.append("sampling.fractions는 0~1 사이 값이 하나 이상 있어야 합니다")
        elif list(fractions) != sorted(set(fractions)):
            problems.append("sampling.fractions는 겹치지 않게 오름차순이어야 합니다")
        if not 1 <= self.min_frames_passed <= len(fractions):
            problems.append("thresholds.min_frames_passed는 1 이상, 프레임 수 이하여야 합니다")
        if not 0.0 < self.silhouette_iou_min <= 1.0:
            problems.append("thresholds.silhouette_iou_min은 0보다 크고 1 이하여야 합니다")
        if self.width_change_max < 0 or self.color_delta_e_max < 0:
            problems.append("폭 변화·색 차이 상한은 0 이상이어야 합니다")
        if not 0.0 <= self.chest_height < self.hem_height <= 1.0:
            problems.append("measurement.chest_height < hem_height, 둘 다 0~1이어야 합니다")
        if self.width_rows < 1 or self.width_rows % 2 == 0:
            problems.append("measurement.width_rows는 1 이상 홀수여야 합니다")
        if not 0.0 < self.area_ratio_min < 1.0 < self.area_ratio_max:
            problems.append("measurement.area_ratio_min < 1 < area_ratio_max여야 합니다")
        if self.color_erode_px < 0 or not 0.0 <= self.color_min_common_ratio <= 1.0:
            problems.append("색 비교 설정(color_erode_px, color_min_common_ratio)이 범위 밖입니다")
        if self.max_duration_gap_s < 0 or self.max_aspect_gap < 0:
            problems.append("길이·화면비 차이 상한은 0 이상이어야 합니다")
        if problems:
            raise ValueError("; ".join(problems))

    def to_record(self) -> dict[str, Any]:
        return {
            "sample_fractions": list(self.sample_fractions),
            "max_duration_gap_s": self.max_duration_gap_s,
            "max_aspect_gap": self.max_aspect_gap,
            "silhouette_iou_min": self.silhouette_iou_min,
            "width_change_max": self.width_change_max,
            "color_delta_e_max": self.color_delta_e_max,
            "min_frames_passed": self.min_frames_passed,
            "chest_height": self.chest_height,
            "hem_height": self.hem_height,
            "width_rows": self.width_rows,
            "area_ratio_min": self.area_ratio_min,
            "area_ratio_max": self.area_ratio_max,
            "color_erode_px": self.color_erode_px,
            "color_min_common_ratio": self.color_min_common_ratio,
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# 색
# ---------------------------------------------------------------------------

# sRGB(D65) → XYZ 행렬과 D65 기준 백색 (IEC 61966-2-1, CIE 15)
_RGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ]
)
_WHITE_D65 = np.array([0.95047, 1.0, 1.08883])


def _linearize(c: np.ndarray) -> np.ndarray:
    """sRGB 감마를 푼다(c는 0~1)."""
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


# 8비트 영상은 256칸 표로 감마를 푼다(같은 식, 프레임마다 거듭제곱을 하지 않으려고)
_LINEAR_U8 = _linearize(np.arange(256, dtype=np.float64) / 255.0)


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB 값(0~255, 마지막 축이 R·G·B)을 CIE L*a*b*(D65, 2°)로 바꾼다."""
    array = np.asarray(rgb)
    if array.dtype == np.uint8:
        linear = _LINEAR_U8[array]
    else:
        linear = _linearize(array.astype(np.float64) / 255.0)
    shape = linear.shape
    xyz = (linear.reshape(-1, 3) @ (_RGB_TO_XYZ.T / _WHITE_D65)).reshape(shape)
    epsilon, kappa = 216 / 24389, 24389 / 27
    f = np.cbrt(xyz)
    small = xyz <= epsilon
    f[small] = (kappa * xyz[small] + 16) / 116
    lightness = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([lightness, a, b], axis=-1)


def delta_e_2000(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """CIEDE2000 색 차이(kL = kC = kH = 1). Sharma, Wu, Dalal(2005) 식 그대로다."""
    l1, a1, b1 = np.moveaxis(np.asarray(lab1, dtype=np.float64), -1, 0)
    l2, a2, b2 = np.moveaxis(np.asarray(lab2, dtype=np.float64), -1, 0)
    c_bar = (np.hypot(a1, b1) + np.hypot(a2, b2)) / 2
    g = 0.5 * (1 - np.sqrt(c_bar**7 / (c_bar**7 + 25.0**7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360
    chroma_zero = (c1p * c2p) == 0

    dl = l2 - l1
    dc = c2p - c1p
    dh = h2p - h1p
    dh = np.where(dh > 180, dh - 360, np.where(dh < -180, dh + 360, dh))
    dh = np.where(chroma_zero, 0.0, dh)
    dh_big = 2 * np.sqrt(c1p * c2p) * np.sin(np.radians(dh / 2))

    l_bar = (l1 + l2) / 2
    cp_bar = (c1p + c2p) / 2
    h_sum = h1p + h2p
    h_bar = np.where(
        np.abs(h1p - h2p) <= 180,
        h_sum / 2,
        np.where(h_sum < 360, (h_sum + 360) / 2, (h_sum - 360) / 2),
    )
    h_bar = np.where(chroma_zero, h_sum, h_bar)
    t = (
        1
        - 0.17 * np.cos(np.radians(h_bar - 30))
        + 0.24 * np.cos(np.radians(2 * h_bar))
        + 0.32 * np.cos(np.radians(3 * h_bar + 6))
        - 0.20 * np.cos(np.radians(4 * h_bar - 63))
    )
    d_theta = 30 * np.exp(-(((h_bar - 275) / 25) ** 2))
    r_c = 2 * np.sqrt(cp_bar**7 / (cp_bar**7 + 25.0**7))
    s_l = 1 + 0.015 * (l_bar - 50) ** 2 / np.sqrt(20 + (l_bar - 50) ** 2)
    s_c = 1 + 0.045 * cp_bar
    s_h = 1 + 0.015 * cp_bar * t
    r_t = -np.sin(np.radians(2 * d_theta)) * r_c
    tl, tc, th = dl / s_l, dc / s_c, dh_big / s_h
    return np.sqrt(tl**2 + tc**2 + th**2 + r_t * tc * th)


def median_lab(lab: np.ndarray, region: np.ndarray) -> tuple[float, float, float]:
    """영역 안 Lab 값의 채널별 중앙값."""
    values = lab[np.asarray(region, dtype=bool)]
    if values.size == 0:
        raise ValueError("빈 영역의 대표색은 구할 수 없습니다")
    median = np.median(values, axis=0)
    return float(median[0]), float(median[1]), float(median[2])


# ---------------------------------------------------------------------------
# 실루엣
# ---------------------------------------------------------------------------


def box_height(mask: np.ndarray) -> int:
    """마스크 상자의 높이(행 수). 비었으면 0."""
    rows = np.flatnonzero(np.asarray(mask, dtype=bool).any(axis=1))
    return int(rows[-1] - rows[0] + 1) if rows.size else 0


def mask_iou(a: np.ndarray, b: np.ndarray) -> float | None:
    """두 마스크의 IoU. 둘 다 비었으면 None."""
    a, b = np.asarray(a, dtype=bool), np.asarray(b, dtype=bool)
    if a.shape != b.shape:
        raise ValueError(f"마스크 크기가 다릅니다: {a.shape} / {b.shape}")
    union = int(np.logical_or(a, b).sum())
    if union == 0:
        return None
    return int(np.logical_and(a, b).sum()) / union


@dataclass(frozen=True)
class WidthRows:
    """렌더 옷 마스크 상자에서 정한 가슴·밑단 높이(행 번호)."""

    top: int
    bottom: int
    chest: int
    hem: int

    def to_dict(self) -> dict[str, int]:
        return {"top": self.top, "bottom": self.bottom, "chest": self.chest, "hem": self.hem}


def width_rows(render_mask: np.ndarray, chest_height: float, hem_height: float) -> WidthRows:
    """렌더 옷 마스크 상자 위에서 `chest_height`·`hem_height` 비율 높이의 행.

    Raises:
        ValueError: 마스크가 비었다.
    """
    rows = np.flatnonzero(np.asarray(render_mask, dtype=bool).any(axis=1))
    if rows.size == 0:
        raise ValueError("렌더 옷 마스크가 비었습니다")
    top, bottom = int(rows[0]), int(rows[-1])
    span = bottom - top
    return WidthRows(
        top=top,
        bottom=bottom,
        chest=top + _round_half_up(chest_height * span),
        hem=top + _round_half_up(hem_height * span),
    )


def band_width(mask: np.ndarray, row: int, rows: int) -> float | None:
    """`row` 주변 `rows`행에서 행마다 잰 가로폭(맨 왼쪽~맨 오른쪽 픽셀)의 중앙값.

    옷이 없는 행은 폭 0으로 센다. 잴 행이 화면 밖이면 None.
    """
    mask = np.asarray(mask, dtype=bool)
    half = rows // 2
    start, stop = max(row - half, 0), min(row + half + 1, mask.shape[0])
    if start >= stop:
        return None
    widths = []
    for r in range(start, stop):
        cols = np.flatnonzero(mask[r])
        widths.append(int(cols[-1] - cols[0] + 1) if cols.size else 0)
    return float(statistics.median(widths))


def width_change(render_width: float | None, real_width: float | None) -> float | None:
    """원본 대비 폭 변화 비율(실사 / 렌더 − 1). 렌더 폭이 0이거나 모르면 None."""
    if render_width is None or real_width is None or render_width <= 0:
        return None
    return real_width / render_width - 1.0


def sample_indices(frame_count: int, fractions: Sequence[float]) -> list[int]:
    """영상 길이 비율 위치의 프레임 번호(0.5는 올림)."""
    if frame_count <= 0:
        raise ValueError(f"프레임이 없는 영상입니다: {frame_count}")
    last = frame_count - 1
    return [min(max(_round_half_up(f * last), 0), last) for f in fractions]


# ---------------------------------------------------------------------------
# 판정
# ---------------------------------------------------------------------------


def _r(value: float | None, digits: int) -> float | None:
    return None if value is None else round(float(value), digits)


@dataclass(frozen=True)
class FrameMeasure:
    """프레임 하나의 측정값. 값은 기록 자릿수로 반올림해 둔다(판정도 이 값으로 한다)."""

    fraction: float
    real_index: int
    real_time_s: float
    render_index: int
    render_time_s: float
    render_area: int
    real_area: int  # 0이면 실사 옷 영역을 찾지 못했다
    iou: float | None
    chest_change: float | None
    hem_change: float | None
    delta_e: float | None
    common_ratio: float | None  # 색 비교 공통 영역 / 렌더 마스크
    render_lab: tuple[float, float, float] | None = None
    real_lab: tuple[float, float, float] | None = None
    # 옷 상자 높이 변화(실사 / 렌더 − 1). 명세서 기준에 없어 판정에 쓰지 않고 기록만 한다([D 제안])
    length_change: float | None = None

    @classmethod
    def build(cls, **values: Any) -> FrameMeasure:
        for key in ("iou", "chest_change", "hem_change", "common_ratio", "length_change"):
            values[key] = _r(values.get(key), 3)
        values["delta_e"] = _r(values.get("delta_e"), 2)
        for key in ("render_lab", "real_lab"):
            lab = values.get(key)
            values[key] = None if lab is None else tuple(round(float(v), 2) for v in lab)
        return cls(**values)

    @property
    def area_ratio(self) -> float | None:
        if self.render_area <= 0:
            return None
        return round(self.real_area / self.render_area, 3)

    @property
    def width_change_abs(self) -> float | None:
        known = [abs(v) for v in (self.chest_change, self.hem_change) if v is not None]
        return round(max(known), 3) if known else None

    def to_record(self) -> dict[str, Any]:
        return {
            "fraction": self.fraction,
            "real_index": self.real_index,
            "real_time_s": self.real_time_s,
            "render_index": self.render_index,
            "render_time_s": self.render_time_s,
            "render_area": self.render_area,
            "real_area": self.real_area,
            "area_ratio": self.area_ratio,
            "silhouette_iou": self.iou,
            "chest_width_change": self.chest_change,
            "hem_width_change": self.hem_change,
            "color_delta_e": self.delta_e,
            "color_common_ratio": self.common_ratio,
            "length_change": self.length_change,
            "render_lab": None if self.render_lab is None else list(self.render_lab),
            "real_lab": None if self.real_lab is None else list(self.real_lab),
        }


@dataclass(frozen=True)
class FrameResult:
    measure: FrameMeasure
    status: str
    fails: tuple[str, ...] = ()  # FAIL_REASONS 코드
    unverifiable: tuple[str, ...] = ()  # UNVERIFIABLE_REASONS 코드

    def to_record(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "fails": list(self.fails),
            "unverifiable": list(self.unverifiable),
            **self.measure.to_record(),
        }


def judge_frame(measure: FrameMeasure, criteria: VisualCriteria) -> FrameResult:
    """프레임 하나를 기준과 비교한다. 면적이 허용 배율 밖이면 다른 값과 상관없이 측정 불가다."""
    if measure.real_area <= 0:
        return FrameResult(measure, UNVERIFIABLE, unverifiable=("no_garment",))
    ratio = measure.area_ratio
    if ratio is None or not criteria.area_ratio_min <= ratio <= criteria.area_ratio_max:
        return FrameResult(measure, UNVERIFIABLE, unverifiable=("area_ratio",))

    fails: list[str] = []
    unknown: list[str] = []
    if measure.iou is None or measure.iou < criteria.silhouette_iou_min:
        fails.append("silhouette_iou")
    for code, change in (("chest_width", measure.chest_change), ("hem_width", measure.hem_change)):
        if change is None:
            if "width_rows" not in unknown:
                unknown.append("width_rows")
        elif abs(change) > criteria.width_change_max:
            fails.append(code)
    if measure.delta_e is None:
        unknown.append("color_area")
    elif measure.delta_e > criteria.color_delta_e_max:
        fails.append("color_delta_e")

    if fails:
        status = FAIL
    elif unknown:
        status = UNVERIFIABLE
    else:
        status = PASS
    return FrameResult(measure, status, fails=tuple(fails), unverifiable=tuple(unknown))


def _median(values: Sequence[float | None], digits: int) -> float | None:
    known = [v for v in values if v is not None]
    return round(float(statistics.median(known)), digits) if known else None


@dataclass(frozen=True)
class VisualCheckResult:
    status: str  # PASS · FAIL · UNVERIFIABLE
    frames: tuple[FrameResult, ...]
    criteria: VisualCriteria
    representative: int | None  # frames 안의 위치. 프레임이 없으면 None
    blockers: tuple[str, ...] = ()  # 프레임 비교 전에 검증할 수 없게 만든 이유(한국어)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == PASS

    @property
    def frames_passed(self) -> int:
        return sum(1 for f in self.frames if f.status == PASS)

    @property
    def frames_failed(self) -> int:
        return sum(1 for f in self.frames if f.status == FAIL)

    @property
    def frames_unverifiable(self) -> int:
        return sum(1 for f in self.frames if f.status == UNVERIFIABLE)

    @property
    def representative_frame(self) -> FrameResult | None:
        return None if self.representative is None else self.frames[self.representative]

    @property
    def human_review_required(self) -> bool:
        """명세서 7장: 중대한 변형을 자동으로 찾지 못하므로 PASS 후보도 사람 확인이 필요하다."""
        return self.passed

    def reasons(self) -> list[str]:
        """판정 이유(한국어, 기록·로그용)."""
        out = list(self.blockers)
        if not self.frames:
            return out
        need = self.criteria.min_frames_passed
        out.append(
            f"{len(self.frames)}프레임 중 통과 {self.frames_passed}, 실패 {self.frames_failed}, "
            f"측정 불가 {self.frames_unverifiable} (통과 {need}프레임 이상이어야 PASS)"
        )
        for codes, names in (("fails", FAIL_REASONS), ("unverifiable", UNVERIFIABLE_REASONS)):
            for code, text in names.items():
                count = sum(1 for f in self.frames if code in getattr(f, codes))
                if count:
                    out.append(f"{text}: {count}프레임")
        return out

    def metrics(self) -> dict[str, float | int | str]:
        """포트 지표. 숫자는 측정된 프레임 값의 중앙값이고 화면(`quality_metrics`)에 간다.

        `color_delta_e`·`silhouette_iou`는 포트 설명의 지표 키, `width_change_ratio`는 가슴·밑단 중
        큰 폭 변화 비율, `frames_passed`는 통과 프레임 수다. 측정하지 못한 값은 넣지 않는다.
        """
        # 옷 영역을 못 찾았거나 면적이 허용 배율 밖인 프레임은 명세서대로 측정 불가라 빼고 센다
        measures = [
            f.measure for f in self.frames if not {"no_garment", "area_ratio"} & set(f.unverifiable)
        ]
        values: dict[str, float | int | str] = {"quality_status": self.status}
        numbers = {
            "color_delta_e": _median([m.delta_e for m in measures], 2),
            "silhouette_iou": _median([m.iou for m in measures], 3),
            "width_change_ratio": _median([m.width_change_abs for m in measures], 3),
        }
        values.update({k: v for k, v in numbers.items() if v is not None})
        if self.frames:
            values["frames_passed"] = self.frames_passed
        return values

    def to_record(self) -> dict[str, Any]:
        rep = self.representative_frame
        return {
            "check_version": CHECK_VERSION,
            "status": self.status,
            "passed": self.passed,
            "reasons": self.reasons(),
            "blockers": list(self.blockers),
            "frames_checked": len(self.frames),
            "frames_passed": self.frames_passed,
            "frames_failed": self.frames_failed,
            "frames_unverifiable": self.frames_unverifiable,
            "representative": None
            if rep is None
            else {
                "position": self.representative,
                "real_index": rep.measure.real_index,
                "render_index": rep.measure.render_index,
                "fraction": rep.measure.fraction,
                "passed": rep.status == PASS,
            },
            "human_review_required": self.human_review_required,
            "deformation_check": "not_automated",
            "metrics": self.metrics(),
            "criteria": self.criteria.to_record(),
            "frames": [f.to_record() for f in self.frames],
            "details": self.details,
        }


def pick_representative(frames: Sequence[FrameResult], video_status: str) -> int | None:
    """대표 프레임 위치. PASS면 통과 프레임 중 50 %에 가장 가까운 것(같으면 이른 것),
    아니면 모든 프레임 중 50 %에 가장 가까운 것."""
    pool = [i for i, f in enumerate(frames) if video_status != PASS or f.status == PASS]
    if not pool:
        return None

    def distance(i: int) -> tuple[float, float]:
        fraction = frames[i].measure.fraction
        return (round(abs(fraction - 0.5), 9), fraction)

    return min(pool, key=distance)


def judge_video(
    measures: Sequence[FrameMeasure],
    criteria: VisualCriteria,
    *,
    blockers: Sequence[str] = (),
    details: Mapping[str, Any] | None = None,
) -> VisualCheckResult:
    """프레임 측정값으로 영상 전체를 판정한다.

    `blockers`(길이·화면비 차이, 마스크 문제 등)가 있으면 프레임 결과와 상관없이 UNVERIFIABLE이다.
    """
    frames = tuple(judge_frame(m, criteria) for m in measures)
    passed = sum(1 for f in frames if f.status == PASS)
    failed = sum(1 for f in frames if f.status == FAIL)
    need = criteria.min_frames_passed
    if blockers or not frames:
        status = UNVERIFIABLE
    elif passed >= need:
        status = PASS
    elif len(frames) - failed < need:
        status = FAIL
    else:
        status = UNVERIFIABLE
    blocker_list = tuple(blockers) if frames or blockers else ("비교할 프레임이 없습니다.",)
    return VisualCheckResult(
        status=status,
        frames=frames,
        criteria=criteria,
        representative=pick_representative(frames, status),
        blockers=blocker_list,
        details=dict(details or {}),
    )
