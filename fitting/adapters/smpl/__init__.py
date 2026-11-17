"""SMPL 모델 어댑터: 모델 파일 읽기, 체형·자세 적용, OBJ 저장(numpy만 사용)과
몸 생성 어댑터(D3, `BodyBuilder` 포트). 팩토리는 `factory.py`(server를 import하므로 따로 둔다)."""

from .builder import (
    A_POSE_DEG,
    BODY_FIT_RECORD,
    PosedBodyModel,
    SmplBodyBuilder,
    SmplPosedBody,
)
from .model import (
    JOINT,
    JOINT_NAMES,
    SmplModel,
    SmplOutput,
    a_pose,
    find_neutral_model,
    load_model_data,
    rodrigues,
    write_obj,
)

__all__ = [
    "A_POSE_DEG",
    "BODY_FIT_RECORD",
    "JOINT",
    "JOINT_NAMES",
    "PosedBodyModel",
    "SmplBodyBuilder",
    "SmplModel",
    "SmplOutput",
    "SmplPosedBody",
    "a_pose",
    "find_neutral_model",
    "load_model_data",
    "rodrigues",
    "write_obj",
]
