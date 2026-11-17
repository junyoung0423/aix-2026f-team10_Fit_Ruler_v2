"""fal / LTX-2.3 실사화 어댑터 (D4, `Realizer` 포트, #10).

D1 스파이크(`render_to_real.py`)의 설정·가격·내려받기를 그대로 쓰고,
정식 어댑터에 필요한 것(재시도·재조회·타임아웃·비용 상한·기록)을 더했다.

- 흐름: 렌더 클립 업로드 → 생성 요청 제출 → 상태 조회로 대기 → 결과 받기 → 영상 내려받기
  → `artifacts_dir/{job_id}_video.mp4`. 오디오는 끈다(`generate_audio: false`).
- 재시도: 이 어댑터는 외부 호출(업로드·상태 조회·결과·내려받기)마다 일시 오류(연결 실패·시간
  초과, 429·5xx)면 한 번 더 시도한다. **fal_client 1.0.3도 안에서 따로 재시도한다**
  (`_maybe_retry_request`, 최대 10회: 408·409·429·502·503·504, 시간 초과·전송 오류).
  그래서 업로드·조회는 일시 오류 한 번에 최대 20번까지 시도될 수 있다. 같은 요청을 다시 보내도
  생성 비용이 들지 않는 호출이라 그대로 둔다.
- **제출은 한 번만 보낸다.** 팩토리가 넣는 클라이언트(`factory.FalQueueClient`)는 제출할 때만
  fal_client 내부 재시도를 끈다. 이 어댑터는 요청이 fal에 닿지 않았다고 확실할 때만(연결 실패,
  서버가 429·503으로 거절) 한 번 더 보낸다. 500·502·504나 응답을 못 받은 경우는 이미 접수됐을 수
  있어 다시 보내지 않는다(생성 비용이 두 번 나가지 않게).
- 같은 요청 ID 재조회: 제출에 성공한 뒤에는 다시 제출하지 않는다(B1 `Realizer` 약속). 같은 작업으로
  다시 부르면 `work_dir/{job_id}/realize/realize.json`의 요청 ID로 상태·결과만 다시 받는다.
  생성은 끝났는데 영상 파일이 없으면(기록은 성공) 기록의 결과 주소로 내려받기만 다시 한다.
- 접수 여부를 모르는 제출(요청 ID 없음) 뒤에 같은 작업으로 다시 부르면 **다시 제출하지 않고 실패로
  알린다.** 다시 보내려면 `RealizeOptions.allow_resubmit`(명령 `--resubmit`)을 명시해야 한다.
- 타임아웃: 대기 한도(기본 `PortRequest.timeout_seconds`)를 넘으면 `REALIZE_TIMEOUT`.
  요청 ID를 남기고 요청은 취소하지 않는다(나중에 같은 ID로 결과를 받을 수 있게).
- 비용: 제출 전에 예상 비용(출력 크기 상한 기준)이 `max_cost_usd`를 넘으면 `BUDGET_LIMIT`.
  실제 비용은 내려받은 영상 크기로 다시 계산해 지표 `cost_usd`에 쓴다.
- 기록: `work_dir/{job_id}/realize/`에 `plan.json`(입력·예상 비용), `request.json`(보낸 값),
  `response.json`(받은 값), `error.json`(실패), `realize.json`(상태·요청 ID·시간·재시도·비용)을
  남긴다.
  FAL_KEY 값은 어디에도 쓰지 않는다.

오류 코드. `retryable=true`는 B1 계약대로 **같은 요청 ID 재조회**만 뜻한다. 요청 ID가 없는 실패는
모두 `retryable=false`이고, 다시 하려면 새 작업으로 한다.
(U = `EXTERNAL_SUBMIT_UNKNOWN`, R = retryable, ID = `external_request_id`)
| 상황 | 코드 | R | ID |
|---|---|---|---|
| 렌더 클립이 없다 | U | false | 없음 |
| 예상 비용이 상한을 넘는다 | `BUDGET_LIMIT` | false | 없음 |
| 업로드 두 번 실패, 제출 거절(429·503 두 번 또는 4xx) | U | false | 없음 |
| 제출 결과를 모름(응답 없음, 500·502·504 등) | U | false | 없음(모름) |
| 위 상태 뒤 같은 작업으로 다시 부름(`allow_resubmit` 없이) | U | false | 없음 |
| 대기 한도 초과 | `REALIZE_TIMEOUT` | true | 있음 |
| 접수 뒤 상태·결과·내려받기가 두 번 실패 | U | true | 있음 |
| fal이 생성 실패를 알림 | U | false | 없음(기록에만) |

마지막 줄은 계약에 "외부 생성 실패" 코드가 없어서 임시로 쓴다. 재조회하면 안 되므로 요청 ID를
`PortError`에 넣지 않는다. `REALIZE_FAILED` 추가는 B가 따로 계약 PR로 하고, 들어오면 이 줄만
바꾼다.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from fitting.domain import SCHEMA_VERSION, ErrorCode, GarmentVariant, Stage
from fitting.ports import REALIZE_OUTPUTS, PortError, PortFailure, PortRequest, PortResult

from .render_to_real import (
    ENDPOINT,
    MAX_SECONDS,
    PRICE_CHECKED_ON,
    Intensity,
    RenderToRealSettings,
    Resolution,
    download,
    estimate_cost,
    frames_for_clip,
    output_size,
)

REALIZE_VERSION = "realize_fal_v1"
RECORD_DIR = "realize"
SUMMARY_NAME = "realize.json"
MAX_ATTEMPTS = 2  # 외부 호출마다 처음 1번 + 재시도 1번

# D1 스파이크와 같은 프롬프트. 얼굴 처리(턱 아래) 문구 조정은 D5에서 한다.
PROMPT_TEMPLATE = (
    "A realistic photo of a person wearing a {garment}, front view from the neck down, "
    "plain light gray studio background, soft even lighting, natural fabric folds. "
    "Keep the garment color and shape exactly as in the render."
)

# 상태 이름 (realize.json의 state)
SUBMITTED, COMPLETED, SUCCEEDED, FAILED, TIMEOUT, UNKNOWN = (
    "submitted",
    "completed",
    "succeeded",
    "failed",
    "timeout",
    "unknown",
)
# 이 상태로 끝난 기록에 요청 ID가 있으면 다시 제출하지 않고 같은 ID로 이어 간다
_RESUMABLE = {SUBMITTED, COMPLETED, TIMEOUT, UNKNOWN}

_NETWORK_ERROR_NAMES = {
    "TransportError",  # httpx 네트워크 오류의 부모
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "WriteTimeout",
    "PoolTimeout",
    "ReadError",
    "WriteError",
    "RemoteProtocolError",
    "FalClientTimeoutError",
}
# 요청이 서버에 닿지 않았다고 확실한 오류 (제출을 다시 보내도 되는 경우)
_NOT_SENT_ERROR_NAMES = {"ConnectError", "ConnectTimeout", "PoolTimeout"}


class FalQueueClient(Protocol):
    """`fal_client` 모듈에서 쓰는 함수만 적었다. 테스트에서는 가짜를 넣는다."""

    def upload_file(self, path: Path) -> str: ...

    def submit(self, application: str, arguments: dict[str, Any]) -> Any:
        """`request_id` 속성이 있는 요청 핸들."""
        ...

    def status(self, application: str, request_id: str, *, with_logs: bool = False) -> Any:
        """`Queued`(position) · `InProgress`(logs) · `Completed`(logs, error) 중 하나."""
        ...

    def result(self, application: str, request_id: str) -> dict[str, Any]: ...


Downloader = Callable[[str, Path], None]


def _error_names(error: BaseException) -> set[str]:
    return {cls.__name__ for cls in type(error).__mro__}


def is_transient(error: BaseException) -> bool:
    """다시 시도할 만한 일시 오류인지(연결·시간 초과, 429·5xx)."""
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        return status == 429 or status >= 500
    return bool(_error_names(error) & _NETWORK_ERROR_NAMES) or isinstance(
        error, (ConnectionError, TimeoutError)
    )


# 서버가 받지 않았다고 볼 수 있는 응답 코드 (다시 보내도 되는 거절)
_RESEND_STATUS = {429, 503}
# 응답은 왔지만 처리됐는지 알 수 없는 코드 (408 요청 시간 초과, 409 충돌)
_AMBIGUOUS_STATUS = {408, 409}


def submit_not_sent(error: BaseException) -> bool:
    """제출이 fal에 접수되지 않았다고 확실해서 다시 보내도 되는지.

    연결 실패(요청이 서버에 닿지 않음)와 서버가 429·503으로 거절한 경우만이다. 500·502·504는
    서버가 요청을 받은 뒤 실패했을 수 있어(접수됐을 수 있음) 넣지 않는다.
    """
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        return status in _RESEND_STATUS
    return bool(_error_names(error) & _NOT_SENT_ERROR_NAMES) or isinstance(
        error, ConnectionRefusedError
    )


def submit_rejected(error: BaseException) -> bool:
    """서버가 제출을 받아들이지 않았다고 확실한지(4xx, 429·408·409 제외). 다시 보내지 않는다."""
    status = getattr(error, "status_code", None)
    return (
        isinstance(status, int)
        and 400 <= status < 500
        and status not in _RESEND_STATUS
        and status not in _AMBIGUOUS_STATUS
    )


def garment_description(garment: GarmentVariant) -> str:
    """프롬프트에 넣을 옷 설명(영어). 원단 설명이 있으면 쓰고, 없으면 색으로 만든다."""
    fabric = garment.fabric.description_en if garment.fabric else None
    if fabric:
        return f"{fabric.strip().rstrip('.')} short-sleeve crew-neck T-shirt"
    color = "" if garment.color == "unknown" else f"{garment.color} "
    return f"plain {color}short-sleeve crew-neck cotton T-shirt"


@dataclass(frozen=True)
class RealizeOptions:
    intensity: Intensity = "light"
    resolution: Resolution = "720p"
    output_fps: float = 24.0
    seed: int | None = 42
    max_cost_usd: float = 0.5  # 예상 비용 상한(D1 스파이크와 같다)
    poll_interval_s: float = 2.0
    wait_limit_s: float | None = None  # None이면 PortRequest.timeout_seconds
    garment_text: str | None = None  # 프롬프트 옷 설명(시험용). 없으면 의류에서 만든다
    # 접수 여부를 모르는 이전 제출(요청 ID 없음)이 있어도 다시 제출한다. 비용이 두 번 나갈 수 있어
    # 기본은 끔이다. 사람이 fal 대시보드를 확인한 뒤 명시적으로만 켠다
    # (`tools.realize_clip --resubmit`).
    allow_resubmit: bool = False


@dataclass
class _Record:
    """`realize.json` 내용. 단계가 바뀔 때마다 파일로 쓴다."""

    path: Path
    data: dict[str, Any] = field(default_factory=dict)

    def save(self, **updates: Any) -> None:
        self.data.update(updates)
        self.data["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )


def _video_info(path: Path):
    """영상 크기·fps·프레임 수. OpenCV(선택 의존성 realize)는 쓸 때만 불러온다."""
    from fitting.adapters.media.frames import read_video_info

    return read_video_info(path)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


@dataclass
class FalRealizer:
    """`Realizer` 포트 구현. 만들 때 `fitting.adapters.fal_ltx.factory.make_realizer`를 쓴다."""

    client: FalQueueClient
    artifacts_dir: Path
    work_dir: Path
    options: RealizeOptions = field(default_factory=RealizeOptions)
    downloader: Downloader = download
    clock: Callable[[], float] = time.monotonic  # 대기 한도 계산용
    wall: Callable[[], float] = time.time  # 기록의 시각(다시 불러도 이어지게 벽시계)
    sleep: Callable[[float], None] = time.sleep
    client_version: str = "unknown"

    # --- 계획(드라이런) ---------------------------------------------------------------------

    def input_clip(self, request: PortRequest) -> Path | None:
        """`input_artifact_ids[0]`(렌더 클립)의 파일. 없으면 None."""
        if not request.input_artifact_ids:
            return None
        artifact_id = request.input_artifact_ids[0]
        directory = Path(self.artifacts_dir)
        exact = directory / f"{artifact_id}.mp4"
        if exact.is_file():
            return exact
        matches = sorted(directory.glob(f"{artifact_id}.*"))
        return matches[0] if matches else None

    def plan(self, request: PortRequest, garment: GarmentVariant) -> dict[str, Any]:
        """fal을 부르지 않고 보낼 설정과 예상 비용을 계산한다(드라이런·기록용).

        Raises:
            FileNotFoundError: 렌더 클립이 없다.
        """
        clip = self.input_clip(request)
        if clip is None:
            raise FileNotFoundError(f"렌더 클립이 없습니다: {request.input_artifact_ids}")
        info = _video_info(clip)
        options = self.options
        limit = int(math.floor(MAX_SECONDS[options.resolution] * options.output_fps))
        max_frames = max(9, (limit - 1) // 8 * 8 + 1)
        frames = min(frames_for_clip(info.frame_count, info.fps, options.output_fps), max_frames)
        settings = RenderToRealSettings(
            prompt=PROMPT_TEMPLATE.format(
                garment=options.garment_text or garment_description(garment)
            ),
            intensity=options.intensity,
            resolution=options.resolution,
            num_frames=frames,
            frames_per_second=options.output_fps,
            seed=options.seed,
            generate_audio=False,
        )
        settings.check()
        width, height = output_size(
            info.width, info.height, settings.resolution, settings.enable_detail_refine
        )
        estimate = estimate_cost(width, height, settings.num_frames)
        return {
            "endpoint": ENDPOINT,
            "input": {"path": clip.name, **info.to_dict()},
            "settings": settings,
            "arguments_preview": settings.to_arguments("<업로드 후 fal 주소>"),
            "estimate": {
                "output_width": estimate.width,
                "output_height": estimate.height,
                "frames": estimate.frames,
                "megapixels": estimate.megapixels,
                "usd": estimate.usd,
                "price_checked_on": PRICE_CHECKED_ON,
            },
            "max_cost_usd": options.max_cost_usd,
        }

    # --- 포트 -------------------------------------------------------------------------------

    def tool_versions(self) -> dict[str, str]:
        return {
            "fal_endpoint": ENDPOINT,
            "fal_client": self.client_version,
            "realize": REALIZE_VERSION,
            "price_checked_on": PRICE_CHECKED_ON,
        }

    def realize(self, request: PortRequest, garment: GarmentVariant) -> PortResult:
        record_dir = Path(self.work_dir) / request.job_id / RECORD_DIR
        record = _Record(record_dir / SUMMARY_NAME, self._load_summary(record_dir))
        output = Path(self.artifacts_dir) / f"{request.job_id}_{REALIZE_OUTPUTS[0]}.mp4"
        artifact_id = output.stem

        state = record.data.get("state")
        request_id = record.data.get("request_id")
        # 이미 끝난 작업: 다시 부르지 않고 같은 결과를 돌려준다(비용 없음)
        if state == SUCCEEDED and output.is_file():
            return self._result(artifact_id, record.data)
        # 접수 여부를 모르는 제출 뒤: 다시 보내면 비용이 두 번 나갈 수 있어 명시할 때만 보낸다
        if state == UNKNOWN and not request_id and not self.options.allow_resubmit:
            raise self._fail(
                record,
                record_dir,
                code=ErrorCode.EXTERNAL_SUBMIT_UNKNOWN,
                retryable=False,
                message="이전 실사화 요청이 접수됐는지 알 수 없어 다시 보내지 않았습니다.",
                state=UNKNOWN,
                stage="resubmit_guard",
            )

        started = self.clock()
        # 생성은 끝났는데 영상 파일만 없으면(기록은 성공) 내려받기만 다시 한다
        resumed = bool(request_id) and (state in _RESUMABLE or state == SUCCEEDED)
        record.save(
            job_id=request.job_id,
            attempt_id=request.attempt_id,
            endpoint=ENDPOINT,
            calls=int(record.data.get("calls", 0)) + 1,
            retries=record.data.get("retries", []),
            resumed_from=record.data.get("state") if resumed else None,
        )

        if not resumed:
            record.save(t0=self.wall(), timeline={"started": 0.0}, retries=[])
            request_id = self._upload_and_submit(request, garment, record, record_dir)
            record.save(state=SUBMITTED, request_id=request_id)

        response = record.data.get("response") if state in (COMPLETED, SUCCEEDED) else None
        if response is None:
            self._wait(request, request_id, record, record_dir, started)
            response = self._fetch_result(request_id, record, record_dir)
            record.save(state=COMPLETED, response=response)
            self._mark(record, "completed")

        self._download(response, output, request_id, record, record_dir)
        self._mark(record, "downloaded")
        info = _video_info(output)
        actual = estimate_cost(info.width, info.height, info.frame_count)
        record.save(
            state=SUCCEEDED,
            artifact_id=artifact_id,
            output={"file": output.name, **info.to_dict()},
            cost={"megapixels": actual.megapixels, "usd": actual.usd},
            error=None,
        )
        return self._result(artifact_id, record.data)

    # --- 단계 -------------------------------------------------------------------------------

    @staticmethod
    def _load_summary(record_dir: Path) -> dict[str, Any]:
        path = record_dir / SUMMARY_NAME
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _fail(
        self,
        record: _Record,
        record_dir: Path,
        *,
        code: ErrorCode,
        retryable: bool,
        message: str,
        state: str,
        error: BaseException | None = None,
        stage: str,
        external_request_id: str | None = None,
    ) -> PortFailure:
        detail: dict[str, Any] = {"stage": stage, "code": code.value, "retryable": retryable}
        if error is not None:
            detail.update(type=type(error).__name__, message=str(error)[:2000])
            status = getattr(error, "status_code", None)
            if status is not None:
                detail["status_code"] = status
                body = getattr(getattr(error, "response", None), "text", None)
                if body:
                    detail["response_body"] = str(body)[:2000]
        _write_json(record_dir / "error.json", detail)
        record.save(state=state, error=detail)
        return PortFailure(
            PortError(
                code=code,
                stage=Stage.REALIZE,
                retryable=retryable,
                safe_message=message,
                internal_details_ref=f"{record.data.get('job_id')}/{RECORD_DIR}/error.json",
                external_request_id=external_request_id,
            )
        )

    def _mark(self, record: _Record, name: str, *, once: bool = False) -> None:
        """기록의 timeline에 첫 호출 시작부터 지난 초를 남긴다."""
        timeline = dict(record.data.get("timeline") or {})
        if once and name in timeline:
            return
        t0 = record.data.get("t0") or self.wall()
        timeline[name] = round(self.wall() - float(t0), 2)
        record.save(timeline=timeline)

    def _retry_note(self, record: _Record, step: str, error: BaseException) -> None:
        retries = list(record.data.get("retries", []))
        retries.append({"step": step, "type": type(error).__name__, "message": str(error)[:300]})
        record.save(retries=retries)

    def _upload_and_submit(self, request, garment, record, record_dir) -> str:
        try:
            plan = self.plan(request, garment)
        except FileNotFoundError as error:
            raise self._fail(
                record,
                record_dir,
                code=ErrorCode.EXTERNAL_SUBMIT_UNKNOWN,
                retryable=False,
                message="실사화할 렌더 클립을 찾지 못했습니다.",
                state=FAILED,
                error=error,
                stage="input",
            ) from error
        settings: RenderToRealSettings = plan.pop("settings")
        _write_json(record_dir / "plan.json", plan)
        record.save(estimate=plan["estimate"])
        if plan["estimate"]["usd"] > self.options.max_cost_usd:
            raise self._fail(
                record,
                record_dir,
                code=ErrorCode.BUDGET_LIMIT,
                retryable=False,
                message="실사화 예상 비용이 한도를 넘어 요청하지 않았습니다.",
                state=FAILED,
                stage="budget",
            )

        clip = self.input_clip(request)
        assert clip is not None  # plan에서 확인했다
        video_url = self._call("upload", lambda: self.client.upload_file(clip), record)
        if isinstance(video_url, BaseException):
            raise self._fail(
                record,
                record_dir,
                code=ErrorCode.EXTERNAL_SUBMIT_UNKNOWN,
                retryable=False,  # 요청 ID가 없다(B1: retryable은 같은 ID 재조회만)
                message="렌더 클립을 실사화 서비스에 올리지 못했습니다.",
                state=FAILED,
                error=video_url,
                stage="upload",
            )
        self._mark(record, "uploaded")

        arguments = settings.to_arguments(video_url)
        _write_json(record_dir / "request.json", {"endpoint": ENDPOINT, "arguments": arguments})
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                handle = self.client.submit(ENDPOINT, arguments)
                break
            except Exception as error:  # noqa: BLE001 - 아래에서 종류를 가린다
                if submit_not_sent(error) and attempt < MAX_ATTEMPTS:
                    self._retry_note(record, "submit", error)
                    continue
                # 거절이 확실하면(연결 실패·429·503을 두 번, 또는 4xx) 실패, 아니면 접수 여부를 모름
                refused = submit_not_sent(error) or submit_rejected(error)
                raise self._fail(
                    record,
                    record_dir,
                    code=ErrorCode.EXTERNAL_SUBMIT_UNKNOWN,
                    retryable=False,  # 요청 ID가 없다(B1: retryable은 같은 ID 재조회만)
                    message="실사화 요청을 접수하지 못했습니다."
                    if refused
                    else "실사화 요청이 접수됐는지 확인하지 못했습니다.",
                    state=FAILED if refused else UNKNOWN,
                    error=error,
                    stage="submit",
                ) from error
        self._mark(record, "submitted")
        return str(handle.request_id)

    def _call(self, step: str, fn: Callable[[], Any], record: _Record) -> Any:
        """일시 오류면 한 번 더 시도한다. 끝내 실패하면 그 예외를 돌려준다(던지지 않는다)."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return fn()
            except Exception as error:  # noqa: BLE001 - 일시 오류만 다시 시도한다
                if is_transient(error) and attempt < MAX_ATTEMPTS:
                    self._retry_note(record, step, error)
                    continue
                return error
        raise AssertionError("도달하지 않는다")

    def _wait(self, request, request_id, record, record_dir, started) -> None:
        limit = self.options.wait_limit_s or float(request.timeout_seconds)
        deadline = started + limit
        failures = 0
        positions: list[int] = []
        logs: list[str] = []
        while True:
            if self.clock() > deadline:
                raise self._fail(
                    record,
                    record_dir,
                    code=ErrorCode.REALIZE_TIMEOUT,
                    retryable=True,
                    message="실사화 응답이 제시간에 오지 않았습니다.",
                    state=TIMEOUT,
                    stage="wait",
                    external_request_id=request_id,
                )
            try:
                status = self.client.status(ENDPOINT, request_id, with_logs=True)
                failures = 0
            except Exception as error:  # noqa: BLE001
                failures += 1
                if is_transient(error) and failures < MAX_ATTEMPTS:
                    self._retry_note(record, "status", error)
                    self.sleep(self.options.poll_interval_s)
                    continue
                raise self._fail(
                    record,
                    record_dir,
                    code=ErrorCode.EXTERNAL_SUBMIT_UNKNOWN,
                    retryable=True,
                    message="실사화 진행 상태를 확인하지 못했습니다.",
                    state=UNKNOWN,
                    error=error,
                    stage="status",
                    external_request_id=request_id,
                ) from error
            kind = type(status).__name__
            position = getattr(status, "position", None)
            if position is not None:
                positions.append(int(position))
            for entry in getattr(status, "logs", None) or []:
                message = entry.get("message") if isinstance(entry, dict) else str(entry)
                if message and message not in logs[-1:]:
                    logs.append(str(message))
            if kind in ("InProgress", "Completed"):
                self._mark(record, "processing", once=True)  # 대기열을 빠져나온 때
            if kind == "Completed":
                record.save(queue_positions=positions[-20:], logs=logs[-50:])
                error_text = getattr(status, "error", None)
                if error_text:
                    raise self._fail(
                        record,
                        record_dir,
                        code=ErrorCode.EXTERNAL_SUBMIT_UNKNOWN,
                        retryable=False,
                        message="실사화 서비스가 영상을 만들지 못했습니다.",
                        state=FAILED,
                        error=RuntimeError(f"{getattr(status, 'error_type', '')}: {error_text}"),
                        stage="generate",
                    )
                return
            self.sleep(self.options.poll_interval_s)

    def _fetch_result(self, request_id, record, record_dir) -> dict[str, Any]:
        response = self._call("result", lambda: self.client.result(ENDPOINT, request_id), record)
        if isinstance(response, BaseException) or not isinstance(response, dict):
            error = response if isinstance(response, BaseException) else ValueError(response)
            raise self._fail(
                record,
                record_dir,
                code=ErrorCode.EXTERNAL_SUBMIT_UNKNOWN,
                retryable=True,
                message="실사화 결과를 받지 못했습니다.",
                state=UNKNOWN,
                error=error,
                stage="result",
                external_request_id=request_id,
            )
        _write_json(record_dir / "response.json", response)
        return response

    def _download(self, response, output: Path, request_id, record, record_dir) -> None:
        try:
            url = response["video"]["url"]
        except (KeyError, TypeError) as error:
            raise self._fail(
                record,
                record_dir,
                code=ErrorCode.EXTERNAL_SUBMIT_UNKNOWN,
                retryable=False,
                message="실사화 결과에 영상이 없습니다.",
                state=FAILED,
                error=error,
                stage="download",
            ) from error
        partial = output.with_name(output.name + ".part")

        def fetch() -> None:
            self.downloader(url, partial)
            os.replace(partial, output)  # 다 받은 뒤에만 산출물 이름으로 바꾼다

        outcome = self._call("download", fetch, record)
        if isinstance(outcome, BaseException):
            partial.unlink(missing_ok=True)
            raise self._fail(
                record,
                record_dir,
                code=ErrorCode.EXTERNAL_SUBMIT_UNKNOWN,
                retryable=True,
                message="실사 영상을 내려받지 못했습니다.",
                state=COMPLETED,  # 생성은 끝났다: 다시 부르면 내려받기만 한다
                error=outcome,
                stage="download",
                external_request_id=request_id,
            )

    def _result(self, artifact_id: str, data: dict[str, Any]) -> PortResult:
        timeline = data.get("timeline", {})

        def gap(a: str, b: str) -> float | None:
            if timeline.get(a) is None or timeline.get(b) is None:
                return None
            return round(float(timeline[b]) - float(timeline[a]), 2)

        metrics: dict[str, Any] = {
            "external_request_id": data.get("request_id"),
            "cost_usd": (data.get("cost") or {}).get("usd"),
            "queue_s": gap("submitted", "processing"),
            "processing_s": gap("processing", "completed"),
            "upload_s": gap("started", "uploaded"),
            "download_s": gap("completed", "downloaded"),
            "estimated_cost_usd": (data.get("estimate") or {}).get("usd"),
            "retries": len(data.get("retries", [])),
        }
        return PortResult(
            schema_version=SCHEMA_VERSION,
            output_artifact_ids=[artifact_id],
            metrics={key: value for key, value in metrics.items() if value is not None},
            tool_versions=self.tool_versions(),
        )
