"""SMPL 어댑터 테스트. 실제 모델 파일 없이 작은 합성 모델로 확인한다."""

import pickle
import sys
import types
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")  # 선택 의존성 body가 없으면 건너뛴다
scipy_sparse = pytest.importorskip("scipy.sparse")

from fitting.adapters.smpl import (  # noqa: E402 (numpy가 있을 때만 불러온다)
    JOINT,
    SmplModel,
    a_pose,
    load_model_data,
    rodrigues,
    write_obj,
)


def tiny_model_data(num_joints: int = 3) -> dict:
    """관절 3개짜리 사슬(뿌리 → 오른쪽(+x)으로 뻗은 자식 → 손자)과 정점 3개."""
    v_template = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]])
    j_regressor = np.eye(3)  # 관절 = 각 정점 위치
    weights = np.eye(3)
    shapedirs = np.zeros((3, 3, 2))
    shapedirs[:, 1, 0] = 1.0  # beta0가 모든 정점을 y로 1만큼 올린다
    return {
        "v_template": v_template,
        "shapedirs": shapedirs,
        "posedirs": np.zeros((3, 3, (num_joints - 1) * 9)),
        "J_regressor": scipy_sparse.csc_matrix(j_regressor),
        "kintree_table": np.array([[4294967295, 0, 1], [0, 1, 2]]),
        "weights": weights,
        "f": np.array([[0, 1, 2]]),
    }


def test_rodrigues_rotates_x_to_y_around_z() -> None:
    r = rodrigues(np.array([[0.0, 0.0, np.pi / 2]]))[0]
    assert r @ np.array([1.0, 0.0, 0.0]) == pytest.approx([0.0, 1.0, 0.0], abs=1e-12)
    assert rodrigues(np.zeros((1, 3)))[0] == pytest.approx(np.eye(3))


def test_forward_rest_pose_returns_template() -> None:
    model = SmplModel.from_data(tiny_model_data())
    out = model.forward(body_pose=np.zeros((2, 3)))
    assert out.vertices == pytest.approx(model.v_template)
    assert out.joints == pytest.approx(model.v_template)
    assert list(model.parents) == [-1, 0, 1]


def test_forward_applies_betas() -> None:
    model = SmplModel.from_data(tiny_model_data())
    out = model.forward(betas=np.array([0.5]), body_pose=np.zeros((2, 3)))
    assert out.vertices[:, 1] == pytest.approx([0.5, 0.5, 0.5])


def test_forward_rotating_middle_joint_moves_child_only() -> None:
    model = SmplModel.from_data(tiny_model_data())
    pose = np.zeros((2, 3))
    pose[0] = (
        0.0,
        0.0,
        -np.pi / 2,
    )  # 관절 1을 z축으로 -90도: +x로 뻗은 자식이 -y로 내려간다
    out = model.forward(body_pose=pose)
    assert out.vertices[0] == pytest.approx([0.0, 0.0, 0.0])
    assert out.vertices[1] == pytest.approx([1.0, 0.0, 0.0])
    assert out.vertices[2] == pytest.approx([1.0, -1.0, 0.0], abs=1e-12)


def test_a_pose_lowers_left_arm_with_negative_z_rotation() -> None:
    pose = a_pose(45)
    assert pose.shape == (23, 3)
    left = pose[JOINT["left_shoulder"] - 1]
    right = pose[JOINT["right_shoulder"] - 1]
    assert left == pytest.approx([0.0, 0.0, -np.pi / 4])
    assert right == pytest.approx([0.0, 0.0, np.pi / 4])
    # 왼팔(+x) 방향 벡터에 왼쪽 어깨 회전을 적용하면 아래로 내려간다
    down = rodrigues(left[None])[0] @ np.array([1.0, 0.0, 0.0])
    assert down[1] < 0 and down[0] > 0


@pytest.fixture
def fake_chumpy():
    """공식 SMPL pkl처럼 chumpy 객체가 든 pickle을 만들기 위한 가짜 chumpy 모듈."""
    chumpy = types.ModuleType("chumpy")
    ch = types.ModuleType("chumpy.ch")

    class Ch:
        def __init__(self, x):
            self.x = x
            self._cache = {"unused": True}

    Ch.__module__ = "chumpy.ch"
    Ch.__qualname__ = "Ch"
    ch.Ch = Ch
    sys.modules["chumpy"], sys.modules["chumpy.ch"] = chumpy, ch
    yield Ch
    del sys.modules["chumpy"], sys.modules["chumpy.ch"]


def test_load_pkl_with_chumpy_objects_without_chumpy(tmp_path: Path, fake_chumpy) -> None:
    data = tiny_model_data()
    data["shapedirs"] = fake_chumpy(data["shapedirs"])
    path = tmp_path / "model.pkl"
    path.write_bytes(pickle.dumps(data, protocol=2))
    del sys.modules["chumpy.ch"]  # 읽을 때는 chumpy가 없는 상태
    sys.modules["chumpy.ch"] = types.ModuleType("chumpy.ch")

    model = SmplModel.load(path)
    assert model.shapedirs.shape == (3, 3, 2)
    assert model.j_regressor == pytest.approx(np.eye(3))


def test_load_refuses_unexpected_classes(tmp_path: Path) -> None:
    class Evil:
        def __reduce__(self):
            import os

            return (os.system, ("echo should-not-run",))

    path = tmp_path / "evil.pkl"
    path.write_bytes(pickle.dumps({"v_template": Evil()}))
    with pytest.raises(pickle.UnpicklingError):
        load_model_data(path)


def test_load_npz(tmp_path: Path) -> None:
    data = tiny_model_data()
    data["J_regressor"] = data["J_regressor"].toarray()
    path = tmp_path / "model.npz"
    np.savez(path, **data)
    model = SmplModel.load(path)
    assert model.faces.tolist() == [[0, 1, 2]]


def test_write_obj(tmp_path: Path) -> None:
    path = write_obj(
        tmp_path / "out" / "body.obj",
        np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]]),
        np.array([[0, 1, 2]]),
        comment="test",
    )
    assert path.read_text(encoding="utf-8").splitlines() == [
        "# test",
        "v 0.000000 0.000000 0.000000",
        "v 1.000000 0.000000 0.000000",
        "v 0.000000 1.000000 0.000000",
        "f 1 2 3",
    ]
