"""피팅룸 실사화 어댑터 테스트. 가짜 fal(사진 편집·Veo)과 합성 그림·영상만 쓴다(비용 없음)."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

cv2 = pytest.importorskip("cv2")  # 선택 의존성 realize가 없으면 건너뛴다
np = pytest.importorskip("numpy")

from fitting.adapters.fal_fitroom.realizer import (  # noqa: E402
    ENDPOINTS,
    FIT_FRAME_ROLE,
    REALIZE_VERSION,
    FitRoomOptions,
    FitRoomRealizer,
)
from fitting.adapters.fal_fitroom.steps import (  # noqa: E402
    PHOTO_ENDPOINT,
    TURN_ENDPOINT,
    TURN_MODELS,
)
from fitting.adapters.media.validator import FIT_FRAME_ROLE as VALIDATOR_FIT_FRAME  # noqa: E402
from fitting.domain import ErrorCode, GarmentVariant, Stage  # noqa: E402
from fitting.ports import PortFailure, PortRequest, PortResult  # noqa: E402
from tests.adapters import visual_clips as vc  # noqa: E402
from tests.adapters.test_fal_realizer import Completed, FakeClock, Queued, ReadTimeout  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
GARMENT = GarmentVariant.model_validate_json(
    (ROOT / "data" / "garments" / "tee_regular_m_navy_v1.json").read_text(encoding="utf-8")
)
JOB = "jobfr"


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("fitroom")
    render, _ = vc.render_clip(root / "render.mp4")
    photo, _ = vc.draw((180, 240), real=True, seed=1)
    ok, buf = cv2.imencode(".png", photo)
    (root / "photo.png").write_bytes(buf.tobytes())
    turn = vc.real_clip(root / "turn.mp4")
    black = vc.write_clip(root / "black.mp4", [np.zeros((240, 180, 3), np.uint8)] * 25)
    return {"render": render, "photo": root / "photo.png", "turn": turn, "black": black}


class FakeFal:
    """모델별로 제출을 기록하는 가짜 fal. 요청 ID는 `req-{단계}-{순번}`."""

    def __init__(self, *, statuses: dict[str, list[Any]] | None = None,
                 submit_errors: dict[str, list[Exception]] | None = None,
                 turn_url: str = "https://fal.example/turn.mp4"):
        self.statuses = statuses or {}
        self.submit_errors = submit_errors or {}
        self.turn_url = turn_url
        self.submits: list[tuple[str, dict[str, Any]]] = []
        self.uploads: list[str] = []
        self.ids: dict[str, str] = {}
        self.queries: list[tuple[str, str]] = []  # (모델, 요청 ID): 상태·결과 조회

    def upload_file(self, path: Path) -> str:
        self.uploads.append(Path(path).name)
        return f"https://fal.example/upload/{Path(path).name}"

    def _step(self, endpoint: str, arguments: dict[str, Any]) -> str:
        if endpoint == PHOTO_ENDPOINT:
            return "pose" if any(e == PHOTO_ENDPOINT for e, _ in self.submits) else "photo"
        return "turn"

    def submit(self, application: str, arguments: dict[str, Any]) -> Any:
        step = self._step(application, arguments)
        errors = self.submit_errors.get(step) or []
        if errors:
            raise errors.pop(0)
        self.submits.append((application, arguments))
        request_id = f"req-{step}-{len(self.submits)}"
        self.ids[request_id] = step
        return SimpleNamespace(request_id=request_id)

    def status(self, application: str, request_id: str, *, with_logs: bool = False) -> Any:
        self.queries.append((application, request_id))
        queue = self.statuses.get(self.ids[request_id]) or []
        return queue.pop(0) if queue else Completed()

    def result(self, application: str, request_id: str) -> dict[str, Any]:
        self.queries.append((application, request_id))
        step = self.ids[request_id]
        if step in ("photo", "pose"):
            return {"images": [{"url": f"https://fal.example/{step}.png"}]}
        return {"video": {"url": self.turn_url}}


class Files:
    """주소 → 합성 파일. `fail[이름]`번 만큼 내려받기를 실패시킨다."""

    def __init__(self, media: dict[str, Path], fail: dict[str, int] | None = None):
        self.media = media
        self.fail = dict(fail or {})
        self.calls: list[str] = []

    def __call__(self, url: str, path: Path) -> None:
        name = url.rsplit("/", 1)[-1].split(".")[0]
        self.calls.append(name)
        if self.fail.get(name):
            self.fail[name] -= 1
            raise ReadTimeout("download timed out")
        source = {"photo": "photo", "pose": "photo", "turn": "turn", "black": "black"}[name]
        path.write_bytes(self.media[source].read_bytes())


def make(tmp_path: Path, media, client: FakeFal, files: Files | None = None, **options):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(exist_ok=True)
    clip = artifacts / f"{JOB}_render_clip.mp4"
    if not clip.exists():
        clip.write_bytes(media["render"].read_bytes())
    clock = FakeClock()
    return FitRoomRealizer(
        client=client,
        artifacts_dir=artifacts,
        work_dir=tmp_path / "work",
        options=FitRoomOptions(poll_interval_s=1.0, **options),
        downloader=files or Files(media),
        clock=clock,
        wall=clock,
        sleep=clock.sleep,
        client_version="test",
    )


def request(timeout: int = 900) -> PortRequest:
    return PortRequest(job_id=JOB, attempt_id=f"{JOB}_realize_1",
                       input_artifact_ids=[f"{JOB}_render_clip"], config_version="test",
                       timeout_seconds=timeout, trace_id=JOB)


def record(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / "work" / JOB / "realize" / "realize.json").read_text("utf-8"))


def test_default_photo_pose_turn_saves_every_output(tmp_path, media):
    client = FakeFal()
    realizer = make(tmp_path, media, client)
    result = realizer.realize(request(), GARMENT)

    assert isinstance(result, PortResult)
    assert realizer.steps() == ("photo", "pose", "turn")  # 팔을 내린 자세 사진이 기본
    assert [e for e, _ in client.submits] == [PHOTO_ENDPOINT, PHOTO_ENDPOINT, TURN_ENDPOINT]
    assert result.output_artifact_ids == [f"{JOB}_video"]
    outputs = realizer.outputs(JOB)
    assert outputs["photo"].name == f"{JOB}_{FIT_FRAME_ROLE}.png"  # 사진이 핏 확인 프레임
    assert all(outputs[s].is_file() for s in ("photo", "pose", "turn"))
    m = result.metrics
    assert m["external_request_id"] == "req-turn-3"
    assert m["realize_fit_frame_artifact_id"] == f"{JOB}_{FIT_FRAME_ROLE}"
    assert m["realize_pose_artifact_id"] == f"{JOB}_realize_pose"
    assert "realize_photo_artifact_id" not in m and "transfer_s" not in m
    assert m["cost_usd"] == pytest.approx(0.15 + 0.15 + 0.40)  # 사진 2장 + 회전 4초
    assert m["estimated_cost_usd"] == pytest.approx(0.70)
    assert result.tool_versions["realize"] == REALIZE_VERSION == "realize_fitroom_v3"
    assert result.tool_versions["turn_endpoint"] == TURN_ENDPOINT
    assert "transfer_endpoint" not in result.tool_versions
    assert record(tmp_path)["state"] == "done"


def test_step_arguments_follow_the_experiments(tmp_path, media):
    client = FakeFal()
    make(tmp_path, media, client).realize(request(), GARMENT)
    photo, pose, turn = (a for _, a in client.submits)

    assert photo["aspect_ratio"] == "3:4" and photo["num_images"] == 1
    assert "fitting room" in photo["prompt"] and "light beige chino trousers" in photo["prompt"]
    assert "same looseness at the chest and waist" in photo["prompt"]  # 렌더 핏을 그대로 둔다
    assert photo["image_urls"] == ["https://fal.example/upload/render_still.png"]
    assert turn["first_frame_url"] == turn["last_frame_url"]
    assert "lower both arms" in pose["prompt"]
    assert pose["image_urls"] == [f"https://fal.example/upload/{JOB}_{FIT_FRAME_ROLE}.png"]
    assert turn["first_frame_url"].endswith(f"{JOB}_realize_pose.png")  # 팔 내린 자세에서 시작
    assert turn["duration"] == "4s" and turn["generate_audio"] is False and turn["seed"] == 42
    assert "a quarter turn" in turn["prompt"] and "face" in turn["negative_prompt"]
    assert "back view" in turn["negative_prompt"]  # 1/4 바퀴에서 뒤로 넘어가지 않게


def test_calling_again_after_success_does_not_call_fal(tmp_path, media):
    client = FakeFal()
    realizer = make(tmp_path, media, client)
    first = realizer.realize(request(), GARMENT)
    second = realizer.realize(request(), GARMENT)
    assert len(client.submits) == 3
    assert first.output_artifact_ids == second.output_artifact_ids


def test_budget_over_the_limit_stops_before_uploading(tmp_path, media):
    client = FakeFal()
    with pytest.raises(PortFailure) as caught:
        make(tmp_path, media, client, max_cost_usd=0.5).realize(request(), GARMENT)
    assert caught.value.error.code == ErrorCode.BUDGET_LIMIT
    assert caught.value.error.retryable is False
    assert client.submits == [] and client.uploads == []


def test_timeout_keeps_the_step_id_and_the_next_call_resumes_without_resubmitting(tmp_path, media):
    client = FakeFal(statuses={"turn": [Queued(1)] * 1000})
    realizer = make(tmp_path, media, client, wait_limit_s=60)
    with pytest.raises(PortFailure) as caught:
        realizer.realize(request(), GARMENT)
    error = caught.value.error
    assert (error.code, error.retryable, error.stage) == (ErrorCode.REALIZE_TIMEOUT, True,
                                                        Stage.REALIZE)
    assert error.external_request_id == "req-turn-3"

    client.statuses["turn"] = []  # 그 사이 fal에서 생성이 끝났다
    result = realizer.realize(request(), GARMENT)
    assert len(client.submits) == 3  # 끝난 단계는 건너뛰고 회전도 다시 제출하지 않았다
    assert result.metrics["external_request_id"] == "req-turn-3"


def test_unknown_submit_is_not_sent_again_unless_allowed(tmp_path, media):
    client = FakeFal(submit_errors={"turn": [ReadTimeout("no response")]})
    realizer = make(tmp_path, media, client)
    with pytest.raises(PortFailure) as caught:
        realizer.realize(request(), GARMENT)
    assert caught.value.error.code == ErrorCode.EXTERNAL_SUBMIT_UNKNOWN
    assert caught.value.error.retryable is False
    assert record(tmp_path)["steps"]["turn"]["state"] == "unknown"

    with pytest.raises(PortFailure) as again:
        realizer.realize(request(), GARMENT)
    assert "다시 보내지 않았습니다" in again.value.error.safe_message
    assert len(client.submits) == 2  # 사진·자세만

    realizer.options.allow_resubmit = True
    realizer.realize(request(), GARMENT)
    assert [e for e, _ in client.submits][2:] == [TURN_ENDPOINT]


def test_black_turn_video_fails_without_creating_the_contract_output(tmp_path, media):
    client = FakeFal(turn_url="https://fal.example/black.mp4")
    realizer = make(tmp_path, media, client)
    with pytest.raises(PortFailure) as caught:
        realizer.realize(request(), GARMENT)
    error = caught.value.error
    assert (error.code, error.retryable) == (ErrorCode.REALIZE_FAILED, False)
    assert "검은 화면" in error.safe_message
    assert not realizer.outputs(JOB)["turn"].exists()


def test_generation_error_reported_by_fal_is_a_realize_failure(tmp_path, media):
    failed = Completed(error="content policy", error_type="safety")
    client = FakeFal(statuses={"photo": [failed]})
    with pytest.raises(PortFailure) as caught:
        make(tmp_path, media, client).realize(request(), GARMENT)
    assert caught.value.error.code == ErrorCode.REALIZE_FAILED
    assert record(tmp_path)["steps"]["photo"]["state"] == "failed"


def test_download_failing_twice_is_finished_on_the_next_call_without_generating(tmp_path, media):
    client = FakeFal()
    files = Files(media, fail={"turn": 2})
    realizer = make(tmp_path, media, client, files=files)
    with pytest.raises(PortFailure) as caught:
        realizer.realize(request(), GARMENT)
    assert caught.value.error.retryable is True
    assert caught.value.error.external_request_id == "req-turn-3"
    realizer.realize(request(), GARMENT)
    assert len(client.submits) == 3
    assert realizer.outputs(JOB)["turn"].is_file()


def test_plan_lists_steps_and_rejects_a_bad_duration(tmp_path, media):
    realizer = make(tmp_path, media, FakeFal())
    plan = realizer.plan(request(), GARMENT)
    assert plan["version"] == "realize_fitroom_v3"
    assert plan["steps"] == ["photo", "pose", "turn"]
    assert plan["endpoints"] == ENDPOINTS
    assert set(plan["estimate_usd"]) == {"photo", "pose", "turn", "total"}
    assert plan["estimate_usd"]["total"] == pytest.approx(0.70)
    assert plan["prompts"]["pants"] == "light beige chino trousers"
    assert "transfer_settings" not in plan
    realizer.options.turn_duration_s = 5
    with pytest.raises(ValueError, match="Veo 길이"):
        realizer.plan(request(), GARMENT)


def test_pose_step_off_turns_straight_from_the_photo(tmp_path, media):
    client = FakeFal()
    realizer = make(tmp_path, media, client, pose_step=False)
    result = realizer.realize(request(), GARMENT)

    assert realizer.steps() == ("photo", "turn")
    assert [e for e, _ in client.submits] == [PHOTO_ENDPOINT, TURN_ENDPOINT]
    turn = client.submits[-1][1]
    assert turn["first_frame_url"].endswith(f"{JOB}_{FIT_FRAME_ROLE}.png")  # 팔 벌린 사진 그대로
    m = result.metrics
    assert "realize_pose_artifact_id" not in m and "pose_s" not in m
    assert m["external_request_id"] == "req-turn-2"
    assert m["cost_usd"] == pytest.approx(0.55)
    assert not realizer.outputs(JOB)["pose"].exists()


def test_default_turn_model_is_veo_fast(tmp_path, media):
    plan = make(tmp_path, media, FakeFal()).plan(request(), GARMENT)
    assert plan["turn_model"] == "fast"
    assert plan["endpoints"]["turn"] == TURN_ENDPOINT
    assert TURN_ENDPOINT == "fal-ai/veo3.1/fast/first-last-frame-to-video"
    assert plan["estimate_usd"]["turn"] == pytest.approx(0.4)  # 4초 x $0.10


def test_standard_turn_model_uses_the_standard_veo_and_price(tmp_path, media):
    client = FakeFal()
    realizer = make(tmp_path, media, client, turn_model="standard")
    plan = realizer.plan(request(), GARMENT)
    assert plan["endpoints"]["turn"] == TURN_MODELS["standard"][0]
    assert plan["estimate_usd"]["turn"] == pytest.approx(0.8)  # 4초 x $0.20
    result = realizer.realize(request(), GARMENT)
    assert client.submits[-1][0] == "fal-ai/veo3.1/first-last-frame-to-video"
    assert result.tool_versions["turn_endpoint"] == "fal-ai/veo3.1/first-last-frame-to-video"


def test_unknown_turn_model_is_rejected_before_calling_fal(tmp_path, media):
    client = FakeFal()
    realizer = make(tmp_path, media, client, turn_model="ultra")
    with pytest.raises(ValueError, match="회전 모델"):
        realizer.plan(request(), GARMENT)
    with pytest.raises(PortFailure) as caught:
        realizer.realize(request(), GARMENT)
    assert caught.value.error.retryable is False
    assert client.submits == [] and client.uploads == []


def test_resumed_turn_is_queried_with_the_model_it_was_submitted_to(tmp_path, media):
    client = FakeFal(statuses={"turn": [Queued(1)] * 1000})
    realizer = make(tmp_path, media, client, wait_limit_s=60, turn_model="standard")
    with pytest.raises(PortFailure):
        realizer.realize(request(), GARMENT)
    client.statuses["turn"] = []
    client.queries.clear()
    realizer.options.turn_model = "fast"  # 다시 부를 때 옵션이 바뀌어도 같은 요청을 본다
    realizer.realize(request(), GARMENT)
    assert len(client.submits) == 3
    assert {model for model, _ in client.queries} == {"fal-ai/veo3.1/first-last-frame-to-video"}


def test_missing_render_clip_fails_without_calling_fal(tmp_path, media):
    client = FakeFal()
    realizer = make(tmp_path, media, client)
    (tmp_path / "artifacts" / f"{JOB}_render_clip.mp4").unlink()
    with pytest.raises(PortFailure) as caught:
        realizer.realize(request(), GARMENT)
    assert caught.value.error.code == ErrorCode.EXTERNAL_SUBMIT_UNKNOWN
    assert client.submits == []


def test_fit_frame_name_matches_the_validator():
    assert FIT_FRAME_ROLE == VALIDATOR_FIT_FRAME


# --- 설정 연결 -----------------------------------------------------------------------------


def settings_for(tmp_path: Path, *, key: bool):
    from server.wiring import AdapterSettings

    return AdapterSettings(artifacts_dir=tmp_path / "artifacts", work_dir=tmp_path / "work",
                           blender_path=None, smpl_model_dir=None, fal_key_present=key,
                           repo_root=ROOT)


def test_visual_fitroom_config_swaps_only_the_realizer():
    from server.wiring import load_adapter_config

    fitroom = load_adapter_config("visual_fitroom", environ={}).ports
    real = load_adapter_config("visual_real", environ={}).ports
    assert fitroom["realize"] == "fitting.adapters.fal_fitroom.factory:make_fitroom_realizer"
    assert {k: v for k, v in fitroom.items() if k != "realize"} == {
        k: v for k, v in real.items() if k != "realize"
    }


def test_factory_needs_the_key(tmp_path):
    from fitting.adapters.fal_fitroom.factory import make_fitroom_realizer
    from server.wiring import AdapterConfigError

    with pytest.raises(AdapterConfigError, match="FAL_KEY"):
        make_fitroom_realizer(settings_for(tmp_path, key=False))


def test_factory_builds_with_the_submit_retry_guard(tmp_path):
    pytest.importorskip("fal_client")
    from fitting.adapters.fal_fitroom.factory import make_fitroom_realizer

    realizer = make_fitroom_realizer(settings_for(tmp_path, key=True))
    assert isinstance(realizer, FitRoomRealizer)
    assert "submit_retry_guard=" in realizer.client_version
    assert realizer.artifacts_dir == tmp_path / "artifacts"


def test_media_factory_turns_on_the_fitroom_check(tmp_path):
    from fitting.adapters.media.factory import make_validator

    validator = make_validator(settings_for(tmp_path, key=False))
    assert validator.fitroom is not None
    assert validator.tool_versions()["fitroom_config"] == "fitroom_check_v1@1.1.0"
