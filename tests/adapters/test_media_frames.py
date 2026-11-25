from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2")  # 선택 의존성 realize가 없으면 건너뛴다
np = pytest.importorskip("numpy")

from fitting.adapters.media.frames import (  # noqa: E402 (cv2가 있을 때만 불러온다)
    candidate_range,
    read_frame,
    read_video_info,
    save_frame_pair,
    save_representative_frame,
    write_image,
)

FPS = 24.0
SIZE = (64, 48)  # (너비, 높이)


def write_clip(path: Path, frames: list[np.ndarray], fps: float = FPS) -> Path:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, SIZE)
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


def test_korean_path_with_space(tmp_path: Path) -> None:
    """저장소가 '바탕 화면' 같은 한글·공백 경로에 있어도 영상 읽기와 프레임 저장이 된다."""
    folder = tmp_path / "바탕 화면" / "결과"
    folder.mkdir(parents=True)
    clip = write_clip(folder / "클립.mp4", [flat(90)] * 10)

    assert read_video_info(clip).frame_count == 10
    result = save_representative_frame(clip, folder / "대표 프레임.png")
    assert result.path.exists() and result.path.stat().st_size > 0
    decoded = cv2.imdecode(np.frombuffer(result.path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == (SIZE[1], SIZE[0], 3)


def test_middle_method(tmp_path: Path) -> None:
    clip = write_clip(tmp_path / "clip.mp4", [flat(80)] * 11)
    result = save_representative_frame(clip, tmp_path / "frame.png", method="middle")
    assert result.index == 5


def test_unreadable_file(tmp_path: Path) -> None:
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    with pytest.raises(ValueError):
        read_video_info(broken)


# --- D5: 프레임 읽기·저장과 대응 렌더 프레임 ---------------------------------------------------


def numbered(count: int) -> list[np.ndarray]:
    """프레임 번호를 밝기로 적은 프레임들(번호 i → 밝기 20 + 8i). 압축 오차보다 간격이 크다."""
    return [flat(20 + 8 * i) for i in range(count)]


def brightness_index(frame: np.ndarray) -> int:
    return round((float(frame.mean()) - 20) / 8)


def test_read_frame_returns_the_frame_at_that_index(tmp_path: Path) -> None:
    clip = write_clip(tmp_path / "clip.mp4", numbered(20))
    for index in (0, 7, 19):
        assert brightness_index(read_frame(clip, index)) == index
    with pytest.raises(ValueError, match="20번 프레임이 없습니다"):
        read_frame(clip, 20)
    with pytest.raises(ValueError, match="0 이상"):
        read_frame(clip, -1)


def test_write_image_leaves_no_partial_file(tmp_path: Path) -> None:
    path = write_image(tmp_path / "바탕 화면" / "frame.png", flat(70))
    assert path.is_file()
    assert not list(path.parent.glob("*.part"))
    decoded = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == (SIZE[1], SIZE[0], 3)


def test_frame_pair_matches_by_time_when_lengths_and_fps_differ(tmp_path: Path) -> None:
    # 렌더 30fps 24프레임(0.8초), 실사 24fps 13프레임(0.54초). 실사 대표 프레임은 가운데 구간에서
    # 가장 선명한 프레임(9번 = 0.375초) → 렌더 0.375초 × 30 = 11.25 → 11번
    real_frames = [flat(120) for _ in range(13)]
    real_frames[9] = checkerboard()
    real = write_clip(tmp_path / "real.mp4", real_frames, fps=24.0)
    render = write_clip(tmp_path / "render.mp4", numbered(24), fps=30.0)

    out = tmp_path / "out"
    pair = save_frame_pair(real, render, out / "frame.png", out / "rf.png")

    assert pair.real.index == 9
    assert pair.match.render_index == 11
    assert pair.match.mode == "time" and not pair.match.clamped
    saved = cv2.imdecode(np.frombuffer(pair.render_path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    assert brightness_index(saved) == 11
    data = pair.to_dict()
    assert data["render_frame"]["index"] == 11
    assert data["coverage"]["real_longer"] is False
    assert data["aspect_gap"] == 0
    assert data["real_video"]["fps"] == pytest.approx(24.0)
    assert data["render_clip"]["frame_count"] == 24


def test_frame_pair_stretch_mode(tmp_path: Path) -> None:
    real = write_clip(tmp_path / "real.mp4", [flat(90)] * 11)
    render = write_clip(tmp_path / "render.mp4", numbered(21))
    pair = save_frame_pair(
        real, render, tmp_path / "f.png", tmp_path / "r.png", method="middle", mode="stretch"
    )
    assert pair.real.index == 5  # 가운데
    assert pair.match.render_index == 10  # 5/10 × 20
    saved = cv2.imdecode(np.frombuffer(pair.render_path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    assert brightness_index(saved) == 10


def test_frame_pair_rejects_an_unreadable_render(tmp_path: Path) -> None:
    real = write_clip(tmp_path / "real.mp4", [flat(90)] * 5)
    broken = tmp_path / "render.mp4"
    broken.write_bytes(b"not a video")
    with pytest.raises(ValueError):
        save_frame_pair(real, broken, tmp_path / "f.png", tmp_path / "r.png")
