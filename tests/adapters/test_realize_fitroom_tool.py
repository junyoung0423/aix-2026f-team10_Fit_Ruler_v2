"""tools.realize_fitroom 테스트. 드라이런과 가짜 fal만 쓴다(실제 호출·비용 없음)."""

import json
from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2")  # 선택 의존성 realize가 없으면 건너뛴다
np = pytest.importorskip("numpy")

from fitting.adapters.fal_fitroom.realizer import FitRoomRealizer  # noqa: E402
from tests.adapters import visual_clips as vc  # noqa: E402
from tests.adapters.test_fal_realizer import FakeClock  # noqa: E402
from tests.adapters.test_fitroom_realizer import FakeEncoder, FakeFal, Files  # noqa: E402
from tools import realize_fitroom as tool  # noqa: E402


@pytest.fixture(autouse=True)
def no_env_file(monkeypatch):
    """저장소의 실제 .env(FAL_KEY·BLENDER_PATH)와 개발자 환경의 Blender 경로를 쓰지 않게 한다."""
    monkeypatch.setattr(tool, "load_env_file", lambda path: None)
    monkeypatch.delenv("BLENDER_PATH", raising=False)
    monkeypatch.delenv("BLENDER_EXE", raising=False)


@pytest.fixture
def fake_blender(tmp_path, monkeypatch) -> Path:
    """실행 파일이 있는지만 보는 가짜 Blender 경로(실행은 가짜 인코더가 한다)."""
    path = tmp_path / "blender.exe"
    path.write_bytes(b"")
    monkeypatch.setenv("BLENDER_PATH", str(path))
    return path


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
    assert plan["estimate_usd"]["turn"] == pytest.approx(0.8)  # 8초 x $0.10 (Veo 3.1 Fast)
    assert plan["estimate_usd"]["total"] == pytest.approx(1.10)
    assert plan["top_crop_px"] == 32
    assert plan["prompts"]["pants"] == "light beige chino trousers"
    assert (out / "artifacts" / "f1_render_clip.mp4").is_file()
    assert not (out / "work").exists()  # fal을 부르지 않아 기록도 없다
    printed = capsys.readouterr().out
    assert "드라이런" in printed and "veo3.1" in printed
    assert "맨 위 자르기 32px" in printed  # 드라이런은 Blender가 없어도 계획을 보여 준다


def test_duration_seed_and_top_crop_go_into_the_plan(tmp_path, media, capsys):
    out = str(tmp_path / "out")
    args = ["--input", str(media["render"]), "--name", "f2", "--out-dir", out]
    assert tool.main([*args, "--duration", "4", "--seed", "random", "--top-crop", "0"]) == 0
    plan = json.loads((tmp_path / "out" / "f2" / "plan.json").read_text(encoding="utf-8"))
    assert plan["estimate_usd"]["turn"] == pytest.approx(0.4)  # 기본 8초 대신 4초
    assert plan["top_crop_px"] == 0 and "맨 위 자르기 끔" in capsys.readouterr().out
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
    assert plan["estimate_usd"]["turn"] == pytest.approx(1.6)  # 8초 x $0.20
    with pytest.raises(SystemExit):
        tool.main([*args, "--turn-model", "ultra"])


def test_no_pose_step_option_skips_the_arm_photo(tmp_path, media, capsys):
    out = str(tmp_path / "out")
    args = ["--input", str(media["render"]), "--name", "f7", "--out-dir", out, "--no-pose-step"]
    assert tool.main(args) == tool.EXIT_OK
    plan = json.loads((tmp_path / "out" / "f7" / "plan.json").read_text(encoding="utf-8"))
    assert plan["steps"] == ["photo", "turn"]
    assert "pose" not in plan["estimate_usd"]
    assert plan["estimate_usd"]["total"] == pytest.approx(0.95)
    assert "2단계" in capsys.readouterr().out


def test_real_call_needs_the_key(tmp_path, media, monkeypatch, capsys, fake_blender):
    monkeypatch.delenv("FAL_KEY", raising=False)
    code = tool.main(["--input", str(media["render"]), "--name", "f3", "--out-dir",
                      str(tmp_path / "out"), "--yes"])
    assert code == tool.EXIT_SETUP
    assert "FAL_KEY" in capsys.readouterr().err


def fake_factory_for(client: FakeFal, media, encoder: FakeEncoder):
    def fake_factory(settings):
        clock = FakeClock()
        return FitRoomRealizer(client=client, artifacts_dir=settings.artifacts_dir,
                               work_dir=settings.work_dir, downloader=Files(media), clock=clock,
                               wall=clock, sleep=clock.sleep, client_version="test",
                               blender_path=settings.blender_path, runner=encoder)

    return fake_factory


def test_real_call_with_a_fake_client_saves_the_video_and_strip(tmp_path, media, monkeypatch,
                                                               capsys, fake_blender):
    monkeypatch.setenv("FAL_KEY", "dummy")
    client, encoder = FakeFal(), FakeEncoder()
    monkeypatch.setattr(tool, "make_fitroom_realizer", fake_factory_for(client, media, encoder))
    code = tool.main(["--input", str(media["render"]), "--name", "f4", "--out-dir",
                      str(tmp_path / "out"), "--yes", "--duration", "6"])

    assert code == tool.EXIT_OK
    out = tmp_path / "out" / "f4"
    for name in ("realize_fit_frame.png", "realize_pose.png", "video.mp4"):
        assert (out / "artifacts" / f"f4_{name}").is_file()
    assert not (out / "artifacts" / "f4_realize_photo.png").exists()
    assert (out / "strip.png").is_file()
    assert client.submits[-1][1]["duration"] == "6s"  # 도구 옵션이 어댑터에 들어간다
    assert encoder.commands[0][0] == str(fake_blender)  # BLENDER_PATH로 맨 위를 잘랐다
    printed = capsys.readouterr().out
    assert "완료" in printed and "회전 영상" in printed


def test_real_call_without_blender_stops_unless_the_crop_is_off(tmp_path, media, monkeypatch,
                                                               capsys):
    monkeypatch.setenv("FAL_KEY", "dummy")
    client, encoder = FakeFal(), FakeEncoder()
    monkeypatch.setattr(tool, "make_fitroom_realizer", fake_factory_for(client, media, encoder))
    args = ["--input", str(media["render"]), "--name", "f8", "--out-dir", str(tmp_path / "out"),
            "--yes"]
    assert tool.main(args) == tool.EXIT_SETUP
    assert "Blender" in capsys.readouterr().err
    assert client.submits == []  # fal을 부르기 전에 멈췄다

    assert tool.main([*args, "--top-crop", "0"]) == tool.EXIT_OK
    assert encoder.commands == []  # 자르기를 끄면 받은 영상을 그대로 쓴다
    assert (tmp_path / "out" / "f8" / "artifacts" / "f8_video.mp4").read_bytes() == (
        media["turn"].read_bytes())


def test_resume_needs_an_existing_clip_and_a_safe_name(tmp_path, capsys):
    assert tool.main(["--name", "none", "--out-dir", str(tmp_path)]) == tool.EXIT_SETUP
    assert tool.main(["--name", "../bad", "--out-dir", str(tmp_path)]) == tool.EXIT_SETUP


def test_unknown_garment_is_a_setup_error(tmp_path, media, capsys):
    code = tool.main(["--input", str(media["render"]), "--name", "f5", "--out-dir",
                      str(tmp_path / "out"), "--garment-id", "없는_옷"])
    assert code == tool.EXIT_SETUP
    assert "의류를 찾을 수 없습니다" in capsys.readouterr().err


def test_mask_turns_on_the_photo_check_and_the_guide_option(tmp_path, media, capsys):
    _, mask = vc.render_clip(tmp_path / "render_for_mask.mp4")
    mask_path = vc.write_mask(tmp_path / "mask.png", mask)
    code = tool.main(["--input", str(media["render"]), "--name", "f5", "--out-dir",
                      str(tmp_path / "out"), "--mask", str(mask_path), "--photo-guide"])

    assert code == tool.EXIT_OK
    out = tmp_path / "out" / "f5"
    plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    assert plan["photo_check"] == {"enabled": True, "max_photos": 3}
    assert plan["photo_guide"] == "render_mask"
    assert plan["estimate_usd"]["max_total"] == pytest.approx(1.40)  # 사진 2장을 더 만들 수 있다
    assert (out / "artifacts" / "f5_render_mask.png").is_file()
    printed = capsys.readouterr().out
    assert "사진 핏 확인 켬" in printed and "사진 기준 그림 켬" in printed


def test_a_missing_mask_file_is_a_setup_error(tmp_path, media, capsys):
    code = tool.main(["--input", str(media["render"]), "--name", "f6", "--out-dir",
                      str(tmp_path / "out"), "--mask", str(tmp_path / "none.png")])
    assert code == tool.EXIT_SETUP
    assert "렌더 옷 마스크가 없습니다" in capsys.readouterr().err
