"""실제 SMPL 모델로 도는 확인. 환경변수 SMPL_MODEL_PATH에 모델 파일 경로가 있을 때만 돈다.

SMPL_MODEL_PATH=models/smpl/basicmodel_neutral_lbs_10_207_0_v1.1.0.pkl python -m pytest -m smpl
"""

import os
from pathlib import Path

import numpy as np
import pytest

from fitting.adapters.smpl import JOINT, SmplModel, a_pose
from fitting.body.measure import measure_body

MODEL_PATH = os.environ.get("SMPL_MODEL_PATH")

pytestmark = [
    pytest.mark.smpl,
    pytest.mark.skipif(
        not MODEL_PATH, reason="SMPL_MODEL_PATH가 없어 실제 모델 확인을 건너뜀"
    ),
]


@pytest.fixture(scope="module")
def model() -> SmplModel:
    return SmplModel.load(Path(MODEL_PATH or ""))


def test_model_shapes(model: SmplModel) -> None:
    assert model.v_template.shape == (6890, 3)
    assert model.faces.shape == (13776, 3)
    assert model.j_regressor.shape == (24, 6890)
    assert model.weights.shape == (6890, 24)
    assert model.num_betas >= 10


def test_axes_and_a_pose(model: SmplModel) -> None:
    out = model.forward(body_pose=a_pose(45))
    # x가 몸의 왼쪽: 왼쪽 관절은 +x
    assert (
        out.joints[JOINT["left_shoulder"], 0]
        > 0
        > out.joints[JOINT["right_shoulder"], 0]
    )
    # A자세: 손목이 어깨보다 아래, 그리고 바깥
    for side in ("left", "right"):
        shoulder, wrist = (
            out.joints[JOINT[f"{side}_shoulder"]],
            out.joints[JOINT[f"{side}_wrist"]],
        )
        assert wrist[1] < shoulder[1]
        assert abs(wrist[0]) > abs(shoulder[0])


def test_average_body_measurements_are_plausible(model: SmplModel) -> None:
    out = model.forward(body_pose=a_pose(45))
    result, debug = measure_body(out.vertices, model.faces, out.joints, model.weights)
    # 넓게 잡은 성인 범위. 실제 값은 docs/spikes/smpl-body-measure.md에 기록한다.
    assert 150 <= result.height_cm <= 195
    assert 75 <= result.chest_circumference_cm <= 120
    assert 60 <= result.waist_circumference_cm <= 110
    assert 30 <= result.shoulder_width_cm <= 50
    assert 45 <= result.arm_length_cm <= 75
    assert result.chest_circumference_cm > result.waist_circumference_cm * 0.9
    assert debug.waist_level_m < debug.chest_level_m


def test_betas_change_measurements(model: SmplModel) -> None:
    pose = a_pose(45)
    base = model.forward(body_pose=pose)
    changed = model.forward(betas=np.array([2.0]), body_pose=pose)
    a, _ = measure_body(base.vertices, model.faces, base.joints, model.weights)
    b, _ = measure_body(changed.vertices, model.faces, changed.joints, model.weights)
    assert a != b
