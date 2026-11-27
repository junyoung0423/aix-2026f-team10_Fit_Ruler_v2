"""옷 패널의 첫 위치를 착장할 몸에 맞춘다 (#6 #7, 순수 numpy, Blender·GarmentCode 불필요).

GarmentCode는 패턴을 만든 몸(`mean_all`, R06)에 맞춰 패널을 몸 둘레에 놓는다. 다른 몸(D3 SMPL)에
그대로 입히면 어깨 높이가 달라 어깨 솔기가 몸 안을 지나고 봉제가 닫히지 않는다(기준 신체 1은 어깨
끝이 `mean_all`보다 약 5 cm 높아 늘어남 4.46배·봉제 틈 24 mm로 실패했다).

그래서 패널 모양(의류 실측)은 그대로 두고(R06) 패널마다 평행 이동만 한다.
- 모든 패널: 두 몸의 어깨 끝 높이 차(좌우 중 큰 쪽)에 봉제 여유 1 cm를 더한 만큼 위아래로.
  패턴을 만든 몸과 같은 몸이면 옮기지 않는다(여유도 없음).
  기준 신체 1(어깨 끝 차 좌 5.1·우 4.6 cm)로 정했다. 4.85 cm(좌우 평균): 봉제 틈 10.2 mm(기준
  10 mm) 실패, 5.1 cm: 늘어남 1.62(기준 1.5) 실패, 5.85 cm: 통과(1.47·7.3 mm), 6.85 cm: 통과
  (1.46·7.7 mm). 실패는 모두 어깨 끝 근처였다. SMPL 어깨는 `mean_all`보다 평평해 봉제하는 동안
  솔기가 어깨에 걸리는 것으로 보인다
- 소매 패널(이름에 `sleeve`): 그쪽 어깨 끝의 옆 위치 차만큼 옆으로

어깨 끝은 두 몸에서 같은 방법으로 찾는다. 키의 70 % 위·88 % 아래, 몸 중심에서 10 cm 밖(목 제외)의
정점 가운데 `z + 0.65·|x|`가 가장 큰 점이다. 기울기 0.65(약 33°)는 어깨선(약 8~21°)과 A자세 팔
(약 45°) 사이라, 이 받침선은 어깨에서 팔로 꺾이는 곳에 닿는다(GarmentCode `mean_all`은
`arm_pose_angle` 45.5°, D3 몸은 A자세 45°).

88 % 위 경계는 머리를 뺀다(v2). 머리가 옆으로 10 cm를 넘는 몸이 있다. 어깨 44·팔 60 cm 몸(키 175,
가슴 100, 허리 80)은 머리가 10.0 cm까지 나와, v1이 키의 93 % 높이 머리 옆을 왼쪽 어깨 끝으로 잡고
옮길 거리 21.3 cm로 멈췄다. 실제 몸 4개(신체 1·2, 이 몸, `mean_all`)에서 옆 10 cm 밖 정점은 어깨
쪽이 모두 키의 86 % 아래, 머리 쪽이 90 % 위라, 그 사이 88 %를 경계로 둔다.

좌표는 명세서 규약(m, Z 위, 정면 -Y, X는 몸의 왼쪽이 +)이다(`coords.py`).
이 파일은 GarmentCode용 Python 3.9에서도 돈다(`export_panels.py`가 부른다).
"""

from __future__ import annotations

import numpy as np

METHOD = "shoulder_tips_v2"  # v1은 위 경계가 없어 머리가 넓은 몸에서 머리 옆을 잡았다
SLOPE = 0.65  # 어깨선과 팔 사이 기울기의 받침선(약 33°)
MIN_LATERAL_M = 0.10  # 목을 빼는 몸 중심에서의 옆 거리(머리는 MAX_HEIGHT_FRAC로 뺀다)
MIN_HEIGHT_FRAC = 0.70  # 키의 이 비율 위만 본다(손·다리 제외)
MAX_HEIGHT_FRAC = 0.88  # 키의 이 비율 아래만 본다(머리 제외. 어깨는 86 % 아래, 머리는 90 % 위)
MAX_TIP_LATERAL_M = 0.35  # 어깨 끝이 이보다 바깥이면 A자세 몸이 아닌 것으로 본다(T자세 등)
MAX_SHIFT_M = 0.20  # 이보다 크게 옮겨야 하면 몸이 이상한 것으로 보고 멈춘다
SEW_CLEARANCE_M = 0.01  # 옮길 때 더 올려 두는 봉제 여유(위 기준 신체 1 시험)
SAME_BODY_M = 0.001  # 어깨 끝이 이보다 덜 다르면 같은 몸으로 보고 옮기지 않는다


def shoulder_tip(vertices, side: int) -> np.ndarray:
    """어깨 끝점(3,). side=+1은 몸의 왼쪽(+X), -1은 오른쪽.

    Raises:
        ValueError: 어깨 쪽 정점이 없거나 어깨 끝이 너무 바깥이다(A자세 몸이 아님).
    """
    v = np.asarray(vertices, dtype=float)
    z_min, z_max = float(v[:, 2].min()), float(v[:, 2].max())
    cutoff = z_min + MIN_HEIGHT_FRAC * (z_max - z_min)
    top = z_min + MAX_HEIGHT_FRAC * (z_max - z_min)
    cand = v[(side * v[:, 0] > MIN_LATERAL_M) & (v[:, 2] > cutoff) & (v[:, 2] < top)]
    if len(cand) == 0:
        raise ValueError(f"어깨 쪽 정점이 없습니다(side={side})")
    tip = cand[int(np.argmax(cand[:, 2] + SLOPE * side * cand[:, 0]))]
    if side * tip[0] > MAX_TIP_LATERAL_M:
        raise ValueError(f"어깨 끝이 몸 중심에서 {side * tip[0]:.2f} m라 A자세 몸이 아닙니다")
    return tip


def _round(values, digits: int = 4) -> list[float]:
    return [round(float(x), digits) for x in values]


def placement_offsets(garment_vertices, panel_of_vertex, panel_names, ref_body, target_body):
    """패널별 평행 이동(m)과 기록.

    Args:
        garment_vertices: (N, 3) 옷 정점(패턴을 만든 몸 기준 위치).
        panel_of_vertex: 정점마다 패널 번호.
        panel_names: 패널 이름(번호 순서).
        ref_body: 패턴을 만든 몸의 정점.
        target_body: 착장할 몸의 정점.

    Returns:
        (offsets (패널 수, 3), record dict)

    Raises:
        ValueError: 어깨 끝을 찾지 못했거나 옮길 거리가 `MAX_SHIFT_M`보다 크다.
    """
    g = np.asarray(garment_vertices, dtype=float)
    pov = np.asarray(panel_of_vertex, dtype=int)
    tips = {
        name: {side: shoulder_tip(body, side) for side in (1, -1)}
        for name, body in (("reference", ref_body), ("target", target_body))
    }
    rise = {side: tips["target"][side][2] - tips["reference"][side][2] for side in (1, -1)}
    dz = max(rise[1], rise[-1])
    dx = {side: tips["target"][side][0] - tips["reference"][side][0] for side in (1, -1)}
    largest = max(abs(dz), abs(dx[1]), abs(dx[-1]))
    clearance = SEW_CLEARANCE_M if largest > SAME_BODY_M else 0.0
    dz += clearance
    if largest > MAX_SHIFT_M:
        raise ValueError(
            f"옷을 {largest:.2f} m 옮겨야 합니다(상한 {MAX_SHIFT_M} m). 몸 메시를 확인하세요"
        )

    offsets = np.zeros((len(panel_names), 3))
    offsets[:, 2] = dz
    for i, name in enumerate(panel_names):
        if "sleeve" in str(name).lower():
            mask = pov == i
            if mask.any():
                side = 1 if float(g[mask, 0].mean()) >= 0 else -1
                offsets[i, 0] = dx[side]

    record = {
        "method": METHOD,
        "slope": SLOPE,
        "min_lateral_m": MIN_LATERAL_M,
        "min_height_frac": MIN_HEIGHT_FRAC,
        "max_height_frac": MAX_HEIGHT_FRAC,
        "shoulder_tips_m": {
            name: {"left": _round(t[1]), "right": _round(t[-1])} for name, t in tips.items()
        },
        "shoulder_rise_m": {"left": round(float(rise[1]), 4), "right": round(float(rise[-1]), 4)},
        "sew_clearance_m": clearance,
        "vertical_shift_m": round(float(dz), 4),
        "sleeve_lateral_shift_m": {
            "left": round(float(dx[1]), 4),
            "right": round(float(dx[-1]), 4),
        },
        "panel_offsets_m": {str(n): _round(offsets[i]) for i, n in enumerate(panel_names)},
    }
    return offsets, record


def apply_offsets(garment_vertices, panel_of_vertex, offsets) -> np.ndarray:
    """패널마다 같은 벡터만큼 옮긴다(패널 안의 거리·모양은 그대로)."""
    g = np.asarray(garment_vertices, dtype=float)
    return g + np.asarray(offsets, dtype=float)[np.asarray(panel_of_vertex, dtype=int)]
