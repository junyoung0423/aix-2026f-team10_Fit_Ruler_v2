"""피팅 한 번을 처음부터 끝까지 순서대로 실행한다 (#1 흐름 확인, #12 실패 처리의 뼈대).

핏 경로(리포트)와 영상 경로(몸 → 착장 → 렌더 → 실사화 → 검증)는 서로 독립이다.
영상 경로의 어느 단계가 실패해도 이미 만든 핏 리포트와 3D 미리보기는 그대로 돌려준다.

지금은 한 번에 끝까지 실행하는 동기 함수다. 작업 큐·상태 저장·API는 B3에서 이 위에 얹는다.
"""

from dataclasses import dataclass
from datetime import datetime

from fitting.domain import (
    ErrorCode,
    FitReport,
    FittingRequest,
    FittingStatus,
    GarmentStatus,
    GarmentVariant,
    JobStatus,
    Mode,
    QualityStatus,
    ReportPart,
    Stage,
    StageProgress,
    StageStatus,
    VisualPart,
)
from fitting.ports import (
    BodyBuilder,
    Draper,
    FitCalculator,
    PortFailure,
    PortRequest,
    PortResult,
    PortResultInvalid,
    Realizer,
    Renderer,
    Validator,
    require_output_artifacts,
)

REPORT_STAGES = (Stage.FIT_CALCULATE, Stage.FIT_REPORT)
VISUAL_STAGES = (Stage.BODY_FIT, Stage.DRAPE, Stage.RENDER, Stage.REALIZE, Stage.VALIDATE)

# 어댑터가 형식에 맞지 않는 결과를 돌려줬을 때 단계별로 기록할 오류 코드.
# [제안] 명세서에 이 경우의 코드가 따로 없어서 각 단계의 실패 코드를 쓴다.
INVALID_RESULT_CODE = {
    Stage.BODY_FIT: ErrorCode.BODY_FIT_NOT_CONVERGED,
    Stage.DRAPE: ErrorCode.DRAPE_INVALID,
    Stage.RENDER: ErrorCode.RENDER_FAILED,
    Stage.REALIZE: ErrorCode.EXTERNAL_SUBMIT_UNKNOWN,
    Stage.VALIDATE: ErrorCode.VALIDATION_UNVERIFIABLE,
}


@dataclass(frozen=True)
class FittingPorts:
    """피팅 흐름에 넣어 줄 구현 묶음. 테스트는 `fitting.adapters.fake.fake_ports()`를 쓴다."""

    fit: FitCalculator
    body: BodyBuilder
    drape: Draper
    render: Renderer
    realize: Realizer
    validate: Validator


@dataclass(frozen=True)
class FittingOutcome:
    status: FittingStatus
    report: FitReport | None


@dataclass(frozen=True)
class RunSettings:
    config_version: str = "cfg_v1"
    timeout_seconds: int = 600


class _VisualFailed(Exception):
    def __init__(self, stage: Stage, code: ErrorCode) -> None:
        super().__init__(f"{stage.value}: {code.value}")
        self.stage = stage
        self.code = code


def run_fitting(
    request: FittingRequest,
    garment: GarmentVariant,
    ports: FittingPorts,
    *,
    job_id: str,
    created_at: datetime,
    settings: RunSettings | None = None,
) -> FittingOutcome:
    """피팅 한 번을 실행하고 최종 상태를 돌려준다."""
    if request.garment_variant_id != garment.id:
        raise ValueError(
            f"요청한 의류({request.garment_variant_id})와 넘긴 의류({garment.id})가 다릅니다"
        )
    settings = settings or RunSettings()
    progress: dict[Stage, StageStatus] = {s: StageStatus.WAITING for s in REPORT_STAGES}
    progress.update({s: StageStatus.WAITING for s in VISUAL_STAGES})

    if garment.status is not GarmentStatus.READY:
        for stage in progress:
            progress[stage] = StageStatus.SKIPPED
        status = FittingStatus(
            job_id=job_id,
            status=JobStatus.FAILED,
            report=ReportPart(status=StageStatus.FAILED, error_code=ErrorCode.GARMENT_NOT_READY),
            visual=VisualPart(status=StageStatus.SKIPPED),
            stages=_stages(progress),
        )
        return FittingOutcome(status=status, report=None)

    report = ports.fit.calculate(
        request.to_body_input(), garment, job_id=job_id, created_at=created_at
    )
    for stage in REPORT_STAGES:
        progress[stage] = StageStatus.SUCCEEDED
    report_part = ReportPart(
        status=StageStatus.SUCCEEDED, rules_version=report.rules_version, regions=report.regions
    )

    if request.mode is Mode.REPORT_ONLY:
        for stage in VISUAL_STAGES:
            progress[stage] = StageStatus.SKIPPED
        visual = VisualPart(status=StageStatus.SKIPPED)
    else:
        visual = _run_visual(request, garment, ports, job_id, settings, progress)

    job_status = JobStatus.SUCCEEDED
    if visual.status is StageStatus.FAILED:
        job_status = JobStatus.PARTIAL_SUCCEEDED
    status = FittingStatus(
        job_id=job_id,
        status=job_status,
        report=report_part,
        visual=visual,
        stages=_stages(progress),
    )
    return FittingOutcome(status=status, report=report)


def _run_visual(
    request: FittingRequest,
    garment: GarmentVariant,
    ports: FittingPorts,
    job_id: str,
    settings: RunSettings,
    progress: dict[Stage, StageStatus],
) -> VisualPart:
    found: dict[str, str | None] = {
        "drape_preview_artifact_id": None,
        "render_artifact_id": None,
        "video_artifact_id": None,
        "frame_artifact_id": None,
    }
    quality = QualityStatus.PENDING

    def step(stage: Stage, inputs: list[str], call) -> PortResult:
        progress[stage] = StageStatus.RUNNING
        port_request = PortRequest(
            job_id=job_id,
            attempt_id=f"{job_id}_{stage.value.lower()}_1",
            input_artifact_ids=inputs,
            config_version=settings.config_version,
            timeout_seconds=settings.timeout_seconds,
            trace_id=job_id,
        )
        try:
            result = require_output_artifacts(call(port_request), stage)
        except PortFailure as failure:
            raise _VisualFailed(stage, failure.error.code) from failure
        except PortResultInvalid as invalid:
            raise _VisualFailed(stage, INVALID_RESULT_CODE[stage]) from invalid
        progress[stage] = StageStatus.SUCCEEDED
        return result

    try:
        body = step(
            Stage.BODY_FIT, [], lambda r: ports.body.build_body(r, request.to_body_input())
        )
        drape = step(
            Stage.DRAPE, body.output_artifact_ids[:1], lambda r: ports.drape.drape(r, garment)
        )
        found["drape_preview_artifact_id"] = _nth(drape.output_artifact_ids, 1)
        render = step(
            Stage.RENDER, drape.output_artifact_ids[:1], lambda r: ports.render.render(r, garment)
        )
        render_id = render.output_artifact_ids[0]
        found["render_artifact_id"] = render_id
        video = step(Stage.REALIZE, [render_id], lambda r: ports.realize.realize(r, garment))
        video_id = video.output_artifact_ids[0]
        found["video_artifact_id"] = video_id
        checked = step(
            Stage.VALIDATE, [render_id, video_id], lambda r: ports.validate.validate(r, garment)
        )
        found["frame_artifact_id"] = _nth(checked.output_artifact_ids, 0)
        quality = _quality(checked)
        if quality is QualityStatus.FAIL:
            progress[Stage.VALIDATE] = StageStatus.FAILED
            raise _VisualFailed(Stage.VALIDATE, ErrorCode.VALIDATION_FAILED)
        if quality is not QualityStatus.PASS:
            progress[Stage.VALIDATE] = StageStatus.FAILED
            raise _VisualFailed(Stage.VALIDATE, ErrorCode.VALIDATION_UNVERIFIABLE)
    except _VisualFailed as failed:
        progress[failed.stage] = StageStatus.FAILED
        for stage in VISUAL_STAGES:
            if progress[stage] is StageStatus.WAITING:
                progress[stage] = StageStatus.SKIPPED
        if failed.stage is Stage.VALIDATE and quality is QualityStatus.PENDING:
            quality = QualityStatus.UNVERIFIABLE
        return VisualPart(
            status=StageStatus.FAILED,
            stage=failed.stage,
            quality_status=quality,
            error_code=failed.code,
            **found,
        )
    return VisualPart(
        status=StageStatus.SUCCEEDED, stage=Stage.VALIDATE, quality_status=quality, **found
    )


def _quality(result: PortResult) -> QualityStatus:
    """검증 결과의 `metrics["quality_status"]`. 없거나 모르는 값이면 검증할 수 없음으로 본다."""
    try:
        quality = QualityStatus(result.metrics.get("quality_status"))
    except ValueError:
        return QualityStatus.UNVERIFIABLE
    if quality is QualityStatus.PENDING:
        return QualityStatus.UNVERIFIABLE
    return quality


def _nth(ids: list[str], index: int) -> str | None:
    return ids[index] if len(ids) > index else None


def _stages(progress: dict[Stage, StageStatus]) -> list[StageProgress]:
    return [StageProgress(stage=s, status=st, attempt_no=1) for s, st in progress.items()]
