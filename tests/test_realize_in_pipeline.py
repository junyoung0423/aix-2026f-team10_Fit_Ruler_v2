"""D5: 피팅 파이프라인(`run_fitting`) 안에서 실사화를 실행·저장하고 프레임까지 뽑는지 확인한다.

몸·착장은 가짜, 렌더는 실제 mp4를 쓰는 스텁(C5 클립 대신), 실사화는 **실제 `FalRealizer`**(가짜 fal
대기열, 비용 없음), 검증은 D5 프레임 어댑터다. 실제 Blender·fal 없이 단계 사이 파일 넘김을 시험한다.
"""

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("cv2")  # 선택 의존성 realize가 없으면 건너뛴다

from fitting.adapters.fake import fake_ports  # noqa: E402
from fitting.adapters.fal_ltx import FalRealizer, RealizeOptions  # noqa: E402
from fitting.adapters.media.validator import FrameValidator  # noqa: E402
from fitting.domain import (  # noqa: E402
    SCHEMA_VERSION,
    ErrorCode,
    FittingRequest,
    GarmentVariant,
    Measurements,
    QualityStatus,
    Stage,
    StageStatus,
)
from fitting.ports import PortRequest, PortResult  # noqa: E402
from fitting.services.fitting import RunSettings, run_fitting  # noqa: E402
from tests.adapters.test_fal_realizer import (  # noqa: E402
    Completed,
    Downloader,
    FakeClock,
    FakeQueue,
    InProgress,
    Queued,
)
from tools.spike_fal_ltx import write_test_clip  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GARMENT = GarmentVariant.model_validate_json(
    (ROOT / "data" / "garments" / "tee_regular_m_navy_v1.json").read_text(encoding="utf-8")
)
JOB = "job_d5"
NOW = datetime(2026, 11, 23, tzinfo=timezone.utc)


class ClipRenderer:
    """렌더 단계 스텁: C5처럼 `{job_id}_render_clip.mp4`를 실제로 쓴다."""

    def __init__(self, artifacts: Path, source: Path) -> None:
        self.artifacts = artifacts
        self.source = source
        self.inputs: list[str] = []

    def render(self, request: PortRequest, garment: GarmentVariant) -> PortResult:
        self.inputs = list(request.input_artifact_ids)
        artifact_id = f"{request.job_id}_render_clip"
        shutil.copy(self.source, self.artifacts / f"{artifact_id}.mp4")
        return PortResult(
            schema_version=SCHEMA_VERSION,
            output_artifact_ids=[artifact_id],
            metrics={},
            tool_versions={"render": "stub"},
        )


@pytest.fixture(scope="module")
def clips(tmp_path_factory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("clips")
    return {
        "render": write_test_clip(root / "render.mp4", seconds=3.0, size=(96, 128)),
        "real": write_test_clip(root / "real.mp4", seconds=65 / 24, size=(102, 136)),
    }


@pytest.fixture
def chain(tmp_path, clips):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    work = tmp_path / "work"
    clock = FakeClock()
    client = FakeQueue()
    realizer = FalRealizer(
        client=client,
        artifacts_dir=artifacts,
        work_dir=work,
        options=RealizeOptions(poll_interval_s=2.0),
        downloader=Downloader(clips["real"]),
        clock=clock,
        wall=clock,
        sleep=clock.sleep,
    )
    ports = fake_ports(
        render=ClipRenderer(artifacts, clips["render"]),
        realize=realizer,
        validate=FrameValidator(artifacts_dir=artifacts, work_dir=work),
    )
    return {"artifacts": artifacts, "work": work, "client": client, "ports": ports}


def fitting_request(body) -> FittingRequest:
    return FittingRequest(
        garment_variant_id=GARMENT.id,
        realize_consent=True,
        measurements=Measurements(**body.model_dump(exclude={"measurement_protocol_version"})),
        measurement_protocol_version=body.measurement_protocol_version,
    )


def run(body, ports, *, settings=None, sleep=None):
    return run_fitting(
        fitting_request(body),
        GARMENT,
        ports,
        job_id=JOB,
        created_at=NOW,
        settings=settings or RunSettings(requery_delay_s=0.0),
        sleep=sleep or (lambda seconds: None),
    )


def by_stage(status) -> dict[Stage, StageStatus]:
    return {p.stage: p.status for p in status.stages}


def test_realize_runs_in_the_pipeline_and_frames_are_saved(body, chain):
    outcome = run(body, chain["ports"])
    status, visual = outcome.status, outcome.status.visual
    artifacts, client = chain["artifacts"], chain["client"]

    # 실사화: 렌더 단계가 쓴 클립을 올리고, D5 프롬프트로 요청하고, 계약 이름으로 저장했다
    assert by_stage(status)[Stage.REALIZE] is StageStatus.SUCCEEDED
    assert client.arguments["video_url"].endswith(f"{JOB}_render_clip.mp4")
    prompt = client.arguments["prompt"]
    assert prompt.startswith("3DREAL.")
    assert "plain navy cotton jersey" in prompt and "regular fit" in prompt
    assert "from the neck down" in prompt
    assert "negative_prompt" not in client.arguments
    assert visual.video_artifact_id == f"{JOB}_video"
    assert (artifacts / f"{JOB}_video.mp4").stat().st_size > 0

    # 검증(D5): 두 프레임 파일이 있고 상태에 ID가 남는다. 판정은 D6 전이라 검증할 수 없음
    assert visual.frame_artifact_id == f"{JOB}_frame"
    assert visual.render_frame_artifact_id == f"{JOB}_render_frame"
    assert (artifacts / f"{JOB}_frame.png").is_file()
    assert (artifacts / f"{JOB}_render_frame.png").is_file()
    assert visual.quality_status is QualityStatus.UNVERIFIABLE
    assert visual.quality_metrics == {}
    assert visual.status is StageStatus.FAILED
    assert visual.stage is Stage.VALIDATE
    assert visual.error_code is ErrorCode.VALIDATION_UNVERIFIABLE
    assert (chain["work"] / JOB / "realize" / "realize.json").is_file()
    assert (chain["work"] / JOB / "validate" / "frames.json").is_file()


def test_realize_timeout_is_requeried_by_the_service_without_resubmitting(body, chain):
    """B5 재조회 + D4 같은 요청 ID: 첫 호출이 대기 한도를 넘기면 서비스가 같은 포트를 다시 부르고,
    어댑터는 다시 제출하지 않고 같은 요청 ID로 결과를 받는다."""
    client = chain["client"]
    client.statuses = [Queued(3)]  # 계속 대기열 → 대기 한도(10초) 초과 → REALIZE_TIMEOUT

    def service_sleep(seconds: float) -> None:  # 재조회 전에 쉬는 동안 fal에서 생성이 끝났다
        client.statuses = [InProgress(None), Completed()]

    settings = RunSettings(stage_timeouts_s={Stage.REALIZE: 10}, requery_delay_s=5.0)
    outcome = run(body, chain["ports"], settings=settings, sleep=service_sleep)

    realize = {p.stage: p for p in outcome.status.stages}[Stage.REALIZE]
    assert realize.status is StageStatus.SUCCEEDED
    assert realize.attempt_no == 2  # 처음 1번 + 재조회 1번
    assert client.calls["submit"] == 1  # 다시 제출하지 않았다(비용 1번)
    assert client.calls["upload"] == 1
    assert (chain["artifacts"] / f"{JOB}_video.mp4").is_file()
    assert outcome.status.visual.frame_artifact_id == f"{JOB}_frame"


def test_without_consent_nothing_is_sent_to_fal(body, chain):
    request = fitting_request(body).model_copy(update={"realize_consent": False})
    outcome = run_fitting(
        request, GARMENT, chain["ports"], job_id=JOB, created_at=NOW, sleep=lambda s: None
    )
    assert chain["client"].calls == {"upload": 0, "submit": 0, "status": 0, "result": 0}
    assert outcome.status.visual.video_artifact_id is None
    assert not (chain["artifacts"] / f"{JOB}_video.mp4").exists()
