"""핏 API(#2)와 피팅 작업 API(#12).

    uvicorn server.app:app --reload

| 경로 | 내용 |
|---|---|
| GET /api/garments | 등록 의류 중 READY만 (`GarmentList`) |
| POST /api/fit | `FitRequest` → `FitReport`. 3D·AI 없이 즉시. 오류는 `ApiError` |
| POST /api/fittings | `FittingRequest` → 202 `FittingStatus`. 핏 리포트와 작업 ID가 든 첫 상태 |
| GET /api/fittings/{job_id} | 작업의 현재 상태 `FittingStatus`. 끝날 때까지 반복해 부른다 |
| GET /api/artifacts/{artifact_id} | 산출물 파일 (3D 미리보기, 렌더 클립, 실사 영상, 프레임) |

`POST /api/fittings`는 선택 헤더 `Idempotency-Key`를 받는다. 같은 키로 같은 본문을 다시 보내면
새 작업을 만들지 않고 200으로 기존 작업의 현재 상태를 돌려준다. 키가 같은데 본문이 다르면 422다.

설정
- 등록 의류 디렉터리: 인자 `garments_dir` > 환경변수 `FIT_GARMENTS_DIR` > `data/garments/`
- 핏 기준표: 인자 `rules` (기본 `config/fit_rules_v0.yaml`)
- 산출물 폴더: 인자 `artifacts_dir` > 환경변수 `FIT_ARTIFACTS_DIR` > `artifacts/files/`
- 영상 경로 포트: 인자 `ports`. 없으면 실제 핏 계산기 + 가짜 영상 어댑터다(실제 어댑터 연결은 B4).
- 작업 실행기: 인자 `runner`. 없으면 위 포트로 만든다.

가짜 영상 어댑터를 쓸 때만 보는 환경변수(`ports`를 직접 넘기면 무시한다)
- `FIT_MAX_WORKERS` (기본 1): 동시에 도는 영상 경로 수
- `FIT_FAKE_STAGE_DELAY_S` (기본 0): 가짜 영상 단계마다 쉬는 초. 화면에서 진행을 볼 때 쓴다
- `FIT_FAKE_PLACEHOLDERS` (`1`이면 켬): 이미지 산출물에 단색 자리표시 PNG를 쓴다

이 모듈이 계산기(`fitting.fit`)·작업 실행기(`fitting.services`)·의류 목록을 조립한다.
`fitting/`은 서버를 모른다.
"""

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, Response
from fastapi.responses import FileResponse

from fitting.adapters.fake import fake_ports, with_stage_delay
from fitting.domain import (
    ApiError,
    FitReport,
    FitRequest,
    FittingRequest,
    FittingStatus,
    GarmentList,
    GarmentListItem,
)
from fitting.fit import FitRules, RuleFitCalculator
from fitting.services.fitting import FittingPorts
from fitting.services.jobs import JobRunner
from server.artifacts import DEFAULT_ARTIFACTS_SUBDIR, ArtifactDirectory, media_type
from server.errors import (
    GARMENT_NOT_READY_STATUS,
    ArtifactNotFound,
    GarmentNotFound,
    JobNotFound,
    register_error_handlers,
)
from server.garments import GarmentDirectory
from server.placeholders import with_placeholders

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GARMENTS_DIR = REPO_ROOT / "data" / "garments"
DEFAULT_ARTIFACTS_DIR = REPO_ROOT / DEFAULT_ARTIFACTS_SUBDIR
GARMENTS_DIR_ENV = "FIT_GARMENTS_DIR"
ARTIFACTS_DIR_ENV = "FIT_ARTIFACTS_DIR"
MAX_WORKERS_ENV = "FIT_MAX_WORKERS"
FAKE_STAGE_DELAY_ENV = "FIT_FAKE_STAGE_DELAY_S"
FAKE_PLACEHOLDERS_ENV = "FIT_FAKE_PLACEHOLDERS"

_ERROR_RESPONSES = {
    404: {"model": ApiError, "description": "없는 의류 ID (GARMENT_NOT_FOUND)"},
    GARMENT_NOT_READY_STATUS: {
        "model": ApiError,
        "description": "계산할 수 없는 의류 (GARMENT_NOT_READY)",
    },
    422: {"model": ApiError, "description": "입력 범위·형식 오류 (INPUT_INVALID)"},
}

_FITTING_RESPONSES = {
    **_ERROR_RESPONSES,
    202: {"model": FittingStatus, "description": "작업을 받았다. 핏 리포트는 이미 들어 있다"},
    200: {"model": FittingStatus, "description": "같은 Idempotency-Key의 기존 작업 상태"},
    422: {
        "model": ApiError,
        "description": "입력 오류, 또는 같은 Idempotency-Key에 다른 본문 (INPUT_INVALID)",
    },
}

_JOB_RESPONSES = {404: {"model": ApiError, "description": "없는 작업 ID (JOB_NOT_FOUND)"}}

_ARTIFACT_RESPONSES = {
    200: {"description": "산출물 파일. Content-Type은 확장자로 정한다"},
    404: {"model": ApiError, "description": "없거나 형식이 틀린 산출물 ID (ARTIFACT_NOT_FOUND)"},
}


def _garments_dir(garments_dir: Path | str | None) -> Path:
    if garments_dir is not None:
        return Path(garments_dir)
    env = os.environ.get(GARMENTS_DIR_ENV)
    return Path(env) if env else DEFAULT_GARMENTS_DIR


def _artifacts_dir(artifacts_dir: Path | str | None) -> Path:
    if artifacts_dir is not None:
        return Path(artifacts_dir)
    env = os.environ.get(ARTIFACTS_DIR_ENV)
    return Path(env) if env else DEFAULT_ARTIFACTS_DIR


def _env_number(name: str, default: float, *, minimum: float, integer: bool) -> float:
    """환경변수의 숫자. 없으면 기본값이고, 틀리면 서버를 띄울 때 바로 알린다."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    kind = "정수" if integer else "숫자"
    try:
        value = int(raw) if integer else float(raw)
    except ValueError:
        raise ValueError(f"{name}은 {minimum:g} 이상의 {kind}여야 합니다: {raw!r}") from None
    if value < minimum or value != value or value == float("inf"):
        raise ValueError(f"{name}은 {minimum:g} 이상의 {kind}여야 합니다: {raw!r}")
    return value


def _default_ports(calculator: RuleFitCalculator, artifacts_dir: Path) -> FittingPorts:
    """실제 핏 계산기 + 가짜 영상 어댑터. 실제 어댑터 연결은 B4에서 한다."""
    ports = fake_ports(fit=calculator)
    if os.environ.get(FAKE_PLACEHOLDERS_ENV) == "1":
        ports = with_placeholders(ports, artifacts_dir)
    delay = _env_number(FAKE_STAGE_DELAY_ENV, 0.0, minimum=0.0, integer=False)
    return with_stage_delay(ports, delay)


def create_app(
    *,
    garments_dir: Path | str | None = None,
    rules: FitRules | None = None,
    ports: FittingPorts | None = None,
    artifacts_dir: Path | str | None = None,
    runner: JobRunner | None = None,
) -> FastAPI:
    garments = GarmentDirectory(_garments_dir(garments_dir))
    calculator = RuleFitCalculator(rules)
    artifacts = ArtifactDirectory(_artifacts_dir(artifacts_dir))
    if runner is None:
        workers = int(_env_number(MAX_WORKERS_ENV, 1, minimum=1, integer=True))
        runner = JobRunner(
            ports if ports is not None else _default_ports(calculator, artifacts.path),
            max_workers=workers,
        )
    jobs = runner

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        jobs.shutdown()  # 앱이 내려갈 때 시작하지 못한 작업을 취소한다. 작업 스레드는 daemon이다

    app = FastAPI(title="Fit Ruler 핏 API", version="0.2.0", lifespan=lifespan)
    app.state.runner = jobs  # 테스트와 운영 점검이 작업 상태를 직접 볼 수 있게 한다
    register_error_handlers(app)

    @app.get("/api/garments", response_model=GarmentList)
    def list_garments() -> GarmentList:
        return GarmentList(
            garments=[GarmentListItem(garment=g, is_demo=g.is_demo) for g in garments.ready()]
        )

    @app.post("/api/fit", response_model=FitReport, responses=_ERROR_RESPONSES)
    def fit(request: FitRequest) -> FitReport:
        garment = garments.get(request.garment_variant_id)
        if garment is None:
            raise GarmentNotFound(request.garment_variant_id)
        return calculator.calculate(
            request.to_body_input(),
            garment,
            job_id=f"fit_{uuid.uuid4().hex[:12]}",
            created_at=datetime.now(timezone.utc),
        )

    @app.post(
        "/api/fittings",
        response_model=FittingStatus,
        status_code=202,
        responses=_FITTING_RESPONSES,
    )
    def create_fitting(
        request: FittingRequest,
        response: Response,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> FittingStatus:
        garment = garments.get(request.garment_variant_id)
        if garment is None:
            raise GarmentNotFound(request.garment_variant_id)
        key = idempotency_key.strip() if idempotency_key else None
        submission = jobs.submit_job(request, garment, idempotency_key=key or None)
        if not submission.created:
            response.status_code = 200
        return submission.status

    @app.get(
        "/api/fittings/{job_id}",
        response_model=FittingStatus,
        responses=_JOB_RESPONSES,
    )
    def get_fitting(job_id: str) -> FittingStatus:
        status = jobs.get(job_id)
        if status is None:
            raise JobNotFound(job_id)
        return status

    # `:path`로 받아야 `a/b`·`../x` 같은 ID도 이 경로가 받아서, 라우터의 기본 404가 아니라
    # ARTIFACT_NOT_FOUND로 답한다.
    @app.get(
        "/api/artifacts/{artifact_id:path}",
        response_class=FileResponse,
        responses=_ARTIFACT_RESPONSES,
    )
    def get_artifact(artifact_id: str) -> FileResponse:
        path = artifacts.find(artifact_id)
        if path is None:
            raise ArtifactNotFound(artifact_id)
        return FileResponse(
            path, media_type=media_type(path), headers={"X-Content-Type-Options": "nosniff"}
        )

    return app


app = create_app()
