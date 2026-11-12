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
    assert "req-123" in capsys.readouterr().out


def test_resume_needs_an_existing_clip(tmp_path, capsys):
    assert tool.main(["--name", "none", "--out-dir", str(tmp_path)]) == tool.EXIT_SETUP
    assert tool.main(["--name", "../bad", "--out-dir", str(tmp_path)]) == tool.EXIT_SETUP
