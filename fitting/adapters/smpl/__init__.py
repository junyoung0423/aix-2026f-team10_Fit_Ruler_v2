"""SMPL 모델 어댑터: 모델 파일 읽기, 체형·자세 적용, OBJ 저장 (numpy만 사용)."""

from .model import (
    JOINT,
    JOINT_NAMES,
    SmplModel,
    SmplOutput,
    a_pose,
    load_model_data,
    rodrigues,
    write_obj,
)

__all__ = [
    "JOINT",
    "JOINT_NAMES",
    "SmplModel",
    "SmplOutput",
    "a_pose",
    "load_model_data",
    "rodrigues",
    "write_obj",
]
