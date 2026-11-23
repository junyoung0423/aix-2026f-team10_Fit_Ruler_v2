"""tools.match_frames 테스트 (D5). 작은 임시 영상만 쓴다(fal 호출 없음)."""

import json

import pytest

cv2 = pytest.importorskip("cv2")

from tools import match_frames as tool  # noqa: E402
from tools.spike_fal_ltx import write_test_clip  # noqa: E402


@pytest.fixture
def run_dir(tmp_path):
    """tools.realize_clip 결과 폴더와 같은 모양."""
    artifacts = tmp_path / "run" / "artifacts"
    artifacts.mkdir(parents=True)
    write_test_clip(artifacts / "r1_render_clip.mp4", seconds=3.0, size=(96, 128))
    write_test_clip(artifacts / "r1_video.mp4", seconds=65 / 24, size=(102, 136))
    return tmp_path / "run"


def test_run_folder_gives_the_frame_pair_and_a_compare_image(tmp_path, run_dir, capsys):
    out = tmp_path / "out"
    assert tool.main(["--run", str(run_dir), "--out-dir", str(out)]) == tool.EXIT_OK

    for name in ("frame.png", "render_frame.png", "compare.png"):
        assert (out / name).is_file()
    compare = cv2.imread(str(out / "compare.png"))
    assert compare.shape[0] == 512  # 같은 높이로 나란히
    record = json.loads((out / "frames.json").read_text(encoding="utf-8"))
    assert record["real"].endswith("r1_video.mp4")
    assert record["render"].endswith("r1_render_clip.mp4")
    assert record["match"]["render_index"] == record["real_frame"]["index"]
    assert record["coverage"]["duration_ratio"] == pytest.approx(0.9028)
    assert "렌더" in capsys.readouterr().out


def test_explicit_videos_and_stretch_mode(tmp_path, run_dir):
    artifacts = run_dir / "artifacts"
    out = tmp_path / "out"
    code = tool.main(
        [
            "--real",
            str(artifacts / "r1_video.mp4"),
            "--render",
            str(artifacts / "r1_render_clip.mp4"),
            "--mode",
            "stretch",
            "--method",
            "middle",
            "--out-dir",
            str(out),
        ]
    )
    assert code == tool.EXIT_OK
    record = json.loads((out / "frames.json").read_text(encoding="utf-8"))
    assert record["match"]["mode"] == "stretch"
    assert record["real_frame"]["index"] == 32  # 65프레임의 가운데
    assert record["match"]["render_index"] == 36  # 32/64 × 71 = 35.5 → 36


def test_missing_inputs_are_input_errors(tmp_path, capsys):
    empty = tmp_path / "empty"
    (empty / "artifacts").mkdir(parents=True)
    assert tool.main(["--run", str(empty)]) == tool.EXIT_INPUT
    assert "하나씩 찾지 못했습니다" in capsys.readouterr().err
    code = tool.main(["--real", str(tmp_path / "x.mp4"), "--render", str(tmp_path / "y.mp4")])
    assert code == tool.EXIT_INPUT
    with pytest.raises(SystemExit):
        tool.main(["--real", str(tmp_path / "x.mp4")])  # --render 없이
