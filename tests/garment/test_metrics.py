import json
from pathlib import Path

import numpy as np
import pytest

import metrics
from drape_config import load_config

CONFIG = Path(__file__).resolve().parents[1] / 'configs' / 'drape_v0.json'
TH, _ = load_config(CONFIG)
TH = TH['thresholds']

# 1 m x 1 m 정사각형 두 삼각형
REST = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], float)
FACES = [[0, 1, 2], [0, 2, 3]]
TRIS = np.array(FACES)


def outside(n=2, d=0.01):
    return np.full(n, d)


def run(cur, disp=None, sd=None, closed=True, pairs=((0, 1, 0),)):
    disp = [0.0] * 10 if disp is None else disp
    sd = outside() if sd is None else sd
    return metrics.evaluate(REST, FACES, cur, list(pairs), disp, sd, closed, TH)


def test_thresholds_are_spec_initial_values():
    assert TH['max_frames'] == 300
    assert TH['stable_window_frames'] == 10
    assert TH['stable_median_m_per_frame'] == 0.001
    assert TH['penetration_depth_m'] == 0.003
    assert TH['penetration_area_ratio_max'] == 0.01
    assert TH['stretch_ratio_max'] == 1.5


def test_stretch_identity_passes():
    r = run(REST)
    assert r['stretch_max'] == 1.0 and r['stretch_ok'] and r['drape_ok']


def test_stretch_2x_fails():
    r = run(REST * np.array([2.0, 1.0, 1.0]))
    assert r['stretch_max'] == 2.0
    assert not r['stretch_ok'] and not r['drape_ok']


def test_stretch_exactly_limit_passes():
    r = run(REST * np.array([1.5, 1.0, 1.0]))
    assert r['stretch_ok']


def test_unique_edges_count():
    assert len(metrics.unique_edges(TRIS)) == 5


def test_nan_fails():
    cur = REST.copy()
    cur[2, 0] = np.nan
    r = run(cur)
    assert r['nan'] and not r['drape_ok']


def test_stable_median():
    assert metrics.stable_median([5.0] * 5 + [0.0005] * 10, 10) == 0.0005
    assert metrics.stable_median([0.0] * 9, 10) is None
    assert not run(REST, disp=[0.0] * 9)['stable_ok']
    assert run(REST, disp=[0.002] * 4 + [0.001] * 10)['stable_ok']   # 1 mm 이하
    assert not run(REST, disp=[0.0011] * 10)['stable_ok']


def test_frame_displacement_median():
    prev = np.zeros((3, 3))
    cur = np.array([[0.001, 0, 0], [0.002, 0, 0], [0.003, 0, 0]])
    assert metrics.frame_displacement_median(prev, cur) == pytest.approx(0.002)


def test_penetration_ratio_and_status():
    # 면 0 이 4 mm 깊이 -> 면적 절반
    r = run(REST, sd=np.array([-0.004, 0.01]))
    assert r['penetration_area_ratio'] == 0.5 and not r['penetration_ok']
    # 2 mm 깊이는 3 mm 기준 안쪽이므로 통과
    assert run(REST, sd=np.array([-0.002, -0.002]))['penetration_ok']


def test_penetration_unverifiable():
    r = run(REST, sd=np.array([np.nan, 0.01]))
    assert r['penetration_status'] == 'UNVERIFIABLE' and r['penetration_unknown_faces'] == 1
    assert not r['penetration_ok']
    r = run(REST, closed=False)
    assert r['penetration_status'] == 'UNVERIFIABLE' and not r['penetration_ok']


def test_seam_gaps():
    cur = REST.copy()
    gaps = metrics.seam_gaps(cur, [[0, 1, 0], [0, 3, 1]])
    assert np.allclose(gaps, [1.0, 1.0])
    r = run(cur, pairs=[(0, 1, 0)])
    assert r['seam_gap_max_mm'] == 1000.0


def test_evaluate_is_deterministic():
    rng = np.random.default_rng(1)
    cur = REST + rng.normal(scale=0.01, size=REST.shape)
    assert json.dumps(run(cur)) == json.dumps(run(cur.copy()))


def test_per_panel_and_stitch_summaries():
    cur = REST * np.array([2.0, 1.0, 1.0])
    panels, worst = metrics.per_panel_stretch(REST, FACES, cur, [0, 0, 0, 0], ['front'], 1.5)
    assert panels[0]['stretch_max'] == 2.0 and panels[0]['n_over_limit'] == 3  # 가로 변 2개 + 대각선(1.58)
    s = metrics.per_stitch_gap(cur, [[0, 1, 3]], [0, 0, 0, 0], ['front'])
    assert s[0]['stitch'] == 3 and s[0]['gap_max_mm'] == 2000.0


def test_seam_closure_sewing_springs_rule():
    th = dict(TH, seam_gap_max_m=0.002)
    cur = REST.copy()
    r = metrics.evaluate(REST, FACES, cur, [[0, 1, 0]], [0.0] * 10, outside(), True, th)
    assert r['seam_closure_mode'] == 'sewing_springs' and not r['seam_closed_ok'] and not r['drape_ok']
    r = metrics.evaluate(REST, FACES, cur, [[0, 0, 0]], [0.0] * 10, outside(), True, th)
    assert r['seam_closed_ok'] and r['drape_ok']


def test_seam_closure_weld_rule_uses_preweld_gap():
    th = dict(TH, seam_gap_max_m=0.002, seam_gap_preweld_max_m=0.010)
    # 용접 뒤 거리 0이어도 용접 전 거리가 기준을 넘으면 실패
    r = metrics.evaluate(REST, FACES, REST, [[0, 0, 0]], [0.0] * 10, outside(), True, th, preweld_gaps=[0.011])
    assert r['seam_closure_mode'] == 'weld' and r['seam_gap_preweld_max_mm'] == 11.0
    assert not r['seam_closed_ok'] and not r['drape_ok']
    r = metrics.evaluate(REST, FACES, REST, [[0, 0, 0]], [0.0] * 10, outside(), True, th, preweld_gaps=[0.0068])
    assert r['seam_closed_ok'] and r['drape_ok']


def test_v0_has_no_seam_rule():
    r = run(REST, pairs=[(0, 1, 0)])
    assert 'seam_closed_ok' not in r and r['drape_ok']


def test_compression_and_gravity_response():
    r = run(REST * np.array([0.5, 1.0, 1.0]))
    assert r['stretch_min'] == 0.5 and r['stretch_p01'] <= 0.6
    ref = REST.copy()
    cur = REST - [0, 0, 0.01]
    g = metrics.gravity_response(ref, cur, [0, 1], [2])
    assert g['disp_median_mm'] == 10.0 and g['hem_min_z_change_mm'] == -10.0
    assert g['sleeve_end_mean_z_change_mm'] == -10.0


def test_free_edge_vertices():
    panels = [{'name': 'left_ftorso', 'edges': [
                  {'index': 0, 'label': '', 'vertices': [0, 1], 'stitched': False},
                  {'index': 1, 'label': 'left_collar', 'vertices': [2, 3], 'stitched': False},
                  {'index': 2, 'label': '', 'vertices': [3, 4], 'stitched': True}]},
              {'name': 'left_sleeve_f', 'edges': [{'index': 0, 'label': '', 'vertices': [5, 6], 'stitched': False}]}]
    assert metrics.free_edge_vertices(panels, 'hem') == [0, 1]
    assert metrics.free_edge_vertices(panels, 'cuff') == [5, 6]
    assert metrics.free_edge_vertices([{'name': 'x'}], 'hem') is None
