"""영상 정보 읽기와 대표 프레임 추출 (OpenCV).

D1 스파이크에서 렌더 클립과 실사화 결과의 대표 프레임을 뽑는 데 쓴다.
대응 프레임 매칭(길이·fps 차이 보정)은 D5에서 이 위에 만든다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import cv2
import numpy as np

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
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # cv2.imwrite는 Windows에서 한글이 든 경로(예: 바탕 화면)에 실패하므로 인코딩 후 직접 쓴다.
    ok, encoded = cv2.imencode(out_path.suffix or ".png", frame)
    if not ok:
        raise OSError(f"프레임을 저장하지 못했습니다: {out_path}")
    out_path.write_bytes(encoded.tobytes())
    return RepresentativeFrame(
        index=index,
        time_s=round(index / info.fps, 3) if info.fps > 0 else 0.0,
        method=method,
        sharpness=round(score, 2),
        path=out_path,
    )
