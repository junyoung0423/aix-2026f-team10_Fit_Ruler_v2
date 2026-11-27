"""정면 카메라 프레이밍 계산 (C5). numpy만 쓰므로 Blender 안과 테스트 양쪽에서 쓴다.

좌표: 착장 장면과 같다(m, x 오른쪽·y 뒤·z 위, 몸 정면이 -y). 카메라는 -y 쪽에서 +y 방향을
수평으로 보고(회전 90°, 0°, 0°) 기울이지 않는다. 상하좌우 맞춤은 렌즈 시프트로 한다.

투영(카메라 위치 c, 깊이 d = y - c_y): a = (x - c_x) / d, b = (z - c_z) / d.
렌즈 f mm, 세로 센서 S mm(sensor_fit=VERTICAL)이면 화면 세로 반폭 tv = S / (2f), 가로 반폭
th = tv × 너비/높이. Blender 시프트는 센서 맞춤 방향(세로) 크기 단위라 화면 가운데는
(shift_x × 2tv, shift_y × 2tv)다.

규칙 (config/render/render_v1.json `framing`, 값은 [제안])
- 좌우: 옷 정점 ± side_margin이 모두 들어온다. 가운데는 그 범위의 가운데.
- 아래: 옷 정점 − bottom_margin이 들어온다.
- 위: min(옷 정점 + top_margin, 머리 정점 중 가장 낮게 투영되는 점 − head_clearance_px).
  머리 정점 = 몸 정점 중 z가 옷 맨 위 + head_min_above_collar보다 높은 것. 위 경계를 화면 맨
  위에 맞춘다(가로가 더 좁게 맞으면 아래로 더 보인다).
- 거리: 위 조건을 만족하는 가장 가까운 거리(이분 탐색).
- 구도를 잡는 프레임: 착장이 팔을 내렸으면(장면 값 `fit_arms_down`) A자세 착장 프레임
  (`fit_a_pose_frame`)에서 잡고, 그 카메라로 팔 내린 상태를 찍는다(`framing_frame`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np


@dataclass(frozen=True)
class Framing:
    camera_location_m: tuple[float, float, float]
    shift_x: float
    shift_y: float
    distance_m: float  # 옷 맨 앞(최소 y)에서 카메라까지
    top_limited_by: str  # "collar_margin" 또는 "head"
    tan_half_v: float
    tan_half_h: float
    n_head_vertices: int

    def to_dict(self) -> dict:
        d = asdict(self)
        d["camera_location_m"] = [round(v, 6) for v in self.camera_location_m]
        return d


def framing_frame(arms_down: object, a_pose_frame: object, current: int) -> int:
    """구도를 잡을 프레임. 착장이 팔을 내렸으면 A자세 착장 프레임, 아니면 지금 프레임.

    팔 내린 상태로 구도를 잡은 시험 렌더(q13)는 목둘레 위쪽이 화면 위에서 잘렸고, 실사 사진 모델이
    목둘레를 보이려고 약 10 % 멀리 찍은 사진을 만들었다. A자세 렌더는 목둘레가 들어왔다.
    """
    if arms_down and a_pose_frame is not None and int(a_pose_frame) != current:
        return int(a_pose_frame)
    return current


def head_vertices(body_co: np.ndarray, garment_top_z: float, min_above_m: float) -> np.ndarray:
    """몸 정점 중 옷 맨 위 + `min_above_m`보다 높은 것."""
    body_co = np.asarray(body_co, dtype=float)
    return body_co[body_co[:, 2] > garment_top_z + min_above_m]


def _bounds(garment: np.ndarray, head: np.ndarray, cam: np.ndarray, m: dict, clear_b: float):
    d = garment[:, 1] - cam[1]
    x, z = garment[:, 0] - cam[0], garment[:, 2] - cam[2]
    a_lo = float(np.min((x - m["side"]) / d))
    a_hi = float(np.max((x + m["side"]) / d))
    b_bot = float(np.min((z - m["bottom"]) / d))
    b_collar = float(np.max((z + m["top"]) / d))
    limited = "collar_margin"
    b_top = b_collar
    if len(head):
        dh = head[:, 1] - cam[1]
        b_head = float(np.min((head[:, 2] - cam[2]) / dh)) - clear_b
        if b_head < b_collar:
            b_top, limited = b_head, "head"
    return a_lo, a_hi, b_bot, b_top, limited


def solve_framing(
    garment_co: np.ndarray,
    body_co: np.ndarray,
    *,
    width: int,
    height: int,
    lens_mm: float,
    sensor_height_mm: float,
    top_margin_cm: float,
    bottom_margin_cm: float,
    side_margin_cm: float,
    head_min_above_collar_cm: float,
    head_clearance_px: float,
    distance_search_m: tuple[float, float] = (1.0, 20.0),
    iterations: int = 60,
) -> Framing:
    """옷·몸 정점(월드 좌표, m)으로 카메라 위치와 시프트를 정한다.

    Raises:
        ValueError: 옷 정점이 없거나, 탐색 범위 끝에서도 다 들어오지 않는다.
    """
    g = np.asarray(garment_co, dtype=float)
    if g.ndim != 2 or g.shape[0] == 0 or g.shape[1] != 3:
        raise ValueError("옷 정점이 없습니다")
    head = head_vertices(body_co, float(g[:, 2].max()), head_min_above_collar_cm / 100.0)
    tv = sensor_height_mm / (2.0 * lens_mm)
    th = tv * width / height
    clear_b = head_clearance_px * (2.0 * tv / height)
    margins = {
        "side": side_margin_cm / 100.0,
        "bottom": bottom_margin_cm / 100.0,
        "top": top_margin_cm / 100.0,
    }
    cx = float((g[:, 0].min() + g[:, 0].max()) / 2)
    cz = float((g[:, 2].min() + g[:, 2].max()) / 2)
    front_y = float(g[:, 1].min())

    def cam_at(dist: float) -> np.ndarray:
        return np.array([cx, front_y - dist, cz])

    def fits(dist: float) -> bool:
        a_lo, a_hi, b_bot, b_top, _ = _bounds(g, head, cam_at(dist), margins, clear_b)
        return (a_hi - a_lo) <= 2 * th and b_top > b_bot and (b_top - b_bot) <= 2 * tv

    lo, hi = distance_search_m
    if not fits(hi):
        raise ValueError(f"거리 {hi} m에서도 옷이 화면에 다 들어오지 않습니다")
    for _ in range(iterations):
        mid = (lo + hi) / 2
        if fits(mid):
            hi = mid
        else:
            lo = mid
    cam = cam_at(hi)
    a_lo, a_hi, _, b_top, limited = _bounds(g, head, cam, margins, clear_b)
    return Framing(
        camera_location_m=(float(cam[0]), float(cam[1]), float(cam[2])),
        shift_x=((a_lo + a_hi) / 2) / (2 * tv),
        shift_y=(b_top - tv) / (2 * tv),
        distance_m=hi,
        top_limited_by=limited,
        tan_half_v=tv,
        tan_half_h=th,
        n_head_vertices=int(len(head)),
    )


def project(points: np.ndarray, framing: Framing) -> np.ndarray:
    """정점 → 화면 정규 좌표 (u 왼쪽 0·오른쪽 1, v 아래 0·위 1, 깊이 m).

    Blender `world_to_camera_view`와 같은 뜻이다(어댑터 밖 검사·테스트용).
    """
    p = np.asarray(points, dtype=float)
    cam = np.asarray(framing.camera_location_m)
    d = p[:, 1] - cam[1]
    a = (p[:, 0] - cam[0]) / d
    b = (p[:, 2] - cam[2]) / d
    tv, th = framing.tan_half_v, framing.tan_half_h
    u = (a - framing.shift_x * 2 * tv + th) / (2 * th)
    v = (b - framing.shift_y * 2 * tv + tv) / (2 * tv)
    return np.stack([u, v, d], axis=1)


def count_in_frame(uvd: np.ndarray) -> int:
    uvd = np.asarray(uvd)
    if len(uvd) == 0:
        return 0
    inside = (uvd[:, 0] >= 0) & (uvd[:, 0] <= 1) & (uvd[:, 1] >= 0) & (uvd[:, 1] <= 1)
    return int(np.count_nonzero(inside & (uvd[:, 2] > 0)))


__all__ = ["Framing", "count_in_frame", "head_vertices", "project", "solve_framing"]
