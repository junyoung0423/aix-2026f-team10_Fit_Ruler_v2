"""피팅 한 번을 처음부터 끝까지 순서대로 실행한다 (#1 흐름 확인, #12 실패 처리의 뼈대).

핏 경로(리포트)와 영상 경로(몸 → 착장 → 렌더 → 실사화 → 검증)는 서로 독립이다.
영상 경로의 어느 단계가 실패해도 이미 만든 핏 리포트와 3D 미리보기는 그대로 돌려준다.
실사화에 동의하지 않은 요청(`realize_consent=false`)은 렌더까지만 하고 성공으로 끝낸다.

끝까지 한 번에 실행하는 동기 함수다. 작업 큐와 상태 저장은 `fitting.services.jobs`가, API는
`server/`가 이 위에 얹는다. 실행 중인 상태를 밖에서 보려면 `on_progress`를 넘긴다.
"""

import logging
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

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
    VisualSkipReason,
)
from fitting.fit import FitEngineError
from fitting.ports import (
    BODY_OUTPUTS,
    DRAPE_OUTPUTS,
    REALIZE_OUTPUTS,
    RENDER_OUTPUTS,
    VALIDATE_OUTPUTS,
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
    output_artifact,
    require_output_artifacts,
)
from fitting.services.watchdog import Wait, WatchdogExpired, call_with_watchdog, real_wait

logger = logging.getLogger(__name__)

# 상태가 바뀔 때마다 현재 상태를 받는 함수. 예외를 던져도 피팅은 계속된다.
ProgressCallback = Callable[[FittingStatus], None]

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
# 어댑터가 시간 제한 + 감시 여유를 넘기고도 돌아오지 않아 서비스가 단계를 끝낼 때의 오류 코드.
# [제안] 새 코드를 만들지 않는다. 실사화만 타임아웃 코드가 있고 나머지는 위 코드를 쓴다.
WATCHDOG_CODE = {**INVALID_RESULT_CODE, Stage.REALIZE: ErrorCode.REALIZE_TIMEOUT}

# 단계별 시간 제한(초) 기본값 = `config/pipeline_v0.yaml`의 `stage_timeouts_s` ([제안])
DEFAULT_STAGE_TIMEOUTS_S: Mapping[Stage, float] = {
    Stage.BODY_FIT: 180.0,
    Stage.DRAPE: 600.0,
    Stage.RENDER: 300.0,
    Stage.REALIZE: 900.0,  # 피팅룸 실사화 4단계(docs/realize-fitroom.md)
    Stage.VALIDATE: 120.0,
}
# 단계별 감시 여유(초) 기본값 = `config/pipeline_v0.yaml`의 `watchdog_grace_s` ([제안]).
# 착장은 Blender 착장(최대 600초) 뒤 분석 단계에 같은 한도를 새로 주므로 600 + 60을 둔다.
# 실사화는 fal 대기 한도가 업로드·제출 뒤부터 세고 결과 받기·내려받기가 더 붙어서 120이다.
DEFAULT_WATCHDOG_GRACE_S: Mapping[Stage, float] = {
    Stage.BODY_FIT: 60.0,
    Stage.DRAPE: 660.0,
    Stage.RENDER: 60.0,
    Stage.REALIZE: 120.0,
    Stage.VALIDATE: 60.0,
}


def report_part_of(report: FitReport) -> ReportPart:
    """핏 리포트 → 작업 상태의 핏 경로 부분."""
    return ReportPart(
        status=StageStatus.SUCCEEDED,
        rules_version=report.rules_version,
        regions=report.regions,
        summary=report.summary,
        warnings=report.warnings,
    )


def pending_progress(*, realize_consent: bool) -> dict[Stage, StageStatus]:
    """핏 리포트가 끝나고 영상 경로가 시작하기 전의 단계 상태.

    실사화에 동의하지 않았으면 처음부터 REALIZE·VALIDATE가 SKIPPED다.
    """
    progress = {stage: StageStatus.SUCCEEDED for stage in REPORT_STAGES}
    progress.update({stage: StageStatus.WAITING for stage in VISUAL_STAGES})
    if not realize_consent:
        progress[Stage.REALIZE] = StageStatus.SKIPPED
        progress[Stage.VALIDATE] = StageStatus.SKIPPED
    return progress


def in_flight_status(
    job_id: str,
    report: ReportPart,
    progress: dict[Stage, StageStatus],
    *,
    realize_consent: bool,
    stage: Stage | None = None,
    artifacts: dict[str, Any] | None = None,
    queued: bool = False,
    attempts: Mapping[Stage, int] | None = None,
) -> FittingStatus:
    """영상 경로가 끝나기 전의 상태 (명세서 11장 진행 중 예시와 같은 모양).

    - 실행 중: 작업 `RUNNING`, `visual.status` `RUNNING`, `visual.stage`는 지금 단계(시작 전이면
      null), `quality_status` `PENDING`, 그때까지 나온 산출물 ID.
    - 대기 중(`queued=True`): 작업 `ACCEPTED`, `visual.status` `QUEUED`. 순서를 기다리는 작업이다.
    - 실사화에 동의하지 않았으면 처음부터 `skip_reason`이 `CONSENT_NOT_GIVEN`, `quality_status`가
      null이다. 검증을 하지 않으니 판정이 없기 때문이다.
    """
    return FittingStatus(
        job_id=job_id,
        status=JobStatus.ACCEPTED if queued else JobStatus.RUNNING,
        report=report,
        visual=VisualPart(
            status=StageStatus.QUEUED if queued else StageStatus.RUNNING,
            stage=stage,
            quality_status=QualityStatus.PENDING if realize_consent else None,
            skip_reason=None if realize_consent else VisualSkipReason.CONSENT_NOT_GIVEN,
            **(artifacts or {}),
        ),
        stages=_stages(progress, attempts),
    )


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
    """피팅 실행 설정. 기본값은 `config/pipeline_v0.yaml`과 같다(`fitting.services.pipeline`).

    - 단계 시간 제한: `stage_timeouts_s`에 있으면 그 값, 없고 `timeout_seconds`를 주었으면
      모든 단계에 그 값(B3·B4 호환), 둘 다 없으면 `DEFAULT_STAGE_TIMEOUTS_S`.
    - 감시 시간: 어댑터가 시간 제한을 넘기고도 돌아오지 않으면, 제한 + 그 단계의 감시 여유
      (`grace_for`) 뒤 서비스가 그 단계를 실패로 끝낸다(`fitting.services.watchdog`).
      `watchdog_grace_s`는 숫자 하나(모든 단계에 같은 값)나 단계별 묶음이다. 어댑터가 정상적으로
      시간 제한을 넘길 수 있으면 그 단계 여유를 키운다(착장: 분석 단계).
    - 재조회: `retryable=true`이고 `external_request_id`가 있는 실패만, `requery_delay_s`초 쉬고
      같은 포트를 다시 부른다. 최대 `requery_max_attempts`번 더(0이면 안 한다).
    """

    config_version: str = "cfg_v1"
    timeout_seconds: int | None = None
    stage_timeouts_s: Mapping[Stage, float] = field(default_factory=dict)
    watchdog_grace_s: float | Mapping[Stage, float] = field(
        default_factory=lambda: dict(DEFAULT_WATCHDOG_GRACE_S)
    )
    requery_max_attempts: int = 2
    requery_delay_s: float = 5.0

    def timeout_for(self, stage: Stage) -> float:
        if stage in self.stage_timeouts_s:
            return float(self.stage_timeouts_s[stage])
        if self.timeout_seconds is not None:
            return float(self.timeout_seconds)
        return DEFAULT_STAGE_TIMEOUTS_S[stage]

    def grace_for(self, stage: Stage) -> float:
        """그 단계의 감시 여유(초). 숫자면 그 값, 묶음에 없는 단계는 기본값."""
        if isinstance(self.watchdog_grace_s, Mapping):
            return float(self.watchdog_grace_s.get(stage, DEFAULT_WATCHDOG_GRACE_S[stage]))
        return float(self.watchdog_grace_s)


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
    on_progress: ProgressCallback | None = None,
    sleep: Callable[[float], None] | None = None,
    wait: Wait | None = None,
) -> FittingOutcome:
    """피팅 한 번을 실행하고 최종 상태를 돌려준다.

    `on_progress`를 넘기면 상태가 바뀔 때마다 그 시점의 상태로 호출한다(`None`이면 호출하지 않고,
    그 밖의 동작은 같다).

    - 핏 리포트가 만들어진 직후 1번(영상 경로를 하는 요청만), 영상 경로의 각 단계가 RUNNING이 될
      때와 성공으로 끝날 때. 진행 중 상태의 모양은 `in_flight_status`다.
    - 마지막 호출은 항상 반환하는 최종 상태와 같다. 실패(건너뜀 포함)와 마지막 단계의 끝은 이 최종
      상태로 알린다. 리포트를 만들 수 없는 경우(의류 미준비, 계산 불가)도 최종 상태 1번이다.
    - `on_progress`가 예외를 던져도 로그만 남기고 피팅은 계속한다.

    영상 경로 단계 하나는 이렇게 처리한다(`_run_visual`의 `step`).
    - 단계별 시간 제한을 `PortRequest.timeout_seconds`로 넘긴다. 어댑터가 그 안에 스스로 끝낸다.
    - 시간 제한 + 감시 여유 안에 호출이 돌아오지 않으면 그 단계를 실패로 끝낸다(재조회 안 함).
    - `PortFailure`·`PortResultInvalid`가 아닌 예상 밖 예외는 그 단계의 실패로 바꾸고 내용은
      로그에만 남긴다.
    - `retryable=true`이고 `external_request_id`가 있는 실패는 같은 요청 ID로 다시 조회한다.
      오류 코드 이름으로 판단하지 않는다.
    `sleep`·`wait`은 테스트가 실제 시간을 기다리지 않게 바꿔 끼우는 자리다.
    """
    if request.garment_variant_id != garment.id:
        raise ValueError(
            f"요청한 의류({request.garment_variant_id})와 넘긴 의류({garment.id})가 다릅니다"
        )
    settings = settings or RunSettings()
    sleep = sleep or time.sleep
    wait = wait or real_wait
    progress: dict[Stage, StageStatus] = {s: StageStatus.WAITING for s in REPORT_STAGES}
    progress.update({s: StageStatus.WAITING for s in VISUAL_STAGES})
    attempts: dict[Stage, int] = {}  # 단계별로 지금까지 부른 횟수 (StageProgress.attempt_no)

    def finish(status: FittingStatus, report: FitReport | None) -> FittingOutcome:
        _notify(on_progress, status)
        return FittingOutcome(status=status, report=report)

    if garment.status is not GarmentStatus.READY:
        for stage in progress:
            progress[stage] = StageStatus.SKIPPED
        status = FittingStatus(
            job_id=job_id,
            status=JobStatus.FAILED,
            report=ReportPart(status=StageStatus.FAILED, error_code=ErrorCode.GARMENT_NOT_READY),
            visual=VisualPart(status=StageStatus.SKIPPED, quality_status=None),
            stages=_stages(progress),
        )
        return finish(status, None)

    try:
        report = ports.fit.calculate(
            request.to_body_input(), garment, job_id=job_id, created_at=created_at
        )
    except FitEngineError as error:
        # 의류군·프로토콜 불일치, 필수 실측 누락 등 계산할 수 없는 조합 (#2). 리포트가 없으므로
        # 영상 경로도 시작하지 않는다(동의 여부와 무관). 내부 사유(error.reason)는 넣지 않는다.
        # 검증 판정이 없으므로 quality_status는 null이고, 사용자가 고른 건너뜀이 아니므로
        # skip_reason도 null이다(의류 미준비와 같다).
        progress[Stage.FIT_CALCULATE] = StageStatus.FAILED
        for stage in progress:
            if progress[stage] is StageStatus.WAITING:
                progress[stage] = StageStatus.SKIPPED
        status = FittingStatus(
            job_id=job_id,
            status=JobStatus.FAILED,
            report=ReportPart(status=StageStatus.FAILED, error_code=error.code),
            visual=VisualPart(status=StageStatus.SKIPPED, quality_status=None),
            stages=_stages(progress),
        )
        return finish(status, None)
    progress = pending_progress(realize_consent=request.realize_consent)
    report_part = report_part_of(report)

    if request.mode is Mode.REPORT_ONLY:
        for stage in VISUAL_STAGES:
            progress[stage] = StageStatus.SKIPPED
        visual = VisualPart(
            status=StageStatus.SKIPPED,
            quality_status=None,
            skip_reason=VisualSkipReason.REPORT_ONLY,
        )
    else:

        def announce(stage: Stage | None, artifacts: dict[str, Any]) -> None:
            if on_progress is None:
                return
            running = in_flight_status(
                job_id,
                report_part,
                progress,
                realize_consent=request.realize_consent,
                stage=stage,
                artifacts=artifacts,
                attempts=attempts,
            )
            _notify(on_progress, running)

        announce(None, {})
        visual = _run_visual(
            request, garment, ports, job_id, settings, progress, attempts, announce, sleep, wait
        )

    job_status = JobStatus.SUCCEEDED
    if visual.status is StageStatus.FAILED:
        job_status = JobStatus.PARTIAL_SUCCEEDED
    status = FittingStatus(
        job_id=job_id,
        status=job_status,
        report=report_part,
        visual=visual,
        stages=_stages(progress, attempts),
    )
    return finish(status, report)


def _notify(on_progress: ProgressCallback | None, status: FittingStatus) -> None:
    if on_progress is None:
        return
    try:
        on_progress(status)
    except Exception:  # 상태를 받는 쪽의 문제로 피팅을 멈추지 않는다
        logger.exception(
            "진행 상태를 전달하지 못했습니다 (job_id=%s). 피팅은 계속합니다", status.job_id
        )


def _run_visual(
    request: FittingRequest,
    garment: GarmentVariant,
    ports: FittingPorts,
    job_id: str,
    settings: RunSettings,
    progress: dict[Stage, StageStatus],
    attempts: dict[Stage, int],
    announce: Callable[[Stage | None, dict[str, Any]], None],
    sleep: Callable[[float], None],
    wait: Wait,
) -> VisualPart:
    found: dict[str, Any] = {
        "drape_preview_artifact_id": None,
        "render_artifact_id": None,
        "video_artifact_id": None,
        "frame_artifact_id": None,
        "render_frame_artifact_id": None,
        "quality_metrics": None,
    }
    # 검증 판정. Validator가 판정을 돌려주기 전에 끝나면(실패 포함) None으로 남는다.
    quality: QualityStatus | None = None

    def step(stage: Stage, inputs: list[str], call) -> PortResult:
        limit = settings.timeout_for(stage)
        for attempt in range(1, settings.requery_max_attempts + 2):
            attempts[stage] = attempt
            progress[stage] = StageStatus.RUNNING
            announce(stage, found)
            port_request = PortRequest(
                job_id=job_id,
                attempt_id=f"{job_id}_{stage.value.lower()}_{attempt}",
                input_artifact_ids=inputs,
                config_version=settings.config_version,
                timeout_seconds=math.ceil(limit),
                trace_id=job_id,
            )
            try:
                called = call_with_watchdog(
                    lambda request=port_request: call(request),
                    limit_s=limit + settings.grace_for(stage),
                    label=f"{job_id}:{stage.value}",
                    wait=wait,
                )
                result = require_output_artifacts(called, stage)
            except WatchdogExpired as expired:
                # 어댑터가 아직 돌고 있을 수 있어 같은 기록을 두 번 건드리지 않게 다시 부르지 않는다
                logger.warning(
                    "%s 단계가 시간 제한(%g초)과 감시 여유(%g초)를 넘겨 실패로 끝냅니다 "
                    "(job_id=%s, %g초 기다림)",
                    stage.value,
                    limit,
                    settings.grace_for(stage),
                    job_id,
                    expired.waited_s,
                )
                raise _VisualFailed(stage, WATCHDOG_CODE[stage]) from expired
            except PortFailure as failure:
                error = failure.error
                if error.retryable and error.external_request_id is None:
                    logger.warning(
                        "%s 단계가 retryable=true인데 external_request_id가 없습니다(계약 위반). "
                        "다시 부르지 않습니다 (job_id=%s, 오류 코드 %s)",
                        stage.value,
                        job_id,
                        error.code.value,
                    )
                elif error.retryable and attempt <= settings.requery_max_attempts:
                    logger.info(
                        "%s 단계를 같은 요청 ID로 다시 조회합니다 (job_id=%s, %d번째 시도 실패, "
                        "오류 코드 %s)",
                        stage.value,
                        job_id,
                        attempt,
                        error.code.value,
                    )
                    sleep(settings.requery_delay_s)
                    continue
                raise _VisualFailed(stage, error.code) from failure
            except PortResultInvalid as invalid:
                raise _VisualFailed(stage, INVALID_RESULT_CODE[stage]) from invalid
            except Exception as error:  # 어댑터가 PortFailure 대신 던진 예상 밖 예외
                logger.exception(
                    "%s 단계에서 예상하지 못한 예외가 났습니다 (job_id=%s)", stage.value, job_id
                )
                raise _VisualFailed(stage, INVALID_RESULT_CODE[stage]) from error
            progress[stage] = StageStatus.SUCCEEDED
            return result
        # 마지막 시도의 실패는 위에서 _VisualFailed로 끝난다
        raise AssertionError("도달하지 않는다")

    try:
        body = step(Stage.BODY_FIT, [], lambda r: ports.body.build_body(r, request.to_body_input()))
        body_mesh = output_artifact(body.output_artifact_ids, BODY_OUTPUTS, "body_mesh")
        announce(Stage.BODY_FIT, found)
        drape = step(Stage.DRAPE, [body_mesh], lambda r: ports.drape.drape(r, garment))
        drape_ids = drape.output_artifact_ids
        found["drape_preview_artifact_id"] = output_artifact(
            drape_ids, DRAPE_OUTPUTS, "drape_preview"
        )
        drape_result = output_artifact(drape_ids, DRAPE_OUTPUTS, "drape_result")
        announce(Stage.DRAPE, found)
        render = step(Stage.RENDER, [drape_result], lambda r: ports.render.render(r, garment))
        render_id = output_artifact(render.output_artifact_ids, RENDER_OUTPUTS, "render_clip")
        found["render_artifact_id"] = render_id
        if not request.realize_consent:
            # 실사화(외부 전송)에 동의하지 않았다. 사용자가 고른 것이라 실패가 아니다.
            # 렌더가 마지막 단계라서 끝 알림 없이 최종 상태로 알린다.
            progress[Stage.REALIZE] = StageStatus.SKIPPED
            progress[Stage.VALIDATE] = StageStatus.SKIPPED
            return VisualPart(
                status=StageStatus.SUCCEEDED,
                stage=Stage.RENDER,
                quality_status=None,
                skip_reason=VisualSkipReason.CONSENT_NOT_GIVEN,
                **found,
            )
        announce(Stage.RENDER, found)
        video = step(Stage.REALIZE, [render_id], lambda r: ports.realize.realize(r, garment))
        video_id = output_artifact(video.output_artifact_ids, REALIZE_OUTPUTS, "video")
        found["video_artifact_id"] = video_id
        announce(Stage.REALIZE, found)
        checked = step(
            Stage.VALIDATE, [render_id, video_id], lambda r: ports.validate.validate(r, garment)
        )
        checked_ids = checked.output_artifact_ids
        found["frame_artifact_id"] = output_artifact(checked_ids, VALIDATE_OUTPUTS, "frame")
        found["render_frame_artifact_id"] = output_artifact(
            checked_ids, VALIDATE_OUTPUTS, "render_frame"
        )
        found["quality_metrics"] = _numeric_metrics(checked)
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


def _numeric_metrics(result: PortResult) -> dict[str, float]:
    """검증 결과의 숫자 지표만 (예: color_delta_e, silhouette_iou). 문자열 지표는 뺀다."""
    return {
        key: float(value)
        for key, value in result.metrics.items()
        if isinstance(value, int | float) and not isinstance(value, bool)
    }


def _stages(
    progress: dict[Stage, StageStatus], attempts: Mapping[Stage, int] | None = None
) -> list[StageProgress]:
    """단계 상태 목록. `attempt_no`는 그 단계를 부른 횟수(부르지 않았으면 1)다."""
    counts = attempts or {}
    return [
        StageProgress(stage=s, status=st, attempt_no=counts.get(s, 1)) for s, st in progress.items()
    ]
