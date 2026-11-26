"""tools.realize_fitroom 테스트. 드라이런과 가짜 fal만 쓴다(실제 호출·비용 없음)."""

import json
from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2")  # 선택 의존성 realize가 없으면 건너뛴다
np = pytest.importorskip("numpy")

from fitting.adapters.fal_fitroom.realizer import FitRoomRealizer  # noqa: E402
from tests.adapters import visual_clips as vc  # noqa: E402
from tests.adapters.test_fal_realizer import FakeClock  # noqa: E402
from tests.adapters.test_fitroom_realizer import FakeFal, Files  # noqa: E402
from tools import realize_fitroom as tool  # noqa: E402


@pytest.fixture(autouse=True)
def no_env_file(monkeypatch):
    """저장소의 실제 .env(FAL_KEY)를 읽지 않게 한다."""
    monkeypatch.setattr(tool, "load_env_file", lambda path: None)


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("fitroom_tool")
    render, _ = vc.render_clip(root / "render.mp4")
    ok, buf = cv2.imencode(".png", vc.draw((180, 240), real=True, seed=1)[0])
    assert ok
    (root / "photo.png").write_bytes(buf.tobytes())
    return {
        "render": render,
        "photo": root / "photo.png",
        "turn": vc.real_clip(root / "turn.mp4"),
    }


def test_dry_run_plans_photo_pose_and_turn_without_calling_fal(tmp_path, media, capsys):
    code = tool.main(["--input", str(media["render"]), "--name", "f1", "--out-dir",
                      str(tmp_path / "out")])

    assert code == tool.EXIT_OK
    out = tmp_path / "out" / "f1"
    plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    assert list(plan["endpoints"]) == ["photo", "pose", "turn"]  # 팔 내린 자세 사진이 기본
    assert plan["turn_model"] == "fast"
    assert plan["estimate_usd"]["turn"] == pytest.approx(0.4)  # 4초 x $0.10 (Veo 3.1 Fast)
    assert plan["estimate_usd"]["total"] == pytest.approx(0.70)
    assert plan["prompts"]["pants"] == "light beige chino trousers"
    assert (out / "artifacts" / "f1_render_clip.mp4").is_file()
    assert not (out / "work").exists()  # fal을 부르지 않아 기록도 없다
    printed = capsys.readouterr().out
    assert "드라이런" in printed and "veo3.1" in printed


def test_duration_and_seed_go_into_the_plan(tmp_path, media):
    out = str(tmp_path / "out")
    args = ["--input", str(media["render"]), "--name", "f2", "--out-dir", out]
    assert tool.main([*args, "--duration", "8", "--seed", "random"]) == tool.EXIT_OK
    plan = json.loads((tmp_path / "out" / "f2" / "plan.json").read_text(encoding="utf-8"))
    assert plan["estimate_usd"]["turn"] == pytest.approx(0.8)  # 기본 4초 대신 8초
    with pytest.raises(SystemExit):
        tool.main([*args, "--duration", "5"])
    with pytest.raises(SystemExit):
        tool.main([*args, "--seed", "abc"])


def test_turn_model_option_selects_the_standard_veo(tmp_path, media, capsys):
    out = str(tmp_path / "out")
    args = ["--input", str(media["render"]), "--name", "f6", "--out-dir", out]
    assert tool.main([*args, "--turn-model", "standard"]) == tool.EXIT_OK
    plan = json.loads((tmp_path / "out" / "f6" / "plan.json").read_text(encoding="utf-8"))
    assert plan["endpoints"]["turn"] == "fal-ai/veo3.1/first-last-frame-to-video"
    assert plan["estimate_usd"]["turn"] == pytest.approx(0.8)  # 4초 x $0.20
    with pytest.raises(SystemExit):
        tool.main([*args, "--turn-model", "ultra"])


def test_no_pose_step_option_skips_the_arm_photo(tmp_path, media, capsys):
    out = str(tmp_path / "out")
    args = ["--input", str(media["render"]), "--name", "f7", "--out-dir", out, "--no-pose-step"]
    assert tool.main(args) == tool.EXIT_OK
    plan = json.loads((tmp_path / "out" / "f7" / "plan.json").read_text(encoding="utf-8"))
    assert plan["steps"] == ["photo", "turn"]
    assert "pose" not in plan["estimate_usd"]
    assert plan["estimate_usd"]["total"] == pytest.approx(0.55)
    assert "2단계" in capsys.readouterr().out


def test_real_call_needs_the_key(tmp_path, media, monkeypatch, capsys):
    monkeypatch.delenv("FAL_KEY", raising=False)
    code = tool.main(["--input", str(media["render"]), "--name", "f3", "--out-dir",
                      str(tmp_path / "out"), "--yes"])
    assert code == tool.EXIT_SETUP
    assert "FAL_KEY" in capsys.readouterr().err


def test_real_call_with_a_fake_client_saves_the_video_and_strip(tmp_path, media, monkeypatch,
                                                               capsys):
    monkeypatch.setenv("FAL_KEY", "dummy")
    client = FakeFal()

    def fake_factory(settings):
        clock = FakeClock()
        return FitRoomRealizer(client=client, artifacts_dir=settings.artifacts_dir,
                               work_dir=settings.work_dir, downloader=Files(media), clock=clock,
                               wall=clock, sleep=clock.sleep, client_version="test")

    monkeypatch.setattr(tool, "make_fitroom_realizer", fake_factory)
    code = tool.main(["--input", str(media["render"]), "--name", "f4", "--out-dir",
                      str(tmp_path / "out"), "--yes", "--duration", "6"])

    assert code == tool.EXIT_OK
    out = tmp_path / "out" / "f4"
    for name in ("realize_fit_frame.png", "realize_pose.png", "video.mp4"):
        assert (out / "artifacts" / f"f4_{name}").is_file()
    assert not (out / "artifacts" / "f4_realize_photo.png").exists()
    assert (out / "strip.png").is_file()
    assert client.submits[-1][1]["duration"] == "6s"  # 도구 옵션이 어댑터에 들어간다
    printed = capsys.readouterr().out
    assert "완료" in printed and "회전 영상" in printed


def test_resume_needs_an_existing_clip_and_a_safe_name(tmp_path, capsys):
    assert tool.main(["--name", "none", "--out-dir", str(tmp_path)]) == tool.EXIT_SETUP
    assert tool.main(["--name", "../bad", "--out-dir", str(tmp_path)]) == tool.EXIT_SETUP


def test_unknown_garment_is_a_setup_error(tmp_path, media, capsys):
    code = tool.main(["--input", str(media["render"]), "--name", "f5", "--out-dir",
                      str(tmp_path / "out"), "--garment-id", "없는_옷"])
    assert code == tool.EXIT_SETUP
    assert "의류를 찾을 수 없습니다" in capsys.readouterr().err
