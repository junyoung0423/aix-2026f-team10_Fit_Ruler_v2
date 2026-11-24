"""영상 정보 읽기, 대표 프레임 추출, 대응 렌더 프레임 저장 (OpenCV).

- D1: 렌더 클립과 실사화 결과의 대표 프레임을 뽑는다(`save_representative_frame`).
- D5: 실사 영상의 대표 프레임과, 그 시각에 대응하는 렌더 클립 프레임을 함께 저장한다
  (`save_frame_pair`). 대응 계산(길이·fps 차이 보정)은 순수 로직 `fitting.verify.frame_match`가
  한다.
- D6: 11프레임을 한 번에 읽는다(`read_frames`). 색·실루엣 측정은 `visual_measure`.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import cv2
import numpy as np

from fitting.verify.frame_match import ClipTiming, FrameMatch, coverage, match_render_frame

FrameMethod = Literal["middle", "sharpest"]

# sharpest 방식에서 앞뒤로 빼는 비율. 생성 영상은 처음과 끝이 흔들리거나 흐린 경우가 많다.
EDGE_RATIO = 0.2


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    fps: float
    frame_count: int

    @property
    def duration_s(self) -> float:
        return self.frame_count / self.fps if self.fps > 0 else 0.0

    def to_dict(self) -> dict[str, float | int]:
        return {**asdict(self), "duration_s": round(self.duration_s, 3)}


@dataclass(frozen=True)
class RepresentativeFrame:
    index: int
    time_s: float
    method: FrameMethod
    sharpness: float
    path: Path

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "path": self.path.as_posix()}


def _open(path: Path) -> cv2.VideoCapture:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"영상을 열 수 없습니다: {path}")
    return capture


def write_image(path: Path, frame: np.ndarray) -> Path:
    """프레임을 이미지 파일로 쓴다(확장자로 형식을 정하고, 없으면 PNG).

    cv2.imwrite는 Windows에서 한글이 든 경로(예: 바탕 화면)에 실패하므로 인코딩 후 직접 쓴다.
    다 쓴 뒤에만 그 이름으로 바꾼다(`.part` → 최종). 산출물 ID가 상태에 보일 때 반쯤 쓴 파일이 없게.
    """
    ok, encoded = cv2.imencode(path.suffix or ".png", frame)
    if not ok:
        raise OSError(f"프레임을 저장하지 못했습니다: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    partial.write_bytes(encoded.tobytes())
    os.replace(partial, path)
    return path


def read_frame(path: Path, index: int) -> np.ndarray:
    """`index`번 프레임(0부터). 위치 이동(`CAP_PROP_POS_FRAMES`)은 코덱에 따라 어긋나서 앞에서부터
    차례로 넘긴다. 클립이 짧아(수 초) 충분히 빠르다."""
    if index < 0:
        raise ValueError(f"프레임 번호는 0 이상이어야 합니다: {index}")
    capture = _open(path)
    try:
        for _ in range(index):
            if not capture.grab():
                raise ValueError(f"{index}번 프레임이 없습니다: {path}")
        ok, frame = capture.read()
    finally:
        capture.release()
    if not ok or frame is None:
        raise ValueError(f"{index}번 프레임이 없습니다: {path}")
    return frame


def read_frames(path: Path, indices: Iterable[int]) -> dict[int, np.ndarray]:
    """여러 프레임을 한 번에 앞에서부터 읽는다(`read_frame`과 같은 방식, D6 11프레임용).

    Raises:
        ValueError: 영상을 열 수 없거나 없는 번호가 있다.
    """
    wanted = sorted(set(indices))
    if not wanted:
        return {}
    if wanted[0] < 0:
        raise ValueError(f"프레임 번호는 0 이상이어야 합니다: {wanted[0]}")
    frames: dict[int, np.ndarray] = {}
    capture = _open(path)
    try:
        position = 0
        for index in wanted:
            while position < index:
                if not capture.grab():
                    raise ValueError(f"{index}번 프레임이 없습니다: {path}")
                position += 1
            ok, frame = capture.read()
            if not ok or frame is None:
                raise ValueError(f"{index}번 프레임이 없습니다: {path}")
            frames[index] = frame
            position += 1
    finally:
        capture.release()
    return frames


def read_image(path: Path, flags: int = cv2.IMREAD_COLOR) -> np.ndarray:
    """이미지 파일을 읽는다. 한글 경로 때문에 `cv2.imread` 대신 바이트로 읽어 디코딩한다.

    Raises:
        OSError: 파일을 읽을 수 없다.
        ValueError: 이미지로 디코딩할 수 없다.
    """
    image = cv2.imdecode(np.frombuffer(Path(path).read_bytes(), np.uint8), flags)
    if image is None:
        raise ValueError(f"이미지를 읽지 못했습니다: {path}")
    return image


def read_video_info(path: Path) -> VideoInfo:
    """크기·fps·프레임 수. 프레임 수는 컨테이너에 적힌 값 대신 실제로 읽어서 센다."""
    capture = _open(path)
    try:
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        count = 0
        while capture.grab():
            count += 1
    finally:
        capture.release()
    return VideoInfo(width=width, height=height, fps=fps, frame_count=count)


def sharpness(frame: np.ndarray) -> float:
    """라플라시안 분산. 클수록 선명하다."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def candidate_range(frame_count: int, method: FrameMethod) -> range:
    """대표 프레임 후보 번호.

    - middle: 가운데 프레임 하나
    - sharpest: 앞뒤 20%를 뺀 가운데 구간 (프레임이 5개 미만이면 가운데 하나)
    """
    if frame_count <= 0:
        raise ValueError("프레임이 없는 영상입니다.")
    middle = frame_count // 2
    if method == "middle" or frame_count < 5:
        return range(middle, middle + 1)
    return range(int(frame_count * EDGE_RATIO), int(frame_count * (1 - EDGE_RATIO)))


def save_representative_frame(
    video_path: Path, out_path: Path, method: FrameMethod = "sharpest"
) -> RepresentativeFrame:
    """대표 프레임을 PNG로 저장하고 그 정보를 돌려준다. 영상은 한 프레임씩 읽어 메모리를 아낀다."""
    info = read_video_info(video_path)
    candidates = candidate_range(info.frame_count, method)

    best: tuple[int, float, np.ndarray] | None = None
    capture = _open(video_path)
    try:
        for index in range(candidates.stop):
            ok, frame = capture.read()
            if not ok:
                break
            if index < candidates.start:
                continue
            score = sharpness(frame)
            if best is None or score > best[1]:
                best = (index, score, frame)
    finally:
        capture.release()
    if best is None:
        raise ValueError(f"대표 프레임을 읽지 못했습니다: {video_path}")

    index, score, frame = best
    write_image(out_path, frame)
    return RepresentativeFrame(
        index=index,
        time_s=round(index / info.fps, 3) if info.fps > 0 else 0.0,
        method=method,
        sharpness=round(score, 2),
        path=out_path,
    )


@dataclass(frozen=True)
class FramePair:
    """실사 대표 프레임과 대응 렌더 프레임 (D5). `VALIDATE_OUTPUTS`의 두 파일과 그 근거."""

    real: RepresentativeFrame
    render_path: Path
    match: FrameMatch
    real_info: VideoInfo
    render_info: VideoInfo

    @property
    def aspect_gap(self) -> float:
        """가로/세로 비율 차이. 0이면 같은 구도로 늘이거나 줄인 크기다(D1: 540×720 → 576×768)."""
        real = self.real_info.width / self.real_info.height
        render = self.render_info.width / self.render_info.height
        return abs(real - render)

    def to_dict(self) -> dict[str, object]:
        timing = coverage(
            ClipTiming(self.real_info.fps, self.real_info.frame_count),
            ClipTiming(self.render_info.fps, self.render_info.frame_count),
        )
        return {
            "real_frame": self.real.to_dict(),
            "render_frame": {
                "index": self.match.render_index,
                "time_s": self.match.render_time_s,
                "path": self.render_path.as_posix(),
            },
            "match": self.match.to_dict(),
            "real_video": self.real_info.to_dict(),
            "render_clip": self.render_info.to_dict(),
            "coverage": timing,
            "aspect_gap": round(self.aspect_gap, 4),
        }


def save_frame_pair(
    real_video: Path,
    render_clip: Path,
    real_out: Path,
    render_out: Path,
    *,
    method: FrameMethod = "sharpest",
    mode: str = "time",
) -> FramePair:
    """실사 영상의 대표 프레임을 고르고, 그 시각에 대응하는 렌더 프레임과 함께 저장한다.

    두 프레임은 각 영상의 원래 크기 그대로 저장한다. 크기 맞춤은 비교하는 쪽(D6)이 한다.

    Raises:
        ValueError: 영상을 열 수 없거나, 프레임이 없거나, fps를 모른다.
        OSError: 파일을 쓰지 못했다.
    """
    real_info = read_video_info(real_video)
    render_info = read_video_info(render_clip)
    real_timing = ClipTiming(real_info.fps, real_info.frame_count)
    render_timing = ClipTiming(render_info.fps, render_info.frame_count)
    real = save_representative_frame(real_video, real_out, method)
    match = match_render_frame(real.index, real_timing, render_timing, mode)
    write_image(render_out, read_frame(render_clip, match.render_index))
    return FramePair(
        real=real,
        render_path=render_out,
        match=match,
        real_info=real_info,
        render_info=render_info,
    )
