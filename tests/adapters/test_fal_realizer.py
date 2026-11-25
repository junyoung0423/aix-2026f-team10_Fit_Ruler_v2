"""fal 실사화 어댑터(D4) 테스트. 실제 fal 대신 가짜 대기열 클라이언트로 확인한다(비용 없음).

확인하는 것: 성공 흐름·기록·지표, 재시도 1회, 제출을 다시 보내지 않는 경우, 같은 요청 ID 재조회,
타임아웃, 비용 상한, 실패 코드, 팩토리.
"""

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("cv2")  # 선택 의존성 realize가 없으면 건너뛴다

from fitting.adapters.fal_ltx import (  # noqa: E402
    ENDPOINT,
    PROMPT_VERSION,
    FalRealizer,
    RealizeOptions,
)
from fitting.adapters.fal_ltx.factory import FalQueueClient, make_realizer  # noqa: E402
from fitting.domain import ErrorCode, GarmentVariant, Stage  # noqa: E402
from fitting.ports import PortFailure, PortRequest, Realizer  # noqa: E402
from server.wiring import AdapterConfigError, AdapterSettings  # noqa: E402
from tools.spike_fal_ltx import write_test_clip  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
GARMENT = GarmentVariant.model_validate_json(
    (ROOT / "data" / "garments" / "tee_regular_m_navy_v1.json").read_text(encoding="utf-8")
)


# --- fal 상태 객체 (fal_client와 같은 이름) ----------------------------------------------------


@dataclass
class Queued:
    position: int


@dataclass
class InProgress:
    logs: list[dict[str, Any]] | None


@dataclass
class Completed:
    logs: list[dict[str, Any]] | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    error_type: str | None = None


class HTTPError(Exception):
    """fal_client.FalClientHTTPError처럼 status_code가 있는 오류."""

    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class ConnectError(Exception):
    """httpx.ConnectError와 같은 이름: 요청이 서버에 닿지 않았다."""


class ReadTimeout(Exception):
    """httpx.ReadTimeout과 같은 이름: 보냈는데 응답을 못 받았다(접수 여부 모름)."""


class FakeQueue:
    def __init__(
        self,
        statuses: list[Any] | None = None,
        *,
        upload_errors: list[Exception] | None = None,
        submit_errors: list[Exception] | None = None,
        status_errors: list[Exception] | None = None,
        result_errors: list[Exception] | None = None,
        response: dict[str, Any] | None = None,
    ):
        self.statuses = list(
            statuses
            if statuses is not None
            else [Queued(2), InProgress([{"message": "step 1/15"}]), Completed()]
        )
        self.errors = {
            "upload": list(upload_errors or []),
            "submit": list(submit_errors or []),
            "status": list(status_errors or []),
            "result": list(result_errors or []),
        }
        self.response = response or {
            "video": {"url": "https://fal.example/out.mp4"},
            "seed": 42,
            "prompt": "3DREAL. test",
        }
        self.calls = {"upload": 0, "submit": 0, "status": 0, "result": 0}
        self.arguments: dict[str, Any] | None = None

    def _maybe_fail(self, step: str) -> None:
        self.calls[step] += 1
        if self.errors[step]:
            raise self.errors[step].pop(0)

    def upload_file(self, path: Path) -> str:
        self._maybe_fail("upload")
        return f"https://fal.example/upload/{Path(path).name}"

    def submit(self, application: str, arguments: dict[str, Any]) -> Any:
        assert application == ENDPOINT
        self._maybe_fail("submit")
        self.arguments = arguments
        return SimpleNamespace(request_id="req-123")

    def status(self, application: str, request_id: str, *, with_logs: bool = False) -> Any:
        assert request_id == "req-123"
        self._maybe_fail("status")
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    def result(self, application: str, request_id: str) -> dict[str, Any]:
        self._maybe_fail("result")
        return self.response


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(scope="module")
def clips(tmp_path_factory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("clips")
    return {
        "render": write_test_clip(root / "render.mp4", seconds=3.0, size=(540, 720)),
        "output": write_test_clip(root / "output.mp4", seconds=0.4, size=(96, 128)),
    }


class Downloader:
    def __init__(self, source: Path, failures: int = 0):
        self.source = source
        self.failures = failures
        self.calls = 0

    def __call__(self, url: str, path: Path) -> None:
        self.calls += 1
        if self.failures:
            self.failures -= 1
            raise ConnectError("다운로드 연결 끊김")
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.source, path)


def make(tmp_path: Path, clips, client: FakeQueue, *, failures: int = 0, **options) -> FalRealizer:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(exist_ok=True)
    shutil.copy(clips["render"], artifacts / "job1_render_clip.mp4")
    clock = FakeClock()
    return FalRealizer(
        client=client,
        artifacts_dir=artifacts,
        work_dir=tmp_path / "work",
        options=RealizeOptions(poll_interval_s=2.0, **options),
        downloader=Downloader(clips["output"], failures),
        clock=clock,
        wall=clock,
        sleep=clock.sleep,
    )


def request(timeout: int = 600) -> PortRequest:
    return PortRequest(
        job_id="job1",
        attempt_id="job1_realize_1",
        input_artifact_ids=["job1_render_clip"],
        config_version="test",
        timeout_seconds=timeout,
        trace_id="job1",
    )


def record(tmp_path: Path, name: str = "realize.json") -> dict[str, Any]:
    return json.loads((tmp_path / "work" / "job1" / "realize" / name).read_text(encoding="utf-8"))


# --- 성공 --------------------------------------------------------------------------------------


def test_success_saves_the_video_and_returns_contract_metrics(tmp_path, clips):
    client = FakeQueue()
    realizer = make(tmp_path, clips, client)
    result = realizer.realize(request(), GARMENT)

    assert result.output_artifact_ids == ["job1_video"]
    assert (tmp_path / "artifacts" / "job1_video.mp4").stat().st_size > 0
    assert not list((tmp_path / "artifacts").glob("*.part"))
    # B1 계약(Realizer 설명)의 지표 키
    contract = re.findall(r"`(\w+)`\((?:외부 요청 ID|대기|생성)\)|`(cost_usd)`", Realizer.__doc__)
    keys = {a or b for a, b in contract}
    assert keys == {"external_request_id", "cost_usd", "queue_s", "processing_s"}
    assert keys <= set(result.metrics)
    assert result.metrics["external_request_id"] == "req-123"
    assert result.metrics["cost_usd"] > 0
    assert result.metrics["retries"] == 0
    assert result.tool_versions["fal_endpoint"] == ENDPOINT


def test_request_settings_follow_the_spike(tmp_path, clips):
    client = FakeQueue()
    make(tmp_path, clips, client).realize(request(), GARMENT)
    arguments = client.arguments

    assert arguments["generate_audio"] is False  # 오디오 끔
    assert arguments["num_frames"] == 65  # 3초 24fps → 8k + 1
    assert arguments["intensity"] == "light"
    assert arguments["prompt"].startswith("3DREAL.")
    assert "navy cotton jersey" in arguments["prompt"]  # 의류 원단 설명
    assert arguments["video_url"].startswith("https://fal.example/upload/")


def test_records_are_written_without_the_key(tmp_path, clips, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "secret-test-key-123")
    make(tmp_path, clips, FakeQueue()).realize(request(), GARMENT)

    folder = tmp_path / "work" / "job1" / "realize"
    assert {p.name for p in folder.iterdir()} == {
        "plan.json",
        "request.json",
        "response.json",
        "realize.json",
    }
    summary = record(tmp_path)
    assert summary["state"] == "succeeded"
    assert summary["request_id"] == "req-123"
    assert summary["estimate"]["usd"] >= summary["cost"]["usd"]
    for path in folder.iterdir():
        assert "secret-test-key-123" not in path.read_text(encoding="utf-8")


def test_calling_again_after_success_does_not_call_fal(tmp_path, clips):
    client = FakeQueue()
    realizer = make(tmp_path, clips, client)
    first = realizer.realize(request(), GARMENT)
    second = realizer.realize(request(), GARMENT)

    assert second == first
    assert client.calls == {"upload": 1, "submit": 1, "status": 3, "result": 1}


# --- 재시도 1회 --------------------------------------------------------------------------------


def test_transient_upload_and_status_errors_are_retried_once(tmp_path, clips):
    client = FakeQueue(upload_errors=[ConnectError("x")], status_errors=[HTTPError(503)])
    result = make(tmp_path, clips, client).realize(request(), GARMENT)

    assert result.metrics["retries"] == 2
    assert client.calls["upload"] == 2
    assert [r["step"] for r in record(tmp_path)["retries"]] == ["upload", "status"]


def test_upload_failing_twice_fails_before_anything_is_submitted(tmp_path, clips):
    client = FakeQueue(upload_errors=[ConnectError("x"), ConnectError("y")])
    with pytest.raises(PortFailure) as caught:
        make(tmp_path, clips, client).realize(request(), GARMENT)

    error = caught.value.error
    assert error.code == ErrorCode.EXTERNAL_SUBMIT_UNKNOWN
    assert error.stage == Stage.REALIZE
    assert error.retryable is False  # 요청 ID가 없다(B1: retryable은 같은 ID 재조회만)
    assert error.external_request_id is None
    assert client.calls["submit"] == 0


def test_submit_rejected_by_the_server_is_sent_again_once(tmp_path, clips):
    client = FakeQueue(submit_errors=[HTTPError(503)])
    make(tmp_path, clips, client).realize(request(), GARMENT)
    assert client.calls["submit"] == 2


def test_submit_without_a_response_is_not_sent_again(tmp_path, clips):
    """보냈는데 응답을 못 받으면 접수됐을 수 있어 다시 보내지 않는다(비용 두 번 방지)."""
    client = FakeQueue(submit_errors=[ReadTimeout("응답 없음")])
    with pytest.raises(PortFailure) as caught:
        make(tmp_path, clips, client).realize(request(), GARMENT)

    assert client.calls["submit"] == 1
    error = caught.value.error
    assert error.code == ErrorCode.EXTERNAL_SUBMIT_UNKNOWN
    assert error.retryable is False
    assert record(tmp_path)["state"] == "unknown"


def test_bad_request_is_not_retried(tmp_path, clips):
    client = FakeQueue(submit_errors=[HTTPError(422)])
    with pytest.raises(PortFailure) as caught:
        make(tmp_path, clips, client).realize(request(), GARMENT)
    assert client.calls["submit"] == 1
    assert caught.value.error.retryable is False
    assert record(tmp_path, "error.json")["status_code"] == 422
    assert record(tmp_path)["state"] == "failed"  # 서버가 거절했다(접수 안 됨)


@pytest.mark.parametrize("status", [500, 502, 504])
def test_server_error_on_submit_may_be_accepted_so_it_is_not_sent_again(tmp_path, clips, status):
    """500·502·504는 서버가 받은 뒤 실패했을 수 있어 다시 보내지 않고 '모름'으로 둔다."""
    client = FakeQueue(submit_errors=[HTTPError(status)])
    with pytest.raises(PortFailure) as caught:
        make(tmp_path, clips, client).realize(request(), GARMENT)
    assert client.calls["submit"] == 1
    assert caught.value.error.retryable is False
    assert record(tmp_path)["state"] == "unknown"


def test_unknown_submit_is_not_sent_again_on_the_next_call(tmp_path, clips):
    """접수 여부를 모르는 제출 뒤 같은 작업으로 다시 부르면 제출하지 않고 실패로 알린다."""
    client = FakeQueue(submit_errors=[ReadTimeout("응답 없음")])
    realizer = make(tmp_path, clips, client)
    with pytest.raises(PortFailure):
        realizer.realize(request(), GARMENT)

    with pytest.raises(PortFailure) as caught:
        realizer.realize(request(), GARMENT)
    error = caught.value.error
    assert error.retryable is False
    assert "다시 보내지 않았습니다" in error.safe_message
    assert client.calls == {"upload": 1, "submit": 1, "status": 0, "result": 0}
    assert record(tmp_path, "error.json")["stage"] == "resubmit_guard"

    # 사람이 확인한 뒤 명시적으로 켜면 다시 보낸다
    realizer.options = RealizeOptions(poll_interval_s=2.0, allow_resubmit=True)
    result = realizer.realize(request(), GARMENT)
    assert result.output_artifact_ids == ["job1_video"]
    assert client.calls["submit"] == 2


def test_succeeded_record_without_the_video_downloads_again_without_submitting(tmp_path, clips):
    client = FakeQueue()
    realizer = make(tmp_path, clips, client)
    realizer.realize(request(), GARMENT)
    (tmp_path / "artifacts" / "job1_video.mp4").unlink()

    result = realizer.realize(request(), GARMENT)

    assert (tmp_path / "artifacts" / "job1_video.mp4").is_file()
    assert result.metrics["external_request_id"] == "req-123"
    assert client.calls["submit"] == 1  # 다시 제출하지 않았다
    assert client.calls["result"] == 1  # 기록의 결과 주소로 내려받기만 다시 했다
    assert realizer.downloader.calls == 2


# --- 같은 요청 ID 재조회와 타임아웃 -------------------------------------------------------------


def test_timeout_keeps_the_request_id_and_the_next_call_requeries_it(tmp_path, clips):
    client = FakeQueue(statuses=[Queued(5)])
    realizer = make(tmp_path, clips, client)
    with pytest.raises(PortFailure) as caught:
        realizer.realize(request(timeout=10), GARMENT)

    error = caught.value.error
    assert error.code == ErrorCode.REALIZE_TIMEOUT
    assert error.retryable is True
    assert error.external_request_id == "req-123"
    assert record(tmp_path)["state"] == "timeout"

    client.statuses = [InProgress(None), Completed()]
    result = realizer.realize(request(timeout=10), GARMENT)

    assert result.metrics["external_request_id"] == "req-123"
    assert client.calls["submit"] == 1  # 다시 제출하지 않았다
    assert client.calls["upload"] == 1
    assert record(tmp_path)["resumed_from"] == "timeout"


def test_status_failing_twice_keeps_the_id_for_requery(tmp_path, clips):
    client = FakeQueue(status_errors=[HTTPError(502), HTTPError(502)])
    with pytest.raises(PortFailure) as caught:
        make(tmp_path, clips, client).realize(request(), GARMENT)

    error = caught.value.error
    assert error.code == ErrorCode.EXTERNAL_SUBMIT_UNKNOWN
    assert error.retryable is True
    assert error.external_request_id == "req-123"


def test_download_failing_twice_can_be_finished_without_generating_again(tmp_path, clips):
    client = FakeQueue()
    realizer = make(tmp_path, clips, client, failures=2)
    with pytest.raises(PortFailure) as caught:
        realizer.realize(request(), GARMENT)
    assert caught.value.error.external_request_id == "req-123"
    assert record(tmp_path)["state"] == "completed"
    assert (tmp_path / "work" / "job1" / "realize" / "response.json").is_file()

    result = realizer.realize(request(), GARMENT)  # 내려받기만 다시 한다
    assert result.output_artifact_ids == ["job1_video"]
    assert client.calls["submit"] == 1
    assert client.calls["result"] == 1


def test_generation_error_is_final_and_does_not_ask_for_requery(tmp_path, clips):
    client = FakeQueue(statuses=[Completed(error="NSFW", error_type="content_policy")])
    with pytest.raises(PortFailure) as caught:
        make(tmp_path, clips, client).realize(request(), GARMENT)

    error = caught.value.error
    assert error.code == ErrorCode.REALIZE_FAILED
    assert error.retryable is False
    assert error.external_request_id is None  # 재조회하지 말라는 뜻
    assert record(tmp_path)["request_id"] == "req-123"  # 기록에는 남긴다
    assert "NSFW" in record(tmp_path, "error.json")["message"]


def _fail_upload_twice(tmp_path, clips):
    return make(tmp_path, clips, FakeQueue(upload_errors=[ConnectError("x"), ConnectError("y")]))


def _fail_submit_rejected(tmp_path, clips):
    return make(tmp_path, clips, FakeQueue(submit_errors=[HTTPError(503), HTTPError(503)]))


def _fail_submit_bad_request(tmp_path, clips):
    return make(tmp_path, clips, FakeQueue(submit_errors=[HTTPError(422)]))


def _fail_submit_without_response(tmp_path, clips):
    return make(tmp_path, clips, FakeQueue(submit_errors=[ReadTimeout("응답 없음")]))


def _fail_status_twice(tmp_path, clips):
    return make(tmp_path, clips, FakeQueue(status_errors=[HTTPError(502), HTTPError(502)]))


def _fail_result_twice(tmp_path, clips):
    return make(tmp_path, clips, FakeQueue(result_errors=[HTTPError(502), HTTPError(502)]))


def _fail_download_twice(tmp_path, clips):
    return make(tmp_path, clips, FakeQueue(), failures=2)


def _fail_generation(tmp_path, clips):
    return make(tmp_path, clips, FakeQueue(statuses=[Completed(error="NSFW", error_type="x")]))


def _fail_without_video_url(tmp_path, clips):
    return make(tmp_path, clips, FakeQueue(response={"seed": 1}))


def _fail_over_budget(tmp_path, clips):
    return make(tmp_path, clips, FakeQueue(), max_cost_usd=0.01)


def _fail_missing_clip(tmp_path, clips):
    realizer = make(tmp_path, clips, FakeQueue())
    (tmp_path / "artifacts" / "job1_render_clip.mp4").unlink()
    return realizer


def _fail_timeout(tmp_path, clips):
    return make(tmp_path, clips, FakeQueue(statuses=[Queued(5)]))


def _fail_bad_prompt(tmp_path, clips):
    return make(tmp_path, clips, FakeQueue(), garment_text="  ")


def _fail_resubmit_guard(tmp_path, clips):
    realizer = _fail_submit_without_response(tmp_path, clips)
    with pytest.raises(PortFailure):
        realizer.realize(request(), GARMENT)
    return realizer  # 다음 호출이 "접수 여부를 모르는 제출을 다시 보내지 않음"으로 실패한다


@pytest.mark.parametrize(
    "scenario",
    [
        _fail_upload_twice,
        _fail_submit_rejected,
        _fail_submit_bad_request,
        _fail_submit_without_response,
        _fail_status_twice,
        _fail_result_twice,
        _fail_download_twice,
        _fail_generation,
        _fail_without_video_url,
        _fail_over_budget,
        _fail_missing_clip,
        _fail_timeout,
        _fail_bad_prompt,
        _fail_resubmit_guard,
    ],
)
def test_retryable_failures_always_carry_the_request_id(tmp_path, clips, scenario):
    """11/17 결정: `retryable=true`는 같은 요청 ID 재조회만 뜻한다. ID 없는 실패는 false다."""
    realizer = scenario(tmp_path, clips)
    with pytest.raises(PortFailure) as caught:
        realizer.realize(request(timeout=10), GARMENT)

    error = caught.value.error
    assert error.stage == Stage.REALIZE
    if error.retryable:
        assert error.external_request_id == "req-123"
    else:
        assert error.external_request_id is None


def test_garment_text_overrides_the_prompt(tmp_path, clips):
    realizer = make(tmp_path, clips, FakeQueue(), garment_text="plain black cotton T-shirt")
    prompt = realizer.plan(request(), GARMENT)["arguments_preview"]["prompt"]
    assert "plain black cotton T-shirt" in prompt and "navy" not in prompt


# --- D5 프롬프트 템플릿 --------------------------------------------------------------------------


def test_prompt_template_version_is_recorded(tmp_path, clips):
    client = FakeQueue()
    result = make(tmp_path, clips, client).realize(request(), GARMENT)

    assert result.tool_versions["prompt"] == PROMPT_VERSION
    plan = record(tmp_path, "plan.json")
    assert plan["prompt"]["version"] == PROMPT_VERSION
    assert plan["prompt"]["subject"] == "person"
    assert client.arguments["prompt"] == f"3DREAL. {plan['prompt']['text']}"
    assert "negative_prompt" not in client.arguments  # fal 기본값을 덮어쓰지 않는다


def test_subject_option_switches_to_the_mannequin(tmp_path, clips):
    realizer = make(tmp_path, clips, FakeQueue(), subject="mannequin")
    prompt = realizer.plan(request(), GARMENT)["arguments_preview"]["prompt"]
    assert "a matte light gray mannequin wearing" in prompt


def test_bad_prompt_settings_fail_before_upload(tmp_path, clips):
    client = FakeQueue()
    with pytest.raises(PortFailure) as caught:
        make(tmp_path, clips, client, garment_text="  ").realize(request(), GARMENT)

    error = caught.value.error
    assert error.code == ErrorCode.EXTERNAL_SUBMIT_UNKNOWN
    assert error.retryable is False
    assert client.calls["upload"] == 0
    assert record(tmp_path)["error"]["stage"] == "prepare"


# --- 입력·비용 ---------------------------------------------------------------------------------


def test_budget_limit_stops_before_upload(tmp_path, clips):
    client = FakeQueue()
    with pytest.raises(PortFailure) as caught:
        make(tmp_path, clips, client, max_cost_usd=0.01).realize(request(), GARMENT)

    assert caught.value.error.code == ErrorCode.BUDGET_LIMIT
    assert client.calls["upload"] == 0


def test_missing_render_clip(tmp_path, clips):
    realizer = make(tmp_path, clips, FakeQueue())
    (tmp_path / "artifacts" / "job1_render_clip.mp4").unlink()
    with pytest.raises(PortFailure) as caught:
        realizer.realize(request(), GARMENT)
    assert caught.value.error.retryable is False
    assert "렌더 클립" in caught.value.error.safe_message


def test_long_clip_is_capped_to_the_resolution_limit(tmp_path, clips):
    long_clip = write_test_clip(tmp_path / "long.mp4", seconds=8.0, size=(96, 128))
    realizer = make(tmp_path, clips, FakeQueue())
    shutil.copy(long_clip, tmp_path / "artifacts" / "job1_render_clip.mp4")
    plan = realizer.plan(request(), GARMENT)
    assert plan["estimate"]["frames"] == 137  # 720p 약 6초 → 24fps 144프레임 안의 8k + 1


# --- 팩토리 ------------------------------------------------------------------------------------


def settings(tmp_path: Path, key: bool) -> AdapterSettings:
    return AdapterSettings(
        artifacts_dir=tmp_path / "artifacts",
        work_dir=tmp_path / "work",
        blender_path=None,
        smpl_model_dir=None,
        fal_key_present=key,
        repo_root=ROOT,
    )


def test_factory_needs_the_key(tmp_path):
    with pytest.raises(AdapterConfigError, match="FAL_KEY"):
        make_realizer(settings(tmp_path, key=False))


def test_factory_builds_the_realizer_with_fal_client(tmp_path):
    pytest.importorskip("fal_client")
    adapter = make_realizer(settings(tmp_path, key=True))
    assert isinstance(adapter, FalRealizer)
    assert adapter.artifacts_dir == tmp_path / "artifacts"
    assert callable(getattr(adapter, "realize", None))
    # 설치된 fal_client에서 제출할 때 내부 재시도를 끌 수 있어야 한다(내부 구조가 바뀌면 알 수 있게)
    assert isinstance(adapter.client, FalQueueClient)
    assert adapter.client.retry_guard is True
    assert "submit_retry_guard=on" in adapter.tool_versions()["fal_client"]


class FakeFalModule:
    """fal_client 모듈 모양의 가짜. 제출할 때 내부 재시도 횟수(client.MAX_ATTEMPTS)를 적어 둔다."""

    def __init__(self, fail: bool = False) -> None:
        self.client = SimpleNamespace(MAX_ATTEMPTS=10)
        self.fail = fail
        self.seen: list[int] = []

    def submit(self, application, arguments):
        self.seen.append(self.client.MAX_ATTEMPTS)
        if self.fail:
            raise ReadTimeout("응답 없음")
        return SimpleNamespace(request_id="req-1")

    def status(self, application, request_id, *, with_logs=False):
        self.seen.append(self.client.MAX_ATTEMPTS)
        return Completed()


def test_queue_client_turns_off_internal_retries_only_while_submitting():
    module = FakeFalModule()
    queue = FalQueueClient(module)

    assert queue.submit(ENDPOINT, {}).request_id == "req-1"
    queue.status(ENDPOINT, "req-1")

    assert module.seen == [1, 10]  # 제출만 1번, 조회는 fal_client 내부 재시도 그대로
    assert module.client.MAX_ATTEMPTS == 10  # 되돌렸다


def test_queue_client_restores_retries_after_a_failed_submit():
    module = FakeFalModule(fail=True)
    with pytest.raises(ReadTimeout):
        FalQueueClient(module).submit(ENDPOINT, {})
    assert module.seen == [1]
    assert module.client.MAX_ATTEMPTS == 10


def test_queue_client_without_the_internal_setting_still_submits():
    module = SimpleNamespace(submit=lambda application, arguments: "ok")
    queue = FalQueueClient(module)
    assert queue.retry_guard is False
    assert queue.submit(ENDPOINT, {}) == "ok"


def test_guard_really_sends_one_submit_with_the_installed_fal_client(monkeypatch):
    """설치된 fal_client로 확인: 응답 없는 제출을 그냥 부르면 10번, 래퍼로 부르면 1번 나간다.

    네트워크는 쓰지 않는다. fal_client 내부 요청 함수를 가짜(항상 ReadTimeout)로 바꾼다.
    """
    fal_client = pytest.importorskip("fal_client")
    httpx = pytest.importorskip("httpx")
    inner = fal_client.client
    monkeypatch.setenv("FAL_KEY", "dummy-not-a-real-key")
    calls = {"n": 0}

    def no_network(client, method, url, **kwargs):
        calls["n"] += 1
        raise httpx.ReadTimeout("가짜 시간 초과", request=httpx.Request(method, url))

    monkeypatch.setattr(inner, "_request", no_network)
    monkeypatch.setattr(inner.time, "sleep", lambda seconds: None)

    with pytest.raises(httpx.ReadTimeout):
        FalQueueClient(fal_client).submit("fal-ai/test/app", {"a": 1})
    assert calls["n"] == 1
    assert inner.MAX_ATTEMPTS == 10

    calls["n"] = 0
    with pytest.raises(httpx.ReadTimeout):
        fal_client.submit("fal-ai/test/app", {"a": 1})
    assert calls["n"] == 10  # 래퍼 없이 부르면 내부에서 10번 보낸다
