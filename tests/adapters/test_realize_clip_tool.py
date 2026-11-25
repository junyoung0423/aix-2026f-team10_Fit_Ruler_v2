"""tools.realize_clip 테스트. 드라이런과 가짜 fal 클라이언트만 쓴다(실제 호출·비용 없음)."""

import json

import pytest

pytest.importorskip("cv2")

from fitting.adapters.fal_ltx import FalRealizer  # noqa: E402
from tests.adapters.test_fal_realizer import Downloader, FakeClock, FakeQueue  # noqa: E402
from tools import realize_clip as tool  # noqa: E402
from tools.spike_fal_ltx import write_test_clip  # noqa: E402


@pytest.fixture
def clip(tmp_path):
    return write_test_clip(tmp_path / "render.mp4", seconds=1.0, size=(96, 128))


@pytest.fixture(autouse=True)
def no_env_file(monkeypatch):
    """저장소의 실제 .env(FAL_KEY)를 읽지 않게 한다."""
    monkeypatch.setattr(tool, "load_env_file", lambda path: None)


def test_dry_run_plans_without_calling_fal(tmp_path, clip, capsys):
    code = tool.main(["--input", str(clip), "--name", "t1", "--out-dir", str(tmp_path / "out")])

    assert code == tool.EXIT_OK
    out = tmp_path / "out" / "t1"
    plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    assert plan["arguments_preview"]["generate_audio"] is False
    assert plan["estimate"]["usd"] > 0
    assert (out / "frame_render.png").is_file()
    assert (out / "artifacts" / "t1_render_clip.mp4").is_file()
    assert not (out / "work").exists()  # fal을 부르지 않아 기록도 없다
    assert "드라이런" in capsys.readouterr().out


def test_real_call_needs_the_key(tmp_path, clip, monkeypatch, capsys):
    monkeypatch.delenv("FAL_KEY", raising=False)
    code = tool.main(
        ["--input", str(clip), "--name", "t2", "--out-dir", str(tmp_path / "out"), "--yes"]
    )
    assert code == tool.EXIT_SETUP
    assert "FAL_KEY" in capsys.readouterr().err


def test_real_call_with_a_fake_client_saves_the_video(tmp_path, clip, monkeypatch, capsys):
    monkeypatch.setenv("FAL_KEY", "dummy")
    output = write_test_clip(tmp_path / "output.mp4", seconds=0.4, size=(96, 128))
    clock = FakeClock()

    def fake_factory(settings):
        return FalRealizer(
            client=FakeQueue(),
            artifacts_dir=settings.artifacts_dir,
            work_dir=settings.work_dir,
            downloader=Downloader(output),
            clock=clock,
            wall=clock,
            sleep=clock.sleep,
        )

    monkeypatch.setattr(tool, "make_realizer", fake_factory)
    code = tool.main(
        ["--input", str(clip), "--name", "t3", "--out-dir", str(tmp_path / "out"), "--yes"]
    )

    assert code == tool.EXIT_OK
    out = tmp_path / "out" / "t3"
    assert (out / "artifacts" / "t3_video.mp4").is_file()
    assert (out / "frame_real.png").is_file()
    # D5: 실사 대표 프레임과 같은 시각의 렌더 프레임
    assert (out / "frame_render_matched.png").is_file()
    frames = json.loads((out / "frames.json").read_text(encoding="utf-8"))
    assert frames["match"]["mode"] == "time"
    assert frames["render_frame"]["index"] == frames["real_frame"]["index"]  # 같은 24fps
    assert "req-123" in capsys.readouterr().out


def test_unknown_submit_is_blocked_until_resubmit_is_given(tmp_path, clip, monkeypatch, capsys):
    """응답 없는 제출 뒤 같은 --name으로 다시 실행하면 멈추고, --resubmit일 때만 다시 보낸다."""
    from tests.adapters.test_fal_realizer import ReadTimeout

    monkeypatch.setenv("FAL_KEY", "dummy")
    output = write_test_clip(tmp_path / "output.mp4", seconds=0.4, size=(96, 128))
    client = FakeQueue(submit_errors=[ReadTimeout("응답 없음")])
    clock = FakeClock()

    def fake_factory(settings):
        return FalRealizer(
            client=client,
            artifacts_dir=settings.artifacts_dir,
            work_dir=settings.work_dir,
            downloader=Downloader(output),
            clock=clock,
            wall=clock,
            sleep=clock.sleep,
        )

    monkeypatch.setattr(tool, "make_realizer", fake_factory)
    base = ["--name", "t4", "--out-dir", str(tmp_path / "out"), "--yes"]
    assert tool.main(["--input", str(clip), *base]) == tool.EXIT_FAILED
    assert tool.main(base) == tool.EXIT_FAILED  # 다시 제출하지 않고 멈춘다
    assert client.calls["submit"] == 1
    assert "--resubmit" in capsys.readouterr().out

    assert tool.main([*base, "--resubmit"]) == tool.EXIT_OK
    assert client.calls["submit"] == 2


def test_resume_needs_an_existing_clip(tmp_path, capsys):
    assert tool.main(["--name", "none", "--out-dir", str(tmp_path)]) == tool.EXIT_SETUP
    assert tool.main(["--name", "../bad", "--out-dir", str(tmp_path)]) == tool.EXIT_SETUP


def test_subject_and_prompt_version_are_shown_in_the_dry_run(tmp_path, clip, capsys):
    code = tool.main(
        [
            "--input",
            str(clip),
            "--name",
            "t5",
            "--out-dir",
            str(tmp_path / "out"),
            "--subject",
            "mannequin",
        ]
    )
    assert code == tool.EXIT_OK
    printed = capsys.readouterr().out
    assert "realize_prompt_v1" in printed and "mannequin" in printed
    plan = json.loads((tmp_path / "out" / "t5" / "plan.json").read_text(encoding="utf-8"))
    assert plan["prompt"]["subject"] == "mannequin"


def test_empty_garment_text_is_a_setup_error(tmp_path, clip, capsys):
    out = str(tmp_path / "out")
    args = ["--input", str(clip), "--name", "t6", "--out-dir", out, "--garment-text", " "]
    code = tool.main(args)
    assert code == tool.EXIT_SETUP
    assert "garment_text" in capsys.readouterr().err


def test_fitting_room_style_and_random_seed_are_shown_in_the_dry_run(tmp_path, clip, capsys):
    out = str(tmp_path / "out")
    args = ["--input", str(clip), "--name", "t7", "--out-dir", out]
    code = tool.main([*args, "--style", "fitting_room", "--seed", "random"])
    assert code == tool.EXIT_OK
    printed = capsys.readouterr().out
    assert "realize_prompt_v2" in printed and "fitting room" in printed
    assert "부정 프롬프트: (보내지 않음" not in printed
    assert "시드: (보내지 않음" in printed
    preview = json.loads((tmp_path / "out" / "t7" / "plan.json").read_text("utf-8"))[
        "arguments_preview"
    ]
    assert "mannequin" in preview["negative_prompt"]
    assert "seed" not in preview


def test_default_style_keeps_the_fal_negative_prompt_and_seed_42(tmp_path, clip, capsys):
    code = tool.main(["--input", str(clip), "--name", "t8", "--out-dir", str(tmp_path / "out")])
    assert code == tool.EXIT_OK
    printed = capsys.readouterr().out
    assert "realize_prompt_v1" in printed
    assert "부정 프롬프트: (보내지 않음, fal 기본값)" in printed
    assert "시드: 42" in printed


@pytest.mark.parametrize("seed", ["abc", "-1", "1.5"])
def test_bad_seed_is_rejected_by_the_parser(tmp_path, clip, seed):
    with pytest.raises(SystemExit) as caught:
        tool.main(["--input", str(clip), "--name", "t9", "--seed", seed])
    assert caught.value.code == 2


def test_fitting_room_style_rejects_the_mannequin_subject(tmp_path, clip, capsys):
    out = str(tmp_path / "out")
    args = ["--input", str(clip), "--name", "t10", "--out-dir", out]
    code = tool.main([*args, "--style", "fitting_room", "--subject", "mannequin"])
    assert code == tool.EXIT_SETUP
    assert "설정 오류" in capsys.readouterr().err
