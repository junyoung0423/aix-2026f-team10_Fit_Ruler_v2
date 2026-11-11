"""SMPL 모델 불러오기와 자세 적용(linear blend skinning), numpy만 쓴다.

- 모델 파일은 SMPL 공식 사이트에서 각자 받는다. 재배포가 금지돼 있어 저장소에 넣지 않는다(models/).
- 공식 .pkl에는 chumpy 객체가 섞여 있어, chumpy를 설치하지 않고 배열만 꺼내는 제한된
  언피클러를 쓴다. numpy·scipy.sparse 외의 클래스는 불러오지 않는다(임의 코드 실행 방지).
- 좌표: y가 위, z가 앞(몸이 보는 방향), x가 몸의 왼쪽. 단위는 미터.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

NUM_JOINTS = 24
NUM_BODY_JOINTS = 23  # 루트(pelvis)를 뺀 관절 수

# SMPL 24관절 순서
JOINT_NAMES: tuple[str, ...] = (
    "pelvis", "left_hip", "right_hip", "spine1", "left_knee", "right_knee",
    "spine2", "left_ankle", "right_ankle", "spine3", "left_foot", "right_foot",
    "neck", "left_collar", "right_collar", "head", "left_shoulder", "right_shoulder",
    "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hand", "right_hand",
)  # fmt: skip
JOINT = {name: index for index, name in enumerate(JOINT_NAMES)}


class _ChumpyStub:
    """pickle 안의 chumpy 객체 자리. 상태(dict)만 받아 두고 배열은 'x'에서 꺼낸다."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.state: Any = None

    def __setstate__(self, state: Any) -> None:
        self.state = state


_ALLOWED_PREFIXES = (
    "numpy",
    "scipy.sparse",
    "copy_reg",
    "copyreg",
    "__builtin__",
    "builtins",
)
_ALLOWED_BUILTINS = {
    "object",
    "dict",
    "list",
    "tuple",
    "set",
    "frozenset",
    "slice",
    "complex",
    "bytearray",
}


class _SafeUnpickler(pickle.Unpickler):
    def find_class(self, module: str, name: str) -> Any:
        if module.startswith("chumpy"):
            return _ChumpyStub
        if module in ("__builtin__", "builtins"):
            if name in _ALLOWED_BUILTINS:
                return super().find_class("builtins", name)
            raise pickle.UnpicklingError(f"허용하지 않는 내장 이름: {name}")
        if module == "_codecs" and name == "encode":  # protocol 2에서 bytes를 담는 방식
            return super().find_class(module, name)
        if module == "copy_reg" or module == "copyreg":
            if name == "_reconstructor":
                return super().find_class("copyreg", name)
            raise pickle.UnpicklingError(f"허용하지 않는 이름: {module}.{name}")
        if module.startswith("scipy.sparse"):
            # 공식 pkl은 scipy.sparse.csc 같은 옛 경로를 가리킨다. 이 경로는 SciPy 2.0에서
            # 없어지므로 같은 이름의 클래스를 scipy.sparse에서 바로 가져온다.
            import scipy.sparse

            if hasattr(scipy.sparse, name):
                return getattr(scipy.sparse, name)
        if module.startswith(_ALLOWED_PREFIXES):
            return super().find_class(module, name)
        raise pickle.UnpicklingError(f"허용하지 않는 클래스: {module}.{name}")


def _to_array(value: Any, dtype: type = np.float64) -> np.ndarray:
    if isinstance(value, _ChumpyStub):
        state = value.state
        if isinstance(state, dict) and "x" in state:
            return np.asarray(state["x"], dtype=dtype)
        raise ValueError("chumpy 객체에서 배열을 찾지 못했습니다. 정리된(.npz) 모델 파일을 쓰세요.")
    if hasattr(value, "toarray"):  # scipy.sparse
        return np.asarray(value.toarray(), dtype=dtype)
    return np.asarray(value, dtype=dtype)


def load_model_data(path: Path) -> dict[str, Any]:
    """.pkl(공식 파일) 또는 .npz에서 모델 배열을 읽는다."""
    path = Path(path)
    if path.suffix == ".npz":
        with np.load(path, allow_pickle=False) as data:
            return {key: data[key] for key in data.files}
    with path.open("rb") as file:
        return _SafeUnpickler(file, encoding="latin1").load()


def rodrigues(rotvecs: np.ndarray) -> np.ndarray:
    """축-각 벡터 (K, 3) → 회전 행렬 (K, 3, 3)."""
    rotvecs = np.asarray(rotvecs, dtype=np.float64).reshape(-1, 3)
    angles = np.linalg.norm(rotvecs, axis=1, keepdims=True)
    axes = np.divide(rotvecs, angles, out=np.zeros_like(rotvecs), where=angles > 1e-12)
    x, y, z = axes[:, 0], axes[:, 1], axes[:, 2]
    zeros = np.zeros_like(x)
    k = np.stack([zeros, -z, y, z, zeros, -x, -y, x, zeros], axis=1).reshape(-1, 3, 3)
    sin, cos = np.sin(angles)[:, :, None], np.cos(angles)[:, :, None]
    return np.eye(3)[None] + sin * k + (1 - cos) * (k @ k)


@dataclass(frozen=True)
class SmplOutput:
    vertices: np.ndarray  # (N, 3) 미터
    joints: np.ndarray  # (24, 3) 미터


@dataclass(frozen=True)
class SmplModel:
    v_template: np.ndarray  # (N, 3)
    shapedirs: np.ndarray  # (N, 3, B)
    posedirs: np.ndarray  # (N, 3, 207)
    j_regressor: np.ndarray  # (24, N)
    parents: np.ndarray  # (24,), 루트는 -1
    weights: np.ndarray  # (N, 24)
    faces: np.ndarray  # (F, 3)

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> SmplModel:
        kintree = np.asarray(data["kintree_table"]).astype(np.int64)
        parents = kintree[0].copy()
        parents[0] = -1
        return cls(
            v_template=_to_array(data["v_template"]),
            shapedirs=_to_array(data["shapedirs"]),
            posedirs=_to_array(data["posedirs"]),
            j_regressor=_to_array(data["J_regressor"]),
            parents=parents,
            weights=_to_array(data["weights"]),
            faces=_to_array(data["f"], dtype=np.int64),
        )

    @classmethod
    def load(cls, path: Path) -> SmplModel:
        return cls.from_data(load_model_data(path))

    @property
    def num_betas(self) -> int:
        return self.shapedirs.shape[2]

    def forward(
        self,
        betas: np.ndarray | None = None,
        body_pose: np.ndarray | None = None,
        global_orient: np.ndarray | None = None,
    ) -> SmplOutput:
        """체형(betas)과 자세(관절별 축-각)를 적용한 메시와 관절 위치."""
        num_joints = self.parents.shape[0]
        betas = np.zeros(self.num_betas) if betas is None else np.asarray(betas, dtype=np.float64)
        if betas.shape[0] > self.num_betas:
            raise ValueError(f"betas는 {self.num_betas}개까지입니다.")
        body_pose = (
            np.zeros((num_joints - 1, 3))
            if body_pose is None
            else np.asarray(body_pose).reshape(-1, 3)
        )
        global_orient = (
            np.zeros(3) if global_orient is None else np.asarray(global_orient).reshape(3)
        )

        # 1) 체형
        v_shaped = self.v_template + self.shapedirs[:, :, : betas.shape[0]] @ betas
        rest_joints = self.j_regressor @ v_shaped

        # 2) 자세에 따른 보정(pose blend shapes)
        rotations = rodrigues(np.vstack([global_orient[None], body_pose]))
        pose_feature = (rotations[1:] - np.eye(3)[None]).reshape(-1)
        v_posed = v_shaped + self.posedirs @ pose_feature

        # 3) 관절 변환을 부모부터 곱해 전역 변환을 만든다
        transforms = np.zeros((num_joints, 4, 4))
        for joint in range(num_joints):
            parent = self.parents[joint]
            offset = rest_joints[joint] - (rest_joints[parent] if parent >= 0 else 0.0)
            local = np.eye(4)
            local[:3, :3] = rotations[joint]
            local[:3, 3] = offset
            transforms[joint] = local if parent < 0 else transforms[parent] @ local
        posed_joints = transforms[:, :3, 3].copy()

        # 4) 쉬는 자세의 관절 위치를 빼서 정점에 적용할 변환으로 바꾼다
        skinning = transforms.copy()
        skinning[:, :3, 3] -= np.einsum("jab,jb->ja", transforms[:, :3, :3], rest_joints)

        # 5) 정점마다 관절 변환을 가중 평균해 적용(linear blend skinning)
        blended = np.einsum("nj,jab->nab", self.weights, skinning)
        vertices = np.einsum("nab,nb->na", blended[:, :3, :3], v_posed) + blended[:, :3, 3]
        return SmplOutput(vertices=vertices, joints=posed_joints)


def a_pose(angle_deg: float = 45.0) -> np.ndarray:
    """T자세에서 양팔을 아래로 angle_deg만큼 내린 A자세의 body_pose (23, 3).

    왼팔은 +x 방향이라 z축으로 음의 각, 오른팔은 양의 각을 준다.
    """
    pose = np.zeros((NUM_BODY_JOINTS, 3))
    angle = np.deg2rad(angle_deg)
    pose[JOINT["left_shoulder"] - 1] = (0.0, 0.0, -angle)
    pose[JOINT["right_shoulder"] - 1] = (0.0, 0.0, angle)
    return pose


def write_obj(path: Path, vertices: np.ndarray, faces: np.ndarray, comment: str = "") -> Path:
    """OBJ로 저장한다. 단위는 미터 그대로(SMPL·Blender 기본)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {line}" for line in comment.splitlines()] if comment else []
    lines += [f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in vertices]
    lines += [f"f {a + 1} {b + 1} {c + 1}" for a, b, c in faces]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
