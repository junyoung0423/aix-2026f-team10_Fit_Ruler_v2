import numpy as np

import metrics
import weld

# 두 패널(각 삼각형 2개)을 변 하나로 봉제: 패널 A 정점 0-3, 패널 B 정점 4-7, 봉제 (1,4),(2,7)
A = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], float)
B = A + [1.5, 0, 0]
REST = np.vstack([A, B])
TRIS = np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]])
PAIRS = [[1, 4, 0], [2, 7, 0]]


def test_weld_map_merges_pairs():
    vmap, n = weld.weld_map(8, PAIRS)
    assert n == 6
    assert vmap[1] == vmap[4] and vmap[2] == vmap[7]
    assert len(set(vmap[[0, 1, 2, 3, 5, 6]].tolist())) == 6


def test_weld_map_chain_three_way():
    vmap, n = weld.weld_map(5, [[0, 1, 0], [1, 2, 1], [3, 4, 2]])
    assert n == 2 and vmap[0] == vmap[2] and vmap[3] == vmap[4]


def test_weld_mesh_average_and_degenerate():
    vmap, n = weld.weld_map(8, PAIRS)
    co, t, n_deg = weld.weld_mesh(REST, TRIS, vmap, n)
    assert n_deg == 0 and len(t) == 4
    assert np.allclose(co[vmap[1]], (A[1] + B[0]) / 2)
    _, t2, n_deg2 = weld.weld_mesh(REST, TRIS, *weld.weld_map(8, [[0, 1, 0]]))
    assert n_deg2 == 1 and len(t2) == 3


def test_target_lengths_and_relax_recover_pattern():
    vmap, n = weld.weld_map(8, PAIRS)
    co, t, _ = weld.weld_mesh(REST, TRIS, vmap, n)
    edges, L0 = weld.target_lengths(REST, TRIS, vmap)
    assert np.isclose(L0.min(), 1.0) and np.isclose(L0.max(), np.sqrt(2))
    # 용접 시점 형상(가운데 0.5 벌어짐을 평균으로 합침)은 길이가 틀리다
    before = weld.length_ratio_stats(co, edges, L0)
    assert before['max'] > 1.1 or before['min'] < 0.9
    x = weld.relax_to_lengths(co, edges, L0, iterations=500)
    after = weld.length_ratio_stats(x, edges, L0)
    assert after['max'] < 1.001 and after['min'] > 0.999


def test_mapped_back_coords_give_zero_gap_and_pattern_stretch():
    vmap, n = weld.weld_map(8, PAIRS)
    co, _, _ = weld.weld_mesh(REST, TRIS, vmap, n)
    edges, L0 = weld.target_lengths(REST, TRIS, vmap)
    x = weld.relax_to_lengths(co, edges, L0, iterations=500)
    back = x[vmap]
    assert np.allclose(metrics.seam_gaps(back, PAIRS), 0)
    _, s = metrics.edge_stretch(REST, back, TRIS)
    assert np.allclose(s, 1, atol=1e-3)
