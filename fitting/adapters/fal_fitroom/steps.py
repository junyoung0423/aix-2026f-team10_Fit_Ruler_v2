"""피팅룸 실사화 단계별 fal 모델·설정·가격과 프레임 처리.

가격은 fal 모델 목록(2026-11-25)에서 확인한 값이다. 예상 비용은 제출 전 상한 확인에 쓰고, 실제
비용은
② 핏 옮기기만 내려받은 영상 크기로 다시 계산한다(나머지는 정해진 값).

| 단계 | fal 모델 | 가격 |
|---|---|---|
| ① 사진·③ 자세 | `fal-ai/nano-banana-pro/edit` | 장당 $0.15(1K·2K) |
| ② 핏 옮기기 | `fal-ai/ltx-2.3-quality/render-to-real` | 메가픽셀당 $0.0024075 (`render_to_real`) |
| ④ 회전(기본 fast) | `fal-ai/veo3.1/fast/first-last-frame-to-video` | 초당 $0.10(소리 끔) |
| ④ 회전(standard) | `fal-ai/veo3.1/first-last-frame-to-video` | 초당 $0.20(소리 끔) |

④는 같은 조건 실험(2026-11-25)에서 Fast가 표준판과 같은 수준이라 Fast를 기본으로 둔다
(docs/realize-fitroom.md 2절).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np

from fitting.adapters.media.frames import read_frames, read_video_info

PHOTO_ENDPOINT = "fal-ai/nano-banana-pro/edit"
# ④ 회전 모델: 이름 → (fal 모델, 초당 가격). 입력 형식은 둘이 같다
TURN_MODELS = {
    "fast": ("fal-ai/veo3.1/fast/first-last-frame-to-video", 0.10),
    "standard": ("fal-ai/veo3.1/first-last-frame-to-video", 0.20),
}
DEFAULT_TURN_MODEL = "fast"
TURN_ENDPOINT = TURN_MODELS[DEFAULT_TURN_MODEL][0]
PRICE_CHECKED_ON = "2026-11-25"
PHOTO_USD = 0.15
TURN_DURATIONS_S = (4, 6, 8)

# 이 밝기(0~255)보다 어둡고 거의 고른 프레임만 있으면 검은 영상으로 본다. LTX 이미지→영상이 큰
# 요청에서
# 오류 없이 전부 검은 영상을 돌려준 일이 있다(실험 4회차, 평균 0·표준편차 0)
BLACK_MEAN_MAX = 8.0
BLACK_STD_MAX = 4.0
# render-to-real이 화면비를 맞추려고 좌우에 덧붙이는 검은 띠를 찾을 때 쓰는 열 밝기 상한
BAR_COLUMN_MEAN_MAX = 10.0


def photo_arguments(image_url: str, prompt: str, *, resolution: str = "2K") -> dict[str, Any]:
    """Nano Banana Pro 편집: 사진 1장, 3:4, PNG."""
    return {
        "prompt": prompt,
        "image_urls": [image_url],
        "num_images": 1,
        "aspect_ratio": "3:4",
        "resolution": resolution,
        "output_format": "png",
    }


def turn_arguments(
    frame_url: str,
    prompt: str,
    negative_prompt: str,
    *,
    duration_s: int = 8,
    resolution: str = "720p",
    seed: int | None = 42,
) -> dict[str, Any]:
    """Veo 3.1 시작·끝 프레임(Fast·표준 같은 형식): 시작과 끝을 같은 정면 사진으로, 소리는 끈다.

    Raises:
        ValueError: Veo가 받지 않는 길이다.
    """
    if duration_s not in TURN_DURATIONS_S:
        raise ValueError(f"Veo 길이는 {TURN_DURATIONS_S}초 중 하나여야 합니다: {duration_s}")
    arguments: dict[str, Any] = {
        "first_frame_url": frame_url,
        "last_frame_url": frame_url,
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "duration": f"{duration_s}s",
        "resolution": resolution,
        "aspect_ratio": "auto",
        "generate_audio": False,
    }
    if seed is not None:
        arguments["seed"] = seed
    return arguments


def turn_endpoint(model: str = DEFAULT_TURN_MODEL) -> str:
    """Raises: ValueError — 모르는 회전 모델이다."""
    if model not in TURN_MODELS:
        raise ValueError(f"회전 모델은 {sorted(TURN_MODELS)} 중 하나여야 합니다: {model}")
    return TURN_MODELS[model][0]


def turn_cost(duration_s: int, model: str = DEFAULT_TURN_MODEL) -> float:
    """Raises: ValueError — 모르는 회전 모델이다."""
    turn_endpoint(model)
    return round(duration_s * TURN_MODELS[model][1], 4)


# --- 프레임 ---------------------------------------------------------------------------------


def read_image(path: Path) -> np.ndarray:
    """Raises: ValueError — 그림으로 읽지 못했다(한글 경로 때문에 바이트로 읽는다)."""
    image = cv2.imdecode(np.frombuffer(Path(path).read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"그림을 읽지 못했습니다: {path}")
    return image


def write_png(path: Path, image: np.ndarray) -> Path:
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise OSError(f"그림을 저장하지 못했습니다: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    partial.write_bytes(encoded.tobytes())
    partial.replace(path)
    return path


def first_frame(video: Path) -> np.ndarray:
    """Raises: ValueError — 영상을 열 수 없거나 프레임이 없다."""
    capture = cv2.VideoCapture(str(video))
    try:
        ok, frame = capture.read()
    finally:
        capture.release()
    if not ok or frame is None:
        raise ValueError(f"영상의 첫 프레임을 읽지 못했습니다: {video}")
    return frame


def sample_frames(video: Path, count: int = 12) -> tuple[list[np.ndarray], dict[str, float]]:
    """영상 전체에서 고르게 `count`장(마지막 프레임 포함)과 정보(크기·fps·프레임 수).

    프레임 수는 실제로 읽어서 센다(`media.frames.read_video_info`).

    Raises:
        ValueError: 영상을 열 수 없거나 프레임이 없다.
    """
    info = read_video_info(Path(video))
    if info.frame_count <= 0:
        raise ValueError(f"영상에 프레임이 없습니다: {video}")
    last = info.frame_count - 1
    wanted = sorted({round(i * last / max(1, count - 1)) for i in range(count)} | {last})
    frames = read_frames(Path(video), wanted)
    return [frames[i] for i in wanted], {
        "width": info.width,
        "height": info.height,
        "fps": info.fps,
        "frame_count": info.frame_count,
    }


def is_black(frames: list[np.ndarray]) -> bool:
    """모든 프레임이 아주 어둡고 고르면 True(생성 실패로 본다)."""
    return all(float(f.mean()) < BLACK_MEAN_MAX and float(f.std()) < BLACK_STD_MAX for f in frames)


def crop_side_bars(frame: np.ndarray, aspect: float = 3 / 4) -> np.ndarray:
    """좌우 검은 띠를 자르고, 가운데에서 `aspect`(가로/세로)로 맞춘다(늘리지 않는다)."""
    columns = frame.mean(axis=(0, 2))
    bright = np.flatnonzero(columns > BAR_COLUMN_MEAN_MAX)
    left, right = (int(bright[0]), int(bright[-1]) + 1) if bright.size else (0, frame.shape[1])
    cropped = frame[:, left:right]
    h, w = cropped.shape[:2]
    target_w = round(h * aspect)
    if target_w < w:
        x0 = (w - target_w) // 2
        cropped = cropped[:, x0 : x0 + target_w]
    elif target_w > w:
        target_h = round(w / aspect)
        y0 = (h - target_h) // 2
        cropped = cropped[y0 : y0 + target_h]
    return np.ascontiguousarray(cropped)


def last_frame(video: Path) -> np.ndarray:
    """Raises: ValueError — 영상을 열 수 없거나 프레임이 없다."""
    frames, _ = sample_frames(video, count=1)
    return frames[-1]
