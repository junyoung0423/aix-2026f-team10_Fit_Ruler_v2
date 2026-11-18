"""메시 기하: OBJ 읽기, 닫힘 검사, 점 → 몸 표면 부호 거리 (D4 착장 점검, numpy·scipy).

Blender 없이 같은 메시에서 관통을 잰다. C2는 부호 거리를 Blender BVH로 구했다
(`fitting/adapters/blender/blender_drape.py`). 이 모듈은 Blender가 없는 PC·CI에서도 돈다.

부호 거리 방법
1. 몸 정점으로 kd-tree를 만들고, 점마다 가장 가까운 정점 `k`개를 찾는다.
2. 그 정점들에 붙은 삼각형 위의 가장 가까운 점을 정확히 계산해(Ericson 방법) 가장 가까운 것을
   고른다.
3. 부호는 가장 가까운 점에서 정점 법선(넓이 가중)을 무게중심 좌표로 보간한 방향으로 정한다(바깥 +).

몸 삼각형이 작고(SMPL 1~2 cm) 옷이 몸 가까이 있다는 가정의 근사다. 삼각형보다 훨씬 먼 점은 가장
가까운 삼각형을 놓칠 수 있지만, 관통 판정(몸 안 3 mm)에는 영향이 없다.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

_CHUNK = 4096


def read_obj(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """OBJ의 정점(v)과 면(f)을 읽는다. 다각형 면은 부채꼴로 삼각형으로 나눈다.

    `f 1/2/3 …` 같은 텍스처·법선 번호와 음수(뒤에서부터) 번호를 받는다.

    Raises:
        ValueError: 정점이나 면이 없거나 번호가 범위를 벗어난다.
    """
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    with Path(path).open(encoding="utf-8", errors="replace") as file:
        for line in file:
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "v" and len(parts) >= 4:
                vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
            elif parts[0] == "f" and len(parts) >= 4:
                index = []
                for token in parts[1:]:
                    number = int(token.split("/")[0])
                    index.append(number - 1 if number > 0 else len(vertices) + number)
                faces.extend((index[0], index[i], index[i + 1]) for i in range(1, len(index) - 1))
    if not vertices or not faces:
        raise ValueError(f"정점이나 면이 없는 OBJ입니다: {path}")
    v = np.asarray(vertices, dtype=np.float64)
    f = np.asarray(faces, dtype=np.int64)
    if f.min() < 0 or f.max() >= len(v):
        raise ValueError(f"면의 정점 번호가 범위를 벗어났습니다: {path}")
    return v, f


def is_closed(faces: np.ndarray) -> bool:
    """닫힌(구멍 없는) 메시인지. 모든 변이 정확히 두 면에 쓰이면 닫혀 있다."""
    faces = np.asarray(faces, dtype=np.int64)
    if faces.size == 0:
        return False
    edges = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    _, counts = np.unique(np.sort(edges, axis=1), axis=0, return_counts=True)
    return bool(np.all(counts == 2))


def triangle_areas(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    a, b, c = (vertices[faces[:, i]] for i in range(3))
    return 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)


def face_centers(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    return vertices[faces].mean(axis=1)


def vertex_normals(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """넓이 가중 정점 법선(단위 벡터). 면 방향(바깥)은 OBJ의 감는 방향을 따른다."""
    a, b, c = (vertices[faces[:, i]] for i in range(3))
    face_normal = np.cross(b - a, c - a)  # 길이가 넓이의 2배라 넓이 가중이 된다
    normals = np.zeros_like(vertices)
    for i in range(3):
        np.add.at(normals, faces[:, i], face_normal)
    length = np.linalg.norm(normals, axis=1, keepdims=True)
    return np.divide(normals, length, out=np.zeros_like(normals), where=length > 0)


def closest_points_on_triangles(
    p: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """점 p(…, 3)에서 삼각형 abc 위의 가장 가까운 점과 그 무게중심 좌표(…, 3).

    Ericson, Real-Time Collision Detection 5.1.5를 배열로 옮겼다.
    """
    ab, ac, ap = b - a, c - a, p - a
    d1 = np.einsum("...i,...i", ab, ap)
    d2 = np.einsum("...i,...i", ac, ap)
    bp = p - b
    d3 = np.einsum("...i,...i", ab, bp)
    d4 = np.einsum("...i,...i", ac, bp)
    cp = p - c
    d5 = np.einsum("...i,...i", ab, cp)
    d6 = np.einsum("...i,...i", ac, cp)

    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2
    denom = va + vb + vc
    safe = np.where(np.abs(denom) > 1e-30, denom, 1.0)
    v = vb / safe
    w = vc / safe
    u = 1.0 - v - w  # 기본: 면 안쪽

    def assign(mask, uu, vv, ww):
        nonlocal u, v, w
        u = np.where(mask, uu, u)
        v = np.where(mask, vv, v)
        w = np.where(mask, ww, w)

    def ratio(num, den):
        return np.divide(num, den, out=np.zeros_like(num), where=np.abs(den) > 1e-30)

    zero, one = np.zeros_like(d1), np.ones_like(d1)
    # 원래 알고리즘은 a → b → ab → c → ac → bc → 면 안쪽 순서로 먼저 맞는 것을 고른다.
    # 배열로는 거꾸로(우선순위가 낮은 것부터) 덮어써서 같은 결과를 낸다.
    t_bc = ratio(d4 - d3, (d4 - d3) + (d5 - d6))
    assign((va <= 0) & (d4 - d3 >= 0) & (d5 - d6 >= 0), zero, 1 - t_bc, t_bc)  # 변 bc
    t_ac = ratio(d2, d2 - d6)
    assign((vb <= 0) & (d2 >= 0) & (d6 <= 0), 1 - t_ac, zero, t_ac)  # 변 ac
    assign((d6 >= 0) & (d5 <= d6), zero, zero, one)  # 꼭짓점 c
    t_ab = ratio(d1, d1 - d3)
    assign((vc <= 0) & (d1 >= 0) & (d3 <= 0), 1 - t_ab, t_ab, zero)  # 변 ab
    assign((d3 >= 0) & (d4 <= d3), zero, one, zero)  # 꼭짓점 b
    assign((d1 <= 0) & (d2 <= 0), one, zero, zero)  # 꼭짓점 a

    bary = np.stack([u, v, w], axis=-1)
    point = bary[..., :1] * a + bary[..., 1:2] * b + bary[..., 2:] * c
    return point, bary


def _vertex_faces(faces: np.ndarray, vertex_count: int) -> np.ndarray:
    """정점별로 붙은 면 번호 (V, 최대 차수), 빈 자리는 -1."""
    order = np.argsort(faces.reshape(-1), kind="stable")
    vertex_of = faces.reshape(-1)[order]
    face_of = order // 3
    counts = np.bincount(vertex_of, minlength=vertex_count)
    table = np.full((vertex_count, max(int(counts.max()), 1)), -1, dtype=np.int64)
    starts = np.r_[0, np.cumsum(counts)[:-1]]
    slot = np.arange(len(vertex_of)) - starts[vertex_of]
    table[vertex_of, slot] = face_of
    return table


def signed_distance(
    points: np.ndarray, vertices: np.ndarray, faces: np.ndarray, *, k: int = 8
) -> tuple[np.ndarray, np.ndarray]:
    """점들에서 메시 표면까지의 부호 거리(바깥 +, 미터)와 가장 가까운 표면 점.

    유한하지 않은 점은 거리 NaN을 돌려준다.
    """
    points = np.asarray(points, dtype=np.float64)
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    result = np.full(len(points), np.nan)
    closest = np.full_like(points, np.nan)
    finite = np.all(np.isfinite(points), axis=1)
    if not finite.any():
        return result, closest

    tree = cKDTree(vertices)
    table = _vertex_faces(faces, len(vertices))
    normals = vertex_normals(vertices, faces)
    k = min(k, len(vertices))
    index = np.flatnonzero(finite)
    for start in range(0, len(index), _CHUNK):
        rows = index[start : start + _CHUNK]
        p = points[rows]
        _, near = tree.query(p, k=k)
        near = np.asarray(near).reshape(len(rows), k)
        candidates = table[near].reshape(len(rows), -1)  # (n, k × 최대 차수), -1은 빈 자리
        valid = candidates >= 0
        safe = np.where(valid, candidates, 0)
        tri = faces[safe]  # (n, m, 3)
        a, b, c = vertices[tri[..., 0]], vertices[tri[..., 1]], vertices[tri[..., 2]]
        q, bary = closest_points_on_triangles(p[:, None, :], a, b, c)
        dist2 = np.einsum("nmi,nmi->nm", q - p[:, None, :], q - p[:, None, :])
        dist2 = np.where(valid, dist2, np.inf)
        best = np.argmin(dist2, axis=1)
        pick = np.arange(len(rows))
        point = q[pick, best]
        weight = bary[pick, best]
        corner = tri[pick, best]
        normal = np.einsum("ni,nij->nj", weight, normals[corner])
        offset = p - point
        distance = np.sqrt(dist2[pick, best])
        sign = np.where(np.einsum("ni,ni->n", offset, normal) < 0, -1.0, 1.0)
        result[rows] = sign * distance
        closest[rows] = point
    return result, closest
