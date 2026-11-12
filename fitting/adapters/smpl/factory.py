"""SMPL 몸 생성 어댑터 팩토리 (D3, docs/adapters.md "팩토리 약속").

설정 파일(`config/adapters/*.yaml`)에 이렇게 적는다.

    body: "fitting.adapters.smpl.factory:make_body_builder"

`server.wiring`을 import하므로 어댑터 본체(`builder.py`)와 파일을 나눴다.
"""

from __future__ import annotations

from pathlib import Path

from server.wiring import AdapterConfigError, AdapterSettings

from .builder import SmplBodyBuilder, SmplPosedBody
from .model import SmplModel, find_neutral_model

# measure_v1의 어깨 끝점 정점 번호(3011·6470)가 있는 SMPL 메시의 정점 수
SMPL_VERTEX_COUNT = 6890
MIN_BETAS = 10

_HOW_TO = (
    "models/README.md대로 SMPL 중성 모델을 받고 "
    "그 폴더(예: models/smpl)를 SMPL_MODEL_DIR에 적으세요"
)


def _model_dir(settings: AdapterSettings) -> Path:
    if settings.smpl_model_dir is None:
        raise AdapterConfigError(f"SMPL_MODEL_DIR이 비어 있습니다. {_HOW_TO}")
    directory = Path(settings.smpl_model_dir)
    # 상대 경로는 지금 폴더에 없으면 저장소 루트 기준으로 본다
    if not directory.is_absolute() and not directory.is_dir():
        directory = settings.repo_root / directory
    return directory


def make_body_builder(settings: AdapterSettings) -> SmplBodyBuilder:
    """`SMPL_MODEL_DIR`의 중성 모델을 읽어 몸 생성 어댑터를 만든다.

    Raises:
        AdapterConfigError: 폴더·모델 파일이 없거나, 읽을 수 없거나, measure_v1에 맞지 않는
            모델이다.
    """
    directory = _model_dir(settings)
    try:
        path = find_neutral_model(directory)
    except FileNotFoundError as error:
        raise AdapterConfigError(f"{error}. {_HOW_TO}") from error
    try:
        model = SmplModel.load(path)
    except Exception as error:  # 손상·형식 오류 등. 원인 이름만 알린다
        raise AdapterConfigError(
            f"SMPL 모델 파일을 읽을 수 없습니다: {path.name} ({type(error).__name__})"
        ) from error
    vertices = int(model.v_template.shape[0])
    if vertices != SMPL_VERTEX_COUNT or model.num_betas < MIN_BETAS:
        raise AdapterConfigError(
            f"SMPL 모델이 measure_v1과 맞지 않습니다: {path.name} "
            f"(정점 {vertices}개·형상 계수 {model.num_betas}개, "
            f"필요: 정점 {SMPL_VERTEX_COUNT}개·형상 계수 {MIN_BETAS}개 이상)"
        )
    return SmplBodyBuilder(
        body_model=SmplPosedBody(model),
        artifacts_dir=settings.artifacts_dir,
        work_dir=settings.work_dir,
        model_name=path.name,
    )
