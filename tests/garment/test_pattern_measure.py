"""수제 패턴으로 측정기 검증. 정답은 손으로 계산했다(주석)."""
import copy
import math

import pytest

import pattern_measure as pm


def mirror(verts):
    return [[-x, y] for x, y in verts]


def edges_loop(n):
    return [{'endpoints': [i, (i + 1) % n]} for i in range(n)]


def make_spec(front_side_top=(25, 30), sleeve_top_curve=None):
    # 왼쪽 앞판: 0 밑단 / 1 옆선 / 2 진동 / 3 어깨선(SP(18,45) -> HPS(8,50)) / 4 목 / 5 앞 중심
    fx, fy = front_side_top
    f = [[0, 0], [25, 0], [fx, fy], [18, 45], [8, 50], [0, 44]]
    b = [[0, 0], [22, 0], [22, 30], [18, 45], [8, 50], [0, 47]]
    s = [[0, 0], [-20, 0], [-20, -15], [0, -15]]  # 0 윗선 / 1 소매산 / 2 밑선 / 3 소매단
    panels = {}
    for side, sgn in (('left', 1), ('right', -1)):
        fv = f if sgn == 1 else mirror(f)
        bv = b if sgn == 1 else mirror(b)
        sv = s if sgn == 1 else mirror(s)
        panels[f'{side}_ftorso'] = {'vertices': fv, 'edges': edges_loop(6), 'translation': [0, 90, 30], 'rotation': [0, 0, 0]}
        panels[f'{side}_btorso'] = {'vertices': bv, 'edges': edges_loop(6), 'translation': [0, 90, -25], 'rotation': [0, 0, 0]}
        for k, z in (('sleeve_f', 15), ('sleeve_b', -15)):
            e = edges_loop(4)
            if sleeve_top_curve is not None:
                e[0] = dict(e[0], curvature=sleeve_top_curve)
            panels[f'{side}_{k}'] = {'vertices': sv, 'edges': e, 'translation': [sgn * 40, 120, z], 'rotation': [0, 0, 0]}
    st = []
    st.append([{'panel': 'left_ftorso', 'edge': 5}, {'panel': 'right_ftorso', 'edge': 5}])
    st.append([{'panel': 'left_btorso', 'edge': 5}, {'panel': 'right_btorso', 'edge': 5}])
    for side in ('left', 'right'):
        st.append([{'panel': f'{side}_ftorso', 'edge': 3}, {'panel': f'{side}_btorso', 'edge': 3}])
        st.append([{'panel': f'{side}_ftorso', 'edge': 1}, {'panel': f'{side}_btorso', 'edge': 1}])
        st.append([{'panel': f'{side}_sleeve_f', 'edge': 0}, {'panel': f'{side}_sleeve_b', 'edge': 0}])
        st.append([{'panel': f'{side}_sleeve_f', 'edge': 2}, {'panel': f'{side}_sleeve_b', 'edge': 2}])
    return {'panels': panels, 'stitches': st}


def test_rectangular_hand_values():
    m, d = pm.measure(make_spec())
    # 가슴: 옆선이 수직이라 겨드랑이 1 cm 아래 반폭 = 앞 25, 뒤 22 -> 2 x (25 + 22) = 94
    assert m['chest_girth_cm'] == pytest.approx(94.0)
    # 어깨: 어깨점 (18, 45) 좌우 -> 18 + 18 = 36
    assert m['shoulder_width_cm'] == pytest.approx(36.0)
    # 기장: HPS (8, 50)에서 밑단 y=0까지 수직 50
    assert m['body_length_cm'] == pytest.approx(50.0)
    # 소매: 윗선 직선 길이 20 (밑선 길이도 20이지만 3D에서 낮으므로 고르지 않음)
    assert m['sleeve_length_cm'] == pytest.approx(20.0)
    assert d['left_right_diff'] == {'body_length': 0.0, 'sleeve_length': 0.0}


def test_tapered_side_seam_chest_at_1cm_along_seam():
    # 앞판 겨드랑이점 (23, 30), 옆선 (25,0)-(23,30). 겨드랑이에서 옆선을 따라 1 cm 내려간 점의 반폭
    m, d = pm.measure(make_spec(front_side_top=(23, 30)))
    seam_len = math.hypot(2, 30)
    w_front = 23 + 2 / seam_len
    assert d['left_ftorso_chest_half_width'] == pytest.approx(w_front, abs=1e-3)
    assert m['chest_girth_cm'] == pytest.approx(2 * (w_front + 22), abs=1e-3)


def test_curved_sleeve_top_seam_exact_length():
    # 윗선이 반지름 10 반원 (양 끝 거리 20) -> 길이 pi * 10
    m, _ = pm.measure(make_spec(sleeve_top_curve={'type': 'circle', 'params': [10, 0, 1]}))
    assert m['sleeve_length_cm'] == pytest.approx(math.pi * 10, rel=1e-6)


def test_cubic_curve_length_matches_polyline():
    v = [[0, 0], [10, 0]]
    e = {'endpoints': [0, 1], 'curvature': {'type': 'cubic', 'params': [[0.3, 0.2], [0.7, 0.2]]}}
    c = pm.edge_curve(v, e)
    pts = [c.point(t / 2000) for t in range(2001)]
    poly = sum(abs(pts[i + 1] - pts[i]) for i in range(2000))
    assert c.length() == pytest.approx(poly, rel=1e-5)
    # 제어점 (3, 2), (7, 2) 확인 (상대 좌표 -> 절대)
    assert pm.rel_to_abs_2d([0, 0], [10, 0], [0.3, 0.2]).tolist() == [3.0, 2.0]


def test_missing_panel_raises():
    s = make_spec()
    del s['panels']['right_btorso']
    with pytest.raises(pm.PatternMeasureError):
        pm.measure(s)


def test_wrong_seam_structure_raises():
    s = copy.deepcopy(make_spec())
    s['stitches'] = [st for st in s['stitches'] if not (st[0]['panel'] == 'left_ftorso' and st[0]['edge'] == 1)]
    with pytest.raises(pm.PatternMeasureError):
        pm.measure(s)


def test_garmentcode_default_tee_fixture():
    import json
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / 'fixtures' / 'tee_gc_default' / 't-shirt__261103-16-24-09_specification.json'
    m, _ = pm.measure(json.loads(p.read_text(encoding='utf-8')))
    # GarmentCode 식으로 손 계산: 가슴 = shirt.width x bust = 1.05 x 99.8407 = 104.833
    assert m['chest_girth_cm'] == pytest.approx(1.05 * 99.8407, abs=0.01)
    # 어깨점 = (shoulder_w - 2) / 2 = (36.4568 - 2) / 2 -> 어깨너비 34.4568
    assert m['shoulder_width_cm'] == pytest.approx(36.4568 - 2, abs=0.01)
