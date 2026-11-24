"""옷 마스크 형태 값 (C6, #11). numpy만 쓰고 외부 SDK·어댑터를 import하지 않는다.

렌더 쪽 옷 마스크(C5 `garment_mask.png`)와 실사 프레임에서 분할한 옷 마스크의 형태를 잰다.
**판정은 하지 않는다.** 값만 내고, 통과 기준과 판정은 D6가 정한다(`docs/silhouette.md`).

- 마스크는 2차원 배열이다. 행 0이 화면 맨 위(이미지 파일을 읽은 그대로)다.
  bool, 또는 값이 {0, 255}·{0, 1}뿐인 정수 배열을 받는다. 그 밖의 값(안티에일리어싱된 경계 등)은
  오류다. 임계값으로 자르는 것은 호출하는 쪽이 정한다.
- 길이는 픽셀(px)이다. `_norm`은 이미지 크기로, `_rel`은 옷 바운딩 박스 높이로 나눈 값이다.
- 빈 마스크(옷 픽셀 0개)는 `EmptyMaskError`다. 0이나 NaN으로 숨기지 않는다.
- 두 마스크 비교는 같은 크기만 한다. 늘이거나 옮겨 맞추지 않는다(명세서 7장).

높이별 가로 폭: 기준 마스크의 바운딩 박스 맨 위에서 박스 높이의 `level` 비율인 행을 가운데로 둔
`band_rows`개 행(박스 안으로 자름)마다 "가장 왼쪽 ~ 가장 오른쪽 옷 픽셀" 폭을 재고 그 중앙값을 쓴다
(옷 픽셀이 없는 행은 0). 기본 높이 0.15·0.45·0.90과 5행은 명세서 7장(가슴 45 %, 밑단 90 %,
주변 5행 중앙값)에 어깨·소매 쪽 0.15를 더한 것이다.

경계 거리: 95 % Hausdorff 거리(HD95)를 쓴다. 각 마스크의 경계 픽셀(4-이웃 중 옷이 아닌 픽셀이
있는 옷 픽셀, 이미지 밖은 옷이 아님)에서 다른 마스크 경계까지의 최소 거리를 구하고, 두 방향 각각의
95 백분위 중 큰 값이다. 평균 경계 거리는 경계 대부분이 맞으면 소매 하나가 빠지는 것 같은 국소 변형을
희석하고, 최대 Hausdorff는 분할 잡음 몇 픽셀에 끌려간다. HD95는 그 사이다(`docs/silhouette.md` 3절).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np

SHAPE_VERSION = "silhouette_shape_v1"
DEFAULT_LEVELS: tuple[float, ...] = (0.15, 0.45, 0.90)
DEFAULT_BAND_ROWS = 5
HAUSDORFF_PERCENTILE = 95.0
_CHUNK = 2048  # 경계 거리 계산을 나누는 크기(메모리: _CHUNK × 경계 픽셀 수 × 4바이트)


class EmptyMaskError(ValueError):
    """옷 픽셀이 하나도 없는 마스크."""


def as_bool_mask(mask: Any) -> np.ndarray:
    """마스크를 2차원 bool 배열로 바꾼다. 0/255·0/1·bool 외의 값이면 ValueError."""
    m = np.asarray(mask)
    if m.ndim != 2:
        raise ValueError(f"마스크는 2차원이어야 합니다: shape={m.shape}")
    if m.size == 0:
        raise ValueError("마스크 크기가 0입니다")
    if m.dtype == bool:
        return m
    if not np.issubdtype(m.dtype, np.integer):
        raise ValueError(f"마스크는 bool 또는 정수 배열이어야 합니다: dtype={m.dtype}")
    values = set(np.unique(m).tolist())
    if values <= {0, 255}:
        return m == 255
    if values <= {0, 1}:
        return m == 1
    raise ValueError(f"마스크 값은 0/255 또는 0/1이어야 합니다: {sorted(values)[:8]}")


def _nonempty(m: np.ndarray) -> None:
    if not m.any():
        raise EmptyMaskError("옷 픽셀이 없는 마스크입니다")


def _bbox(m: np.ndarray) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1), 끝 포함."""
    rows = np.flatnonzero(m.any(axis=1))
    cols = np.flatnonzero(m.any(axis=0))
    return int(cols[0]), int(rows[0]), int(cols[-1]), int(rows[-1])


def _row_spans(m: np.ndarray) -> np.ndarray:
    """행마다 가장 왼쪽 ~ 가장 오른쪽 옷 픽셀 폭(px, 끝 포함). 빈 행은 0."""
    w = m.shape[1]
    any_row = m.any(axis=1)
    left = np.argmax(m, axis=1)
    right = w - 1 - np.argmax(m[:, ::-1], axis=1)
    return np.where(any_row, right - left + 1, 0)


def _level_rows(y0: int, y1: int, level: float, band_rows: int) -> tuple[int, int, int]:
    """박스 맨 위에서 level 비율인 행과 그 주변 band_rows개 행 범위(박스 안, 끝 포함)."""
    if not 0.0 <= level <= 1.0:
        raise ValueError(f"높이 비율은 0~1이어야 합니다: {level}")
    box_h = y1 - y0 + 1
    row = min(y0 + int(np.floor(level * box_h)), y1)
    half = band_rows // 2
    lo = max(y0, row - half)
    hi = min(y1, row - half + band_rows - 1)
    return row, lo, hi


def _widths_at(
    m: np.ndarray, y0: int, y1: int, levels: Sequence[float], band_rows: int
) -> list[dict[str, Any]]:
    spans = _row_spans(m)
    box_h = y1 - y0 + 1
    out = []
    for lv in levels:
        row, lo, hi = _level_rows(y0, y1, float(lv), band_rows)
        width = float(np.median(spans[lo : hi + 1]))
        out.append(
            {
                "level": float(lv),
                "row": row,
                "rows": [lo, hi],
                "width_px": width,
                "width_rel": width / box_h,
            }
        )
    return out


def mask_shape(
    mask: Any,
    levels: Sequence[float] = DEFAULT_LEVELS,
    band_rows: int = DEFAULT_BAND_ROWS,
) -> dict[str, Any]:
    """마스크 하나의 형태 값. 판정하지 않는다.

    반환 키: image_size, area_px, area_ratio, bbox_px, bbox_norm, centroid_px, centroid_norm,
    hem_row_px, hem_norm, widths, symmetry_iou, symmetry_diff, version.
    `symmetry_diff = 1 - symmetry_iou`이고 symmetry_iou는 마스크와 그 좌우 반전(바운딩 박스 가운데
    세로선 기준)의 IoU다. 0이면 좌우 대칭.
    """
    if band_rows < 1:
        raise ValueError("band_rows는 1 이상이어야 합니다")
    m = as_bool_mask(mask)
    _nonempty(m)
    h, w = m.shape
    x0, y0, x1, y1 = _bbox(m)
    ys, xs = np.nonzero(m)
    area = int(len(xs))
    cx, cy = float(xs.mean()), float(ys.mean())

    # 좌우 반전: x' = x0 + x1 - x (항상 정수, 박스 안)
    box = m[y0 : y1 + 1, x0 : x1 + 1]
    mirror = box[:, ::-1]
    inter = int(np.logical_and(box, mirror).sum())
    union = int(np.logical_or(box, mirror).sum())
    sym_iou = inter / union

    return {
        "version": SHAPE_VERSION,
        "image_size": [w, h],
        "area_px": area,
        "area_ratio": area / (w * h),
        "bbox_px": [x0, y0, x1, y1],
        "bbox_norm": [x0 / w, y0 / h, (x1 + 1) / w, (y1 + 1) / h],
        "bbox_size_px": [x1 - x0 + 1, y1 - y0 + 1],
        "centroid_px": [cx, cy],
        "centroid_norm": [(cx + 0.5) / w, (cy + 0.5) / h],
        "hem_row_px": y1,
        "hem_norm": (y1 + 1) / h,
        "widths": _widths_at(m, y0, y1, levels, band_rows),
        "symmetry_iou": sym_iou,
        "symmetry_diff": 1.0 - sym_iou,
    }


def _boundary(m: np.ndarray) -> np.ndarray:
    """경계 픽셀 좌표 (N, 2) [y, x]. 4-이웃 중 옷이 아닌 픽셀(이미지 밖 포함)이 있는 옷 픽셀."""
    p = np.pad(m, 1, constant_values=False)
    inner = p[1:-1, 1:-1] & p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
    return np.argwhere(m & ~inner).astype(np.float32)


def _directed_min_dist(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """a의 각 점에서 b까지의 최소 유클리드 거리."""
    out = np.empty(len(a), dtype=np.float64)
    b_sq = (b**2).sum(axis=1)
    for s in range(0, len(a), _CHUNK):
        ac = a[s : s + _CHUNK]
        d2 = (ac**2).sum(axis=1)[:, None] + b_sq[None, :] - 2.0 * ac @ b.T
        out[s : s + _CHUNK] = np.sqrt(np.maximum(d2.min(axis=1), 0.0))
    return out


def boundary_hd95(ref: Any, other: Any) -> float:
    """두 마스크 경계의 95 % Hausdorff 거리(px). 같은 크기, 비어 있지 않아야 한다."""
    a, b = _pair(ref, other)
    ba, bb = _boundary(a), _boundary(b)
    d_ab = _directed_min_dist(ba, bb)
    d_ba = _directed_min_dist(bb, ba)
    return float(
        max(
            np.percentile(d_ab, HAUSDORFF_PERCENTILE),
            np.percentile(d_ba, HAUSDORFF_PERCENTILE),
        )
    )


def _pair(ref: Any, other: Any) -> tuple[np.ndarray, np.ndarray]:
    a, b = as_bool_mask(ref), as_bool_mask(other)
    if a.shape != b.shape:
        raise ValueError(
            f"두 마스크 크기가 다릅니다: {a.shape[::-1]} vs {b.shape[::-1]} "
            "(늘이거나 줄여 맞추지 않는다. 크기를 바꾸려면 호출 전에 최근접 보간으로 맞춘다)"
        )
    _nonempty(a)
    _nonempty(b)
    return a, b


def compare_masks(
    ref: Any,
    other: Any,
    levels: Sequence[float] = DEFAULT_LEVELS,
    band_rows: int = DEFAULT_BAND_ROWS,
) -> dict[str, Any]:
    """기준 마스크(ref, 보통 렌더)와 다른 마스크(other, 보통 실사 프레임)의 차이. 판정하지 않는다.

    - iou: 교집합 / 합집합
    - area_ratio: other 넓이 / ref 넓이 (명세서 7장 "0.5배 미만 또는 1.5배 초과"에 쓰는 값)
    - area_ratio_diff: other.area_ratio - ref.area_ratio (이미지 대비 넓이 비율의 차)
    - centroid_shift_px: [dx, dy]와 거리 centroid_shift_dist_px (other - ref, 아래·오른쪽이 +)
    - hem_shift_px: other 밑단 행 - ref 밑단 행 (+면 other 밑단이 더 아래)
    - widths: **ref 바운딩 박스로 정한 같은 행**에서 잰 폭. width_diff_px = other - ref,
      width_change_ratio = other / ref - 1 (ref 폭이 0이면 None)
    - boundary_hd95_px: 경계 95 % Hausdorff 거리
    """
    a, b = _pair(ref, other)
    sa = mask_shape(a, levels, band_rows)
    sb = mask_shape(b, levels, band_rows)
    inter = int(np.logical_and(a, b).sum())
    union = int(np.logical_or(a, b).sum())

    _, y0, _, y1 = sa["bbox_px"]
    w_ref = _widths_at(a, y0, y1, levels, band_rows)
    w_oth = _widths_at(b, y0, y1, levels, band_rows)
    widths = []
    for r, o in zip(w_ref, w_oth, strict=True):
        widths.append(
            {
                "level": r["level"],
                "row": r["row"],
                "rows": r["rows"],
                "ref_width_px": r["width_px"],
                "other_width_px": o["width_px"],
                "width_diff_px": o["width_px"] - r["width_px"],
                "width_change_ratio": (o["width_px"] / r["width_px"] - 1.0)
                if r["width_px"] > 0
                else None,
            }
        )

    dx = sb["centroid_px"][0] - sa["centroid_px"][0]
    dy = sb["centroid_px"][1] - sa["centroid_px"][1]
    return {
        "version": SHAPE_VERSION,
        "image_size": sa["image_size"],
        "iou": inter / union,
        "intersection_px": inter,
        "union_px": union,
        "area_ratio": sb["area_px"] / sa["area_px"],
        "area_ratio_diff": sb["area_ratio"] - sa["area_ratio"],
        "centroid_shift_px": [dx, dy],
        "centroid_shift_dist_px": float(np.hypot(dx, dy)),
        "hem_shift_px": sb["hem_row_px"] - sa["hem_row_px"],
        "widths": widths,
        "boundary_hd95_px": boundary_hd95(a, b),
        "symmetry_diff_change": sb["symmetry_diff"] - sa["symmetry_diff"],
    }
