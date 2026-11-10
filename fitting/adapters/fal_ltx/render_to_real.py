"""fal / LTX-2.3 render-to-real 호출 (D1 스파이크).

렌더 클립을 올리고, 실사화를 요청해 기다린 뒤, 결과 영상을 내려받는다.
요청·응답·시간·오류를 파일로 남겨 스파이크 기록(docs/spikes/)에 옮긴다.

엔드포인트, 입력 이름, 가격은 2026-11-10에 fal 모델 페이지에서 확인한 값이다.
정식 어댑터(D4: 재시도·타임아웃·가짜 어댑터)는 B의 포트가 나온 뒤 이 코드를 바탕으로 만든다.
"""

from __future__ import annotations

import json
import math
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

ENDPOINT = "fal-ai/ltx-2.3-quality/render-to-real"

# 생성 영상 1메가픽셀(너비 × 높이 × 프레임 수 ÷ 1,000,000, 올림)당 가격
PRICE_PER_MEGAPIXEL_USD = 0.0024075
PRICE_CHECKED_ON = "2026-11-10"

# 프롬프트 맨 앞에 두는 트리거. fal이 지워도 다시 붙이지만, 기록을 정확히 하려고 직접 붙인다.
PROMPT_TRIGGER = "3DREAL."

Resolution = Literal["480p", "720p"]
Intensity = Literal["light", "strong", "strong-v2"]

SHORT_SIDE: dict[str, int] = {"480p": 480, "720p": 720}

# 해상도별 출력 길이 상한(초, 문서상 대략값). 넘으면 fal이 422로 거절한다.
MAX_SECONDS: dict[str, float] = {"480p": 15.0, "720p": 6.0}


def with_trigger(prompt: str) -> str:
    text = prompt.strip()
    return (
        text if text.startswith(PROMPT_TRIGGER) else f"{PROMPT_TRIGGER} {text}".strip()
    )


def frames_for_clip(
    input_frames: int, input_fps: float, output_fps: float = 24.0
) -> int:
    """입력 클립 길이에 맞는 출력 프레임 수.

    LTX 계열은 프레임 수가 '8의 배수 + 1'일 때 맞게 동작한다(fal 기본값 121 = 8 × 15 + 1).
    입력 길이를 넘지 않는 가장 큰 8k + 1을 고르고, 최소 9프레임으로 둔다.
    """
    if input_frames <= 0 or input_fps <= 0:
        raise ValueError("입력 클립의 프레임 수와 fps가 필요합니다.")
    target = math.floor(input_frames / input_fps * output_fps)
    return max(9, (target - 1) // 8 * 8 + 1)


def output_size(
    width: int, height: int, resolution: Resolution, detail_refine: bool = False
) -> tuple[int, int]:
    """예상 출력 크기. 짧은 변을 해상도에 맞추고 비율은 유지한다. 세부 보정을 켜면 가로세로가 2배."""
    short = SHORT_SIDE[resolution]
    scale = short / min(width, height)
    w, h = round(width * scale), round(height * scale)
    return (w * 2, h * 2) if detail_refine else (w, h)


@dataclass(frozen=True)
class CostEstimate:
    width: int
    height: int
    frames: int
    megapixels: int
    usd: float


def estimate_cost(width: int, height: int, frames: int) -> CostEstimate:
    """fal 가격 계산식: 너비 × 높이 × 프레임 수 ÷ 1,000,000을 올림한 메가픽셀 × 단가."""
    megapixels = math.ceil(width * height * frames / 1_000_000)
    return CostEstimate(
        width,
        height,
        frames,
        megapixels,
        round(megapixels * PRICE_PER_MEGAPIXEL_USD, 6),
    )


@dataclass(frozen=True)
class RenderToRealSettings:
    """fal에 보내는 설정. 값이 None이면 보내지 않고 fal 기본값을 쓴다."""

    prompt: str
    intensity: Intensity = "light"
    resolution: Resolution = "720p"
    num_frames: int = 121
    frames_per_second: float = 24.0
    num_inference_steps: int = 15
    guidance_scale: float = 1.0
    generate_audio: bool = False
    seed: int | None = 42
    negative_prompt: str | None = None
    enable_detail_refine: bool = False
    video_quality: Literal["low", "medium", "high", "maximum"] = "high"

    def check(self) -> None:
        seconds = self.num_frames / self.frames_per_second
        limit = MAX_SECONDS[self.resolution]
        if seconds > limit:
            raise ValueError(
                f"{self.resolution} 출력은 약 {limit:g}초까지입니다(요청 {seconds:.1f}초). "
                "프레임 수를 줄이거나 480p를 쓰세요."
            )

    def to_arguments(self, video_url: str) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "video_url": video_url,
            "prompt": with_trigger(self.prompt),
            "intensity": self.intensity,
            "resolution": self.resolution,
            "num_frames": self.num_frames,
            "frames_per_second": self.frames_per_second,
            "num_inference_steps": self.num_inference_steps,
            "guidance_scale": self.guidance_scale,
            "generate_audio": self.generate_audio,
            "seed": self.seed,
            "negative_prompt": self.negative_prompt,
            "enable_detail_refine": self.enable_detail_refine,
            "video_quality": self.video_quality,
        }
        return {key: value for key, value in arguments.items() if value is not None}


class FalClient(Protocol):
    """fal_client 모듈에서 쓰는 함수만 적었다. 테스트에서는 가짜를 넣는다."""

    def upload_file(self, path: Path) -> str: ...

    def subscribe(
        self,
        application: str,
        arguments: dict[str, Any],
        *,
        with_logs: bool = ...,
        on_enqueue: Callable[[str], None] | None = ...,
        on_queue_update: Callable[[Any], None] | None = ...,
        client_timeout: float | None = ...,
    ) -> dict[str, Any]: ...


Downloader = Callable[[str, Path], None]


def download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, path)


@dataclass
class CallTimeline:
    """호출 단계별 시각(time.monotonic 기준 초)."""

    started: float = 0.0
    uploaded: float | None = None
    enqueued: float | None = None
    processing: float | None = None
    completed: float | None = None
    downloaded: float | None = None

    def durations(self) -> dict[str, float | None]:
        def gap(a: float | None, b: float | None) -> float | None:
            return round(b - a, 2) if a is not None and b is not None else None

        return {
            "upload_s": gap(self.started, self.uploaded),
            "queue_s": gap(self.enqueued, self.processing),
            "processing_s": gap(self.processing, self.completed),
            "download_s": gap(self.completed, self.downloaded),
            "total_s": gap(self.started, self.downloaded or self.completed),
        }


@dataclass
class RenderToRealResult:
    request_id: str | None
    video_url: str
    output_path: Path
    seed: int | None
    prompt: str | None
    arguments: dict[str, Any]
    timeline: CallTimeline
    queue_positions: list[int] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint": ENDPOINT,
            "request_id": self.request_id,
            "output_url": self.video_url,
            "output_path": self.output_path.as_posix(),
            "seed": self.seed,
            "prompt_used": self.prompt,
            "durations": self.timeline.durations(),
            "queue_positions": self.queue_positions,
            "logs": self.logs,
        }


class RenderToRealError(RuntimeError):
    """호출 실패. 실패 기록(error.json)을 남긴 뒤 원래 예외를 감싸서 던진다."""

    def __init__(self, message: str, record: dict[str, Any]):
        super().__init__(message)
        self.record = record


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def _error_record(
    error: Exception, stage: str, timeline: CallTimeline, request_id: str | None
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "stage": stage,
        "type": type(error).__name__,
        "message": str(error),
        "request_id": request_id,
        "durations": timeline.durations(),
    }
    # fal_client.FalClientHTTPError에는 상태 코드와 응답 본문이 있다.
    status = getattr(error, "status_code", None)
    if status is not None:
        record["status_code"] = status
        response = getattr(error, "response", None)
        body = getattr(response, "text", None)
        if body:
            record["response_body"] = body[:2000]
    return record


def run_render_to_real(
    video_path: Path,
    settings: RenderToRealSettings,
    out_dir: Path,
    client: FalClient,
    *,
    downloader: Downloader = download,
    timeout_s: float = 600.0,
    clock: Callable[[], float] = time.monotonic,
) -> RenderToRealResult:
    """렌더 클립 하나를 실사화한다.

    out_dir에 request.json, response.json, output.mp4를 남기고, 실패하면 error.json을 남긴다.
    """
    settings.check()
    timeline = CallTimeline(started=clock())
    state: dict[str, Any] = {"request_id": None, "positions": [], "logs": []}
    stage = "upload"

    def on_enqueue(request_id: str) -> None:
        state["request_id"] = request_id
        timeline.enqueued = clock()

    def on_queue_update(status: Any) -> None:
        kind = type(status).__name__
        position = getattr(status, "position", None)
        if position is not None:
            state["positions"].append(position)
        if kind == "InProgress" and timeline.processing is None:
            timeline.processing = clock()
        for entry in getattr(status, "logs", None) or []:
            message = entry.get("message") if isinstance(entry, dict) else str(entry)
            if message:
                state["logs"].append(message)

    try:
        video_url = client.upload_file(video_path)
        timeline.uploaded = clock()

        stage = "request"
        arguments = settings.to_arguments(video_url)
        _write_json(
            out_dir / "request.json", {"endpoint": ENDPOINT, "arguments": arguments}
        )
        response = client.subscribe(
            ENDPOINT,
            arguments=arguments,
            with_logs=True,
            on_enqueue=on_enqueue,
            on_queue_update=on_queue_update,
            client_timeout=timeout_s,
        )
        timeline.completed = clock()
        if timeline.enqueued is None:
            timeline.enqueued = timeline.uploaded
        if timeline.processing is None:
            timeline.processing = timeline.enqueued
        _write_json(out_dir / "response.json", response)

        stage = "download"
        output_url = response["video"]["url"]
        output_path = out_dir / "output.mp4"
        downloader(output_url, output_path)
        timeline.downloaded = clock()
    except Exception as error:
        record = _error_record(error, stage, timeline, state["request_id"])
        _write_json(out_dir / "error.json", record)
        raise RenderToRealError(
            f"{stage} 단계에서 실패했습니다: {error}", record
        ) from error

    return RenderToRealResult(
        request_id=state["request_id"],
        video_url=output_url,
        output_path=output_path,
        seed=response.get("seed"),
        prompt=response.get("prompt"),
        arguments=arguments,
        timeline=timeline,
        queue_positions=state["positions"],
        logs=state["logs"],
    )
