"""착장 결과를 Blender 없이 다시 계산하고, 신장·봉제선 벌어짐이 큰 곳을 패널·봉제선별로 요약한다.

같은 메시에 같은 결과가 나오는지 보려고 blender_drape.py와 같은 metrics.evaluate를 쓴다.
침투의 부호 거리는 Blender BVH 결과(drape_trace.json)를 그대로 받는다.

사용: python analyze_drape.py <run_dir> [--config <drape_vN.json>]
    run_dir 안의 drape_input.json, garment_draped_co.npy, drape_trace.json, drape_metrics.json을 읽어
    analysis.json을 쓴다.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import drape_measure  # noqa: E402
import metrics  # noqa: E402
import pattern_measure  # noqa: E402
from drape_config import load_config  # noqa: E402

COMPARED_KEYS = ('stable_last10_median_mm', 'stable_ok', 'penetration_area_ratio', 'penetration_ok',
                 'penetration_status', 'penetration_unknown_faces', 'stretch_max', 'stretch_p99',
                 'stretch_ok', 'nan', 'seam_gap_max_mm', 'seam_gap_median_mm', 'seam_closed_ok', 'drape_ok')


def analyze(run_dir, config_path=None):
    run_dir = Path(run_dir).resolve()
    data = json.loads((run_dir / 'drape_input.json').read_text(encoding='utf-8'))
    blender_result = json.loads((run_dir / 'drape_metrics.json').read_text(encoding='utf-8'))
    trace = json.loads((run_dir / 'drape_trace.json').read_text(encoding='utf-8'))
    co = np.load(run_dir / 'garment_draped_co.npy')
    cfg, cfg_rec = load_config(config_path or blender_result['config']['path'])
    if cfg_rec['sha256'] != blender_result['config']['sha256']:
        raise ValueError('config sha256 differs from the one used in Blender run')
    th = cfg['thresholds']

    g = data['garment']
    rest = np.asarray(g['vertices'])
    assert len(co) == len(rest), (len(co), len(rest))
    sd = np.asarray(trace['signed_dist_m'], dtype=float)
    offline = metrics.evaluate(rest, g['faces'], co, g['stitch_pairs'], trace['disp_median_m_per_frame'],
                               sd, data['body']['is_watertight'], th, preweld_gaps=trace.get('preweld_gaps_m'))
    if 'weld_rest_ok' in blender_result:
        # 용접 휴지 형상 검사는 Blender 안에서만 계산된다(이완 형상이 Blender 쪽에만 있음). 그 값을 그대로 합친다.
        offline['weld_rest_ok'] = blender_result['weld_rest_ok']
        offline['drape_ok'] = offline['drape_ok'] and offline['weld_rest_ok']
    mismatch = {k: [blender_result.get(k), offline.get(k)] for k in COMPARED_KEYS
                if blender_result.get(k) != offline.get(k)}

    names = [p['name'] for p in g['panels']]
    panels, worst = metrics.per_panel_stretch(rest, g['faces'], co, g['panel_of_vertex'], names,
                                              th['stretch_ratio_max'])
    stitches = metrics.per_stitch_gap(co, g['stitch_pairs'], g['panel_of_vertex'], names)
    draped = draped_measurements(run_dir, data, co)
    out = {
        'config': cfg_rec,
        'draped_measurements': draped,
        'offline_metrics': offline,
        'matches_blender_metrics': not mismatch,
        'mismatch': mismatch,
        'stretch_by_panel': panels,
        'worst_stretch_edge_xyz_m': worst,
        'seam_gap_by_stitch': stitches,
    }
    (run_dir / 'analysis.json').write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding='utf-8')
    return out


def draped_measurements(run_dir, data, co):
    """패턴 측정선을 착장 메시에서 다시 잰다(진단용). 패턴 값, 휴지(패턴) 좌표로 잰 값(자체 검산),
    기준 프레임(용접 직후) 값, 최종 값을 나란히 둔다."""
    g = data['garment']
    spec_path = Path(data['source']['garment_spec'])
    if 'uv_cm' not in g or not spec_path.exists():
        return None
    spec = json.loads(spec_path.read_text(encoding='utf-8'))
    pattern, pdetail = pattern_measure.measure(spec)
    lm = pdetail['landmarks']
    res = {'pattern_cm': pattern,
           'rest_layout_cm': drape_measure.measure_draped(g, lm, np.asarray(g['vertices']))[0]}
    ref = run_dir / 'garment_ref_co.npy'
    if ref.exists():
        res['reference_frame_cm'] = drape_measure.measure_draped(g, lm, np.load(ref))[0]
    final, fdetail = drape_measure.measure_draped(g, lm, co)
    res['final_cm'] = final
    res['final_detail'] = fdetail
    res['final_minus_pattern_cm'] = {
        'chest_girth': round(final['chest_girth_surface_cm'] - pattern['chest_girth_cm'], 3),
        'body_length_surface': round(final['body_length_surface_cm'] - pattern['body_length_cm'], 3),
        'body_length_z_drop': round(final['body_length_z_drop_cm'] - pattern['body_length_cm'], 3),
        'shoulder_width_straight_3d': round(final['shoulder_width_straight_3d_cm'] - pattern['shoulder_width_cm'], 3),
    }
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('run_dir')
    ap.add_argument('--config')
    args = ap.parse_args()
    out = analyze(args.run_dir, args.config)
    print('matches blender metrics:', out['matches_blender_metrics'], out['mismatch'])
    print('stretch by panel (limit', out['config']['name'], '):')
    for p in out['stretch_by_panel']:
        print(f"  {p['panel']:16s} max {p['stretch_max']}  over {p['n_over_limit']} of {p['n_edges']}")
    print('worst edge xyz m:', out['worst_stretch_edge_xyz_m'])
    print('seam gap by stitch (mm):')
    for s in out['seam_gap_by_stitch']:
        print(f"  stitch {s['stitch']:2d} {s['panel_1']:>16s} - {s['panel_2']:<16s} "
              f"max {s['gap_max_mm']:6.2f}  median {s['gap_median_mm']:5.2f}  n {s['n_pairs']}")


if __name__ == '__main__':
    main()
