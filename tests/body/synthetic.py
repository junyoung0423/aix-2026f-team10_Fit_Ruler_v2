"""측정 테스트용 합성 몸: 원기둥으로 만든 몸통·다리·머리·팔과 SMPL 24관절 배치."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SEGMENTS = 64


def tube(
    start: np.ndarray,
    direction: np.ndarray,
    length: float,
    radius: float,
    rings: int = 6,
) -> tuple[np.ndarray, np.ndarray]:
    """start에서 direction으로 length만큼 뻗은 옆면만 있는 원기둥 (정점, 면)."""
    d = np.asarray(direction, dtype=float)
    d /= np.linalg.norm(d)
    helper = np.array([0.0, 0.0, 1.0]) if abs(d[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = np.cross(d, helper)
    u /= np.linalg.norm(u)
    w = np.cross(d, u)
    # 세로 원기둥이면 u·w를 x·z로 맞춘다(둘레 계산을 정확히 하려고)
    if abs(d[1]) > 0.999:
        u, w = np.array([1.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0])
    angles = np.linspace(0, 2 * np.pi, SEGMENTS, endpoint=False)
    verts = []
    for t in np.linspace(0, length, rings + 1):
        center = np.asarray(start, dtype=float) + t * d
        verts.extend(center + radius * (np.cos(a) * u + np.sin(a) * w) for a in angles)
    faces = []
    for r in range(rings):
        for s in range(SEGMENTS):
            a, b = r * SEGMENTS + s, r * SEGMENTS + (s + 1) % SEGMENTS
            c, e = a + SEGMENTS, b + SEGMENTS
            faces += [(a, b, e), (a, e, c)]
    return np.array(verts), np.array(faces)


def ngon_perimeter(radius: float) -> float:
    return 2 * SEGMENTS * radius * np.sin(np.pi / SEGMENTS)


@dataclass
class SyntheticBody:
    vertices: np.ndarray
    faces: np.ndarray
    joints: np.ndarray
    weights: np.ndarray
    arm_dir_left: np.ndarray
    acromion_vertices: tuple[int, int]  # (왼쪽, 오른쪽) 어깨 끝점 정점 번호


ARM_ANGLE = np.deg2rad(45)
ARM_RADIUS = 0.04
UPPER_ARM = 0.30
FOREARM = 0.27
WAIST_RADIUS = 0.13
CHEST_RADIUS = 0.15
# 어깨 끝점: 어깨 관절 위쪽·바깥쪽에 둔 점 (면에는 쓰지 않는다)
LEFT_ACROMION = np.array([0.21, 1.46, 0.0])
RIGHT_ACROMION = np.array([-0.21, 1.46, 0.0])


# belly_radius를 주면 아랫몸통을 나눈다: 0.90~BELLY_TOP은 배(belly_radius), BELLY_TOP~1.15는 허리
BELLY_TOP = 1.08


def make_body(belly_radius: float | None = None) -> SyntheticBody:
    parts: list[tuple[np.ndarray, np.ndarray, int]] = []  # (정점, 면, 가중치 관절)
    up = np.array([0.0, 1.0, 0.0])
    parts.append((*tube([0.08, 0.0, 0.0], up, 0.9, 0.06), 1))  # 왼다리
    parts.append((*tube([-0.08, 0.0, 0.0], up, 0.9, 0.06), 2))  # 오른다리
    if belly_radius is None:
        parts.append((*tube([0.0, 0.9, 0.0], up, 0.25, WAIST_RADIUS), 3))  # 아랫몸통 0.90~1.15
    else:
        # 배꼽 높이(spine1 1.05)가 배에 걸리고, 가장 잘록한 곳은 그 위(1.08~1.15)에 있다
        parts.append((*tube([0.0, 0.9, 0.0], up, BELLY_TOP - 0.9, belly_radius), 3))
        parts.append((*tube([0.0, BELLY_TOP, 0.0], up, 1.15 - BELLY_TOP, WAIST_RADIUS), 6))
    parts.append((*tube([0.0, 1.15, 0.0], up, 0.30, CHEST_RADIUS), 9))  # 윗몸통 1.15~1.45
    parts.append((*tube([0.0, 1.5, 0.0], up, 0.2, 0.09), 15))  # 머리 1.50~1.70

    left_dir = np.array([np.cos(ARM_ANGLE), -np.sin(ARM_ANGLE), 0.0])
    right_dir = np.array([-np.cos(ARM_ANGLE), -np.sin(ARM_ANGLE), 0.0])
    left_shoulder = np.array([0.2, 1.42, 0.0])
    right_shoulder = np.array([-0.2, 1.42, 0.0])
    parts.append((*tube(left_shoulder, left_dir, UPPER_ARM, ARM_RADIUS), 16))
    parts.append((*tube(left_shoulder + UPPER_ARM * left_dir, left_dir, FOREARM, ARM_RADIUS), 18))
    parts.append((*tube(right_shoulder, right_dir, UPPER_ARM, ARM_RADIUS), 17))
    parts.append(
        (
            *tube(right_shoulder + UPPER_ARM * right_dir, right_dir, FOREARM, ARM_RADIUS),
            19,
        )
    )

    vertices, faces, weights = [], [], []
    offset = 0
    for verts, tris, joint in parts:
        vertices.append(verts)
        faces.append(tris + offset)
        w = np.zeros((len(verts), 24))
        w[:, joint] = 1.0
        weights.append(w)
        offset += len(verts)

    # 어깨 끝점 두 개를 끝에 붙인다. 가중치는 쇄골(13·14)이라 팔 정점으로 빠지지 않는다.
    acromion_start = offset
    vertices.append(np.vstack([LEFT_ACROMION, RIGHT_ACROMION]))
    w = np.zeros((2, 24))
    w[0, 13], w[1, 14] = 1.0, 1.0
    weights.append(w)

    joints = np.zeros((24, 3))
    joints[0] = (0.0, 0.95, 0.0)  # pelvis
    joints[3] = (0.0, 1.05, 0.0)  # spine1 → 허리 높이
    joints[9] = (0.0, 1.25, 0.0)  # spine3 → 가슴 탐색 시작
    joints[16], joints[17] = left_shoulder, right_shoulder
    joints[18] = left_shoulder + UPPER_ARM * left_dir
    joints[19] = right_shoulder + UPPER_ARM * right_dir
    joints[20] = joints[18] + FOREARM * left_dir
    joints[21] = joints[19] + FOREARM * right_dir
    return SyntheticBody(
        np.vstack(vertices),
        np.vstack(faces),
        joints,
        np.vstack(weights),
        left_dir,
        (acromion_start, acromion_start + 1),
    )
