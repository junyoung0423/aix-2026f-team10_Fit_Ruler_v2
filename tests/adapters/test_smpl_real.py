"""실제 SMPL 모델로 도는 확인. 환경변수 SMPL_MODEL_PATH에 모델 파일 경로가 있을 때만 돈다.

SMPL_MODEL_PATH=models/smpl/basicmodel_neutral_lbs_10_207_0_v1.1.0.pkl python -m pytest -m smpl
"""

import os
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")  # 선택 의존성 body가 없으면 건너뛴다

from fitting.adapters.smpl import JOINT, SmplModel, a_pose  # noqa: E402
from fitting.body.measure import measure_body  # noqa: E402

MODEL_PATH = os.environ.get("SMPL_MODEL_PATH")

pytestmark = [
    pytest.mark.smpl,
    pytest.mark.skipif(not MODEL_PATH, reason="SMPL_MODEL_PATH가 없어 실제 모델 확인을 건너뜀"),
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
    assert out.joints[JOINT["left_shoulder"], 0] > 0 > out.joints[JOINT["right_shoulder"], 0]
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
    assert 75 <= result.chest_cm <= 120
    assert 60 <= result.waist_cm <= 110
    assert 30 <= result.shoulder_cm <= 50
    assert 45 <= result.arm_length_cm <= 75
    assert result.chest_cm > result.waist_cm * 0.9
    assert debug.waist_level_m < debug.chest_level_m


def test_shoulder_tips_are_symmetric_and_above_shoulder_joints(
    model: SmplModel,
) -> None:
    """어깨 끝점 정점(3011·6470)이 A자세에서 좌우 대칭이고 어깨 관절 바로 위에 있다."""
    a = model.forward(body_pose=a_pose(45))
    _, debug = measure_body(a.vertices, model.faces, a.joints, model.weights)
    left, right = debug.left_shoulder_point, debug.right_shoulder_point
    assert abs(left[0] + right[0]) < 0.01  # 좌우 거울 대칭(1cm 이내)
    assert abs(left[1] - right[1]) < 0.01
    for point, joint in ((left, "left_shoulder"), (right, "right_shoulder")):
        offset = point - a.joints[JOINT[joint]]
        assert 0.02 < offset[1] < 0.10  # 관절보다 2~10cm 위
        assert abs(offset[0]) < 0.05  # 옆으로는 5cm 안


def test_betas_change_measurements(model: SmplModel) -> None:
    pose = a_pose(45)
    base = model.forward(body_pose=pose)
    changed = model.forward(betas=np.array([2.0]), body_pose=pose)
    a, _ = measure_body(base.vertices, model.faces, base.joints, model.weights)
    b, _ = measure_body(changed.vertices, model.faces, changed.joints, model.weights)
    assert a != b
