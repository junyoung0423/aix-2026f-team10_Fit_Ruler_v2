"""D6 테스트용 합성 영상: C5처럼 회색 배경·회색 마네킹에 시연 티셔츠 1(navy #24324f)을 입힌 정지
렌더 클립과 옷 마스크, 그리고 그것을 흉내 낸 "실사" 영상(피부색 팔, 흐림·잡음, 배경 밝기 차이).

통과·실패 기준 사례(명세서 7장 기준)
- `same`: 렌더와 같은 옷 → PASS
- `recolor`: 옷 색이 바뀜(빨강) → FAIL(대표색)
- `wider`: 옷 폭이 12 % 넓어짐 → FAIL(가슴·밑단 폭)
- 일부 프레임만 바뀜(`bad_frames`) → 실패 프레임 수에 따라 PASS·FAIL
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import cv2
import numpy as np

RENDER_SIZE = (192, 256)  # C5 768x1024의 1/4
REAL_SIZE = (180, 240)  # fal 720x960의 1/4(같은 화면비)
FRAMES = 25  # 8k + 1
FPS = 24.0

NAVY = (0x4F, 0x32, 0x24)  # BGR, #24324f
RED = (0x30, 0x30, 0xC0)
BACKGROUND = (128, 128, 128)  # #808080
MANNEQUIN = (154, 154, 154)  # #9a9a9a
SKIN = (150, 178, 214)


def _tee(width: int, height: int, widen: float) -> np.ndarray:
    """티셔츠 다각형(목 아래 정면). 비율 좌표를 픽셀로 바꾼다. `widen`은 가운데 기준 가로 배율."""
    points = [
        (-0.14, 0.04), (0.14, 0.04), (0.33, 0.12), (0.44, 0.33), (0.31, 0.38), (0.26, 0.28),
        (0.26, 0.62), (-0.26, 0.62), (-0.26, 0.28), (-0.31, 0.38), (-0.44, 0.33), (-0.33, 0.12),
    ]  # fmt: skip
    cx = width / 2
    return np.array(
        [(round(cx + x * widen * width), round(y * height)) for x, y in points], dtype=np.int32
    )


def draw(
    size: tuple[int, int],
    *,
    real: bool,
    garment: tuple[int, int, int] = NAVY,
    widen: float = 1.0,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """장면 한 장과 옷 마스크(bool). `real`이면 피부색 팔·배경 차이·주름 음영·흐림·잡음을 넣는다."""
    width, height = size
    frame = np.full((height, width, 3), (124, 124, 124) if real else BACKGROUND, np.uint8)
    body = SKIN if real else MANNEQUIN
    cx = width // 2
    cv2.rectangle(frame, (cx - width // 12, 0), (cx + width // 12, height // 10), body, -1)  # 목
    for side in (-1, 1):  # 팔
        arm = [(0.36, 0.20), (0.46, 0.34), (0.40, 0.70), (0.33, 0.70)]
        pts = np.array([(round(cx + side * x * width), round(y * height)) for x, y in arm])
        cv2.fillPoly(frame, [pts.astype(np.int32)], body)
    legs = (90, 90, 96) if real else body  # 실사는 어두운 회색 바지(옷과 다른 색)
    for x0, x1 in ((-0.20, -0.03), (0.03, 0.20)):  # 다리(C5 렌더 아래쪽)
        cv2.rectangle(
            frame,
            (round(cx + x0 * width), round(0.60 * height)),
            (round(cx + x1 * width), height - 1),
            legs,
            -1,
        )
    tee = _tee(width, height, widen)
    cv2.fillPoly(frame, [tee], garment)
    mask = np.zeros((height, width), np.uint8)
    cv2.fillPoly(mask, [tee], 255)
    if real:
        shade = tuple(max(0, c - 18) for c in garment)
        for x in (-0.12, 0.03, 0.15):  # 주름 음영
            cv2.line(frame, (round(cx + x * width), round(0.30 * height)),
                     (round(cx + (x + 0.02) * width), round(0.58 * height)), shade, 2)
        frame = cv2.GaussianBlur(frame, (3, 3), 0)
        rng = np.random.default_rng(seed)
        noise = rng.normal(0, 2.0, frame.shape)
        frame = np.clip(frame.astype(np.float64) + noise, 0, 255).astype(np.uint8)
    return frame, mask > 0


def write_clip(path: Path, frames: Iterable[np.ndarray], fps: float = FPS) -> Path:
    frames = list(frames)
    height, width = frames[0].shape[:2]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        raise OSError(f"영상을 쓸 수 없습니다: {path}")
    for frame in frames:
        writer.write(frame)
    writer.release()
    return path


def write_mask(path: Path, mask: np.ndarray) -> Path:
    ok, encoded = cv2.imencode(".png", mask.astype(np.uint8) * 255)
    assert ok
    path.write_bytes(encoded.tobytes())
    return path


def render_clip(path: Path, frames: int = FRAMES) -> tuple[Path, np.ndarray]:
    """C5처럼 같은 프레임을 반복한 정지 렌더 클립과 옷 마스크."""
    frame, mask = draw(RENDER_SIZE, real=False)
    return write_clip(path, [frame] * frames), mask


def real_clip(
    path: Path,
    *,
    frames: int = FRAMES,
    size: tuple[int, int] = REAL_SIZE,
    garment: tuple[int, int, int] = NAVY,
    widen: float = 1.0,
    bad_frames: Iterable[int] = (),
    bad_garment: tuple[int, int, int] = RED,
    fps: float = FPS,
) -> Path:
    """실사 영상 흉내. `bad_frames` 번호의 프레임만 옷 색을 `bad_garment`로 바꾼다."""
    bad = set(bad_frames)
    images = [
        draw(size, real=True, garment=bad_garment if i in bad else garment, widen=widen, seed=i)[0]
        for i in range(frames)
    ]
    return write_clip(path, images, fps=fps)
