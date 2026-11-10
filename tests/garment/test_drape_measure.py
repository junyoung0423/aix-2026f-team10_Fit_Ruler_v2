import numpy as np
import pytest

import drape_measure as dm

# 한 패널: 2D 10 x 10 cm 정사각형(삼각형 2개). 3D 좌표는 2D x 0.01 (m)
UV = [[0, 0], [10, 0], [10, 10], [0, 10]]
G = {'uv_cm': UV, 'faces': [[0, 1, 2], [0, 2, 3]], 'panel_of_vertex': [0, 0, 0, 0],
     'panels': [{'name': 'p'}]}
REST3 = np.array([[x * 0.01, y * 0.01, 0.0] for x, y in UV])


def test_rest_mapping_reproduces_pattern_length():
    pm = dm.PanelMap(G)
    poly = pm.polyline('p', [0, 5], [10, 5], REST3)
    assert dm._length(poly) * 100 == pytest.approx(10.0)
    assert pm.to3d('p', [10, 10], REST3) == pytest.approx([0.1, 0.1, 0.0])


def test_stretched_mesh_line_length():
    cur = REST3 * [2.0, 1.0, 1.0]
    pm = dm.PanelMap(G)
    assert dm._length(pm.polyline('p', [0, 5], [10, 5], cur)) * 100 == pytest.approx(20.0)


def test_outside_point_raises():
    with pytest.raises(ValueError):
        dm.PanelMap(G).to3d("p", [20, 5], REST3)  # 10 cm 밖 -> 오류


def test_point_slightly_outside_snaps_and_records():
    pm = dm.PanelMap(G)
    p = pm.to3d('p', [10.5, 5], REST3)
    assert p == pytest.approx([0.1, 0.05, 0.0])
    assert pm.max_snap_cm == pytest.approx(0.5)
