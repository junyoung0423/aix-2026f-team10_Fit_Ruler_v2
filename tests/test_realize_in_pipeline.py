"""D5·D6: 피팅 파이프라인(`run_fitting`) 안에서 실사화를 실행·저장하고 색·실루엣까지
검증하는지 본다.

몸·착장은 가짜, 렌더는 C5처럼 정지 클립과 옷 마스크를 쓰는 스텁, 실사화는 **실제 `FalRealizer`**
(가짜 fal 대기열, 비용 없음), 검증은 D6 검증 어댑터다. 실제 Blender·fal 없이 단계 사이 파일 넘김과
판정에 따른 작업 상태를 시험한다.
"""

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("cv2")  # 선택 의존성 realize가 없으면 건너뛴다

from fitting.adapters.fake import fake_ports  # noqa: E402
from fitting.adapters.fal_ltx import FalRealizer, RealizeOptions  # noqa: E402
from fitting.adapters.media.validator import VisualValidator  # noqa: E402
from fitting.adapters.media.visual_measure import load_check_config  # noqa: E402
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
from tests.adapters import visual_clips as vc  # noqa: E402
from tests.adapters.test_fal_realizer import (  # noqa: E402
    Completed,
    Downloader,
    FakeClock,
    FakeQueue,
    InProgress,
    Queued,
)

ROOT = Path(__file__).resolve().parents[1]
GARMENT = GarmentVariant.model_validate_json(
    (ROOT / "data" / "garments" / "tee_regular_m_navy_v1.json").read_text(encoding="utf-8")
)
JOB = "job_d5"
NOW = datetime(2026, 11, 23, tzinfo=timezone.utc)


class ClipRenderer:
    """렌더 단계 스텁: C5처럼 `{job_id}_render_clip.mp4`와 `{job_id}_render_mask.png`를 쓴다."""

    def __init__(self, artifacts: Path, source: Path, mask: Path) -> None:
        self.artifacts = artifacts
        self.source = source
        self.mask = mask
        self.inputs: list[str] = []

    def render(self, request: PortRequest, garment: GarmentVariant) -> PortResult:
        self.inputs = list(request.input_artifact_ids)
        artifact_id, mask_id = f"{request.job_id}_render_clip", f"{request.job_id}_render_mask"
        shutil.copy(self.mask, self.artifacts / f"{mask_id}.png")
        shutil.copy(self.source, self.artifacts / f"{artifact_id}.mp4")
        return PortResult(
            schema_version=SCHEMA_VERSION,
            output_artifact_ids=[artifact_id],
            metrics={"render_mask_artifact_id": mask_id},
            tool_versions={"render": "stub"},
        )


@pytest.fixture(scope="module")
def clips(tmp_path_factory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("clips")
    render, mask = vc.render_clip(root / "render.mp4")
    return {
        "render": render,
        "mask": vc.write_mask(root / "mask.png", mask),
        "real": vc.real_clip(root / "real.mp4"),  # 같은 옷 → PASS
        "recolored": vc.real_clip(root / "recolored.mp4", garment=vc.RED),  # 색이 바뀜 → FAIL
    }


def make_chain(tmp_path: Path, clips, real: str) -> dict:
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
        downloader=Downloader(clips[real]),
        clock=clock,
        wall=clock,
        sleep=clock.sleep,
    )
    ports = fake_ports(
        render=ClipRenderer(artifacts, clips["render"], clips["mask"]),
        realize=realizer,
        validate=VisualValidator(
            artifacts_dir=artifacts, work_dir=work, config=load_check_config()
        ),
    )
    return {"artifacts": artifacts, "work": work, "client": client, "ports": ports}


@pytest.fixture
def chain(tmp_path, clips):
    return make_chain(tmp_path, clips, "real")


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


def test_realize_and_validation_pass_in_the_pipeline(body, chain):
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

    # 검증(D6): 렌더 마스크로 11프레임을 비교해 통과했다. 두 프레임 ID와 숫자 지표가 상태에 남는다
    assert visual.frame_artifact_id == f"{JOB}_frame"
    assert visual.render_frame_artifact_id == f"{JOB}_render_frame"
    assert (artifacts / f"{JOB}_frame.png").is_file()
    assert (artifacts / f"{JOB}_render_frame.png").is_file()
    assert visual.quality_status is QualityStatus.PASS
    assert visual.status is StageStatus.SUCCEEDED
    assert visual.stage is Stage.VALIDATE
    assert visual.error_code is None
    assert by_stage(status)[Stage.VALIDATE] is StageStatus.SUCCEEDED
    metrics = visual.quality_metrics
    assert metrics["silhouette_iou"] >= 0.85 and metrics["color_delta_e"] <= 8.0
    assert metrics["frames_passed"] == 11.0
    assert (chain["work"] / JOB / "realize" / "realize.json").is_file()
    assert (chain["work"] / JOB / "validate" / "validate.json").is_file()


def test_a_recolored_video_fails_validation_but_keeps_frames(body, tmp_path, clips):
    chain = make_chain(tmp_path, clips, "recolored")
    outcome = run(body, chain["ports"])
    visual = outcome.status.visual

    assert visual.status is StageStatus.FAILED
    assert visual.stage is Stage.VALIDATE
    assert visual.error_code is ErrorCode.VALIDATION_FAILED
    assert visual.quality_status is QualityStatus.FAIL
    assert visual.quality_metrics["color_delta_e"] > 8.0
    assert visual.quality_metrics["frames_passed"] == 0.0
    # 실패해도 사람이 볼 수 있게 프레임과 실사 영상 ID는 남는다(명세서 7장: 운영자 보관)
    assert visual.video_artifact_id == f"{JOB}_video"
    assert visual.frame_artifact_id == f"{JOB}_frame"
    assert (chain["artifacts"] / f"{JOB}_frame.png").is_file()
    assert outcome.status.report is not None  # 핏 리포트는 그대로


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
