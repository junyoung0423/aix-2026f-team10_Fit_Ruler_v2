"""fal 어댑터 테스트. 실제 fal을 부르지 않고 가짜 클라이언트로 확인한다(비용 발생 금지)."""

import json
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from typing import Any

import pytest

from fitting.adapters.fal_ltx import (
    ENDPOINT,
    RenderToRealError,
    RenderToRealSettings,
    estimate_cost,
    frames_for_clip,
    output_size,
    run_render_to_real,
    with_trigger,
)


@dataclass
class Queued:
    position: int


@dataclass
class InProgress:
    logs: list[dict[str, Any]]


class FakeClient:
    def __init__(
        self, response: dict[str, Any] | None = None, error: Exception | None = None
    ):
        self.response = response or {
            "video": {
                "url": "https://fal.example/out.mp4",
                "content_type": "video/mp4",
            },
            "seed": 42,
            "prompt": "3DREAL. test",
        }
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def upload_file(self, path: Path) -> str:
        return f"https://fal.example/upload/{Path(path).name}"

    def subscribe(
        self, application: str, arguments: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        self.calls.append(
            {"application": application, "arguments": arguments, **kwargs}
        )
        kwargs["on_enqueue"]("req-123")
        kwargs["on_queue_update"](Queued(position=2))
        kwargs["on_queue_update"](InProgress(logs=[{"message": "loading model"}]))
        kwargs["on_queue_update"](InProgress(logs=[{"message": "step 15/15"}]))
        if self.error:
            raise self.error
        return self.response


def fake_download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"video")


def ticking_clock():
    ticks = count()
    return lambda: float(next(ticks))


SETTINGS = RenderToRealSettings(
    prompt="a person wearing a black T-shirt", num_frames=73
)


def test_with_trigger() -> None:
    assert with_trigger("a person") == "3DREAL. a person"
    assert with_trigger("3DREAL. a person") == "3DREAL. a person"


@pytest.mark.parametrize(
    ("frames", "fps", "expected"),
    [(72, 24.0, 65), (73, 24.0, 73), (90, 30.0, 65), (121, 24.0, 121), (3, 24.0, 9)],
)
def test_frames_for_clip_uses_8k_plus_1(frames: int, fps: float, expected: int) -> None:
    result = frames_for_clip(frames, fps)
    assert result == expected
    assert (result - 1) % 8 == 0


def test_output_size_keeps_aspect_and_short_side() -> None:
    assert output_size(1920, 1080, "720p") == (1280, 720)
    assert output_size(540, 720, "720p") == (720, 960)
    assert output_size(1920, 1080, "480p") == (853, 480)
    assert output_size(1920, 1080, "720p", detail_refine=True) == (2560, 1440)


def test_estimate_cost_matches_fal_examples() -> None:
    # fal 모델 페이지 예시: 1280×720, 121프레임 → 112MP, $0.26964
    assert estimate_cost(1280, 720, 121).megapixels == 112
    assert estimate_cost(1280, 720, 121).usd == pytest.approx(0.26964)
    # 세부 보정 예시: 2560×1440, 121프레임 → 447MP
    assert estimate_cost(2560, 1440, 121).megapixels == 447


def test_arguments_turn_audio_off_and_skip_unset_values() -> None:
    arguments = SETTINGS.to_arguments("https://fal.example/in.mp4")
    assert arguments["video_url"] == "https://fal.example/in.mp4"
    assert arguments["prompt"].startswith("3DREAL.")
    assert arguments["intensity"] == "light"
    assert arguments["generate_audio"] is False
    assert "negative_prompt" not in arguments


def test_settings_reject_too_long_output() -> None:
    with pytest.raises(ValueError, match="480p"):
        RenderToRealSettings(
            prompt="x", resolution="720p", num_frames=169
        ).check()  # 7초
    RenderToRealSettings(prompt="x", resolution="480p", num_frames=169).check()


def test_run_saves_request_response_and_output(tmp_path: Path) -> None:
    clip = tmp_path / "render.mp4"
    clip.write_bytes(b"render")
    client = FakeClient()

    result = run_render_to_real(
        clip,
        SETTINGS,
        tmp_path / "out",
        client,
        downloader=fake_download,
        clock=ticking_clock(),
    )

    assert client.calls[0]["application"] == ENDPOINT
    assert client.calls[0]["with_logs"] is True
    assert result.request_id == "req-123"
    assert result.seed == 42
    assert result.queue_positions == [2]
    assert result.logs == ["loading model", "step 15/15"]
    assert result.output_path.read_bytes() == b"video"
    request = json.loads(
        (tmp_path / "out" / "request.json").read_text(encoding="utf-8")
    )
    assert request["arguments"]["video_url"].endswith("render.mp4")
    assert (
        json.loads((tmp_path / "out" / "response.json").read_text(encoding="utf-8"))[
            "seed"
        ]
        == 42
    )
    durations = result.to_dict()["durations"]
    assert durations["upload_s"] == 1.0
    assert durations["queue_s"] is not None and durations["processing_s"] is not None


class FakeHttpError(Exception):
    status_code = 422

    class response:
        text = '{"detail": "num_frames too large"}'


def test_run_writes_error_record_on_failure(tmp_path: Path) -> None:
    clip = tmp_path / "render.mp4"
    clip.write_bytes(b"render")
    client = FakeClient(error=FakeHttpError("Unprocessable Entity"))

    with pytest.raises(RenderToRealError) as caught:
        run_render_to_real(
            clip,
            SETTINGS,
            tmp_path / "out",
            client,
            downloader=fake_download,
            clock=ticking_clock(),
        )

    record = json.loads((tmp_path / "out" / "error.json").read_text(encoding="utf-8"))
    assert record["stage"] == "request"
    assert record["status_code"] == 422
    assert "num_frames" in record["response_body"]
    assert record["request_id"] == "req-123"
    assert caught.value.record == record
    assert not (tmp_path / "out" / "output.mp4").exists()
