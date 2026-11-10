"""몸 메시에서 신체 치수 5개를 잰다 (numpy만 사용, 외부 SDK·어댑터를 import하지 않는다).

입력은 SMPL 24관절 순서를 따르는 메시·관절·스키닝 가중치이고, 좌표는 y가 위, 단위는 미터다.
측정 위치는 입력 화면의 '재는 법'(A2)에 맞춘 임시 정의이며, 팀 공통 기준(#1)이 정해지면 고친다.

| 치수 | 정의 |
|---|---|
| 키 | 메시의 가장 높은 점과 가장 낮은 점의 높이 차 |
| 가슴둘레 | spine3 관절 높이부터 어깨 관절 4cm 아래까지 수평으로 잘라, 몸통 단면의 볼록 둘레가 가장 큰 값 (겨드랑이 아래 가장 두꺼운 곳) |
| 허리둘레 | spine1 관절 높이(배꼽 높이 근사)에서 몸통 단면의 볼록 둘레 |
| 어깨너비 | 좌우 어깨 끝점 사이 직선거리. 어깨 끝점은 SMPL 정점 3011(왼쪽)·6470(오른쪽) |
| 팔길이 | 왼쪽 어깨 끝점 → 팔꿈치 관절 → 손목 관절 거리의 합 |

둘레는 줄자가 오목한 곳을 건너뛰므로 단면의 볼록 껍질 둘레로 잰다.
팔 정점(스키닝 가중치가 팔 관절에 가장 큰 정점)은 몸통 단면에서 뺀다.
어깨 끝점 정점은 실제 SMPL 모델에서 확인했다: 어깨 관절 바로 위 약 5cm, 좌우가 거울 대칭(6mm 이내),
가중치가 쇄골 관절에 가장 커서 팔 자세가 바뀌어도 거의 움직이지 않는다. 다른 메시를 쓰면 정점 번호를 넘긴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# SMPL 24관절 번호 (fitting/adapters/smpl와 같은 순서; 로직이 어댑터를 import하지 않도록 여기 따로 적는다)
PELVIS, SPINE1, SPINE3 = 0, 3, 9
LEFT_SHOULDER, RIGHT_SHOULDER = 16, 17
LEFT_ELBOW, RIGHT_ELBOW = 18, 19
LEFT_WRIST, RIGHT_WRIST = 20, 21
ARM_JOINTS = frozenset({16, 17, 18, 19, 20, 21, 22, 23})

CHEST_TOP_MARGIN_M = (
    0.04  # 어깨 관절 높이에서 이만큼 아래까지만 가슴 후보로 본다(겨드랑이 아래)
)
CHEST_SCAN_STEPS = 15

# (왼쪽, 오른쪽) 어깨 끝점 정점 번호. SMPL 정점 6890개 기준이며 2026-11-10에 실제 모델로 위치를 확인했다.
SMPL_ACROMION_VERTICES = (3011, 6470)


@dataclass(frozen=True)
class BodyMeasurementsCm:
    height_cm: float
    chest_circumference_cm: float
    waist_circumference_cm: float
    shoulder_width_cm: float
    arm_length_cm: float

    def to_dict(self) -> dict[str, float]:
        return {
            "heightCm": self.height_cm,
            "chestCircumferenceCm": self.chest_circumference_cm,
            "waistCircumferenceCm": self.waist_circumference_cm,
            "shoulderWidthCm": self.shoulder_width_cm,
            "armLengthCm": self.arm_length_cm,
        }


@dataclass(frozen=True)
class MeasureDebug:
    """측정 위치 확인용 정보(디버그 그림·기록에 쓴다)."""

    chest_level_m: float
    waist_level_m: float
    left_shoulder_point: np.ndarray
    right_shoulder_point: np.ndarray
    chest_loop: np.ndarray = field(repr=False)  # (K, 2) x, z
    waist_loop: np.ndarray = field(repr=False)


def convex_hull(points: np.ndarray) -> np.ndarray:
    """2차원 점들의 볼록 껍질 꼭짓점 (반시계 방향, monotone chain)."""
    pts = np.unique(np.asarray(points, dtype=np.float64), axis=0)
    if len(pts) <= 2:
        return pts
    pts = pts[np.lexsort((pts[:, 1], pts[:, 0]))]

    def cross(o: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
        return float((a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]))

    lower: list[np.ndarray] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[np.ndarray] = []
    for p in pts[::-1]:
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return np.array(lower[:-1] + upper[:-1])


def polygon_perimeter(vertices: np.ndarray) -> float:
    if len(vertices) < 2:
        return 0.0
    return float(np.linalg.norm(np.roll(vertices, -1, axis=0) - vertices, axis=1).sum())


def convex_perimeter(points: np.ndarray) -> float:
    """줄자처럼 오목한 곳을 건너뛴 둘레."""
    return polygon_perimeter(convex_hull(points))


def hull_contains(hull: np.ndarray, point: np.ndarray) -> bool:
    if len(hull) < 3:
        return False
    edges = np.roll(hull, -1, axis=0) - hull
    to_point = point - hull
    return bool(
        np.all(edges[:, 0] * to_point[:, 1] - edges[:, 1] * to_point[:, 0] >= -1e-12)
    )


def slice_loops(
    vertices: np.ndarray, faces: np.ndarray, level: float
) -> list[np.ndarray]:
    """높이 level의 수평면으로 메시를 잘라, 이어진 단면 조각마다 (K, 2) x·z 점들을 돌려준다."""
    y = vertices[:, 1] - level
    # 꼭짓점이 평면에 딱 걸리면 위쪽으로 아주 조금 민다(교차 판정을 단순하게 하려고)
    y = np.where(np.abs(y) < 1e-12, 1e-12, y)
    above = y > 0
    face_above = above[faces]
    crossing = faces[(face_above.sum(axis=1) == 1) | (face_above.sum(axis=1) == 2)]
    if len(crossing) == 0:
        return []

    edge_ids: dict[tuple[int, int], int] = {}
    points: list[np.ndarray] = []
    segments: list[tuple[int, int]] = []
    for tri in crossing:
        hits = []
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            if above[a] == above[b]:
                continue
            key = (min(a, b), max(a, b))
            if key not in edge_ids:
                t = y[a] / (y[a] - y[b])
                p = vertices[a] + t * (vertices[b] - vertices[a])
                edge_ids[key] = len(points)
                points.append(p[[0, 2]])
            hits.append(edge_ids[key])
        if len(hits) == 2:
            segments.append((hits[0], hits[1]))

    parent = list(range(len(points)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in segments:
        parent[find(a)] = find(b)
    groups: dict[int, list[int]] = {}
    for i in range(len(points)):
        groups.setdefault(find(i), []).append(i)
    all_points = np.array(points)
    return [all_points[idx] for idx in groups.values()]


def torso_section(
    vertices: np.ndarray, faces: np.ndarray, level: float, center_xz: np.ndarray
) -> np.ndarray:
    """높이 level에서 몸통 단면 점들. 중심점을 감싸는 조각을 고르고, 없으면 가장 큰 조각."""
    loops = slice_loops(vertices, faces, level)
    if not loops:
        raise ValueError(f"높이 {level:.3f}m에서 메시 단면이 없습니다.")
    for loop in loops:
        if hull_contains(convex_hull(loop), center_xz):
            return loop
    return max(loops, key=len)


def torso_faces(faces: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """세 꼭짓점 모두 팔이 아닌(가중치가 가장 큰 관절이 팔 관절이 아닌) 면만 남긴다."""
    dominant = np.argmax(weights, axis=1)
    is_torso = ~np.isin(dominant, list(ARM_JOINTS))
    return faces[is_torso[faces].all(axis=1)]


def measure_body(
    vertices: np.ndarray,
    faces: np.ndarray,
    joints: np.ndarray,
    weights: np.ndarray,
    acromion_vertices: tuple[int, int] = SMPL_ACROMION_VERTICES,
) -> tuple[BodyMeasurementsCm, MeasureDebug]:
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    joints = np.asarray(joints, dtype=np.float64)
    body_faces = torso_faces(faces, np.asarray(weights))

    height = float(vertices[:, 1].max() - vertices[:, 1].min())

    center = joints[SPINE3][[0, 2]]
    shoulder_y = float((joints[LEFT_SHOULDER][1] + joints[RIGHT_SHOULDER][1]) / 2)
    chest_levels = np.linspace(
        joints[SPINE3][1], shoulder_y - CHEST_TOP_MARGIN_M, CHEST_SCAN_STEPS
    )
    chest_candidates = []
    for level in chest_levels:
        loop = torso_section(vertices, body_faces, float(level), center)
        chest_candidates.append((convex_perimeter(loop), float(level), loop))
    chest, chest_level, chest_loop = max(chest_candidates, key=lambda item: item[0])

    waist_level = float(joints[SPINE1][1])
    waist_loop = torso_section(
        vertices, body_faces, waist_level, joints[SPINE1][[0, 2]]
    )
    waist = convex_perimeter(waist_loop)

    left, right = vertices[acromion_vertices[0]], vertices[acromion_vertices[1]]
    if not left[0] > right[0]:
        raise ValueError(
            "어깨 끝점 정점 번호를 확인하세요(왼쪽 정점이 +x 쪽에 있어야 합니다)."
        )
    shoulder_width = float(np.linalg.norm(left - right))

    arm = float(
        np.linalg.norm(left - joints[LEFT_ELBOW])
        + np.linalg.norm(joints[LEFT_ELBOW] - joints[LEFT_WRIST])
    )

    def cm(meters: float) -> float:
        return round(meters * 100, 1)

    measurements = BodyMeasurementsCm(
        height_cm=cm(height),
        chest_circumference_cm=cm(chest),
        waist_circumference_cm=cm(waist),
        shoulder_width_cm=cm(shoulder_width),
        arm_length_cm=cm(arm),
    )
    debug = MeasureDebug(
        chest_level_m=chest_level,
        waist_level_m=waist_level,
        left_shoulder_point=left,
        right_shoulder_point=right,
        chest_loop=chest_loop,
        waist_loop=waist_loop,
    )
    return measurements, debug
