"""옷 패널 첫 위치 맞추기(`garmentcode/placement.py`, #6 #7). GarmentCode·Blender 불필요.

몸은 단순한 점 모형이다: 몸통(어깨선이 목 쪽으로 조금 높음), 머리, A자세 45° 팔, 다리.
"""

import numpy as np
import pytest

from fitting.adapters.garmentcode import placement as pl

PANELS = ["left_ftorso", "right_ftorso", "left_sleeve_f", "right_sleeve_f"]


def toy_body(shoulder_z=1.42, half_width=0.19, lift=0.0, t_pose=False):
    pts = []
    for x in np.linspace(-half_width, half_width, 39):  # 몸통: 어깨 끝으로 갈수록 낮아지는 어깨선
        top = shoulder_z + 0.04 * (1 - abs(x) / half_width)
        pts += [(x, y, z) for z in np.linspace(0.9, top, 25) for y in (-0.12, 0.12)]
    pts += [  # 머리·목
        (x, 0.0, z)
        for x in np.linspace(-0.07, 0.07, 8)
        for z in np.linspace(shoulder_z + 0.04, shoulder_z + 0.30, 20)
    ]
    for side in (1, -1):  # 팔: 어깨 끝에서 45° 아래(T자세면 수평)
        for t in np.linspace(0.0, 0.6, 50):
            drop = 0.0 if t_pose else t * 0.7071
            reach = t if t_pose else t * 0.7071
            pts.append((side * (half_width + reach), 0.0, shoulder_z - drop))
    pts += [(x, 0.0, z) for x in (-0.1, 0.1) for z in np.linspace(0.0, 0.9, 30)]  # 다리
    v = np.array(pts, dtype=float)
    v[:, 2] += lift
    return v


def toy_garment():
    verts, pov = [], []
    boxes = [
        ((0.0, 0.28), -0.30, (0.71, 1.41)),
        ((-0.28, 0.0), -0.30, (0.71, 1.41)),
        ((0.13, 0.38), -0.175, (1.11, 1.42)),
        ((-0.38, -0.13), -0.175, (1.11, 1.42)),
    ]
    for i, ((x0, x1), y, (z0, z1)) in enumerate(boxes):
        for x in np.linspace(x0, x1, 6):
            for z in np.linspace(z0, z1, 6):
                verts.append((x, y, z))
                pov.append(i)
    return np.array(verts), np.array(pov)


def test_shoulder_tip_is_the_corner_between_shoulder_and_arm():
    body = toy_body()
    left, right = pl.shoulder_tip(body, 1), pl.shoulder_tip(body, -1)
    assert left[0] == pytest.approx(0.19, abs=0.01) and left[2] == pytest.approx(1.42, abs=0.01)
    assert right[0] == pytest.approx(-0.19, abs=0.01) and right[2] == pytest.approx(1.42, abs=0.01)


def test_same_body_does_not_move_the_garment():
    g, pov = toy_garment()
    body = toy_body()
    offsets, record = pl.placement_offsets(g, pov, PANELS, body, body)
    assert np.allclose(offsets, 0.0)  # 같은 몸이면 봉제 여유도 더하지 않는다
    assert record["vertical_shift_m"] == 0.0 and record["sew_clearance_m"] == 0.0
    assert record["method"] == "shoulder_tips_v1"


def test_higher_shoulders_lift_every_panel_and_keep_its_shape():
    g, pov = toy_garment()
    offsets, record = pl.placement_offsets(g, pov, PANELS, toy_body(), toy_body(lift=0.05))
    assert offsets[:, 2] == pytest.approx([0.05 + pl.SEW_CLEARANCE_M] * 4, abs=1e-9)
    assert np.allclose(offsets[:, :2], 0.0)  # 어깨 폭이 같아 옆으로는 옮기지 않는다
    moved = pl.apply_offsets(g, pov, offsets)
    for i in range(len(PANELS)):  # 패널마다 같은 벡터만큼만 옮겨 모양(의류 실측)은 그대로다
        assert np.allclose(moved[pov == i] - g[pov == i], offsets[i])
    assert record["panel_offsets_m"]["left_sleeve_f"] == [0.0, 0.0, 0.06]
    assert record["sew_clearance_m"] == pl.SEW_CLEARANCE_M


def test_the_higher_shoulder_sets_the_lift():
    g, pov = toy_garment()
    target = toy_body(lift=0.05)
    target[(target[:, 0] > 0.1) & (target[:, 2] > 1.3), 2] += 0.01  # 왼쪽 어깨만 1 cm 더 높다
    offsets, record = pl.placement_offsets(g, pov, PANELS, toy_body(), target)
    assert offsets[:, 2] == pytest.approx([0.06 + pl.SEW_CLEARANCE_M] * 4, abs=1e-9)
    assert record["shoulder_rise_m"]["left"] == pytest.approx(0.06)


def test_wider_shoulders_move_only_the_sleeves_sideways():
    g, pov = toy_garment()
    offsets, record = pl.placement_offsets(
        g, pov, PANELS, toy_body(), toy_body(half_width=0.21)
    )
    assert offsets[2, 0] == pytest.approx(0.02, abs=0.006)  # 왼쪽 소매는 +X(몸의 왼쪽)로
    assert offsets[3, 0] == pytest.approx(-0.02, abs=0.006)
    assert np.allclose(offsets[:2, 0], 0.0)  # 몸통 패널은 옆으로 옮기지 않는다
    assert record["sleeve_lateral_shift_m"]["left"] == pytest.approx(0.02, abs=0.006)


def test_too_large_a_shift_is_rejected():
    g, pov = toy_garment()
    with pytest.raises(ValueError, match="옮겨야"):
        pl.placement_offsets(g, pov, PANELS, toy_body(), toy_body(lift=0.5))


def test_a_body_that_is_not_in_a_pose_is_rejected():
    with pytest.raises(ValueError, match="A자세"):
        pl.shoulder_tip(toy_body(t_pose=True), 1)
