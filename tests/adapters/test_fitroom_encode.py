"""피팅룸 회전 영상 맨 위 자르기(`fal_fitroom.encode`) 테스트.

자르기 계산·프레임 쓰기·Blender 명령은 합성 영상으로 본다(비용 없음). 실제 Blender의 H.264 쓰기는
마커 blender로 한 번 본다(CI에서 빠짐, 로컬: pytest -m blender). Blender 경로: BLENDER_PATH >
BLENDER_EXE > 기본 설치 경로.
"""

import os
from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2")  # 선택 의존성 realize가 없으면 건너뛴다
np = pytest.importorskip("numpy")

from fitting.adapters.fal_fitroom.encode import (  # noqa: E402
    DEFAULT_TOP_CROP_PX,
    ENCODE_SCRIPT,
    crop_geometry,
    crop_top,
    encode_command,
    encode_h264,
    write_cropped_frames,
)
from fitting.adapters.fal_fitroom.steps import first_frame  # noqa: E402
from fitting.adapters.media.frames import read_video_info  # noqa: E402
from tests.adapters import visual_clips as vc  # noqa: E402

BLENDER = (
    os.environ.get("BLENDER_PATH")
    or os.environ.get("BLENDER_EXE")
    or r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
)


def test_default_crop_on_a_veo_frame_is_a_2_6_percent_zoom():
    assert DEFAULT_TOP_CROP_PX == 32  # q23 수염 끝 최대 15px의 두 배쯤
    assert crop_geometry(720, 1280, DEFAULT_TOP_CROP_PX) == {
        "px": 32,
        "scale": 1.0256,
        "scaled_width": 738,
        "side_crop_px": 9,
    }


def test_crop_top_removes_the_top_rows_and_keeps_the_frame_size():
    frame = np.full((1280, 720, 3), 128, np.uint8)
    frame[:32] = (0, 0, 255)  # 잘려 나갈 맨 위(빨강)
    frame[640:650] = (255, 255, 255)  # 흰 줄
    frame[-10:] = (255, 0, 0)  # 아래 끝(파랑)
    out = crop_top(frame, 32)

    assert out.shape == frame.shape
    assert np.abs(out[:5].astype(int) - 128).max() <= 2  # 빨강이 남지 않았다
    rows = np.where(out[:, 360, 1] > 200)[0]  # 흰 줄은 위로 올라가고 2.6 % 두꺼워진다
    assert abs(rows.mean() - ((644.5 - 32 + 0.5) * 1280 / 1248 - 0.5)) < 1.5
    assert out[-1, 360, 0] > 200 and out[-1, 360, 2] < 50  # 아래 끝은 그대로 아래 끝


def test_crop_limits():
    with pytest.raises(ValueError, match="맨 위 자르기"):
        crop_geometry(720, 1280, 0)
    with pytest.raises(ValueError, match="맨 위 자르기"):
        crop_geometry(180, 240, 61)  # 영상 높이의 25 %를 넘는다
    assert crop_geometry(180, 240, 32)["px"] == 32


def test_write_cropped_frames_writes_every_frame_as_bmp(tmp_path):
    video = vc.real_clip(tmp_path / "turn.mp4")
    info = read_video_info(video)
    result = write_cropped_frames(video, tmp_path / "frames", 32)

    files = sorted((tmp_path / "frames").glob("*.bmp"))
    assert len(files) == info.frame_count == result["frames"]
    assert (result["width"], result["height"]) == (info.width, info.height)
    assert result["fps"] == pytest.approx(vc.FPS)
    first = cv2.imdecode(np.frombuffer(files[0].read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    expected = crop_top(first_frame(video), 32)
    assert np.array_equal(first, expected)  # BMP라 손실이 없다


def test_write_cropped_frames_rejects_a_missing_video(tmp_path):
    with pytest.raises(ValueError, match="영상을 열지 못했습니다"):
        write_cropped_frames(tmp_path / "none.mp4", tmp_path / "frames", 32)


def test_encode_command_runs_the_blender_script_in_the_background(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cmd = encode_command(Path("blender.exe"), Path("work/frames"), Path("work/out.mp4"), 24.0)
    assert cmd[:5] == ["blender.exe", "--background", "--factory-startup", "--python-exit-code",
                       "1"]
    assert cmd[cmd.index("--python") + 1] == str(ENCODE_SCRIPT)
    # 상대 경로도 절대 경로로 넘긴다(Blender의 그림 읽기가 상대 경로를 cwd 기준으로 풀지 않는다)
    work = (tmp_path / "work").resolve()
    assert cmd[cmd.index("--") + 1 :] == [str(work / "frames"), str(work / "out.mp4"), "24"]
    assert ENCODE_SCRIPT.is_file()


@pytest.mark.blender
@pytest.mark.skipif(not Path(BLENDER).is_file(), reason="Blender 없음")
def test_real_blender_writes_an_h264_mp4_with_the_same_frames(tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir()
    images = []
    for i in range(12):
        image = np.full((256, 144, 3), 60 + i * 8, np.uint8)
        cv2.circle(image, (72, 40 + i * 14), 12, (40, 60, 200), -1)
        images.append(image)
        ok, buf = cv2.imencode(".bmp", image)  # 어댑터처럼 BMP로 넘긴다
        assert ok
        (frames / f"f{i:05d}.bmp").write_bytes(buf.tobytes())
    out = tmp_path / "out.mp4"

    outcome = encode_h264(Path(BLENDER), frames, out, 24.0, log_path=tmp_path / "encode.log",
                          timeout_s=180)

    assert outcome.returncode == 0 and not outcome.timed_out, (tmp_path / "encode.log").read_text(
        encoding="utf-8", errors="replace")
    info = read_video_info(out)
    assert (info.width, info.height, info.frame_count) == (144, 256, 12)
    assert info.fps == pytest.approx(24.0)
    assert b"avc1" in out.read_bytes()  # H.264(브라우저가 트는 형식)
    # 색을 바꾸지 않는다(보기 변환 Standard, sRGB). 남는 차이는 압축·색 변환 오차뿐이다
    assert np.abs(first_frame(out).astype(int) - images[0].astype(int)).mean() < 6
