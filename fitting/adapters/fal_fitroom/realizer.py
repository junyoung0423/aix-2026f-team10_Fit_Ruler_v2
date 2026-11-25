"""피팅룸 실사화 어댑터 (`Realizer` 포트, #10). 렌더를 핏 참고로 써서 매장 피팅룸의 실제 사람이
제자리에서 한 바퀴 도는 영상을 만든다.

단계 (모두 fal, 이름은 기록·지표에 쓴다)
1. `photo`: 렌더 클립 첫 프레임 → 실사 사진 `{job}_realize_fit_frame.png` (Nano Banana Pro 편집).
   체형·자세·구도·옷 핏은 렌더 그대로 두라고 지시하고, 검증 어댑터가 이 사진을 렌더와 겹쳐 핏을 본다
2. `pose`(옵션 `pose_step`, 기본 끔): 1의 사진에서 팔을 내린 정면 사진 `{job}_realize_pose.png`
   (Nano Banana Pro 편집)
3. `turn`: 시작·끝을 1의 사진(2를 켜면 2의 사진)으로 둔 8초 회전 영상 `{job}_video.mp4`
   (Veo 3.1 Fast first-last-frame, `turn_model="standard"`이면 Veo 3.1 표준판)

예전에 있던 "핏 옮기기"(렌더 클립 + 사진 → LTX render-to-real의 마지막 프레임)는 뺐다. 사진 문장에
핏 지시와 옷과 대비되는 바지를 넣은 뒤로는 1의 사진이 렌더 핏을 더 잘 지켰고(IoU 0.973 대 0.948),
빼면 실사화가 약 2분 짧아진다(docs/realize-fitroom.md 2.6절).

계약 출력은 `REALIZE_OUTPUTS`(실사 영상)이고, 중간 사진은 계약 밖 산출물로 같은 폴더에 두고 지표
(`realize_*_artifact_id`)로 알린다(C5 옷 마스크와 같은 방식). 검증 어댑터가 이름 규칙으로 찾는다.

제출·재조회 규칙은 D4 어댑터(`fal_ltx.realizer`)와 같다.
- 외부 호출(업로드·상태·결과·내려받기)은 일시 오류면 한 번 더 시도한다. **제출은 한 번만** 보낸다
  (요청이 fal에 닿지 않았다고 확실할 때만 한 번 더).
- 단계마다 요청 ID와 상태를 `work_dir/{job}/realize/realize.json`에 남긴다. 같은 작업으로 다시
  부르면 **끝난 단계는 건너뛰고**, 진행 중이던 단계는 다시 제출하지 않고 같은 요청 ID로 결과만
  받는다.
- 접수 여부를 모르는 제출(요청 ID 없음) 뒤에 다시 부르면 다시 보내지 않고 실패로 알린다
  (`allow_resubmit`을 명시할 때만 다시 보낸다).
- 대기 한도(기본 `PortRequest.timeout_seconds`)는 모든 단계에 한 번 적용한다. 넘으면
  `REALIZE_TIMEOUT`(retryable, 그 단계의 요청 ID).
- 제출 전에 단계별 예상 비용 합이 `max_cost_usd`를 넘으면 `BUDGET_LIMIT`.
- 회전 영상이 모두 검은 화면이면 `REALIZE_FAILED`(retryable=false). 다시 조회해도 같은 결과다.
- 상태·결과 조회는 제출할 때 기록한 모델로 한다(다시 부를 때 옵션이 바뀌어도 같은 요청을 본다).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fitting.adapters.fal_ltx.realizer import (
    MAX_ATTEMPTS,
    FalQueueClient,
    is_transient,
    submit_not_sent,
    submit_rejected,
)
from fitting.adapters.fal_ltx.render_to_real import download
from fitting.domain import SCHEMA_VERSION, ErrorCode, GarmentVariant, Stage
from fitting.ports import REALIZE_OUTPUTS, PortError, PortFailure, PortRequest, PortResult

from .prompts import FITROOM_PROMPT_VERSION, FitRoomPrompts, build_fitroom_prompts
from .steps import (
    PHOTO_ENDPOINT,
    PHOTO_USD,
    PRICE_CHECKED_ON,
    TURN_ENDPOINT,
    TURN_MODELS,
    first_frame,
    is_black,
    photo_arguments,
    read_image,
    sample_frames,
    turn_arguments,
    turn_cost,
    turn_endpoint,
    write_png,
)

REALIZE_VERSION = "realize_fitroom_v2"  # v1은 핏 옮기기(LTX)가 있던 흐름
RECORD_DIR = "realize"
SUMMARY_NAME = "realize.json"
# 단계 순서. `pose`는 옵션(`FitRoomOptions.pose_step`)이고 기본은 건너뛴다
STEPS = ("photo", "pose", "turn")
# 계약 밖 중간 산출물 역할(지표로 ID를 알린다). 1의 사진이 핏 확인 프레임이고 검증 어댑터가 같은
# 이름으로 찾는다
FIT_FRAME_ROLE, POSE_ROLE = "realize_fit_frame", "realize_pose"
# 기본 옵션(④ Veo 3.1 Fast) 기준 단계별 모델. 옵션에 맞춘 값은 `FitRoomRealizer.endpoints()`
ENDPOINTS = {
    "photo": PHOTO_ENDPOINT,
    "pose": PHOTO_ENDPOINT,
    "turn": TURN_ENDPOINT,
}

# 단계 상태 (realize.json의 steps.{이름}.state)
SUBMITTED, COMPLETED, DONE, FAILED, TIMEOUT, UNKNOWN = (
    "submitted",
    "completed",
    "done",
    "failed",
    "timeout",
    "unknown",
)
_RESUMABLE = {SUBMITTED, COMPLETED, TIMEOUT, UNKNOWN}

Downloader = Callable[[str, Path], None]


@dataclass
class FitRoomOptions:
    seed: int | None = 42  # 회전 영상에 보내는 시드(사진 편집은 fal이 고른다)
    max_cost_usd: float = 3.0  # 예상 비용 합 상한(기본 흐름 $0.95)
    poll_interval_s: float = 2.0
    wait_limit_s: float | None = None  # None이면 PortRequest.timeout_seconds
    garment_text: str | None = None  # 프롬프트 옷 설명(시험용)
    allow_resubmit: bool = False  # 접수 여부를 모르는 이전 제출이 있어도 다시 보낸다(비용 위험)
    photo_resolution: str = "2K"
    pose_step: bool = False  # 팔 내리기 사진. 끄면 1의 사진을 회전 영상의 시작·끝으로 바로 쓴다
    turn_duration_s: int = 8
    turn_resolution: str = "720p"
    turn_model: str = "fast"  # "fast": Veo 3.1 Fast(초당 $0.10) · "standard": 표준판($0.20)


@dataclass
class _Record:
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

    def step(self, name: str) -> dict[str, Any]:
        steps = self.data.setdefault("steps", {})
        return steps.setdefault(name, {"state": None})

    def save_step(self, name: str, **updates: Any) -> None:
        self.step(name).update(updates)
        self.save()


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


@dataclass
class FitRoomRealizer:
    """`Realizer` 포트 구현. 만들 때
    `fitting.adapters.fal_fitroom.factory.make_fitroom_realizer`."""

    client: FalQueueClient
    artifacts_dir: Path
    work_dir: Path
    options: FitRoomOptions = field(default_factory=FitRoomOptions)
    downloader: Downloader = download
    clock: Callable[[], float] = time.monotonic
    wall: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    client_version: str = "unknown"

    # --- 파일 -----------------------------------------------------------------------------

    def input_clip(self, request: PortRequest) -> Path | None:
        if not request.input_artifact_ids:
            return None
        directory = Path(self.artifacts_dir)
        exact = directory / f"{request.input_artifact_ids[0]}.mp4"
        if exact.is_file():
            return exact
        matches = sorted(directory.glob(f"{request.input_artifact_ids[0]}.*"))
        return matches[0] if matches else None

    def outputs(self, job_id: str) -> dict[str, Path]:
        directory = Path(self.artifacts_dir)
        return {
            "photo": directory / f"{job_id}_{FIT_FRAME_ROLE}.png",
            "pose": directory / f"{job_id}_{POSE_ROLE}.png",
            "turn": directory / f"{job_id}_{REALIZE_OUTPUTS[0]}.mp4",
        }

    def steps(self) -> tuple[str, ...]:
        """옵션에 맞춘 단계(순서대로)."""
        return tuple(s for s in STEPS if s != "pose" or self.options.pose_step)

    def endpoints(self) -> dict[str, str]:
        """옵션에 맞춘 단계별 fal 모델. Raises: ValueError — 모르는 회전 모델이다."""
        models = {**ENDPOINTS, "turn": turn_endpoint(self.options.turn_model)}
        return {step: models[step] for step in self.steps()}

    # --- 계획(드라이런) ---------------------------------------------------------------------

    def plan(self, request: PortRequest, garment: GarmentVariant) -> dict[str, Any]:
        """fal을 부르지 않고 단계별 모델·설정·예상 비용을 계산한다.

        Raises:
            FileNotFoundError: 렌더 클립이 없다.
            ValueError: 클립을 읽지 못했거나 설정·프롬프트가 틀렸다.
        """
        clip = self.input_clip(request)
        if clip is None:
            raise FileNotFoundError(f"렌더 클립이 없습니다: {request.input_artifact_ids}")
        _, info = sample_frames(clip, count=1)
        prompts = build_fitroom_prompts(garment, garment_text=self.options.garment_text)
        endpoints = self.endpoints()
        turn_arguments(  # 길이 검사
            "<사진>",
            prompts.turn,
            prompts.turn_negative,
            duration_s=self.options.turn_duration_s,
            resolution=self.options.turn_resolution,
            seed=self.options.seed,
        )
        estimate = {
            "photo": PHOTO_USD,
            "pose": PHOTO_USD,
            "turn": turn_cost(self.options.turn_duration_s, self.options.turn_model),
        }
        estimate = {step: estimate[step] for step in self.steps()}
        return {
            "version": REALIZE_VERSION,
            "input": {"path": clip.name, **info},
            "steps": list(self.steps()),
            "endpoints": endpoints,
            "turn_model": self.options.turn_model,
            "prompts": prompts.to_dict(),
            "estimate_usd": {**estimate, "total": round(sum(estimate.values()), 4)},
            "max_cost_usd": self.options.max_cost_usd,
            "price_checked_on": PRICE_CHECKED_ON,
        }

    # --- 포트 -------------------------------------------------------------------------------

    def tool_versions(self) -> dict[str, str]:
        return {
            "realize": REALIZE_VERSION,
            "prompt": FITROOM_PROMPT_VERSION,
            "photo_endpoint": PHOTO_ENDPOINT,
            "turn_endpoint": TURN_MODELS[self.options.turn_model][0]
            if self.options.turn_model in TURN_MODELS
            else f"unknown:{self.options.turn_model}",
            "fal_client": self.client_version,
            "price_checked_on": PRICE_CHECKED_ON,
        }

    def realize(self, request: PortRequest, garment: GarmentVariant) -> PortResult:
        record_dir = Path(self.work_dir) / request.job_id / RECORD_DIR
        record = _Record(record_dir / SUMMARY_NAME, self._load(record_dir))
        outputs = self.outputs(request.job_id)
        needed = [outputs[step] for step in self.steps()]
        if record.data.get("state") == DONE and all(p.is_file() for p in needed):
            return self._result(request.job_id, record)

        started = self.clock()
        record.save(
            job_id=request.job_id,
            attempt_id=request.attempt_id,
            version=REALIZE_VERSION,
            calls=int(record.data.get("calls", 0)) + 1,
            retries=record.data.get("retries", []),
            state="running",
            error=None,
        )
        record.data.setdefault("t0", self.wall())

        try:
            plan = self.plan(request, garment)
        except FileNotFoundError as error:
            raise self._fail(
                record,
                record_dir,
                "input",
                ErrorCode.EXTERNAL_SUBMIT_UNKNOWN,
                False,
                "실사화할 렌더 클립을 찾지 못했습니다.",
                FAILED,
                error,
            ) from error
        except ValueError as error:
            raise self._fail(
                record,
                record_dir,
                "prepare",
                ErrorCode.EXTERNAL_SUBMIT_UNKNOWN,
                False,
                "실사화 요청을 준비하지 못해 요청하지 않았습니다.",
                FAILED,
                error,
            ) from error
        _write_json(record_dir / "plan.json", plan)
        record.save(estimate=plan["estimate_usd"], prompts_version=FITROOM_PROMPT_VERSION)
        if plan["estimate_usd"]["total"] > self.options.max_cost_usd:
            raise self._fail(
                record,
                record_dir,
                "budget",
                ErrorCode.BUDGET_LIMIT,
                False,
                "실사화 예상 비용이 한도를 넘어 요청하지 않았습니다.",
                FAILED,
            )

        prompts = build_fitroom_prompts(garment, garment_text=self.options.garment_text)
        clip = self.input_clip(request)
        assert clip is not None
        deadline = started + (self.options.wait_limit_s or float(request.timeout_seconds))
        context = {
            "request": request,
            "garment": garment,
            "clip": clip,
            "prompts": prompts,
            "outputs": outputs,
            "record_dir": record_dir,
            "deadline": deadline,
        }
        for name in self.steps():
            self._run_step(name, record, context)
        record.save(state=DONE, error=None)
        return self._result(request.job_id, record)

    # --- 단계 하나 ---------------------------------------------------------------------------

    def _run_step(self, name: str, record: _Record, ctx: dict[str, Any]) -> None:
        output: Path = ctx["outputs"][name]
        step = record.step(name)
        state, request_id = step.get("state"), step.get("request_id")
        if state == DONE and output.is_file():
            return
        if state == UNKNOWN and not request_id and not self.options.allow_resubmit:
            raise self._fail(
                record,
                ctx["record_dir"],
                f"{name}:resubmit_guard",
                ErrorCode.EXTERNAL_SUBMIT_UNKNOWN,
                False,
                "이전 실사화 요청이 접수됐는지 알 수 없어 다시 보내지 않았습니다.",
                UNKNOWN,
                step_name=name,
            )
        resumed = bool(request_id) and state in (_RESUMABLE | {DONE})
        if not resumed:
            arguments = self._arguments(name, record, ctx)
            request_id = self._submit(name, arguments, record, ctx)
        response = step.get("response") if step.get("state") in (COMPLETED, DONE) else None
        if response is None:
            self._wait(name, request_id, record, ctx)
            response = self._fetch(name, request_id, record, ctx)
        self._finish(name, response, request_id, record, ctx)
        record.save_step(name, state=DONE, finished_at=round(self.wall() - record.data["t0"], 2))

    def _arguments(self, name: str, record: _Record, ctx: dict[str, Any]) -> dict[str, Any]:
        prompts: FitRoomPrompts = ctx["prompts"]
        record_dir: Path = ctx["record_dir"]
        if name == "photo":
            still = write_png(record_dir / "render_still.png", first_frame(ctx["clip"]))
            url = self._upload(name, still, record, ctx)
            return photo_arguments(url, prompts.photo, resolution=self.options.photo_resolution)
        if name == "pose":
            url = self._upload(name, ctx["outputs"]["photo"], record, ctx)
            return photo_arguments(url, prompts.pose, resolution=self.options.photo_resolution)
        start = ctx["outputs"]["pose" if self.options.pose_step else "photo"]
        url = self._upload(name, start, record, ctx)
        return turn_arguments(
            url,
            prompts.turn,
            prompts.turn_negative,
            duration_s=self.options.turn_duration_s,
            resolution=self.options.turn_resolution,
            seed=self.options.seed,
        )

    def _upload(self, name: str, path: Path, record: _Record, ctx: dict[str, Any]) -> str:
        url = self._call(f"{name}:upload", lambda: self.client.upload_file(path), record)
        if isinstance(url, BaseException):
            raise self._fail(
                record,
                ctx["record_dir"],
                f"{name}:upload",
                ErrorCode.EXTERNAL_SUBMIT_UNKNOWN,
                False,
                "실사화 서비스에 파일을 올리지 못했습니다.",
                FAILED,
                url,
                step_name=name,
            )
        return str(url)

    def _submit(
        self, name: str, arguments: dict[str, Any], record: _Record, ctx: dict[str, Any]
    ) -> str:
        endpoint = self.endpoints()[name]
        _write_json(
            ctx["record_dir"] / f"{name}_request.json",
            {"endpoint": endpoint, "arguments": arguments},
        )
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                handle = self.client.submit(endpoint, arguments)
                break
            except Exception as error:  # noqa: BLE001 - 아래에서 종류를 가린다
                if submit_not_sent(error) and attempt < MAX_ATTEMPTS:
                    self._retry_note(record, f"{name}:submit", error)
                    continue
                refused = submit_not_sent(error) or submit_rejected(error)
                raise self._fail(
                    record,
                    ctx["record_dir"],
                    f"{name}:submit",
                    ErrorCode.EXTERNAL_SUBMIT_UNKNOWN,
                    False,
                    "실사화 요청을 접수하지 못했습니다."
                    if refused
                    else "실사화 요청이 접수됐는지 확인하지 못했습니다.",
                    FAILED if refused else UNKNOWN,
                    error,
                    step_name=name,
                ) from error
        request_id = str(handle.request_id)
        record.save_step(
            name,
            state=SUBMITTED,
            request_id=request_id,
            endpoint=endpoint,
            submitted_at=round(self.wall() - record.data["t0"], 2),
            response=None,
        )
        return request_id

    def _endpoint(self, name: str, record: _Record) -> str:
        """제출할 때 기록한 모델(없으면 지금 옵션의 모델)."""
        return str(record.step(name).get("endpoint") or self.endpoints()[name])

    def _wait(self, name: str, request_id: str, record: _Record, ctx: dict[str, Any]) -> None:
        endpoint = self._endpoint(name, record)
        failures = 0
        while True:
            if self.clock() > ctx["deadline"]:
                raise self._fail(
                    record,
                    ctx["record_dir"],
                    f"{name}:wait",
                    ErrorCode.REALIZE_TIMEOUT,
                    True,
                    "실사화 응답이 제시간에 오지 않았습니다.",
                    TIMEOUT,
                    step_name=name,
                    external_request_id=request_id,
                )
            try:
                status = self.client.status(endpoint, request_id, with_logs=False)
                failures = 0
            except Exception as error:  # noqa: BLE001
                failures += 1
                if is_transient(error) and failures < MAX_ATTEMPTS:
                    self._retry_note(record, f"{name}:status", error)
                    self.sleep(self.options.poll_interval_s)
                    continue
                raise self._fail(
                    record,
                    ctx["record_dir"],
                    f"{name}:status",
                    ErrorCode.EXTERNAL_SUBMIT_UNKNOWN,
                    True,
                    "실사화 진행 상태를 확인하지 못했습니다.",
                    UNKNOWN,
                    error,
                    step_name=name,
                    external_request_id=request_id,
                ) from error
            kind = type(status).__name__
            if kind == "Completed":
                error_text = getattr(status, "error", None)
                if error_text:
                    raise self._fail(
                        record,
                        ctx["record_dir"],
                        f"{name}:generate",
                        ErrorCode.REALIZE_FAILED,
                        False,
                        "실사화 서비스가 결과를 만들지 못했습니다.",
                        FAILED,
                        RuntimeError(f"{getattr(status, 'error_type', '')}: {error_text}"),
                        step_name=name,
                    )
                return
            self.sleep(self.options.poll_interval_s)

    def _fetch(
        self, name: str, request_id: str, record: _Record, ctx: dict[str, Any]
    ) -> dict[str, Any]:
        endpoint = self._endpoint(name, record)
        response = self._call(
            f"{name}:result", lambda: self.client.result(endpoint, request_id), record
        )
        if isinstance(response, BaseException) or not isinstance(response, dict):
            error = response if isinstance(response, BaseException) else ValueError(response)
            raise self._fail(
                record,
                ctx["record_dir"],
                f"{name}:result",
                ErrorCode.EXTERNAL_SUBMIT_UNKNOWN,
                True,
                "실사화 결과를 받지 못했습니다.",
                UNKNOWN,
                error,
                step_name=name,
                external_request_id=request_id,
            )
        _write_json(ctx["record_dir"] / f"{name}_response.json", response)
        record.save_step(
            name,
            state=COMPLETED,
            response=response,
            completed_at=round(self.wall() - record.data["t0"], 2),
        )
        return response

    def _finish(
        self,
        name: str,
        response: dict[str, Any],
        request_id: str,
        record: _Record,
        ctx: dict[str, Any],
    ) -> None:
        """결과 파일을 내려받아 단계 산출물로 만든다."""
        record_dir: Path = ctx["record_dir"]
        output: Path = ctx["outputs"][name]
        try:
            if name in ("photo", "pose"):
                url = response["images"][0]["url"]
            else:
                url = response["video"]["url"]
        except (KeyError, IndexError, TypeError) as error:
            raise self._fail(
                record,
                record_dir,
                f"{name}:download",
                ErrorCode.REALIZE_FAILED,
                False,
                "실사화 결과에 파일이 없습니다.",
                FAILED,
                error,
                step_name=name,
            ) from error
        raw = (
            record_dir
            / {
                "photo": "photo.png",
                "pose": "pose.png",
                "turn": "turn.mp4",
            }[name]
        )
        outcome = self._call(f"{name}:download", lambda: self._fetch_file(url, raw), record)
        if isinstance(outcome, BaseException):
            raise self._fail(
                record,
                record_dir,
                f"{name}:download",
                ErrorCode.EXTERNAL_SUBMIT_UNKNOWN,
                True,
                "실사화 결과를 내려받지 못했습니다.",
                COMPLETED,
                outcome,
                step_name=name,
                external_request_id=request_id,
            )
        try:
            if name in ("photo", "pose"):
                write_png(output, read_image(raw))
            else:
                frames, info = sample_frames(raw)
                if is_black(frames):
                    raise _BlackOutput(info)
                partial = output.with_name(output.name + ".part")
                partial.write_bytes(raw.read_bytes())
                os.replace(partial, output)
                record.save_step(name, video=info)
        except _BlackOutput as black:
            raise self._fail(
                record,
                record_dir,
                f"{name}:black",
                ErrorCode.REALIZE_FAILED,
                False,
                "실사화 결과가 검은 화면이었습니다.",
                FAILED,
                RuntimeError(f"black output {black.info}"),
                step_name=name,
            ) from None
        except (ValueError, OSError) as error:
            raise self._fail(
                record,
                record_dir,
                f"{name}:output",
                ErrorCode.REALIZE_FAILED,
                False,
                "실사화 결과 파일을 읽지 못했습니다.",
                FAILED,
                error,
                step_name=name,
            ) from error

    def _fetch_file(self, url: str, path: Path) -> None:
        partial = path.with_name(path.name + ".part")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.downloader(url, partial)
        os.replace(partial, path)

    # --- 공통 -------------------------------------------------------------------------------

    @staticmethod
    def _load(record_dir: Path) -> dict[str, Any]:
        path = record_dir / SUMMARY_NAME
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _call(self, step: str, fn: Callable[[], Any], record: _Record) -> Any:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return fn()
            except Exception as error:  # noqa: BLE001 - 일시 오류만 다시 시도한다
                if is_transient(error) and attempt < MAX_ATTEMPTS:
                    self._retry_note(record, step, error)
                    continue
                return error
        raise AssertionError("도달하지 않는다")

    def _retry_note(self, record: _Record, step: str, error: BaseException) -> None:
        retries = list(record.data.get("retries", []))
        retries.append({"step": step, "type": type(error).__name__, "message": str(error)[:300]})
        record.save(retries=retries)

    def _fail(
        self,
        record: _Record,
        record_dir: Path,
        stage: str,
        code: ErrorCode,
        retryable: bool,
        message: str,
        state: str,
        error: BaseException | None = None,
        *,
        step_name: str | None = None,
        external_request_id: str | None = None,
    ) -> PortFailure:
        detail: dict[str, Any] = {"stage": stage, "code": code.value, "retryable": retryable}
        if error is not None:
            detail.update(type=type(error).__name__, message=str(error)[:2000])
            status = getattr(error, "status_code", None)
            if status is not None:
                detail["status_code"] = status
        _write_json(record_dir / "error.json", detail)
        if step_name is not None:
            record.save_step(step_name, state=state)
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

    def _result(self, job_id: str, record: _Record) -> PortResult:
        steps = record.data.get("steps", {})
        outputs = self.outputs(job_id)

        def seconds(name: str) -> float | None:
            step = steps.get(name, {})
            if step.get("submitted_at") is None or step.get("completed_at") is None:
                return None
            return round(float(step["completed_at"]) - float(step["submitted_at"]), 2)

        estimate = record.data.get("estimate") or {}
        cost = sum(
            float(steps.get(n, {}).get("cost_usd") or estimate.get(n) or 0.0) for n in self.steps()
        )
        video = steps.get("turn", {}).get("video") or {}
        metrics: dict[str, Any] = {
            "external_request_id": steps.get("turn", {}).get("request_id"),
            "cost_usd": round(cost, 4),
            "estimated_cost_usd": estimate.get("total"),
            "photo_s": seconds("photo"),
            "pose_s": seconds("pose"),
            "turn_s": seconds("turn"),
            "retries": len(record.data.get("retries", [])),
            "video_width": video.get("width"),
            "video_height": video.get("height"),
            "realize_fit_frame_artifact_id": outputs["photo"].stem,
            "realize_pose_artifact_id": outputs["pose"].stem if self.options.pose_step else None,
        }
        return PortResult(
            schema_version=SCHEMA_VERSION,
            output_artifact_ids=[outputs["turn"].stem],
            metrics={k: v for k, v in metrics.items() if v is not None},
            tool_versions=self.tool_versions(),
        )


class _BlackOutput(Exception):
    def __init__(self, info: dict[str, float]) -> None:
        super().__init__("black output")
        self.info = info
