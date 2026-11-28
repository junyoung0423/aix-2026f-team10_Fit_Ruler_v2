"""피팅룸 영상 측정 (#11, OpenCV). 판정은 순수 로직 `fitting.verify.fitroom_check`.

- 핏 확인 사진: 렌더 첫 프레임 + 렌더 옷 마스크 ↔ `{job}_realize_fit_frame.png`(같은 구도). 실사
  프레임은 같은 비율로 맞추고 가운데를 잘라(`cover`) D6과 같은 방법으로 옷 영역을 찾는다. 사진
  모델이 구도를 조금 멀리 잡는 일이 있어(q13: 약 10 %), 찾은 옷 영역이 렌더 마스크와 가장 많이
  겹치게 크기·위치를 맞춘 뒤(설정 `fit_frame`의 범위 안) 다시 찾고 잰다. 맞춘 값은 기록에 남긴다.
- 영상: 1초 간격 프레임(+ 마지막)으로 검은 화면, 첫·끝 프레임 차이, 시점마다 옷 색을 찾았는지,
  옷 밝기 흐름을 잰다. 모든 프레임을 차례로 읽어 화면 튐(연속 프레임 변화의 최대 ÷ 중앙값)도 잰다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from fitting.verify.fitroom_check import (
    FitRoomCheckResult,
    FitRoomVideoCriteria,
    VideoMeasure,
    judge_fitroom,
)
from fitting.verify.frame_match import FrameMatch
from fitting.verify.visual_check import FrameResult, judge_frame

from .frames import read_frames, read_video_info
from .visual_measure import (
    REPO_ROOT,
    CheckConfig,
    bgr_to_lab,
    cover,
    measure_frame,
    segment_garment,
)

DEFAULT_FITROOM_CONFIG = REPO_ROOT / "config" / "validate" / "fitroom_check_v1.json"


@dataclass(frozen=True)
class FitAlign:
    """핏 확인 사진 크기·위치 맞춤 범위(렌더 크기 기준)."""

    scale_min: float = 1.0
    scale_max: float = 1.0
    scale_step: float = 0.02
    shift_max_frac: float = 0.0  # 가로·세로 크기 대비 최대 이동 비율

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> FitAlign:
        """Raises: ValueError — 값이 범위 밖이다. `fit_frame` 블록이 없으면 맞추지 않는다."""
        block = config.get("fit_frame")
        if not isinstance(block, dict):
            return cls()
        keys = ("align_scale_min", "align_scale_max", "align_scale_step", "align_shift_max_frac")
        missing = [k for k in keys if k not in block]
        if missing:
            raise ValueError(f"fit_frame 맞춤 값이 없습니다: {', '.join(missing)}")
        align = cls(
            scale_min=float(block["align_scale_min"]),
            scale_max=float(block["align_scale_max"]),
            scale_step=float(block["align_scale_step"]),
            shift_max_frac=float(block["align_shift_max_frac"]),
        )
        if not (0 < align.scale_min <= 1.0 <= align.scale_max) or align.scale_step <= 0:
            raise ValueError("fit_frame 맞춤 크기 범위가 잘못됐습니다(최소 ≤ 1 ≤ 최대)")
        if not 0 <= align.shift_max_frac < 0.5:
            raise ValueError("fit_frame 맞춤 이동 비율은 0 이상 0.5 미만이어야 합니다")
        return align

    def to_record(self) -> dict[str, float]:
        return {
            "scale_min": self.scale_min,
            "scale_max": self.scale_max,
            "scale_step": self.scale_step,
            "shift_max_frac": self.shift_max_frac,
        }


@dataclass(frozen=True)
class FitRoomConfig:
    video: FitRoomVideoCriteria
    source: str
    sha256: str
    path: str
    align: FitAlign = FitAlign()

    def to_record(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "sha256": self.sha256,
            "path": self.path,
            "fit_frame_align": self.align.to_record(),
        }


def load_fitroom_config(path: Path = DEFAULT_FITROOM_CONFIG) -> FitRoomConfig:
    """Raises: OSError — 파일을 읽지 못함, ValueError — 형식·값이 잘못됨."""
    raw = Path(path).read_bytes()
    try:
        config = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"피팅룸 검증 설정을 읽지 못했습니다: {path}: {error}") from error
    if not isinstance(config, dict):
        raise ValueError(f"피팅룸 검증 설정은 JSON 객체여야 합니다: {path}")
    source = f"{config.get('name', Path(path).stem)}@{config.get('version', '?')}"
    try:
        rel = Path(path).resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        rel = Path(path).name
    return FitRoomConfig(
        video=FitRoomVideoCriteria.from_config(config, source=source),
        source=source,
        sha256=hashlib.sha256(raw).hexdigest(),
        path=rel,
        align=FitAlign.from_config(config),
    )


@dataclass
class FitRoomRun:
    result: FitRoomCheckResult
    render_frame: np.ndarray
    fit_frame: np.ndarray  # 렌더 크기로 맞춘 핏 확인 사진
    fit_mask: np.ndarray | None
    render_mask: np.ndarray
    video_frames: list[np.ndarray]  # 1초 간격(+마지막), 원래 크기
    video_times: list[float]
    segment_info: dict[str, Any]

    def to_record(self) -> dict[str, Any]:
        return {
            **self.result.to_record(),
            "segmentation": self.segment_info,
            "video_times": self.video_times,
        }


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    union = int(np.count_nonzero(a | b))
    return int(np.count_nonzero(a & b)) / union if union else 0.0


def best_alignment(
    render_mask: np.ndarray, found: np.ndarray, align: FitAlign
) -> tuple[float, int, int, float, float]:
    """찾은 옷 영역을 렌더 마스크에 가장 많이 겹치게 하는 (크기, 가로·세로 이동 px, 전·후 IoU).

    1/4 크기에서 크기(가운데 기준)와 이동을 모두 훑는다. 범위가 없으면 그대로(1.0, 0, 0)다.
    """
    before = _iou(render_mask, found)
    if align.scale_min == align.scale_max == 1.0 and align.shift_max_frac == 0:
        return 1.0, 0, 0, before, before
    q = 4
    height, width = render_mask.shape
    size = (width // q, height // q)
    target = cv2.resize(render_mask.astype(np.uint8), size, interpolation=cv2.INTER_NEAREST) > 0
    small = cv2.resize(found.astype(np.uint8), size, interpolation=cv2.INTER_NEAREST)
    max_dx = int(align.shift_max_frac * size[0])
    max_dy = int(align.shift_max_frac * size[1])
    best = (_iou(target, small > 0), 1.0, 0, 0)
    steps = int(round((align.scale_max - align.scale_min) / align.scale_step)) + 1
    for scale in np.linspace(align.scale_min, align.scale_max, steps):
        matrix = np.float32(
            [[scale, 0, (1 - scale) * size[0] / 2], [0, scale, (1 - scale) * size[1] / 2]]
        )
        scaled = cv2.warpAffine(small, matrix, size, flags=cv2.INTER_NEAREST) > 0
        for dy in range(-max_dy, max_dy + 1):
            rolled = np.roll(scaled, dy, axis=0)
            for dx in range(-max_dx, max_dx + 1):
                value = _iou(target, np.roll(rolled, dx, axis=1))
                if value > best[0] + 1e-9:
                    best = (value, float(scale), dx * q, dy * q)
    _, scale, dx, dy = best
    return round(scale, 3), dx, dy, before, best[0]


def _warp(image: np.ndarray, scale: float, dx: int, dy: int, nearest: bool) -> np.ndarray:
    height, width = image.shape[:2]
    matrix = np.float32(
        [[scale, 0, (1 - scale) * width / 2 + dx], [0, scale, (1 - scale) * height / 2 + dy]]
    )
    flags = cv2.INTER_NEAREST if nearest else cv2.INTER_LINEAR
    return cv2.warpAffine(image, matrix, (width, height), flags=flags,
                          borderMode=cv2.BORDER_REPLICATE)


def measure_fit_frame(
    render_bgr: np.ndarray,
    render_mask: np.ndarray,
    fit_bgr: np.ndarray,
    config: CheckConfig,
    align: FitAlign | None = None,
) -> tuple[FrameResult, np.ndarray, np.ndarray, dict[str, Any]]:
    """(판정, 렌더 크기로 맞춘 실사 프레임, 찾은 옷 영역, 분할 정보).

    `align`이 있으면 크기·위치를 맞춘 사진에서 옷 영역을 다시 찾아 잰다(`info["alignment"]`).
    """
    height, width = render_mask.shape
    mapped = cover(fit_bgr, (width, height))
    found, info = segment_garment(mapped, render_mask, config.params)
    if align is not None:
        scale, dx, dy, before, after = best_alignment(render_mask, found, align)
        if (scale, dx, dy) != (1.0, 0, 0):
            mapped = _warp(mapped, scale, dx, dy, nearest=False)
            found, info = segment_garment(mapped, render_mask, config.params)
        info = {**info, "alignment": {
            "scale": scale, "shift_px": [dx, dy],
            "iou_before": round(before, 3), "iou_aligned_search": round(after, 3),
        }}
    match = FrameMatch(0, 0.0, 0, 0.0, "still", False)
    measure = measure_frame(
        render_bgr, mapped, render_mask, found, config.criteria, fraction=0.0, match=match
    )
    return judge_frame(measure, config.criteria), mapped, found, info


def measure_video(
    video: Path, criteria: FitRoomVideoCriteria
) -> tuple[VideoMeasure, list[np.ndarray], list[float]]:
    """Raises: ValueError — 영상을 열 수 없거나 프레임이 없다."""
    info = read_video_info(Path(video))
    if info.frame_count <= 0:
        raise ValueError(f"영상에 프레임이 없습니다: {video}")
    fps = info.fps or 24.0
    last = info.frame_count - 1
    step = max(1, round(criteria.sample_step_s * fps))
    indices = sorted(set(range(0, last + 1, step)) | {last})
    by_index = read_frames(Path(video), indices)
    frames = [by_index[i] for i in indices]
    times = [round(i / fps, 2) for i in indices]
    black = all(
        float(f.mean()) < criteria.black_mean_max and float(f.std()) < criteria.black_std_max
        for f in frames
    )
    small = [
        cv2.resize(f, (96, 128), interpolation=cv2.INTER_AREA).astype(np.int16) for f in frames
    ]
    end_gap = round(float(np.abs(small[-1] - small[0]).mean()), 1)
    h, w = frames[0].shape[:2]
    torso = bgr_to_lab(frames[0][int(0.25 * h) : int(0.5 * h), int(0.4 * w) : int(0.6 * w)])
    ref = np.median(torso.reshape(-1, 3), axis=0)
    lightness: list[float] = []
    for frame in frames:
        diff = bgr_to_lab(frame) - ref
        diff[..., 0] *= criteria.lightness_weight
        near = np.linalg.norm(diff, axis=2) < criteria.garment_color_distance
        lab = diff / np.array([criteria.lightness_weight, 1.0, 1.0]) + ref
        if near.mean() >= criteria.garment_min_fraction:
            lightness.append(round(float(np.median(lab[near][:, 0])), 1))
    jump_ratio, jump_at = (
        (None, None) if black else frame_jump(Path(video), fps, criteria.jump_median_floor)
    )
    measure = VideoMeasure(
        black=black,
        end_frame_gap=None if black else end_gap,
        jump_ratio=jump_ratio,
        jump_at_s=jump_at,
        lightness=tuple(lightness),
        seconds=round(info.frame_count / fps, 2),
        size=(info.width, info.height),
        garment_missing=0 if black else len(frames) - len(lightness),
    )
    return measure, frames, times


def frame_jump(
    video: Path, fps: float, median_floor: float = 0.0
) -> tuple[float | None, float | None]:
    """모든 프레임을 96×128로 줄여 연속 프레임 평균 차이를 재고 (최대 ÷ 중앙값, 그 시점 초)를 낸다.

    장면이 끊겨 한 번에 바뀌면(q9·q13) 최대가 평소의 4배를 넘었다. 프레임이 3장 미만이면 None.
    중앙값은 `median_floor` 아래로 보지 않는다. 거의 멈춘 영상은 압축 키프레임마다 생기는
    작은 변화가 중앙값의 10배쯤 되기 때문이다(실제 회전 영상의 중앙값은 3.1~4.4).
    """
    capture = cv2.VideoCapture(str(video))
    diffs: list[float] = []
    previous = None
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            small = cv2.resize(frame, (96, 128), interpolation=cv2.INTER_AREA).astype(np.int16)
            if previous is not None:
                diffs.append(float(np.abs(small - previous).mean()))
            previous = small
    finally:
        capture.release()
    if len(diffs) < 2:
        return None, None
    values = np.array(diffs)
    median = max(float(np.median(values)), median_floor)
    if median <= 0:
        return None, None
    worst = int(values.argmax())
    return round(float(values[worst]) / median, 1), round((worst + 1) / (fps or 24.0), 2)


def check_fitroom(
    render_clip: Path,
    render_mask: np.ndarray,
    fit_frame: Path,
    video: Path,
    config: CheckConfig,
    fitroom: FitRoomConfig,
) -> FitRoomRun:
    """Raises: ValueError — 영상·그림을 읽지 못했다(판정 전 입력 오류)."""
    render_frames = read_frames(Path(render_clip), [0])
    if 0 not in render_frames:
        raise ValueError(f"렌더 클립의 첫 프레임을 읽지 못했습니다: {render_clip}")
    render = render_frames[0]
    if render_mask.shape != render.shape[:2]:
        raise ValueError(
            f"렌더 옷 마스크 크기 {render_mask.shape[::-1]}가 렌더 {render.shape[1::-1]}와 다릅니다"
        )
    fit_bgr = cv2.imdecode(np.frombuffer(Path(fit_frame).read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    if fit_bgr is None:
        raise ValueError(f"핏 확인 사진을 읽지 못했습니다: {fit_frame}")
    fit_result, mapped, found, info = measure_fit_frame(
        render, render_mask, fit_bgr, config, fitroom.align
    )
    video_measure, frames, times = measure_video(video, fitroom.video)
    result = judge_fitroom(
        fit_result,
        video_measure,
        fitroom.video,
        details={"fit_frame_size": list(fit_bgr.shape[1::-1])},
    )
    return FitRoomRun(
        result=result,
        render_frame=render,
        fit_frame=mapped,
        fit_mask=found if found.any() else None,
        render_mask=render_mask,
        video_frames=frames,
        video_times=times,
        segment_info=info,
    )


def fitroom_sheet(run: FitRoomRun, height: int = 300) -> np.ndarray:
    """사람 확인용 그림: 렌더 · 핏 확인 사진(초록 = 렌더 마스크, 자주 = 찾은 옷) · 영상 1초
    간격."""
    from .visual_measure import _outline  # 같은 그리기 도구

    render = run.render_frame.copy()
    fit = run.fit_frame.copy()
    _outline(render, run.render_mask, (60, 200, 60))
    _outline(fit, run.render_mask, (60, 200, 60))
    _outline(fit, run.fit_mask, (220, 60, 220))
    tiles = [render, fit, *run.video_frames]
    labels = ["render", "fit photo", *[f"{t:g}s" for t in run.video_times]]
    resized = []
    for tile, label in zip(tiles, labels, strict=True):
        image = cv2.resize(tile, (round(tile.shape[1] * height / tile.shape[0]), height))
        for color, thick in (((255, 255, 255), 3), ((0, 0, 0), 1)):
            cv2.putText(
                image, label, (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, thick, cv2.LINE_AA
            )
        resized.append(image)
    gap = np.full((height, 6, 3), 255, np.uint8)
    sheet = resized[0]
    for image in resized[1:]:
        sheet = np.hstack([sheet, gap, image])
    header = np.full((28, sheet.shape[1], 3), 255, np.uint8)
    result = run.result
    cv2.putText(
        header,
        f"{result.status}  " + ", ".join(result.fails or result.unverifiable),
        (6, 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (0, 0, 0),
        1,
        cv2.LINE_AA,
    )
    return np.vstack([header, sheet])
