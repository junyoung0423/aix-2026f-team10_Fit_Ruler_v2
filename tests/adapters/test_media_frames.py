from pathlib import Path

import cv2
import numpy as np
import pytest

from fitting.adapters.media.frames import (
    candidate_range,
    read_video_info,
    save_representative_frame,
)

FPS = 24.0
SIZE = (64, 48)  # (너비, 높이)


def write_clip(path: Path, frames: list[np.ndarray]) -> Path:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, SIZE)
    assert writer.isOpened()
    for frame in frames:
        writer.write(frame)
    writer.release()
    return path


def flat(value: int) -> np.ndarray:
    return np.full((SIZE[1], SIZE[0], 3), value, dtype=np.uint8)


def checkerboard() -> np.ndarray:
    tile = (np.indices((SIZE[1], SIZE[0])).sum(axis=0) // 4 % 2 * 255).astype(np.uint8)
    return cv2.cvtColor(tile, cv2.COLOR_GRAY2BGR)


def test_read_video_info(tmp_path: Path) -> None:
    clip = write_clip(tmp_path / "clip.mp4", [flat(100)] * 30)
    info = read_video_info(clip)
    assert (info.width, info.height) == SIZE
    assert info.fps == pytest.approx(FPS)
    assert info.frame_count == 30
    assert info.to_dict()["duration_s"] == pytest.approx(1.25)


def test_candidate_range() -> None:
    assert candidate_range(100, "middle") == range(50, 51)
    assert candidate_range(100, "sharpest") == range(20, 80)
    assert candidate_range(3, "sharpest") == range(1, 2)
    with pytest.raises(ValueError):
        candidate_range(0, "sharpest")


def test_sharpest_frame_is_chosen_inside_middle_range(tmp_path: Path) -> None:
    frames = [flat(120) for _ in range(30)]
    frames[2] = checkerboard()  # 앞 20% 안: 후보가 아니다
    frames[17] = checkerboard()  # 가운데 구간: 이 프레임이 뽑혀야 한다
    clip = write_clip(tmp_path / "clip.mp4", frames)

    result = save_representative_frame(clip, tmp_path / "out" / "frame.png")

    assert result.index == 17
    assert result.time_s == pytest.approx(17 / FPS, abs=1e-3)
    assert result.path.exists()
    saved = cv2.imread(str(result.path))
    assert saved.shape == (SIZE[1], SIZE[0], 3)


def test_middle_method(tmp_path: Path) -> None:
    clip = write_clip(tmp_path / "clip.mp4", [flat(80)] * 11)
    result = save_representative_frame(clip, tmp_path / "frame.png", method="middle")
    assert result.index == 5


def test_unreadable_file(tmp_path: Path) -> None:
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    with pytest.raises(ValueError):
        read_video_info(broken)
