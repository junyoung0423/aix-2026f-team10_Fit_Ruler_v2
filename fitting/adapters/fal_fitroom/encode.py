"""피팅룸 회전 영상의 맨 위 자르기 (#10).

Veo 회전 영상은 옆모습에서 턱 끝·수염이 화면 맨 위에 걸칠 때가 있다(q23: 옆모습 두 곳, 가장 깊게
15px. 턱 금지 문장이 없던 q18은 45px). 그래서 받은 영상의 맨 위 `px`줄을 잘라 낸다. 화면 크기를
지키려고 같은 배율(높이 / (높이 - px))로 키운 뒤 가로 가운데를 원래 폭으로 맞춘다. 아래 끝(발 쪽)은
그대로라 사람이 조금 커지고(720×1280에서 32px면 2.6 %) 위로 조금 올라간다.

H.264는 Blender 내장 FFmpeg로 쓴다(`blender_encode.py`, 렌더 클립 C5와 같은 방식). pip OpenCV는
H.264를 쓰려면 OpenH264 DLL이 따로 있어야 하고, 없으면 MPEG-4 Part 2(mp4v)만 쓰는데 브라우저가 이를
틀지 못한다. 영상 경로에는 착장·렌더(C4·C5) 때문에 Blender가 이미 있어야 한다.
"""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np

from fitting.adapters.blender.renderer import Runner, RunOutcome, subprocess_runner

__all__ = [
    "DEFAULT_TOP_CROP_PX",
    "ENCODE_SCRIPT",
    "ENCODE_TIMEOUT_S",
    "TOP_CROP_MAX_PX",
    "RunOutcome",
    "Runner",
    "crop_geometry",
    "crop_top",
    "encode_command",
    "encode_h264",
    "subprocess_runner",
    "write_cropped_frames",
]

# 기본값: q23에서 수염 끝이 가장 깊게 들어온 곳(15px)의 두 배쯤(docs/realize-fitroom.md 2.8절)
DEFAULT_TOP_CROP_PX = 32
TOP_CROP_MAX_PX = 128  # 720p 세로 영상 높이(1280)의 10 %. 설정 실수로 화면을 크게 자르지 않게
TOP_CROP_MAX_FRACTION = 0.25  # 실제 영상 높이에 대한 상한(작은 시험 영상도 받게 넉넉히)
ENCODE_SCRIPT = Path(__file__).resolve().with_name("blender_encode.py")
REPO_ROOT = Path(__file__).resolve().parents[3]
ENCODE_TIMEOUT_S = 300.0  # 8초 720p 192프레임은 Blender 시작을 포함해 약 30~40초 걸린다
# 중간 프레임은 무압축 BMP로 쓴다. 720×1280에서 PNG(압축 1)는 장당 약 107 ms, BMP는 약 4 ms라
# 192프레임에서 약 20초 차이가 난다. 대신 임시 파일이 약 0.5 GB라 시스템 임시 폴더에 쓰고
# 바로 지운다
FRAME_NAME = "f{:05d}.bmp"


def crop_geometry(width: int, height: int, px: int) -> dict[str, float | int]:
    """맨 위 `px`줄을 자른 뒤 키우는 배율과 좌우로 잘리는 폭.

    Raises:
        ValueError: 자를 줄 수가 1보다 작거나 영상 높이의 25 %를 넘는다.
    """
    limit = int(height * TOP_CROP_MAX_FRACTION)
    if px < 1 or px > limit:
        raise ValueError(f"맨 위 자르기는 1~{limit}px이어야 합니다(영상 높이 {height}): {px}")
    scale = height / (height - px)
    scaled_width = round(width * scale)
    return {
        "px": px,
        "scale": round(scale, 4),
        "scaled_width": scaled_width,
        "side_crop_px": (scaled_width - width) // 2,
    }


def crop_top(frame: np.ndarray, px: int) -> np.ndarray:
    """맨 위 `px`줄을 자르고 같은 배율로 키워 원래 크기로 맞춘다(아래 끝 고정, 가로 가운데).

    Raises:
        ValueError: 자를 줄 수가 틀렸다(`crop_geometry`).
    """
    height, width = frame.shape[:2]
    geometry = crop_geometry(width, height, px)
    scaled = cv2.resize(
        frame[px:], (int(geometry["scaled_width"]), height), interpolation=cv2.INTER_LANCZOS4
    )
    x0 = int(geometry["side_crop_px"])
    return np.ascontiguousarray(scaled[:, x0 : x0 + width])


def write_cropped_frames(video: Path, frames_dir: Path, px: int) -> dict[str, float | int]:
    """영상을 처음부터 끝까지 읽어 프레임마다 `crop_top`한 BMP를 `frames_dir`에 쓴다.

    한 장씩 읽고 쓴다(8초 720p를 한꺼번에 들고 있으면 0.5 GB가 넘는다). BMP는 손실이 없다.

    Returns:
        자르기 값(`crop_geometry`)과 프레임 수·fps·크기.

    Raises:
        ValueError: 영상을 열 수 없거나 프레임이 없거나 자를 줄 수가 틀렸다.
        OSError: 그림을 쓰지 못했다.
    """
    capture = cv2.VideoCapture(str(video))
    count = 0
    geometry: dict[str, float | int] | None = None
    size = (0, 0)
    try:
        if not capture.isOpened():
            raise ValueError(f"영상을 열지 못했습니다: {video}")
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frames_dir.mkdir(parents=True, exist_ok=True)
        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            if geometry is None:
                size = (frame.shape[1], frame.shape[0])
                geometry = crop_geometry(size[0], size[1], px)
            ok, encoded = cv2.imencode(".bmp", crop_top(frame, px))
            if not ok:
                raise OSError(f"프레임을 BMP로 만들지 못했습니다: {count}")
            (frames_dir / FRAME_NAME.format(count)).write_bytes(encoded.tobytes())
            count += 1
    finally:
        capture.release()
    if geometry is None:
        raise ValueError(f"영상에 프레임이 없습니다: {video}")
    return {**geometry, "frames": count, "fps": fps or 24.0, "width": size[0], "height": size[1]}


def encode_command(blender: Path, frames_dir: Path, out: Path, fps: float) -> list[str]:
    """프레임 폴더(BMP·PNG)를 H.264 MP4로 쓰는 Blender 명령.

    폴더·출력은 절대 경로로 넘긴다. Blender의 그림 읽기는 상대 경로를 실행 위치(cwd)가 아닌 곳에
    맞춰 풀어서, 작업 폴더가 상대 경로면 프레임을 찾지 못했다.
    """
    return [
        str(blender),
        "--background",
        "--factory-startup",
        "--python-exit-code",
        "1",
        "--python",
        str(ENCODE_SCRIPT),
        "--",
        str(Path(frames_dir).resolve()),
        str(Path(out).resolve()),
        f"{fps:g}",
    ]


def encode_h264(
    blender: Path,
    frames_dir: Path,
    out: Path,
    fps: float,
    *,
    log_path: Path,
    runner: Runner = subprocess_runner,
    timeout_s: float = ENCODE_TIMEOUT_S,
) -> RunOutcome:
    """Blender로 `frames_dir`의 프레임을 무음 H.264 MP4 `out`으로 쓴다. 판정은 부르는 쪽이 한다."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    command = encode_command(blender, frames_dir, out, fps)
    return runner(command, REPO_ROOT, env, timeout_s, log_path)
