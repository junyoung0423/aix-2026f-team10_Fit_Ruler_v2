"""렌더 색 계산과 정면 카메라 프레이밍 (#10, C5). Blender 없이 순수 계산만 본다."""

import numpy as np
import pytest

from fitting.adapters.blender import render_color as rc
from fitting.adapters.blender import render_framing as rf

# --- 색 ---


def test_hex_round_trip_and_bad_format():
    assert rc.hex_to_srgb("#ff0080") == (1.0, 0.0, 128 / 255)
    assert rc.srgb_to_hex(rc.hex_to_srgb("#24324f")) == "#24324f"
    for bad in ("24324f", "#24324", "#24324g", "#24324f0"):
        with pytest.raises(ValueError):
            rc.hex_to_srgb(bad)


@pytest.mark.parametrize(
    ("srgb", "linear"),
    [(0.0, 0.0), (1.0, 1.0), (0.04045, 0.04045 / 12.92), (0.5, 0.214041)],
)
def test_srgb_to_linear_known_values(srgb, linear):
    assert rc.srgb_to_linear(srgb) == pytest.approx(linear, abs=1e-6)
    # sRGB 식은 꺾이는 점(0.04045 / 0.0031308)에서 1e-6 정도 어긋난다(표준 식 그대로)
    assert rc.linear_to_srgb(rc.srgb_to_linear(srgb)) == pytest.approx(srgb, abs=1e-6)


def test_srgb_to_linear_is_continuous_at_the_break():
    eps = 1e-7
    assert rc.srgb_to_linear(0.04045 - eps) == pytest.approx(
        rc.srgb_to_linear(0.04045 + eps), abs=1e-6
    )


def test_white_is_lab_100():
    lab = rc.srgb_to_lab((1.0, 1.0, 1.0))
    assert lab == pytest.approx((100.0, 0.0, 0.0), abs=1e-2)


@pytest.mark.parametrize(
    ("lab1", "lab2", "expected"),
    [
        # Sharma·Wu·Dalal(2005) 시험 자료의 쌍 1, 7, 17, 25
        ((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485), 2.0425),
        ((50.0, 0.0, 0.0), (50.0, -1.0, 2.0), 2.3669),
        ((50.0, 2.5, 0.0), (73.0, 25.0, -18.0), 27.1492),
        ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ],
)
def test_ciede2000_matches_the_sharma_test_data(lab1, lab2, expected):
    assert rc.ciede2000(lab1, lab2) == pytest.approx(expected, abs=1e-4)
    assert rc.ciede2000(lab2, lab1) == pytest.approx(expected, abs=1e-4)


def test_same_color_has_zero_delta_e():
    assert rc.delta_e_hex("#222326", "#222326") == 0.0
    assert rc.delta_e_hex("#24324f", "#283248") > 0


# --- 프레이밍 ---


def _scene(head_bottom=1.47):
    """몸 정면 -y, z 위. 옷: 상자(z 0.9~1.45, 폭 0.7). 머리: 원기둥(z head_bottom~1.70)."""
    rng = np.random.default_rng(0)
    garment = np.column_stack(
        [rng.uniform(-0.35, 0.35, 500), rng.uniform(-0.15, 0.15, 500), rng.uniform(0.9, 1.45, 500)]
    )
    corners = [[x, -0.15, z] for x in (-0.35, 0.35) for z in (0.9, 1.45)]
    garment = np.vstack([garment, corners])
    theta = rng.uniform(0, 2 * np.pi, 400)
    z = rng.uniform(head_bottom, 1.70, 400)
    head = np.column_stack([0.1 * np.cos(theta), -0.02 + 0.1 * np.sin(theta), z])
    legs = np.column_stack(
        [rng.uniform(-0.2, 0.2, 200), rng.uniform(-0.1, 0.1, 200), rng.uniform(0, 0.9, 200)]
    )
    return garment, np.vstack([head, legs])


def _solve(garment, body, **kw):
    args = dict(
        width=768,
        height=1024,
        lens_mm=50.0,
        sensor_height_mm=36.0,
        top_margin_cm=3.0,
        bottom_margin_cm=5.0,
        side_margin_cm=5.0,
        head_min_above_collar_cm=0.0,
        head_clearance_px=4,
    )
    args.update(kw)
    return rf.solve_framing(garment, body, **args)


def test_head_is_out_of_frame_and_garment_is_inside():
    garment, body = _scene(head_bottom=1.55)
    fr = _solve(garment, body)
    head = rf.head_vertices(body, garment[:, 2].max(), 0.0)
    assert len(head) == fr.n_head_vertices > 0
    assert rf.count_in_frame(rf.project(head, fr)) == 0
    assert rf.count_in_frame(rf.project(garment, fr)) == len(garment)
    # 카메라는 몸 앞(-y)에 있고, 옷 경계 상자 가운데 높이다
    assert fr.camera_location_m[1] < garment[:, 1].min()
    assert fr.camera_location_m[2] == pytest.approx((0.9 + 1.45) / 2)


def test_top_is_set_by_the_head_when_the_head_is_close_to_the_collar():
    garment, body = _scene(head_bottom=1.47)
    fr = _solve(garment, body)
    assert fr.top_limited_by == "head"
    head = rf.head_vertices(body, garment[:, 2].max(), 0.0)
    assert rf.count_in_frame(rf.project(head, fr)) == 0
    # 머리가 옷 깃 바로 위면 머리를 빼는 쪽이 먼저다: 깃 앞 모서리가 조금 잘릴 수 있다
    assert rf.count_in_frame(rf.project(garment, fr)) < len(garment)
    # 머리를 멀리 올리면 옷 깃 + 여유가 위쪽 경계다
    far = body.copy()
    far[far[:, 2] > 1.45, 2] += 0.5
    fr = _solve(garment, far)
    assert fr.top_limited_by == "collar_margin"
    top = rf.project(np.array([[0.0, garment[:, 1].min(), 1.45 + 0.03]]), fr)[0, 1]
    assert top == pytest.approx(1.0, abs=1e-3)


def test_side_margin_fits_the_wider_direction_and_is_centred():
    garment, body = _scene()
    fr = _solve(garment, body)
    front = garment[:, 1].min()
    edges = rf.project(np.array([[-0.40, front, 1.2], [0.40, front, 1.2]]), fr)
    # 소매 끝 + 5 cm(가장 가까운 면 기준)가 화면 양쪽 끝 근처에 맞는다
    assert edges[0, 0] == pytest.approx(0.0, abs=0.02)
    assert edges[1, 0] == pytest.approx(1.0, abs=0.02)
    assert edges[0, 0] == pytest.approx(1 - edges[1, 0], abs=1e-6)


def test_head_clearance_keeps_the_lowest_head_vertex_above_the_top():
    garment, body = _scene()
    fr = _solve(garment, body, head_clearance_px=4)
    head = rf.head_vertices(body, garment[:, 2].max(), 0.0)
    v = rf.project(head, fr)[:, 1]
    assert v.min() > 1.0
    assert (v.min() - 1.0) * 1024 == pytest.approx(4, abs=0.5)


def test_project_matches_known_camera_geometry():
    fr = rf.Framing(
        camera_location_m=(0.0, -3.0, 1.0),
        shift_x=0.0,
        shift_y=0.0,
        distance_m=3.0,
        top_limited_by="collar_margin",
        tan_half_v=0.36,
        tan_half_h=0.27,
        n_head_vertices=0,
    )
    uvd = rf.project(np.array([[0.0, 0.0, 1.0], [0.81, 0.0, 1.0], [0.0, 0.0, 2.08]]), fr)
    assert uvd[0].tolist() == pytest.approx([0.5, 0.5, 3.0])
    assert uvd[1, 0] == pytest.approx(1.0)  # 0.81 / 3 = 0.27 = 가로 반폭
    assert uvd[2, 1] == pytest.approx(1.0)  # 1.08 / 3 = 0.36 = 세로 반폭
    assert rf.count_in_frame(uvd) == 3
    assert rf.count_in_frame(uvd + [0.0, 1e-6, 0.0]) == 2


def test_nothing_fits_or_no_garment_is_an_error():
    garment, body = _scene()
    with pytest.raises(ValueError):
        _solve(garment, body, distance_search_m=(0.5, 0.6))
    with pytest.raises(ValueError):
        _solve(np.empty((0, 3)), body)
