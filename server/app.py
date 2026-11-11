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
- 영상 경로 포트: 인자 `ports`를 직접 넘기면 그것을 쓴다. 없으면 **어댑터 설정**
  (`config/adapters/`)대로 영상 단계 5개를 가짜 또는 실제 어댑터로 만든다. 설정은 인자
  `adapters` > 환경변수 `FIT_ADAPTERS` > `default` 순서로 고른다(`server/wiring.py`,
  `docs/adapters.md`). 핏 계산기는 항상 실제 `RuleFitCalculator`다.
  **설정이 틀리면 가짜로 대신하지 않고 `AdapterConfigError`로 서버가 뜨지 않는다.**
- 작업 실행기: 인자 `runner`. 없으면 위 포트로 만든다.
- 어댑터에 넘기는 환경: `FIT_WORK_DIR`(중간 파일, 기본 `artifacts/work/`),
  `BLENDER_PATH`/`BLENDER_EXE`, `SMPL_MODEL_DIR`, `FAL_KEY`(값은 읽지 않고 있는지만 본다)

환경변수
- `FIT_MAX_WORKERS` (기본 1): 동시에 도는 영상 경로 수
- `FIT_FAKE_STAGE_DELAY_S` (기본 0): 가짜 영상 단계마다 쉬는 초. 화면에서 진행을 볼 때 쓴다
- `FIT_FAKE_PLACEHOLDERS` (`1`이면 켬): 이미지 산출물에 단색 자리표시 PNG를 쓴다
  위 둘은 **영상 포트 5개가 모두 가짜일 때만** 적용한다. 하나라도 실제면 무시하고 경고를 남긴다
  (실제 산출물 위에 자리표시 그림을 덮어쓰지 않게).

이 모듈이 계산기(`fitting.fit`)·작업 실행기(`fitting.services`)·의류 목록을 조립한다.
`fitting/`은 서버를 모른다.
"""

import logging
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, Response
from fastapi.responses import FileResponse

from fitting.adapters.fake import with_stage_delay
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
from server.wiring import AdapterSettings, build_ports, describe, load_adapter_config

# uvicorn이 설정해 둔 로거라 시작 로그가 터미널에 보인다.
logger = logging.getLogger("uvicorn.error")

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


def _fake_option_requested() -> bool:
    """가짜 전용 환경변수가 효과가 있게 켜져 있는가: 자리표시가 `1`이거나 지연이 0이 아니다.

    지연 값이 숫자가 아니어도 켜려고 한 것으로 본다(실제 어댑터와 섞였을 때 무시했다고 알리려는
    용도).
    """
    if os.environ.get(FAKE_PLACEHOLDERS_ENV) == "1":
        return True
    raw = os.environ.get(FAKE_STAGE_DELAY_ENV, "").strip()
    if not raw:
        return False
    try:
        return float(raw) != 0
    except ValueError:
        return True


def _adapter_ports(
    calculator: RuleFitCalculator, artifacts_dir: Path, adapters: Path | str | None
) -> tuple[FittingPorts, dict[str, str]]:
    """어댑터 설정대로 영상 포트를 만든다. 실제 핏 계산기 + 설정이 고른 영상 어댑터.

    가짜 전용 옵션은 5개가 모두 가짜일 때만 적용한다. 하나라도 실제면 무시하고 경고를 남긴다.

    Raises:
        AdapterConfigError: 설정이 틀렸다. 가짜로 대신하지 않는다.
    """
    config = load_adapter_config(adapters)
    settings = AdapterSettings.from_environ(artifacts_dir=artifacts_dir)
    ports = build_ports(config, settings, fit=calculator)
    if not config.all_fake():
        if _fake_option_requested():
            logger.warning(
                "가짜 어댑터 전용 옵션(%s, %s)은 영상 포트가 모두 가짜일 때만 적용합니다. "
                "실제 어댑터가 섞여 있어 무시합니다: %s",
                FAKE_STAGE_DELAY_ENV,
                FAKE_PLACEHOLDERS_ENV,
                ", ".join(f"{name}={spec}" for name, spec in describe(config).items()),
            )
        return ports, describe(config)
    if os.environ.get(FAKE_PLACEHOLDERS_ENV) == "1":
        ports = with_placeholders(ports, artifacts_dir)
    delay = _env_number(FAKE_STAGE_DELAY_ENV, 0.0, minimum=0.0, integer=False)
    return with_stage_delay(ports, delay), describe(config)


def create_app(
    *,
    garments_dir: Path | str | None = None,
    rules: FitRules | None = None,
    ports: FittingPorts | None = None,
    artifacts_dir: Path | str | None = None,
    runner: JobRunner | None = None,
    adapters: Path | str | None = None,
) -> FastAPI:
    """앱을 만든다. `adapters`는 어댑터 설정의 이름이나 파일 경로다(`ports`·`runner`를 직접 넘기면
    쓰지 않는다).

    Raises:
        AdapterConfigError: 어댑터 설정이 틀렸다. 서버가 뜨지 않게 그대로 올린다.
    """
    garments = GarmentDirectory(_garments_dir(garments_dir))
    calculator = RuleFitCalculator(rules)
    artifacts = ArtifactDirectory(_artifacts_dir(artifacts_dir))
    adapter_summary: dict[str, str] | None = None
    if runner is None:
        workers = int(_env_number(MAX_WORKERS_ENV, 1, minimum=1, integer=True))
        if ports is None:
            ports, adapter_summary = _adapter_ports(calculator, artifacts.path, adapters)
        runner = JobRunner(ports, max_workers=workers)
    jobs = runner

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if adapter_summary is not None:
            logger.info(
                "피팅 어댑터: %s",
                ", ".join(f"{name}={spec}" for name, spec in adapter_summary.items()),
            )
        else:
            logger.info("피팅 어댑터: 직접 넘긴 포트나 작업 실행기를 씁니다")
        yield
        jobs.shutdown()  # 앱이 내려갈 때 시작하지 못한 작업을 취소한다. 작업 스레드는 daemon이다

    app = FastAPI(title="Fit Ruler 핏 API", version="0.2.0", lifespan=lifespan)
    app.state.runner = jobs  # 테스트와 운영 점검이 작업 상태를 직접 볼 수 있게 한다
    app.state.adapters = adapter_summary  # 포트별 `fake` 또는 `모듈:팩토리`. 직접 넘겼으면 None
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


def __getattr__(name: str) -> FastAPI:
    """`uvicorn server.app:app`이 처음 `app`을 찾을 때 만든다.

    모듈을 불러오는 것만으로는 어댑터 설정을 읽지 않는다. 설정이 틀리면 서버를 띄울 때 멈추고,
    이 모듈을 불러오기만 하는 도구(테스트 수집 등)는 설정의 영향을 받지 않는다.
    """
    if name == "app":
        created = create_app()
        globals()["app"] = created  # 다음부터는 모듈 속성으로 바로 찾는다
        return created
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
