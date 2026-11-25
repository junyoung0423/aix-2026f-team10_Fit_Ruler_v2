"""`tools.check_visual` 테스트(D6). 합성 영상만 쓰고 fal·Blender를 부르지 않는다."""

import json
from pathlib import Path

import pytest

pytest.importorskip("cv2")  # 선택 의존성 realize가 없으면 건너뛴다

from tests.adapters import visual_clips as vc  # noqa: E402
from tools import check_visual  # noqa: E402


@pytest.fixture(scope="module")
def clips(tmp_path_factory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("tool")
    render, mask = vc.render_clip(root / "render.mp4")
    return {
        "render": render,
        "mask": vc.write_mask(root / "mask.png", mask),
        "real": vc.real_clip(root / "real.mp4"),
        "wider": vc.real_clip(root / "wider.mp4", widen=1.12),
    }


def test_writes_the_contact_sheet_frames_and_record(tmp_path, clips, capsys):
    out = tmp_path / "out"
    code = check_visual.main(
        ["--real", str(clips["real"]), "--render", str(clips["render"]),
         "--mask", str(clips["mask"]), "--out-dir", str(out)]
    )  # fmt: skip
    assert code == check_visual.EXIT_OK
    for name in ("contact_sheet.png", "frame.png", "render_frame.png", "check.json"):
        assert (out / name).is_file()
    assert len(list((out / "masks").glob("real_*.png"))) == 11
    record = json.loads((out / "check.json").read_text(encoding="utf-8"))
    assert record["status"] == "PASS"
    assert record["config"]["source"] == "visual_check_v1@1.0.0"
    assert record["representative_frames"] == {"real_index": 12, "render_index": 12}
    printed = capsys.readouterr().out
    assert "판정: PASS" in printed and "결과 폴더" in printed


def test_failing_video_still_exits_zero_with_fail_verdict(tmp_path, clips, capsys):
    out = tmp_path / "out"
    code = check_visual.main(
        ["--real", str(clips["wider"]), "--render", str(clips["render"]),
         "--mask", str(clips["mask"]), "--out-dir", str(out)]
    )  # fmt: skip
    assert code == check_visual.EXIT_OK
    assert json.loads((out / "check.json").read_text(encoding="utf-8"))["status"] == "FAIL"
    assert "판정: FAIL" in capsys.readouterr().out


def test_run_folder_and_dark_mask_option(tmp_path, clips):
    run = tmp_path / "run"
    (run / "artifacts").mkdir(parents=True)
    (run / "artifacts" / "x_video.mp4").write_bytes(clips["real"].read_bytes())
    (run / "artifacts" / "x_render_clip.mp4").write_bytes(clips["render"].read_bytes())
    out = tmp_path / "out"
    # 마스크 파일이 없으면 입력 오류
    assert check_visual.main(["--run", str(run), "--out-dir", str(out)]) == check_visual.EXIT_INPUT
    # 렌더 navy 옷(R·G·B ≤ 80)을 프레임마다 옷으로 본다
    code = check_visual.main(
        ["--run", str(run), "--render-mask-dark", "80", "--out-dir", str(out)]
    )
    assert code == check_visual.EXIT_OK
    record = json.loads((out / "check.json").read_text(encoding="utf-8"))
    assert record["render_mask_dark"] == 80
    assert record["details"]["render_mask"] == "per_frame"
    # 폴더에 마스크가 있으면 그것을 쓴다
    (run / "artifacts" / "x_render_mask.png").write_bytes(clips["mask"].read_bytes())
    assert check_visual.main(["--run", str(run), "--out-dir", str(out)]) == check_visual.EXIT_OK
    assert json.loads((out / "check.json").read_text(encoding="utf-8"))["status"] == "PASS"


def test_input_errors(tmp_path, clips, capsys):
    missing = tmp_path / "none.mp4"
    args = ["--real", str(missing), "--render", str(clips["render"]), "--mask", str(clips["mask"])]
    assert check_visual.main(args) == check_visual.EXIT_INPUT
    assert "입력 오류" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        check_visual.main(["--real", str(clips["real"])])
    with pytest.raises(SystemExit):
        check_visual.main(
            ["--real", "a", "--render", "b", "--mask", "m", "--render-mask-dark", "80"]
        )
