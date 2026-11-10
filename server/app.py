"""핏 API (#2). 3D·AI 없이 신체 치수와 의류 실측만으로 핏 리포트를 즉시 돌려준다.

    uvicorn server.app:app --reload

| 경로 | 내용 |
|---|---|
| GET /api/garments | 등록 의류 중 READY만 (`GarmentList`) |
| POST /api/fit | `FitRequest` → `FitReport`. 오류는 `ApiError` |

설정
- 등록 의류 디렉터리: 인자 `garments_dir` > 환경변수 `FIT_GARMENTS_DIR` > `data/garments/`
- 핏 기준표: 인자 `rules` (기본 `config/fit_rules_v0.yaml`)

이 모듈이 계산기(`fitting.fit`)와 의류 목록을 조립한다. `fitting/`은 서버를 모른다.
"""

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI

from fitting.domain import ApiError, FitReport, FitRequest, GarmentList, GarmentListItem
from fitting.fit import FitRules, RuleFitCalculator
from server.errors import GARMENT_NOT_READY_STATUS, GarmentNotFound, register_error_handlers
from server.garments import GarmentDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GARMENTS_DIR = REPO_ROOT / "data" / "garments"
GARMENTS_DIR_ENV = "FIT_GARMENTS_DIR"

_ERROR_RESPONSES = {
    404: {"model": ApiError, "description": "없는 의류 ID (GARMENT_NOT_FOUND)"},
    GARMENT_NOT_READY_STATUS: {
        "model": ApiError,
        "description": "계산할 수 없는 의류 (GARMENT_NOT_READY)",
    },
    422: {"model": ApiError, "description": "입력 범위·형식 오류 (INPUT_INVALID)"},
}


def _garments_dir(garments_dir: Path | str | None) -> Path:
    if garments_dir is not None:
        return Path(garments_dir)
    env = os.environ.get(GARMENTS_DIR_ENV)
    return Path(env) if env else DEFAULT_GARMENTS_DIR


def create_app(*, garments_dir: Path | str | None = None, rules: FitRules | None = None) -> FastAPI:
    garments = GarmentDirectory(_garments_dir(garments_dir))
    calculator = RuleFitCalculator(rules)

    app = FastAPI(title="Fit Ruler 핏 API", version="0.1.0")
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

    return app


app = create_app()
