"""GarmentCode 패턴을 Blender 봉제용으로 내보낸다.

BoxMesh는 봉제선 정점을 하나로 합쳐 저장하지만, Blender의 sewing은
떨어진 패널 + 봉제선 정점 쌍(loose edge)이 필요하다. 그래서 합치기 전
단계(load_panels, gen_panel_meshes)까지만 실행해 패널과 정점 쌍을 꺼낸다.

좌표는 coords.py(명세서 6장 규약)로 바꾼다. 의류 실측은 바꾸지 않는다(R06): 축 회전과 cm->m만 한다.

사용 (cwd=C:\\dev\\GarmentCode, PYTHONPATH=C:\\dev\\GarmentCode, 가상환경 garmentcode):
    python export_panels.py <spec.json> <body.obj> <out_dir> --config <drape_vN.json>
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import trimesh
from pygarment.meshgen.boxmeshgen import BoxMesh, Edge

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coords import BODY_SCALE, GARMENT_SCALE, to_spec, transform_record  # noqa: E402
from drape_config import load_config, sha256_file  # noqa: E402

# GarmentCode 2.0.2 버그: res > 1.0에서 짧은 모서리 경고를 출력할 때 없는 Edge.name을 참조한다.
# 원본 코드는 고치지 않고 여기서 클래스 속성만 채운다.
Edge.name = 'Edge'


def collect_stitch_pairs(box, offsets):
    """봉제선마다 양쪽 정점을 짝지어 (전역 a, 전역 b, stitch_id) 목록으로."""
    pairs = []
    for sid, stitch in enumerate(box.stitches):
        r1, r2 = box._swap_stitch_ranges(stitch)
        if len(r1) != len(r2):
            raise ValueError(f'stitch {sid}: vertex count mismatch {len(r1)} != {len(r2)}')
        o1, o2 = offsets[stitch.panel_1], offsets[stitch.panel_2]
        for a, b in zip(r1, r2):
            if stitch.panel_1 == stitch.panel_2 and a == b:
                continue
            pairs.append([int(o1 + a), int(o2 + b), sid])
    return pairs


def export_garment(spec_path, res):
    box = BoxMesh(str(spec_path), res)
    box.load_panels()
    box.gen_panel_meshes()

    verts, faces, panel_of_vert, panels, uv = [], [], [], [], []
    offsets = {}
    offset = 0
    for name in box.panelNames:
        panel = box.panels[name]
        v = panel.rot_trans_panel(panel.panel_vertices)
        f = np.asarray(panel.panel_faces, dtype=int)
        offsets[name] = offset
        verts.append(v)
        faces.append(f + offset)
        uv.append(np.asarray(panel.panel_vertices, dtype=float)[:, :2])  # 패널 안 2D 좌표(cm), 착장 후 측정용
        panel_of_vert += [len(panels)] * len(v)
        # 모서리별 정점(전역 번호)과 라벨: 밑단·소매단 같은 자유 모서리를 찾는 데 쓴다(시뮬레이션에는 안 씀)
        edges = [{'index': i, 'label': e.label, 'vertices': [int(offset + k) for k in e.vertex_range]}
                 for i, e in enumerate(panel.edges)]
        panels.append({'name': name, 'n_verts': len(v), 'n_faces': len(f), 'edges': edges})
        offset += len(v)
    stitched = {(st.panel_1, st.edge_1) for st in box.stitches} | {(st.panel_2, st.edge_2) for st in box.stitches}
    for p in panels:
        for e in p['edges']:
            e['stitched'] = (p['name'], e['index']) in stitched

    stitch_pairs = collect_stitch_pairs(box, offsets)
    box.uv_cm = np.concatenate(uv)
    verts = to_spec(np.concatenate(verts), GARMENT_SCALE)
    faces = np.concatenate(faces)
    return box, verts, faces, panel_of_vert, panels, stitch_pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('spec')
    ap.add_argument('body')
    ap.add_argument('out_dir')
    ap.add_argument('--config', required=True)
    args = ap.parse_args()

    cfg, cfg_rec = load_config(args.config)
    res = float(cfg['export']['mesh_resolution_cm'])
    spec, body_path = Path(args.spec).resolve(), Path(args.body).resolve()
    out = Path(args.out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    box, gv, gf, panel_of_vert, panels, pairs = export_garment(spec, res)

    body = trimesh.load(str(body_path), process=False, force='mesh')
    bv = to_spec(body.vertices, BODY_SCALE)

    data = {
        'units': 'm',
        'axes': {'up': '+Z', 'front': '-Y'},
        'source': {'garment_spec': str(spec), 'garment_spec_sha256': sha256_file(spec),
                   'body': str(body_path), 'body_sha256': sha256_file(body_path),
                   'body_kind': 'GarmentCode reference body (not D body T07)',
                   'mesh_res_cm': res},
        'config': cfg_rec,
        'transform_from_garmentcode': transform_record(),
        'garment': {
            'vertices': gv.round(6).tolist(),
            'faces': gf.tolist(),
            'panel_of_vertex': panel_of_vert,
            'panels': panels,
            'n_stitches': len(box.stitches),
            'stitch_pairs': pairs,
            'uv_cm': box.uv_cm.round(6).tolist(),
        },
        'body': {
            'vertices': bv.round(6).tolist(),
            'faces': np.asarray(body.faces).tolist(),
            'is_watertight': bool(body.is_watertight),
            'is_winding_consistent': bool(body.is_winding_consistent),
        },
    }
    with open(out / 'drape_input.json', 'w', encoding='utf-8') as f:
        json.dump(data, f)

    print(f'garment: {len(gv)} verts, {len(gf)} faces, {len(panels)} panels, '
          f'{len(box.stitches)} stitches, {len(pairs)} stitch pairs')
    print(f'garment bounds m: {gv.min(0).round(3).tolist()} {gv.max(0).round(3).tolist()}')
    print(f'body: {len(bv)} verts, watertight {body.is_watertight}, '
          f'bounds m: {bv.min(0).round(3).tolist()} {bv.max(0).round(3).tolist()}')
    print(f'saved {out / "drape_input.json"}')


if __name__ == '__main__':
    main()
