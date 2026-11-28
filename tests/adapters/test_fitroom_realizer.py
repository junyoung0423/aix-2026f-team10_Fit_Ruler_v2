"""피팅룸 실사화 어댑터 테스트. 가짜 fal(사진 편집·Veo)·가짜 Blender 인코더와 합성 그림·영상만
쓴다(비용 없음). 실제 Blender 인코딩은 test_fitroom_encode.py(마커 blender)에서 본다."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

cv2 = pytest.importorskip("cv2")  # 선택 의존성 realize가 없으면 건너뛴다
np = pytest.importorskip("numpy")

from fitting.adapters.fal_fitroom.encode import RunOutcome, crop_top  # noqa: E402
from fitting.adapters.fal_fitroom.realizer import (  # noqa: E402
    ENDPOINTS,
    FIT_FRAME_ROLE,
    REALIZE_VERSION,
    FitRoomOptions,
    FitRoomRealizer,
    PhotoCheck,
    _photo_score,
)
from fitting.adapters.fal_fitroom.steps import (  # noqa: E402
    PHOTO_ENDPOINT,
    TURN_ENDPOINT,
    TURN_MODELS,
    first_frame,
)
from fitting.adapters.media.fitroom_measure import load_fitroom_config  # noqa: E402
from fitting.adapters.media.frames import read_video_info  # noqa: E402
from fitting.adapters.media.validator import FIT_FRAME_ROLE as VALIDATOR_FIT_FRAME  # noqa: E402
from fitting.adapters.media.visual_measure import load_check_config  # noqa: E402
from fitting.domain import ErrorCode, GarmentVariant, Stage  # noqa: E402
from fitting.ports import PortFailure, PortRequest, PortResult  # noqa: E402
from tests.adapters import visual_clips as vc  # noqa: E402
from tests.adapters.test_fal_realizer import Completed, FakeClock, Queued, ReadTimeout  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
GARMENT = GarmentVariant.model_validate_json(
    (ROOT / "data" / "garments" / "tee_regular_m_navy_v1.json").read_text(encoding="utf-8")
)
JOB = "jobfr"
# 사진 핏 확인 기준(검증 어댑터와 같은 설정 파일)
CHECK = PhotoCheck(config=load_check_config(), align=load_fitroom_config().align)


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("fitroom")
    render, mask = vc.render_clip(root / "render.mp4")
    photo, _ = vc.draw((180, 240), real=True, seed=1)
    ok, buf = cv2.imencode(".png", photo)
    (root / "photo.png").write_bytes(buf.tobytes())
    turn = vc.real_clip(root / "turn.mp4")
    black = vc.write_clip(root / "black.mp4", [np.zeros((240, 180, 3), np.uint8)] * 25)
    files = {"render": render, "photo": root / "photo.png", "turn": turn, "black": black,
             "mask": vc.write_mask(root / "render_mask.png", mask)}
    # 옷 폭을 줄인 사진: 0.9배는 밑단 폭 하나만, 0.8·0.7배는 IoU·가슴·밑단 폭이 떨어진다
    for widen in (0.9, 0.8, 0.7):
        narrow, _ = vc.draw((180, 240), real=True, widen=widen, seed=1)
        name = f"photo_w{round(widen * 100)}"
        ok, buf = cv2.imencode(".png", narrow)
        (root / f"{name}.png").write_bytes(buf.tobytes())
        files[name] = root / f"{name}.png"
    return files


class FakeFal:
    """모델별로 제출을 기록하는 가짜 fal. 요청 ID는 `req-{단계}-{순번}`."""

    def __init__(self, *, statuses: dict[str, list[Any]] | None = None,
                 submit_errors: dict[str, list[Exception]] | None = None,
                 turn_url: str = "https://fal.example/turn.mp4",
                 photos: list[str] | None = None):
        self.statuses = statuses or {}
        # 사진 단계 결과 파일 이름(제출 차례대로, 모자라면 마지막 것)
        self.photos = list(photos or ["photo"])
        self.names: dict[str, str] = {}
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
            return "pose" if "lower both arms" in arguments["prompt"] else "photo"
        return "turn"

    def submit(self, application: str, arguments: dict[str, Any]) -> Any:
        step = self._step(application, arguments)
        errors = self.submit_errors.get(step) or []
        if errors:
            raise errors.pop(0)
        self.submits.append((application, arguments))
        request_id = f"req-{step}-{len(self.submits)}"
        self.ids[request_id] = step
        if step == "photo":
            made = sum(1 for name in self.ids.values() if name == "photo") - 1
            self.names[request_id] = self.photos[min(made, len(self.photos) - 1)]
        return SimpleNamespace(request_id=request_id)

    def status(self, application: str, request_id: str, *, with_logs: bool = False) -> Any:
        self.queries.append((application, request_id))
        queue = self.statuses.get(self.ids[request_id]) or []
        return queue.pop(0) if queue else Completed()

    def result(self, application: str, request_id: str) -> dict[str, Any]:
        self.queries.append((application, request_id))
        step = self.ids[request_id]
        if step == "photo":
            return {"images": [{"url": f"https://fal.example/{self.names[request_id]}.png"}]}
        if step == "pose":
            return {"images": [{"url": "https://fal.example/pose.png"}]}
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
        source = {"pose": "photo"}.get(name, name)
        path.write_bytes(self.media[source].read_bytes())


class FakeEncoder:
    """Blender 대신 프레임 폴더를 OpenCV(mp4v)로 영상에 쓴다. `returncodes` 차례로 끝난다."""

    def __init__(self, returncodes: list[int] | None = None):
        self.returncodes = list(returncodes or [])
        self.commands: list[list[str]] = []

    def __call__(self, cmd, cwd, env, timeout_s, log_path) -> RunOutcome:
        self.commands.append(list(cmd))
        code = self.returncodes.pop(0) if self.returncodes else 0
        if code == 0:
            frames_dir, out, fps = cmd[cmd.index("--") + 1 :]
            files = sorted(Path(frames_dir).glob("*.bmp"))
            images = [cv2.imdecode(np.frombuffer(f.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
                      for f in files]
            vc.write_clip(Path(out), images, fps=float(fps))
        return RunOutcome(returncode=code, timed_out=False, seconds=0.1)


def make(tmp_path: Path, media, client: FakeFal, files: Files | None = None, *,
         encoder: FakeEncoder | None = None, blender: bool = True, mask: bool = False,
         check: bool = False, **options):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(exist_ok=True)
    clip = artifacts / f"{JOB}_render_clip.mp4"
    if not clip.exists():
        clip.write_bytes(media["render"].read_bytes())
    if mask:  # C5 렌더 어댑터가 남기는 렌더 옷 마스크
        (artifacts / f"{JOB}_render_mask.png").write_bytes(media["mask"].read_bytes())
    blender_path = None
    if blender:  # 실행 파일이 있는지만 본다(실행은 가짜 인코더가 한다)
        blender_path = tmp_path / "blender.exe"
        blender_path.write_bytes(b"")
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
        blender_path=blender_path,
        runner=encoder or FakeEncoder(),
        photo_check=CHECK if check else None,
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
    assert m["cost_usd"] == pytest.approx(0.15 + 0.15 + 0.80)  # 사진 2장 + 회전 8초
    assert m["estimated_cost_usd"] == pytest.approx(1.10)
    assert m["top_crop_px"] == 32
    assert result.tool_versions["realize"] == REALIZE_VERSION == "realize_fitroom_v5"
    assert result.tool_versions["top_crop"] == "32px (blender)"
    assert result.tool_versions["turn_endpoint"] == TURN_ENDPOINT
    assert "transfer_endpoint" not in result.tool_versions
    # 핏 확인 기준이 없으면 사진 1장으로 확인 없이 간다
    assert m["photo_attempts"] == 1 and "photo_fit_check" not in m
    assert result.tool_versions["photo_check"] == "off"
    assert result.tool_versions["photo_guide"] == "off"
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
    assert turn["duration"] == "8s" and turn["generate_audio"] is False and turn["seed"] == 42
    assert "one full 360-degree rotation" in turn["prompt"] and "face" in turn["negative_prompt"]
    # 옆모습에서도 턱·수염이 화면에 들어오지 않게(q19·q23)
    assert "The chin, mouth, beard and face never enter the frame" in turn["prompt"]
    assert "beard" in turn["negative_prompt"].split(", ")


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
    assert plan["version"] == "realize_fitroom_v5"
    assert plan["steps"] == ["photo", "pose", "turn"]
    assert plan["endpoints"] == ENDPOINTS
    assert set(plan["estimate_usd"]) == {
        "photo", "pose", "turn", "total", "photo_retries_max", "max_total"
    }
    assert plan["estimate_usd"]["total"] == pytest.approx(1.10)
    assert plan["estimate_usd"]["max_total"] == pytest.approx(1.10)  # 핏 확인이 없어 다시 안 만듦
    assert plan["photo_check"] == {"enabled": False, "max_photos": 1}
    assert plan["photo_guide"] == "off"
    assert plan["top_crop_px"] == 32
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
    assert m["cost_usd"] == pytest.approx(0.95)  # 사진 1장 + 회전 8초
    assert not realizer.outputs(JOB)["pose"].exists()


def test_default_turn_model_is_veo_fast(tmp_path, media):
    plan = make(tmp_path, media, FakeFal()).plan(request(), GARMENT)
    assert plan["turn_model"] == "fast"
    assert plan["endpoints"]["turn"] == TURN_ENDPOINT
    assert TURN_ENDPOINT == "fal-ai/veo3.1/fast/first-last-frame-to-video"
    assert plan["estimate_usd"]["turn"] == pytest.approx(0.8)  # 8초 x $0.10


def test_standard_turn_model_uses_the_standard_veo_and_price(tmp_path, media):
    client = FakeFal()
    realizer = make(tmp_path, media, client, turn_model="standard")
    plan = realizer.plan(request(), GARMENT)
    assert plan["endpoints"]["turn"] == TURN_MODELS["standard"][0]
    assert plan["estimate_usd"]["turn"] == pytest.approx(1.6)  # 8초 x $0.20
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


# --- 맨 위 자르기 --------------------------------------------------------------------------


def test_turn_video_top_is_cropped_with_blender_and_keeps_its_size(tmp_path, media):
    encoder = FakeEncoder()
    realizer = make(tmp_path, media, FakeFal(), encoder=encoder)
    realizer.realize(request(), GARMENT)

    assert len(encoder.commands) == 1
    cmd = encoder.commands[0]
    assert cmd[0] == str(realizer.blender_path)
    assert cmd[cmd.index("--python") + 1].endswith("blender_encode.py")
    frames_dir, out, fps = cmd[cmd.index("--") + 1 :]
    assert Path(out).name == "turn_cropped.mp4" and fps == f"{vc.FPS:g}"
    assert not Path(frames_dir).exists() and not Path(out).exists()  # 다 쓰고 지운다
    raw = tmp_path / "work" / JOB / "realize" / "turn.mp4"  # 받은 그대로의 영상은 남긴다
    video = realizer.outputs(JOB)["turn"]
    raw_info, out_info = read_video_info(raw), read_video_info(video)
    assert (out_info.width, out_info.height, out_info.frame_count) == (
        raw_info.width, raw_info.height, raw_info.frame_count)
    raw0, out0 = first_frame(raw).astype(int), first_frame(video).astype(int)
    expected = crop_top(first_frame(raw), 32).astype(int)
    assert np.abs(out0 - expected).mean() < np.abs(out0 - raw0).mean()  # 자른 프레임과 같다
    crop = record(tmp_path)["steps"]["turn"]["top_crop"]
    assert crop["px"] == 32 and crop["encoder"] == "blender"
    assert crop["frames"] == raw_info.frame_count


def test_without_blender_the_crop_stops_before_calling_fal(tmp_path, media):
    client = FakeFal()
    with pytest.raises(PortFailure) as caught:
        make(tmp_path, media, client, blender=False).realize(request(), GARMENT)
    error = caught.value.error
    assert error.retryable is False and "Blender" in error.safe_message
    assert client.submits == [] and client.uploads == []


def test_crop_off_keeps_the_turn_video_as_received_without_blender(tmp_path, media):
    encoder = FakeEncoder()
    realizer = make(tmp_path, media, FakeFal(), encoder=encoder, blender=False, top_crop_px=0)
    result = realizer.realize(request(), GARMENT)
    assert encoder.commands == []
    assert realizer.outputs(JOB)["turn"].read_bytes() == media["turn"].read_bytes()
    assert "top_crop_px" not in result.metrics
    assert result.tool_versions["top_crop"] == "off"


def test_crop_failure_keeps_the_paid_turn_and_the_next_call_only_crops_again(tmp_path, media):
    encoder = FakeEncoder(returncodes=[1])
    client = FakeFal()
    realizer = make(tmp_path, media, client, encoder=encoder)
    with pytest.raises(PortFailure) as caught:
        realizer.realize(request(), GARMENT)
    error = caught.value.error
    assert (error.code, error.retryable) == (ErrorCode.REALIZE_FAILED, False)
    assert "맨 위" in error.safe_message
    assert record(tmp_path)["steps"]["turn"]["state"] == "completed"
    detail = json.loads((tmp_path / "work" / JOB / "realize" / "error.json").read_text("utf-8"))
    assert detail["stage"] == "turn:crop" and "종료 코드 1" in detail["message"]
    assert not realizer.outputs(JOB)["turn"].exists()

    realizer.realize(request(), GARMENT)  # 이번에는 인코딩이 된다
    assert len(client.submits) == 3  # 회전을 다시 제출하지 않았다
    assert len(encoder.commands) == 2
    assert realizer.outputs(JOB)["turn"].is_file()


def test_top_crop_outside_the_range_is_rejected_before_calling_fal(tmp_path, media):
    client = FakeFal()
    realizer = make(tmp_path, media, client, top_crop_px=500)
    with pytest.raises(ValueError, match="맨 위 자르기"):
        realizer.plan(request(), GARMENT)
    with pytest.raises(PortFailure):
        realizer.realize(request(), GARMENT)
    assert client.submits == [] and client.uploads == []


def test_fit_frame_name_matches_the_validator():
    assert FIT_FRAME_ROLE == VALIDATOR_FIT_FRAME


# --- 설정 연결 -----------------------------------------------------------------------------


def settings_for(tmp_path: Path, *, key: bool, blender: Path | None = None):
    from server.wiring import AdapterSettings

    return AdapterSettings(artifacts_dir=tmp_path / "artifacts", work_dir=tmp_path / "work",
                           blender_path=blender, smpl_model_dir=None, fal_key_present=key,
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


def test_factory_builds_with_the_submit_retry_guard_and_the_photo_check(tmp_path):
    pytest.importorskip("fal_client")
    from fitting.adapters.fal_fitroom.factory import make_fitroom_realizer

    blender = tmp_path / "blender.exe"
    realizer = make_fitroom_realizer(settings_for(tmp_path, key=True, blender=blender))
    assert isinstance(realizer, FitRoomRealizer)
    assert "submit_retry_guard=" in realizer.client_version
    assert realizer.artifacts_dir == tmp_path / "artifacts"
    assert realizer.blender_path == blender  # 맨 위 자르기에 쓴다(BLENDER_PATH)
    assert realizer.photo_check == CHECK  # 사진 핏 확인은 검증 어댑터와 같은 기준


def test_media_factory_turns_on_the_fitroom_check(tmp_path):
    from fitting.adapters.media.factory import make_validator

    validator = make_validator(settings_for(tmp_path, key=False))
    assert validator.fitroom is not None
    assert validator.tool_versions()["fitroom_config"] == "fitroom_check_v1@1.1.0"


# --- 사진 핏 확인 ---------------------------------------------------------------------------


def photo_step(tmp_path: Path) -> dict[str, Any]:
    return record(tmp_path)["steps"]["photo"]


def pixels(path: Path):
    return cv2.imdecode(np.frombuffer(Path(path).read_bytes(), np.uint8), cv2.IMREAD_COLOR)


def test_a_photo_that_passes_the_fit_check_goes_on_without_another_photo(tmp_path, media):
    client = FakeFal()
    realizer = make(tmp_path, media, client, mask=True, check=True)
    result = realizer.realize(request(), GARMENT)

    assert [e for e, _ in client.submits] == [PHOTO_ENDPOINT, PHOTO_ENDPOINT, TURN_ENDPOINT]
    step = photo_step(tmp_path)
    assert step["check"]["status"] == "PASS" and step["check"]["request_id"] == "req-photo-1"
    assert step["chosen"] == 1 and not step.get("attempts")
    assert result.metrics["photo_attempts"] == 1
    assert result.metrics["photo_fit_check"] == "PASS"
    assert result.metrics["cost_usd"] == pytest.approx(1.10)
    assert result.tool_versions["photo_check"] == "fit_frame (max 3 photos)"


def test_a_photo_that_fails_the_fit_check_is_made_again_before_the_turn(tmp_path, media):
    client = FakeFal(photos=["photo_w80", "photo"])  # 첫 사진은 옷 폭이 0.8배
    realizer = make(tmp_path, media, client, mask=True, check=True)
    plan = realizer.plan(request(), GARMENT)
    assert plan["photo_check"] == {"enabled": True, "max_photos": 3}
    assert plan["estimate_usd"]["max_total"] == pytest.approx(1.40)  # 사진 2장 더
    result = realizer.realize(request(), GARMENT)

    endpoints = [e for e, _ in client.submits]
    assert endpoints == [PHOTO_ENDPOINT, PHOTO_ENDPOINT, PHOTO_ENDPOINT, TURN_ENDPOINT]
    step = photo_step(tmp_path)
    assert step["request_id"] == "req-photo-2" and step["check"]["status"] == "PASS"
    (failed,) = step["attempts"]
    assert failed["number"] == 1 and failed["request_id"] == "req-photo-1"
    assert failed["check"]["status"] == "FAIL" and "chest_width" in failed["check"]["fails"]
    directory = tmp_path / "work" / JOB / "realize"
    assert (directory / failed["file"]).is_file()
    assert (directory / "photo_attempts" / "1_photo_response.json").is_file()
    # 핏 확인 사진(검증·자세 단계 입력)은 다시 만든 사진이다
    fit_frame = realizer.outputs(JOB)["photo"]
    assert np.array_equal(pixels(fit_frame), pixels(media["photo"]))
    assert client.uploads.count(fit_frame.name) == 1  # 자세 단계가 올린 사진
    assert result.metrics["photo_attempts"] == 2
    assert result.metrics["photo_fit_check"] == "PASS"
    assert result.metrics["cost_usd"] == pytest.approx(0.15 * 3 + 0.80)


def test_when_every_photo_fails_the_best_one_is_kept_and_the_job_goes_on(tmp_path, media):
    client = FakeFal(photos=["photo_w80", "photo_w90", "photo_w70"])
    realizer = make(tmp_path, media, client, mask=True, check=True)
    result = realizer.realize(request(), GARMENT)

    assert [e for e, _ in client.submits].count(PHOTO_ENDPOINT) == 4  # 사진 3장 + 자세
    step = photo_step(tmp_path)
    attempts = {a["number"]: a for a in step["attempts"]}
    assert set(attempts) == {1, 3}  # 쓰지 않은 사진
    assert step["chosen"] == 2 and step["request_id"] == "req-photo-2"
    assert step["check"]["status"] == "FAIL"
    assert all(_photo_score(step["check"]) <= _photo_score(a["check"]) for a in attempts.values())
    fit_frame = realizer.outputs(JOB)["photo"]
    assert np.array_equal(pixels(fit_frame), pixels(media["photo_w90"]))  # 0.9배가 가장 낫다
    assert result.metrics["photo_attempts"] == 3
    assert result.metrics["photo_fit_check"] == "FAIL"  # 마지막 판정은 검증 단계가 한다
    assert result.metrics["cost_usd"] == pytest.approx(0.15 * 4 + 0.80)

    again = realizer.realize(request(), GARMENT)  # 끝난 작업은 다시 만들지 않는다
    assert len(client.submits) == 5 and again.metrics == result.metrics


def test_one_photo_attempt_only_checks_and_does_not_make_another(tmp_path, media):
    client = FakeFal(photos=["photo_w80"])
    realizer = make(tmp_path, media, client, mask=True, check=True, photo_attempts=1)
    result = realizer.realize(request(), GARMENT)
    assert [e for e, _ in client.submits].count(PHOTO_ENDPOINT) == 2
    assert photo_step(tmp_path)["check"]["status"] == "FAIL"
    assert result.metrics["photo_attempts"] == 1
    assert realizer.plan(request(), GARMENT)["estimate_usd"]["max_total"] == pytest.approx(1.10)


def test_without_the_render_mask_the_photo_is_used_without_a_check(tmp_path, media):
    client = FakeFal(photos=["photo_w80"])
    realizer = make(tmp_path, media, client, check=True)  # 렌더 옷 마스크 없음
    result = realizer.realize(request(), GARMENT)
    assert len(client.submits) == 3
    step = photo_step(tmp_path)
    assert "check" not in step and step["check_skipped"] == "렌더 옷 마스크가 없음"
    assert "photo_fit_check" not in result.metrics


def test_a_timeout_while_making_the_photo_again_resumes_the_same_request(tmp_path, media):
    client = FakeFal(photos=["photo_w80", "photo"],
                     statuses={"photo": [Completed()] + [Queued(1)] * 1000})
    realizer = make(tmp_path, media, client, mask=True, check=True, wait_limit_s=60)
    with pytest.raises(PortFailure) as caught:
        realizer.realize(request(), GARMENT)
    assert caught.value.error.code == ErrorCode.REALIZE_TIMEOUT
    assert caught.value.error.external_request_id == "req-photo-2"
    assert len(photo_step(tmp_path)["attempts"]) == 1

    client.statuses["photo"] = []  # 그 사이 두 번째 사진이 끝났다
    result = realizer.realize(request(), GARMENT)
    photos = [a for e, a in client.submits
              if e == PHOTO_ENDPOINT and "lower both arms" not in a["prompt"]]
    assert len(photos) == 2  # 떨어진 사진도, 기다리던 사진도 다시 보내지 않았다
    assert photo_step(tmp_path)["request_id"] == "req-photo-2"
    assert result.metrics["photo_fit_check"] == "PASS" and result.metrics["photo_attempts"] == 2


# --- 사진 기준 그림 ---------------------------------------------------------------------------


def test_the_render_mask_guide_is_sent_as_the_second_image_when_turned_on(tmp_path, media):
    client = FakeFal()
    realizer = make(tmp_path, media, client, mask=True, photo_mask_reference=True)
    result = realizer.realize(request(), GARMENT)
    photo = client.submits[0][1]
    assert photo["image_urls"] == [
        "https://fal.example/upload/render_still.png",
        "https://fal.example/upload/photo_guide.png",
    ]
    assert photo["prompt"].endswith("more fitted than this silhouette.")
    assert "second image is only a guide" in photo["prompt"]
    guide = pixels(tmp_path / "work" / JOB / "realize" / "photo_guide.png")
    still = pixels(tmp_path / "work" / JOB / "realize" / "render_still.png")
    assert guide.shape == still.shape  # 렌더와 같은 크기·구도
    assert set(np.unique(guide)) <= {0, 255}  # 검은 바탕에 옷 자리만 흰색
    white = (guide[..., 0] == 255).mean()
    assert white == pytest.approx(cv2.imread(str(media["mask"]), 0).astype(bool).mean(), abs=0.01)
    assert result.tool_versions["photo_guide"] == "render_mask"
    # 자세 사진에는 기준 그림을 보내지 않는다
    pose = client.submits[1][1]
    assert len(pose["image_urls"]) == 1 and "silhouette" not in pose["prompt"]


def test_the_guide_is_off_by_default_and_skipped_without_the_render_mask(tmp_path, media):
    assert FitRoomOptions().photo_mask_reference is False
    client = FakeFal()
    realizer = make(tmp_path, media, client, photo_mask_reference=True)  # 마스크 없음
    realizer.realize(request(), GARMENT)
    assert client.submits[0][1]["image_urls"] == ["https://fal.example/upload/render_still.png"]
    assert realizer.plan(request(), GARMENT)["photo_guide"] == "off"
