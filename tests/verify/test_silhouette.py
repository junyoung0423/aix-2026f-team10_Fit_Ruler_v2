"""옷 마스크 형태 값 (C6, fitting/verify/silhouette.py). 합성 마스크로 손계산과 비교한다."""

import numpy as np
import pytest

from fitting.verify import EmptyMaskError, compare_masks, mask_shape
from fitting.verify.silhouette import as_bool_mask, boundary_hd95


def rect(w=100, h=80, x0=20, y0=10, x1=59, y1=29):
    m = np.zeros((h, w), dtype=np.uint8)
    m[y0 : y1 + 1, x0 : x1 + 1] = 255
    return m


def trapezoid():
    """10행, 행 y는 열 49-y ~ 50+y (폭 2y+2), 가운데 49.5 기준 좌우 대칭."""
    m = np.zeros((20, 100), dtype=bool)
    for y in range(10):
        m[y, 49 - y : 51 + y] = True
    return m


def by_level(widths):
    return {w["level"]: w for w in widths}


# ---------------------------------------------------------------- mask_shape


def test_rectangle_shape_matches_hand_values():
    s = mask_shape(rect())
    assert s["image_size"] == [100, 80]
    assert s["area_px"] == 20 * 40
    assert s["area_ratio"] == pytest.approx(800 / 8000)
    assert s["bbox_px"] == [20, 10, 59, 29]
    assert s["bbox_size_px"] == [40, 20]
    assert s["bbox_norm"] == pytest.approx([0.2, 0.125, 0.6, 0.375])
    assert s["centroid_px"] == pytest.approx([39.5, 19.5])
    assert s["centroid_norm"] == pytest.approx([0.4, 0.25])
    assert s["hem_row_px"] == 29
    assert s["hem_norm"] == pytest.approx(30 / 80)
    w = by_level(s["widths"])
    # D6와 같은 행: 맨 위 10 + round(level × (29 − 10)). 0.15 → 10+3, 0.45 → 10+9, 0.90 → 10+17
    assert (w[0.15]["row"], w[0.15]["rows"]) == (13, [11, 15])
    assert (w[0.45]["row"], w[0.45]["rows"]) == (19, [17, 21])
    assert (w[0.9]["row"], w[0.9]["rows"]) == (27, [25, 29])
    for v in w.values():
        assert v["width_px"] == 40
        assert v["width_rel"] == pytest.approx(2.0)
    assert s["symmetry_iou"] == 1.0
    assert s["symmetry_diff"] == 0.0


def test_trapezoid_widths_area_centroid():
    s = mask_shape(trapezoid())
    assert s["area_px"] == sum(2 * y + 2 for y in range(10))  # 110
    assert s["bbox_px"] == [40, 0, 59, 9]
    # cy = Σ y(2y+2) / 110 = (2·285 + 2·45) / 110 = 6
    assert s["centroid_px"] == pytest.approx([49.5, 6.0])
    w = by_level(s["widths"])
    assert w[0.15]["rows"] == [0, 3] and w[0.15]["width_px"] == 5.0  # 2,4,6,8
    assert w[0.45]["rows"] == [2, 6] and w[0.45]["width_px"] == 10.0  # 6..14
    # 0.90 → 행 8, 띠 6~10. 박스 밖 10행은 옷이 없어 폭 0으로 센다(D6 band_width와 같음)
    assert w[0.9]["rows"] == [6, 10] and w[0.9]["width_px"] == 16.0  # 14,16,18,20,0
    assert w[0.45]["width_rel"] == pytest.approx(1.0)
    assert s["symmetry_diff"] == 0.0


def test_width_is_span_not_pixel_count():
    m = np.zeros((5, 20), dtype=bool)
    m[:, 2:5] = True  # 왼쪽 조각
    m[:, 15:18] = True  # 오른쪽 조각 (가운데 빈칸)
    s = mask_shape(m, levels=(0.5,), band_rows=1)
    assert s["widths"][0]["width_px"] == 16  # 2 ~ 17


def test_symmetry_diff_of_asymmetric_shape():
    m = np.zeros((4, 6), dtype=bool)
    m[0, 0:4] = True
    m[1, 0:2] = True
    # 박스 x 0~3을 뒤집으면 1행은 2~3. 교집합 4, 합집합 8
    s = mask_shape(m)
    assert s["symmetry_iou"] == pytest.approx(0.5)
    assert s["symmetry_diff"] == pytest.approx(0.5)


def test_single_pixel_mask():
    m = np.zeros((10, 10), dtype=bool)
    m[3, 7] = True
    s = mask_shape(m)
    assert s["area_px"] == 1
    assert s["bbox_px"] == [7, 3, 7, 3]
    assert all(w["row"] == 3 for w in s["widths"])
    # 5행 띠 중 옷이 있는 행은 1개라 중앙값은 0이다(D6와 같음). 1행 띠면 1이다
    assert all(w["width_px"] == 0 for w in s["widths"])
    assert all(w["width_px"] == 1 for w in mask_shape(m, band_rows=1)["widths"])


def test_level_one_is_last_row():
    s = mask_shape(rect(), levels=(0.0, 1.0), band_rows=1)
    w = by_level(s["widths"])
    assert w[0.0]["row"] == 10
    assert w[1.0]["row"] == 29


def test_empty_mask_is_error():
    with pytest.raises(EmptyMaskError):
        mask_shape(np.zeros((8, 8), dtype=np.uint8))
    with pytest.raises(EmptyMaskError):
        compare_masks(rect(), np.zeros((80, 100), dtype=np.uint8))


@pytest.mark.parametrize(
    "bad",
    [
        np.full((4, 4), 128, dtype=np.uint8),  # 안티에일리어싱 값
        np.zeros((4, 4, 3), dtype=np.uint8),  # 3채널
        np.zeros((4, 4), dtype=np.float32),  # 실수
        np.zeros((0, 4), dtype=bool),
    ],
)
def test_invalid_mask_values(bad):
    with pytest.raises(ValueError):
        as_bool_mask(bad)


def test_zero_one_and_bool_inputs_equal_zero_255():
    m255 = rect()
    assert mask_shape(m255) == mask_shape(m255 == 255) == mask_shape((m255 // 255).astype(np.int32))


def test_invalid_level_and_band():
    with pytest.raises(ValueError):
        mask_shape(rect(), levels=(1.2,))
    with pytest.raises(ValueError):
        mask_shape(rect(), band_rows=0)
    with pytest.raises(ValueError):
        mask_shape(rect(), band_rows=4)  # 짝수: 가운데 행 위아래가 같지 않다


def test_widths_match_d6_measurement():
    """D6 판정(visual_check)이 재는 가슴·밑단 폭과 같은 값이다."""
    from fitting.verify.visual_check import band_width, width_rows

    rng = np.random.default_rng(0)
    ref = np.zeros((120, 90), dtype=bool)
    for y in range(15, 100):
        half = 10 + (y - 15) // 3 + int(rng.integers(0, 4))
        ref[y, 45 - half : 45 + half] = True
    ref[40:44, 2:12] = True  # 처진 소매 끝처럼 떨어진 조각
    other = np.roll(ref, (3, -2), axis=(0, 1))
    rows = width_rows(ref, 0.45, 0.90)
    w = by_level(compare_masks(ref, other)["widths"])
    assert w[0.45]["row"] == rows.chest and w[0.9]["row"] == rows.hem
    assert w[0.45]["ref_width_px"] == band_width(ref, rows.chest, 5)
    assert w[0.45]["other_width_px"] == band_width(other, rows.chest, 5)
    assert w[0.9]["other_width_px"] == band_width(other, rows.hem, 5)


# ---------------------------------------------------------------- compare_masks


def test_same_mask_compare():
    c = compare_masks(rect(), rect())
    assert c["iou"] == 1.0
    assert c["area_ratio"] == 1.0
    assert c["area_ratio_diff"] == 0.0
    assert c["centroid_shift_px"] == [0.0, 0.0]
    assert c["hem_shift_px"] == 0
    assert c["boundary_hd95_px"] == 0.0
    assert all(w["width_diff_px"] == 0 and w["width_change_ratio"] == 0 for w in c["widths"])


def test_one_pixel_right_shift():
    c = compare_masks(rect(), rect(x0=21, x1=60))
    assert c["iou"] == pytest.approx(780 / 820)  # 20×39 / 20×41
    assert c["centroid_shift_px"] == pytest.approx([1.0, 0.0])
    assert c["centroid_shift_dist_px"] == pytest.approx(1.0)
    assert c["hem_shift_px"] == 0
    assert c["area_ratio"] == 1.0
    assert c["boundary_hd95_px"] == pytest.approx(1.0)


def test_one_pixel_down_shift_moves_hem():
    c = compare_masks(rect(), rect(y0=11, y1=30))
    assert c["iou"] == pytest.approx(19 * 40 / (21 * 40))
    assert c["hem_shift_px"] == 1
    assert c["centroid_shift_px"] == pytest.approx([0.0, 1.0])


def test_widths_measured_at_ref_rows():
    ref = rect()  # 폭 40, 행 10~29
    other = rect(x0=25, x1=54, y0=15, y1=34)  # 폭 30, 5행 아래
    c = compare_masks(ref, other)
    w = by_level(c["widths"])
    # ref 기준 행 13(띠 11~15): other는 15행만 옷 → 폭 [0,0,0,0,30] 중앙값 0
    assert w[0.15]["row"] == 13 and w[0.15]["other_width_px"] == 0.0
    assert w[0.45]["other_width_px"] == 30.0
    assert w[0.45]["width_diff_px"] == -10.0
    assert w[0.45]["width_change_ratio"] == pytest.approx(-0.25)
    assert c["hem_shift_px"] == 5
    assert c["area_ratio"] == pytest.approx(600 / 800)


def test_compare_is_not_symmetric_for_widths_but_iou_is():
    a, b = rect(), rect(x0=25, x1=54, y0=15, y1=34)
    assert compare_masks(a, b)["iou"] == compare_masks(b, a)["iou"]
    assert compare_masks(a, b)["boundary_hd95_px"] == compare_masks(b, a)["boundary_hd95_px"]


def test_size_mismatch_is_error():
    with pytest.raises(ValueError, match="크기"):
        compare_masks(rect(), rect(w=101))


def test_hd95_ignores_few_outliers_but_sees_large_change():
    ref = rect()
    noisy = ref.copy()
    noisy[0, 99] = 255  # 멀리 떨어진 1픽셀 잡음
    assert boundary_hd95(ref, noisy) == 0.0  # 경계 117점 중 1점(1 % 미만)
    big = ref.copy()
    big[30:60, 20:60] = 255  # 아래로 30행 늘어남
    assert boundary_hd95(ref, big) >= 20


def test_chunked_distance_matches_bruteforce(monkeypatch):
    from fitting.verify import silhouette as sil

    rng = np.random.default_rng(0)
    m = np.zeros((60, 60), dtype=bool)
    m[10:50, 10:50] = True
    m2 = m.copy()
    m2[rng.integers(5, 55, 200), rng.integers(5, 55, 200)] ^= True
    whole = sil.boundary_hd95(m, m2)
    monkeypatch.setattr(sil, "_CHUNK", 7)
    assert sil.boundary_hd95(m, m2) == pytest.approx(whole)
