"""실사화 결과의 색·실루엣 측정 (D6, #11, OpenCV). 판정은 순수 로직 `fitting.verify.visual_check`.

흐름 (`check_videos`)
1. 두 영상 정보를 읽고 길이 차이(0.5초)·화면비 차이(1 %)를 확인한다.
2. 실사 영상 길이의 0~100 % 11프레임과, 시각으로 대응하는 렌더 프레임을 읽는다.
3. 렌더 옷 마스크: C5가 낸 마스크 한 장을 모든 렌더 프레임에 쓴다(정지 클립). 고른 렌더 프레임이
   서로 다르면 정지 클립이 아니라고 보고 검증할 수 없다. 테스트·임시 클립용으로 프레임마다 마스크를
   만드는 함수도 받는다.
4. 실사 프레임을 같은 비율로 렌더 크기에 맞추고 옷 영역을 찾는다(`segment_garment`, 색 기반).
5. 프레임마다 면적 비율·IoU·가슴/밑단 폭 변화·대표색 CIEDE2000을 재고 판정한다.

실사 옷 영역 찾기는 명세서의 SAM 2 대신 모델 없이 도는 색 기반 방법이다([D 제안],
`config/validate/visual_check_v1.json`의 `segmentation`). 옷과 비슷한 색의 바지·배경이 옷과 붙어
있으면 둘을 가르지 못한다(docs/visual-check.md 6절).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from fitting.verify.frame_match import ClipTiming, FrameMatch, match_render_frame
from fitting.verify.visual_check import (
    FrameMeasure,
    VisualCheckResult,
    VisualCriteria,
    band_width,
    box_height,
    delta_e_2000,
    judge_video,
    mask_iou,
    median_lab,
    sample_indices,
    srgb_to_lab,
    width_change,
    width_rows,
)

from .frames import VideoInfo, read_frames, read_video_info

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CHECK_CONFIG = REPO_ROOT / "config" / "validate" / "visual_check_v1.json"

# 렌더 옷 마스크: 정지 클립용 한 장, 또는 (렌더 프레임 번호, 렌더 프레임) -> 마스크
MaskSource = np.ndarray | Callable[[int, np.ndarray], np.ndarray]


@dataclass(frozen=True)
class SegmentParams:
    """실사 옷 영역 찾기와 렌더 마스크 확인 설정([D 제안])."""

    method: str
    core_erode_ratio: float
    outside_margin_ratio: float
    other_colors: int
    min_other_distance: float
    color_distance_max: float
    open_ratio: float
    close_ratio: float
    min_component_overlap: float
    static_mean_abs_diff_max: float

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> SegmentParams:
        """Raises: ValueError — 블록이나 값이 없다."""
        seg, render = config.get("segmentation"), config.get("render_mask")
        if not isinstance(seg, Mapping) or not isinstance(render, Mapping):
            raise ValueError("검증 설정에 segmentation·render_mask 블록이 없습니다")
        try:
            params = cls(
                method=str(seg["method"]),
                core_erode_ratio=float(seg["core_erode_ratio"]),
                outside_margin_ratio=float(seg["outside_margin_ratio"]),
                other_colors=int(seg["other_colors"]),
                min_other_distance=float(seg["min_other_distance"]),
                color_distance_max=float(seg["color_distance_max"]),
                open_ratio=float(seg["open_ratio"]),
                close_ratio=float(seg["close_ratio"]),
                min_component_overlap=float(seg["min_component_overlap"]),
                static_mean_abs_diff_max=float(render["static_mean_abs_diff_max"]),
            )
        except KeyError as error:
            raise ValueError(f"검증 설정에 분할 값이 없습니다: {error.args[0]}") from error
        if params.method != "color_seed_v1":
            raise ValueError(f"모르는 분할 방법입니다: {params.method}")
        if params.other_colors < 0 or params.color_distance_max <= 0:
            raise ValueError("other_colors는 0 이상, color_distance_max는 0보다 커야 합니다")
        return params

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CheckConfig:
    """검증 설정 파일 하나(기준 + 분할 설정)."""

    criteria: VisualCriteria
    params: SegmentParams
    source: str  # "이름@버전"
    sha256: str
    path: str

    def to_record(self) -> dict[str, Any]:
        return {"source": self.source, "sha256": self.sha256, "path": self.path}


def load_check_config(path: Path = DEFAULT_CHECK_CONFIG) -> CheckConfig:
    """Raises: OSError — 파일을 읽지 못함, ValueError — 형식·값이 잘못됨."""
    raw = Path(path).read_bytes()
    try:
        config = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"검증 설정을 읽지 못했습니다: {path}: {error}") from error
    if not isinstance(config, dict):
        raise ValueError(f"검증 설정은 JSON 객체여야 합니다: {path}")
    source = f"{config.get('name', Path(path).stem)}@{config.get('version', '?')}"
    try:
        rel = Path(path).resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        rel = Path(path).name
    return CheckConfig(
        criteria=VisualCriteria.from_config(config, source=source),
        params=SegmentParams.from_config(config),
        source=source,
        sha256=hashlib.sha256(raw).hexdigest(),
        path=rel,
    )


# ---------------------------------------------------------------------------
# 마스크 도구
# ---------------------------------------------------------------------------


def _kernel(px: int) -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * px + 1, 2 * px + 1))


def _distance(mask: np.ndarray) -> np.ndarray:
    """마스크 안 픽셀마다 가장 가까운 마스크 밖 픽셀까지의 유클리드 거리.

    화면 밖은 마스크 밖으로 보지 않는다.
    """
    return cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)


def erode(mask: np.ndarray, px: int) -> np.ndarray:
    """마스크를 안쪽으로 `px`픽셀 줄인다(원형). 화면 가장자리 쪽은 줄이지 않는다."""
    mask = np.asarray(mask, dtype=bool)
    if px <= 0:
        return mask.copy()
    return _distance(mask) > px


def dilate(mask: np.ndarray, px: int) -> np.ndarray:
    """마스크를 바깥으로 `px`픽셀 넓힌다(원형)."""
    mask = np.asarray(mask, dtype=bool)
    if px <= 0:
        return mask.copy()
    return _distance(~mask) <= px


def fill_holes(mask: np.ndarray) -> np.ndarray:
    """화면 가장자리와 이어지지 않은 안쪽 구멍을 메운다."""
    mask = np.asarray(mask, dtype=bool)
    inverse = (~mask).astype(np.uint8)
    count, labels = cv2.connectedComponents(inverse, connectivity=4)
    if count <= 1:
        return mask.copy()
    border = np.unique(
        np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])
    )
    holes = ~np.isin(labels, border) & (inverse > 0)
    return mask | holes


def as_mask(image: np.ndarray) -> np.ndarray:
    """흑백·컬러 마스크 그림을 bool로(밝기 128 이상이 옷)."""
    array = np.asarray(image)
    if array.dtype == bool:
        return array
    if array.ndim == 3:
        array = array.max(axis=2)
    return array >= 128


def bgr_to_lab(bgr: np.ndarray) -> np.ndarray:
    return srgb_to_lab(np.asarray(bgr)[..., ::-1])


def cover(frame: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """같은 비율로 키우거나 줄여 `size`(가로, 세로)를 덮은 뒤 가운데를 자른다. 늘리지 않는다.

    fal render-to-real은 출력 크기를 맞추려고 화면비를 바꾼다(768x1024 입력 → 704x896 출력, 4.8 %).
    이때 내용은 세로에 맞춰 같은 비율로 줄이고 좌우를 덧붙인다(옷 위치로 확인,
    docs/realize-fitroom.md).
    그래서 같은 비율로 되돌린 뒤 덧붙은 가장자리를 잘라 내면 렌더와 겹친다.
    """
    width, height = size
    fh, fw = frame.shape[:2]
    if (fw, fh) == (width, height):
        return frame
    scale = max(width / fw, height / fh)
    nw, nh = max(width, round(fw * scale)), max(height, round(fh * scale))
    method = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    resized = cv2.resize(frame, (nw, nh), interpolation=method)
    x0, y0 = (nw - width) // 2, (nh - height) // 2
    return np.ascontiguousarray(resized[y0 : y0 + height, x0 : x0 + width])


def _px(ratio: float, height: int) -> int:
    return max(1, round(ratio * height))


# ---------------------------------------------------------------------------
# 실사 옷 영역
# ---------------------------------------------------------------------------


def segment_garment(
    real_bgr: np.ndarray, render_mask: np.ndarray, params: SegmentParams
) -> tuple[np.ndarray, dict[str, Any]]:
    """렌더 크기로 맞춘 실사 프레임에서 옷 영역을 찾는다. 못 찾으면 빈 마스크.

    Returns:
        (옷 마스크, 기록용 정보: 옷 색, 바깥 대표색, 남긴 연결 영역 수 등)
    """
    render_mask = np.asarray(render_mask, dtype=bool)
    height = render_mask.shape[0]
    empty = np.zeros_like(render_mask)
    core = erode(render_mask, _px(params.core_erode_ratio, height))
    info: dict[str, Any] = {"core_px": int(core.sum())}
    if core.sum() < 25:
        info["reason"] = "렌더 마스크 안쪽이 너무 작아 옷 색을 잡을 수 없음"
        return empty, info

    lab = bgr_to_lab(real_bgr).astype(np.float32)
    garment = np.median(lab[core], axis=0)
    info["garment_lab"] = [round(float(v), 2) for v in garment]

    others: list[np.ndarray] = []
    outside = ~dilate(render_mask, _px(params.outside_margin_ratio, height))
    if params.other_colors > 0 and outside.sum() >= params.other_colors * 20:
        samples = lab[outside]
        step = max(1, samples.shape[0] // 20000)  # 결정적으로 고르게 솎는다
        samples = np.ascontiguousarray(samples[::step], dtype=np.float32)
        k = min(params.other_colors, samples.shape[0])
        cv2.setRNGSeed(0)
        _, _, centers = cv2.kmeans(
            samples,
            k,
            None,
            (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.1),
            3,
            cv2.KMEANS_PP_CENTERS,
        )
        others = [
            c
            for c in centers
            if float(np.linalg.norm(c - garment)) >= params.min_other_distance
        ]
        info["other_lab"] = [[round(float(v), 2) for v in c] for c in others]
        info["other_dropped"] = int(len(centers) - len(others))

    dist_garment = np.linalg.norm(lab - garment, axis=2)
    candidate = dist_garment <= params.color_distance_max
    for center in others:
        candidate &= dist_garment < np.linalg.norm(lab - center, axis=2)
    info["candidate_px"] = int(candidate.sum())

    mask = candidate.astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, _kernel(_px(params.open_ratio, height)))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _kernel(_px(params.close_ratio, height)))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    overlap = np.bincount(labels[render_mask], minlength=count)
    keep = [
        k
        for k in range(1, count)
        if overlap[k] >= params.min_component_overlap * stats[k, cv2.CC_STAT_AREA]
    ]
    info["components_total"] = int(count - 1)
    info["components_kept"] = len(keep)
    if not keep:
        info["reason"] = "렌더 옷 자리와 겹치는 옷 색 영역이 없음"
        return empty, info
    return fill_holes(np.isin(labels, keep)), info


# ---------------------------------------------------------------------------
# 영상 두 개 비교
# ---------------------------------------------------------------------------


@dataclass
class VisualRun:
    """`check_videos` 결과: 판정과, 저장·그림에 쓸 프레임·마스크."""

    result: VisualCheckResult
    real_info: VideoInfo
    render_info: VideoInfo
    matches: list[FrameMatch]
    real_frames: dict[int, np.ndarray]  # 원래 크기
    render_frames: dict[int, np.ndarray]
    render_masks: list[np.ndarray | None] = field(default_factory=list)  # 샘플 순서
    real_masks: list[np.ndarray | None] = field(default_factory=list)  # 렌더 크기
    segment_info: list[dict[str, Any]] = field(default_factory=list)

    def representative_pair(self) -> tuple[int, int]:
        """대표 프레임 (실사 번호, 렌더 번호). 판정에 대표가 없으면 50 %에 가장 가까운 샘플."""
        rep = self.result.representative_frame
        if rep is not None:
            return rep.measure.real_index, rep.measure.render_index
        fractions = self.result.criteria.sample_fractions
        position = min(range(len(fractions)), key=lambda i: (abs(fractions[i] - 0.5), i))
        match = self.matches[position]
        return match.real_index, match.render_index

    def to_record(self) -> dict[str, Any]:
        return {
            **self.result.to_record(),
            "real_video": self.real_info.to_dict(),
            "render_clip": self.render_info.to_dict(),
            "segmentation": self.segment_info,
        }


def _aspect_gap(real: VideoInfo, render: VideoInfo) -> float:
    return abs((real.width / real.height) / (render.width / render.height) - 1.0)


def _render_masks(
    source: MaskSource,
    matches: list[FrameMatch],
    render_frames: dict[int, np.ndarray],
    params: SegmentParams,
) -> tuple[list[np.ndarray | None], list[str], dict[str, Any]]:
    """샘플마다 렌더 옷 마스크. 정지 클립 마스크면 크기·정지 여부를 확인한다."""
    blockers: list[str] = []
    details: dict[str, Any] = {}
    frames = [render_frames[m.render_index] for m in matches]
    if callable(source):
        masks = [as_mask(source(m.render_index, f)) for m, f in zip(matches, frames, strict=True)]
        details["render_mask"] = "per_frame"
    else:
        mask = as_mask(source)
        masks = [mask] * len(frames)
        details["render_mask"] = "static"
        base = frames[0].astype(np.int16)
        diffs = [float(np.abs(f.astype(np.int16) - base).mean()) for f in frames]
        details["render_static_mean_abs_diff_max"] = round(max(diffs), 3)
        if max(diffs) > params.static_mean_abs_diff_max:
            blockers.append(
                f"렌더 클립 프레임이 서로 달라(평균 차이 최대 {max(diffs):.2f}) 마스크 한 장으로 "
                "비교할 수 없습니다."
            )
    for mask, frame in zip(masks, frames, strict=True):
        if mask.shape != frame.shape[:2]:
            blockers.append(
                f"렌더 옷 마스크 크기 {mask.shape[1]}x{mask.shape[0]}가 렌더 프레임 "
                f"{frame.shape[1]}x{frame.shape[0]}와 다릅니다."
            )
            break
        if not mask.any():
            blockers.append("렌더 옷 마스크가 비었습니다.")
            break
    return list(masks), blockers, details


def measure_frame(
    render_bgr: np.ndarray,
    real_bgr: np.ndarray,
    render_mask: np.ndarray,
    real_mask: np.ndarray,
    criteria: VisualCriteria,
    *,
    fraction: float,
    match: FrameMatch,
) -> FrameMeasure:
    """같은 크기의 렌더·실사 프레임과 두 옷 마스크로 프레임 하나의 측정값을 낸다."""
    rows = width_rows(render_mask, criteria.chest_height, criteria.hem_height)
    n = criteria.width_rows
    chest = width_change(
        band_width(render_mask, rows.chest, n), band_width(real_mask, rows.chest, n)
    )
    hem = width_change(band_width(render_mask, rows.hem, n), band_width(real_mask, rows.hem, n))
    render_area = int(render_mask.sum())
    common = erode(render_mask, criteria.color_erode_px) & erode(
        real_mask, criteria.color_erode_px
    )
    common_ratio = common.sum() / render_area if render_area else 0.0
    render_lab = real_lab = delta = None
    if real_mask.any() and common_ratio >= criteria.color_min_common_ratio:
        # 공통 영역 픽셀만 Lab으로 바꾼다(프레임 전체를 바꾸는 것보다 빠르고 값은 같다)
        ones = np.ones(int(common.sum()), dtype=bool)
        render_lab = median_lab(bgr_to_lab(render_bgr[common]), ones)
        real_lab = median_lab(bgr_to_lab(real_bgr[common]), ones)
        delta = float(delta_e_2000(np.array(render_lab), np.array(real_lab)))
    return FrameMeasure.build(
        fraction=fraction,
        real_index=match.real_index,
        real_time_s=match.real_time_s,
        render_index=match.render_index,
        render_time_s=match.render_time_s,
        render_area=render_area,
        real_area=int(real_mask.sum()),
        iou=mask_iou(render_mask, real_mask),
        chest_change=chest,
        hem_change=hem,
        delta_e=delta,
        common_ratio=common_ratio,
        render_lab=render_lab,
        real_lab=real_lab,
        length_change=width_change(box_height(render_mask), box_height(real_mask) or None),
    )


def check_videos(
    real_video: Path,
    render_clip: Path,
    render_mask: MaskSource,
    config: CheckConfig,
    *,
    mode: str = "time",
) -> VisualRun:
    """실사 영상과 렌더 클립을 11프레임에서 비교하고 판정한다.

    Raises:
        ValueError: 영상을 열 수 없거나 프레임을 읽지 못했다(판정 전 입력 오류).
    """
    criteria, params = config.criteria, config.params
    real_info, render_info = read_video_info(Path(real_video)), read_video_info(Path(render_clip))
    real_t = ClipTiming(real_info.fps, real_info.frame_count)
    render_t = ClipTiming(render_info.fps, render_info.frame_count)
    blockers: list[str] = []
    gap = abs(real_t.duration_s - render_t.duration_s)
    if gap > criteria.max_duration_gap_s + 1e-9:
        blockers.append(
            f"두 영상 길이 차이 {gap:.2f}초가 기준 {criteria.max_duration_gap_s:g}초를 넘습니다."
        )
    aspect = _aspect_gap(real_info, render_info)
    if aspect > criteria.max_aspect_gap + 1e-9:
        blockers.append(
            f"두 영상 화면비 차이 {aspect * 100:.1f} %가 기준 "
            f"{criteria.max_aspect_gap * 100:g} %를 넘어 늘리지 않고는 겹칠 수 없습니다."
        )

    indices = sample_indices(real_info.frame_count, criteria.sample_fractions)
    matches = [match_render_frame(i, real_t, render_t, mode) for i in indices]
    real_frames = read_frames(Path(real_video), indices)
    render_frames = read_frames(Path(render_clip), [m.render_index for m in matches])
    masks, mask_blockers, details = _render_masks(render_mask, matches, render_frames, params)
    blockers.extend(mask_blockers)
    details.update(duration_gap_s=round(gap, 4), aspect_gap=round(aspect, 4), match_mode=mode)

    run = VisualRun(
        result=judge_video([], criteria, blockers=blockers, details=details),
        real_info=real_info,
        render_info=render_info,
        matches=matches,
        real_frames=real_frames,
        render_frames=render_frames,
        render_masks=list(masks),
    )
    if blockers:
        return run

    size = (render_info.width, render_info.height)
    measures: list[FrameMeasure] = []
    for fraction, match, mask in zip(criteria.sample_fractions, matches, masks, strict=True):
        assert mask is not None
        real = real_frames[match.real_index]
        real = cover(real, size)
        real_mask, info = segment_garment(real, mask, params)
        run.real_masks.append(real_mask)
        run.segment_info.append({"fraction": fraction, **info})
        measures.append(
            measure_frame(
                render_frames[match.render_index],
                real,
                mask,
                real_mask,
                criteria,
                fraction=fraction,
                match=match,
            )
        )
    run.result = judge_video(measures, criteria, details=details)
    return run


# ---------------------------------------------------------------------------
# 사람 확인용 그림
# ---------------------------------------------------------------------------

_GREEN, _MAGENTA, _WHITE, _BLACK = (60, 200, 60), (220, 60, 220), (255, 255, 255), (0, 0, 0)
_STATUS_COLOR = {"PASS": (60, 170, 60), "FAIL": (50, 50, 220), "UNVERIFIABLE": (0, 160, 220)}


def _outline(image: np.ndarray, mask: np.ndarray | None, color: tuple[int, int, int]) -> None:
    if mask is None or not mask.any():
        return
    contours, _ = cv2.findContours(
        mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
    )
    cv2.drawContours(image, contours, -1, color, 2)


def contact_sheet(run: VisualRun, height: int = 240) -> np.ndarray:
    """11프레임 접촉 시트(명세서 7장 사람 검수용). 위: 렌더, 아래: 실사(렌더 크기로 맞춤).

    초록 선 = 렌더 옷 마스크, 자주 선 = 실사에서 찾은 옷 영역. 글자는 ASCII만 쓴다(OpenCV 글꼴).
    """
    render_w, render_h = run.render_info.width, run.render_info.height
    width = round(render_w * height / render_h)
    columns = []
    frames = run.result.frames
    for k, match in enumerate(run.matches):
        render = run.render_frames[match.render_index].copy()
        real = run.real_frames[match.real_index]
        if (real.shape[1], real.shape[0]) != (render_w, render_h):
            real = cover(real, (render_w, render_h))
        real = real.copy()
        render_mask = run.render_masks[k] if k < len(run.render_masks) else None
        real_mask = run.real_masks[k] if k < len(run.real_masks) else None
        _outline(render, render_mask, _GREEN)
        _outline(real, render_mask, _GREEN)
        _outline(real, real_mask, _MAGENTA)
        top = cv2.resize(render, (width, height), interpolation=cv2.INTER_AREA)
        bottom = cv2.resize(real, (width, height), interpolation=cv2.INTER_AREA)
        label = np.full((52, width, 3), 255, dtype=np.uint8)
        fraction = run.result.criteria.sample_fractions[k]
        status = frames[k].status if k < len(frames) else "-"
        cv2.putText(label, f"{fraction * 100:.0f}% {status}", (4, 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, _STATUS_COLOR.get(status, _BLACK), 1,
                    cv2.LINE_AA)
        if k < len(frames):
            m = frames[k].measure
            iou = "-" if m.iou is None else f"{m.iou:.2f}"
            de = "-" if m.delta_e is None else f"{m.delta_e:.1f}"
            wc = "-" if m.width_change_abs is None else f"{m.width_change_abs * 100:.0f}%"
            cv2.putText(label, f"IoU {iou} dE {de}", (4, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.38,
                        _BLACK, 1, cv2.LINE_AA)
            cv2.putText(label, f"width {wc}", (4, 48), cv2.FONT_HERSHEY_SIMPLEX, 0.38, _BLACK,
                        1, cv2.LINE_AA)
        column = np.vstack([top, bottom, label])
        columns.append(column)
        columns.append(np.full((column.shape[0], 4, 3), 255, dtype=np.uint8))
    sheet = np.hstack(columns[:-1]) if columns else np.full((height, width, 3), 255, np.uint8)
    header = np.full((28, sheet.shape[1], 3), 255, dtype=np.uint8)
    result = run.result
    cv2.putText(
        header,
        f"{result.status}  passed {result.frames_passed}/{len(result.frames)} "
        f"(need {result.criteria.min_frames_passed})  green=render mask  magenta=real garment",
        (6, 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        _STATUS_COLOR.get(result.status, _BLACK),
        1,
        cv2.LINE_AA,
    )
    return np.vstack([header, sheet])
