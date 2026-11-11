"""피팅 작업 실행기 (#12). 핏 리포트는 바로, 영상 경로는 백그라운드에서 돌린다.

`submit`은 핏 리포트를 요청 스레드에서 바로 계산해 첫 상태에 담고, 영상 경로(몸 → 착장 → 렌더
→ 실사화 → 검증)는 작업 스레드에 맡긴다. 화면은 `get`을 반복해 불러 단계가 넘어가는 모습을 본다.
영상 경로가 실패해도 첫 상태의 핏 리포트는 그대로 남는다(#12).

- 동시에 도는 영상 경로는 기본 1개다(Blender가 무겁다). 순서를 기다리는 작업은 작업 `ACCEPTED`,
  `visual.status` `QUEUED`, 영상 단계 전부 `WAITING`이고, 실행을 시작하면 `RUNNING`이 된다.
- 상태는 메모리에만 둔다(`dict` + `threading.Lock`). 프로세스를 다시 켜면 작업이 사라진다.
  보관하는 작업이 `max_jobs`를 넘으면 끝난 작업을 오래된 것부터 지운다.
  진행 중 작업은 지우지 않는다.
- 작업 스레드는 daemon이다. 서버를 끌 때 영상 경로가 한창이어도 프로세스가 끝나기를 기다리지 않는다.
- 타임아웃·재시도는 B5 범위다. 여기서 하지 않는다.
"""

import logging
import queue
import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
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
    Stage,
    StageProgress,
    StageStatus,
)
from fitting.fit import FitEngineError
from fitting.ports import PortError, PortFailure, PortResultInvalid
from fitting.services.fitting import (
    INVALID_RESULT_CODE,
    VISUAL_STAGES,
    FittingPorts,
    RunSettings,
    in_flight_status,
    pending_progress,
    report_part_of,
    run_fitting,
)

logger = logging.getLogger(__name__)

_UNEXPECTED_MESSAGE = "처리 중 예상하지 못한 오류가 났습니다."


class IdempotencyConflict(Exception):
    """같은 `Idempotency-Key`로 다른 요청 본문이 왔다."""

    def __init__(self, key: str) -> None:
        super().__init__(f"같은 Idempotency-Key({key})로 다른 요청이 왔습니다")
        self.key = key


@dataclass(frozen=True)
class Submission:
    """`submit_job`의 결과. `created`가 false면 같은 키로 이미 있던 작업의 현재 상태다."""

    status: FittingStatus
    created: bool


@dataclass
class _Job:
    job_id: str
    request: FittingRequest
    garment: GarmentVariant
    report: FitReport
    created_at: datetime
    status: FittingStatus
    idempotency_key: str | None = None
    done: threading.Event = field(default_factory=threading.Event)


class _FixedReport:
    """이미 계산한 리포트를 그대로 돌려주는 핏 계산기. 작업당 핏 계산을 한 번만 하려는 것이다."""

    def __init__(self, report: FitReport) -> None:
        self._report = report

    def calculate(self, body: Any, garment: Any, *, job_id: str, created_at: Any) -> FitReport:
        return self._report


class _Guarded:
    """영상 경로 포트를 감싸, `PortFailure`가 아닌 예상 밖 예외를 그 단계의 실패로 바꾼다.

    예외 내용은 로그에만 남기고, 상태에는 단계별 `INVALID_RESULT_CODE`만 나간다. 이렇게 하면
    `run_fitting`이 정해 둔 실패 처리(그 단계 FAILED, 남은 단계 SKIPPED, 핏 리포트 유지)를
    그대로 탄다.
    """

    def __init__(self, port: Any, stage: Stage) -> None:
        self._port = port
        self._stage = stage

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._port, name)
        if not callable(attribute):
            return attribute

        def guarded(*args: Any, **kwargs: Any) -> Any:
            try:
                return attribute(*args, **kwargs)
            except (PortFailure, PortResultInvalid):
                raise
            except Exception as error:
                logger.exception("%s 단계에서 예상하지 못한 예외가 났습니다", self._stage.value)
                raise PortFailure(
                    PortError(
                        code=INVALID_RESULT_CODE[self._stage],
                        stage=self._stage,
                        retryable=False,
                        safe_message=_UNEXPECTED_MESSAGE,
                    )
                ) from error

        return guarded


def _guard(ports: FittingPorts) -> FittingPorts:
    return replace(
        ports,
        body=_Guarded(ports.body, Stage.BODY_FIT),
        drape=_Guarded(ports.drape, Stage.DRAPE),
        render=_Guarded(ports.render, Stage.RENDER),
        realize=_Guarded(ports.realize, Stage.REALIZE),
        validate=_Guarded(ports.validate, Stage.VALIDATE),
    )


class JobRunner:
    """피팅 작업을 받아 영상 경로를 백그라운드에서 돌리고, 작업 ID로 상태를 돌려준다."""

    def __init__(
        self,
        ports: FittingPorts,
        *,
        max_workers: int = 1,
        max_jobs: int = 200,
        settings: RunSettings | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError(f"max_workers는 1 이상이어야 합니다: {max_workers}")
        if max_jobs < 1:
            raise ValueError(f"max_jobs는 1 이상이어야 합니다: {max_jobs}")
        self._ports = ports
        self._max_workers = max_workers
        self._max_jobs = max_jobs
        self._settings = settings
        self._lock = threading.Lock()
        self._jobs: dict[str, _Job] = {}  # 들어온 순서 = 오래된 순서
        self._keys: dict[str, str] = {}  # Idempotency-Key → 작업 ID
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._workers: list[threading.Thread] = []
        self._closed = False

    # --- 공개 -----------------------------------------------------------------------------

    def __len__(self) -> int:
        """지금 보관 중인 작업 수 (진행 중 + 끝난 것)."""
        with self._lock:
            return len(self._jobs)

    def submit(
        self,
        request: FittingRequest,
        garment: GarmentVariant,
        *,
        idempotency_key: str | None = None,
    ) -> FittingStatus:
        """작업을 만들고 첫 상태를 돌려준다. 첫 상태에는 핏 리포트와 작업 ID가 들어 있다."""
        return self.submit_job(request, garment, idempotency_key=idempotency_key).status

    def submit_job(
        self,
        request: FittingRequest,
        garment: GarmentVariant,
        *,
        idempotency_key: str | None = None,
    ) -> Submission:
        """`submit`과 같고, 새 작업인지(`created`)도 알려 준다.

        - `report_only`는 스레드 없이 최종 상태(작업 SUCCEEDED, 영상 SKIPPED)를 바로 저장·반환한다.
        - 같은 `idempotency_key`로 같은 요청이 다시 오면 새 작업을 만들지 않고 기존 작업의 현재
          상태를 돌려준다.

        Raises:
            IdempotencyConflict: 같은 키인데 요청 본문이 다르다.
            FitEngineError: 의류가 READY가 아니거나 핏 계산기가 계산할 수 없다.
                작업을 만들지 않는다.
            ValueError: 요청한 의류와 넘긴 의류가 다르다.
            RuntimeError: 이미 `shutdown`했다.
        """
        if request.garment_variant_id != garment.id:
            raise ValueError(
                f"요청한 의류({request.garment_variant_id})와 넘긴 의류({garment.id})가 다릅니다"
            )
        with self._lock:
            if self._closed:
                raise RuntimeError("작업 실행기가 종료되었습니다")
            if idempotency_key is not None:
                existing = self._existing(idempotency_key, request)
                if existing is not None:
                    return Submission(status=existing.status, created=False)
            if garment.status is not GarmentStatus.READY:
                raise FitEngineError(
                    ErrorCode.GARMENT_NOT_READY, f"의류 상태가 {garment.status.value}입니다"
                )
            job = self._create(request, garment, idempotency_key)
            self._jobs[job.job_id] = job
            if idempotency_key is not None:
                self._keys[idempotency_key] = job.job_id
            if not job.done.is_set():
                self._start_workers()
                self._queue.put(job.job_id)
            self._evict(keep=job.job_id)
            return Submission(status=job.status, created=True)

    def get(self, job_id: str) -> FittingStatus | None:
        """작업의 현재 상태. 없는(또는 지워진) 작업이면 None."""
        with self._lock:
            job = self._jobs.get(job_id)
            return job.status if job is not None else None

    def wait(self, job_id: str, timeout: float | None = None) -> FittingStatus:
        """작업이 끝날 때까지 기다려 최종 상태를 돌려준다 (테스트용).

        Raises:
            KeyError: 없는 작업이다.
            TimeoutError: `timeout`초 안에 끝나지 않았다.
        """
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if not job.done.wait(timeout):
            raise TimeoutError(f"{job_id}이 {timeout}초 안에 끝나지 않았습니다")
        with self._lock:
            return job.status

    def shutdown(self, *, wait: bool = False, timeout: float | None = None) -> None:
        """새 작업을 받지 않고, 시작하지 못한 작업은 취소한다. 실행 중인 작업은 끊지 않는다.

        작업 스레드가 daemon이라 `wait=False`여도 프로세스는 끝난다. 여러 번 불러도 된다.
        """
        with self._lock:
            workers = list(self._workers)
            if not self._closed:
                self._closed = True
                while True:
                    try:
                        waiting = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    if waiting is not None:
                        self._cancel(self._jobs[waiting])
                for _ in workers:
                    self._queue.put(None)  # 쉬고 있는 작업 스레드를 깨워 끝낸다
        if wait:
            for worker in workers:
                worker.join(timeout)

    # --- 작업 만들기·저장 -------------------------------------------------------------------

    def _existing(self, key: str, request: FittingRequest) -> _Job | None:
        job_id = self._keys.get(key)
        if job_id is None:
            return None
        job = self._jobs[job_id]
        if job.request != request:
            raise IdempotencyConflict(key)
        return job

    def _create(self, request: FittingRequest, garment: GarmentVariant, key: str | None) -> _Job:
        job_id = f"fit_{uuid.uuid4().hex[:12]}"
        while job_id in self._jobs:
            job_id = f"fit_{uuid.uuid4().hex[:12]}"
        created_at = datetime.now(timezone.utc)
        # 핏 리포트는 여기서 한 번만 계산한다. 계산할 수 없으면(FitEngineError) 작업을 만들지
        # 않는다.
        report = self._ports.fit.calculate(
            request.to_body_input(), garment, job_id=job_id, created_at=created_at
        )
        if request.mode is Mode.REPORT_ONLY:
            outcome = run_fitting(
                request,
                garment,
                replace(self._ports, fit=_FixedReport(report)),
                job_id=job_id,
                created_at=created_at,
                settings=self._settings,
            )
            status = outcome.status
        else:
            consent = request.realize_consent
            status = in_flight_status(
                job_id,
                report_part_of(report),
                pending_progress(realize_consent=consent),
                realize_consent=consent,
                queued=True,
            )
        job = _Job(
            job_id=job_id,
            request=request,
            garment=garment,
            report=report,
            created_at=created_at,
            status=status,
            idempotency_key=key,
        )
        if request.mode is Mode.REPORT_ONLY:
            job.done.set()
        return job

    def _evict(self, *, keep: str | None) -> None:
        """보관한 작업이 `max_jobs`를 넘으면 끝난 작업을 오래된 것부터 지운다.

        진행 중 작업과 `keep`은 지우지 않는다. 호출하는 쪽이 잠근다.
        """
        excess = len(self._jobs) - self._max_jobs
        for job_id in list(self._jobs):
            if excess <= 0:
                return
            job = self._jobs[job_id]
            if job_id == keep or not job.done.is_set():
                continue
            del self._jobs[job_id]
            if job.idempotency_key is not None and self._keys.get(job.idempotency_key) == job_id:
                del self._keys[job.idempotency_key]
            excess -= 1

    # --- 작업 스레드 ------------------------------------------------------------------------

    def _start_workers(self) -> None:
        """작업 스레드는 처음 영상 경로가 필요할 때 만든다. 호출하는 쪽이 잠근다."""
        while len(self._workers) < self._max_workers:
            worker = threading.Thread(
                target=self._work, name=f"fit-job-{len(self._workers) + 1}", daemon=True
            )
            self._workers.append(worker)
            worker.start()

    def _work(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                return
            self._execute(job_id)

    def _execute(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.done.is_set():
                return
        ports = _guard(replace(self._ports, fit=_FixedReport(job.report)))
        try:
            outcome = run_fitting(
                job.request,
                job.garment,
                ports,
                job_id=job_id,
                created_at=job.created_at,
                settings=self._settings,
                on_progress=lambda status: self._publish(job, status),
            )
            final = outcome.status
        except Exception:  # 포트 밖의 예상 밖 오류라도 화면이 영원히 진행 중으로 남지 않게 한다
            logger.exception("작업 %s을 끝까지 실행하지 못했습니다", job_id)
            with self._lock:
                final = _failed_in_place(job.status)
        with self._lock:
            job.status = final
            job.done.set()
            self._evict(keep=None)

    def _publish(self, job: _Job, status: FittingStatus) -> None:
        with self._lock:
            job.status = status

    def _cancel(self, job: _Job) -> None:
        """시작하지 못한 작업을 취소한 상태로 끝낸다. 호출하는 쪽이 잠근다."""
        job.status = _cancelled(job.status)
        job.done.set()


def _failed_in_place(status: FittingStatus) -> FittingStatus:
    """마지막으로 알려진 상태에서 지금 단계를 실패, 남은 단계를 건너뜀으로 바꿔 끝낸다."""
    by_stage = {p.stage: p.status for p in status.stages}
    failing = next(
        (s for s in VISUAL_STAGES if by_stage[s] is StageStatus.RUNNING),
        next((s for s in VISUAL_STAGES if by_stage[s] is StageStatus.WAITING), VISUAL_STAGES[-1]),
    )
    stages = []
    for item in status.stages:
        new = item.status
        if item.stage is failing:
            new = StageStatus.FAILED
        elif item.stage in VISUAL_STAGES and item.status in (
            StageStatus.WAITING,
            StageStatus.RUNNING,
        ):
            new = StageStatus.SKIPPED
        stages.append(StageProgress(stage=item.stage, status=new, attempt_no=item.attempt_no))
    visual = status.visual.model_copy(
        update={
            "status": StageStatus.FAILED,
            "stage": failing,
            "quality_status": None,
            "error_code": INVALID_RESULT_CODE[failing],
        }
    )
    return status.model_copy(
        update={"status": JobStatus.PARTIAL_SUCCEEDED, "visual": visual, "stages": stages}
    )


def _cancelled(status: FittingStatus) -> FittingStatus:
    """시작하지 못한 작업의 취소 상태. 대기 중이던 단계만 CANCELLED로 바꾼다."""
    waiting = (StageStatus.WAITING, StageStatus.QUEUED)
    stages = [
        StageProgress(
            stage=item.stage,
            status=StageStatus.CANCELLED if item.status in waiting else item.status,
            attempt_no=item.attempt_no,
        )
        for item in status.stages
    ]
    visual = status.visual.model_copy(
        update={"status": StageStatus.CANCELLED, "quality_status": None}
    )
    return status.model_copy(
        update={"status": JobStatus.CANCELLED, "visual": visual, "stages": stages}
    )
