import pytest

np = pytest.importorskip("numpy", reason="garment 선택 의존성 필요: pip install -e .[garment]")

from fitting.adapters.blender import drape_measure as dm  # noqa: E402

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


# C6: 기장선 허용 오차. 기장선(HPS -> 밑단)이 목 구멍 안을 지나는 경우(가슴 118 cm 이상 견본)


def _four_panels(dx_length_line):
    """몸판 4개(각 10 x 10 cm 정사각형). 앞판 기장선은 오른쪽 변에서 dx만큼 밖을 지난다."""
    names = ["left_ftorso", "left_btorso", "right_ftorso", "right_btorso"]
    uv, faces, pov, rest = [], [], [], []
    for i, _ in enumerate(names):
        base = len(uv)
        uv += UV
        faces += [[base, base + 1, base + 2], [base, base + 2, base + 3]]
        pov += [i] * 4
        rest += [[x * 0.01, i * 0.2, y * 0.01] for x, y in UV]  # z가 위
    g = {"uv_cm": uv, "faces": faces, "panel_of_vertex": pov,
         "panels": [{"name": n} for n in names]}
    lm = {}
    for n in names:
        lm[f"{n}_chest_line"] = [[0, 5], [10, 5]]
        lm[f"{n}_length_line"] = [[10 + dx_length_line, 10], [10 + dx_length_line, 0]]
        lm[f"{n}_shoulder_point"] = [10, 10]
    return g, lm, np.array(rest)


def test_length_line_through_neck_hole_snaps_up_to_length_limit():
    g, lm, rest = _four_panels(1.5)  # 1 cm는 넘고 기장선 한도(2 cm) 안
    out, detail = dm.measure_draped(g, lm, rest)
    assert out["body_length_surface_cm"] == pytest.approx(10.0)
    assert out["body_length_z_drop_cm"] == pytest.approx(10.0)
    assert detail["max_snap_to_mesh_cm"] == pytest.approx(1.5)


def test_length_line_beyond_length_limit_still_raises():
    g, lm, rest = _four_panels(dm.LENGTH_LINE_MAX_SNAP_CM + 0.5)
    with pytest.raises(ValueError, match="outside panel"):
        dm.measure_draped(g, lm, rest)


def test_other_lines_keep_one_cm_limit():
    g, lm, rest = _four_panels(0.0)
    lm["left_ftorso_chest_line"] = [[0, 5], [11.5, 5]]  # 가슴선은 1 cm 한도 그대로
    with pytest.raises(ValueError, match="outside panel"):
        dm.measure_draped(g, lm, rest)
    assert dm.MAX_SNAP_CM == 1.0
