"""Blender 봉제·착장 (명세서 6장 "사용자별 착장과 원본 렌더").

export_panels.py가 만든 drape_input.json과 설정 파일(configs/drape_vN.json)을 읽어
  1) 떨어진 패널 + 봉제선 loose edge로 옷 메시를 만들고
  2) 몸에 충돌, 옷에 천(봉제 스프링, 자기 충돌)을 설정한 뒤
  3) 처음 sew_frames 동안은 중력 없이 봉제하고, 이후 중력을 켜 안정화한다.
  4) metrics.py로 안정·침투·신장·NaN을 검사하고 (기준값은 설정 thresholds 블록)
  5) .blend, 옷 OBJ, 착장 좌표(.npy), 정면 확인 PNG, 지표 JSON을 저장한다.

시뮬레이션·재질·충돌 값은 모두 설정 파일에서 읽는다. 이 파일에 수치 상수를 두지 않는다.

사용:
    blender --background --python blender_drape.py -- <drape_input.json> <out_dir> <config.json> [max_frames]
"""
import json
import math
import sys
import time
from pathlib import Path

import bpy
import numpy as np
from mathutils.bvhtree import BVHTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics  # noqa: E402
import weld  # noqa: E402
from drape_config import load_config, sha256_file  # noqa: E402


def parse_args():
    argv = sys.argv[sys.argv.index('--') + 1:]
    in_path, out_dir, cfg_path = Path(argv[0]).resolve(), Path(argv[1]).resolve(), Path(argv[2]).resolve()
    max_frames = int(argv[3]) if len(argv) > 3 else None
    return in_path, out_dir, cfg_path, max_frames


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def make_mesh(name, verts, faces, edges=()):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, list(edges), faces)
    mesh.validate()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def setup_body(body, cfg):
    obj = make_mesh('body', body['vertices'], body['faces'])
    obj.modifiers.new('Collision', 'COLLISION')
    bc = cfg['body_collision']
    obj.collision.thickness_outer = bc['thickness_outer']
    obj.collision.cloth_friction = bc['cloth_friction']
    obj.color = tuple(cfg['preview']['body_rgba'])
    return obj


def setup_garment(g, cfg, max_frames):
    pairs = [(a, b) for a, b, _ in g['stitch_pairs']]
    obj = make_mesh('garment', g['vertices'], g['faces'], pairs)
    obj.data.shade_smooth()
    obj.color = tuple(cfg['preview']['garment_rgba'])
    add_cloth(obj, cfg, 1, max_frames, phase_mass(cfg, g, len(g['vertices']), welded=False))
    return obj


def vertex_mass(block, g, n_sim_verts):
    """Blender cloth mass는 정점당 질량(kg)이다. 설정 블록(cloth 또는 weld)에 areal_density_kg_m2가 있으면
    패턴 면적 x 면밀도 / 시뮬레이션 정점 수로 정한다(옷 전체 질량이 메시 해상도·용접과 무관해진다).
    없으면 block['mass'](정점당 값)를 그대로 쓴다."""
    if 'areal_density_kg_m2' not in block:
        return block['mass']
    tris, _ = metrics.triangles(g['faces'])
    area = float(metrics.tri_areas(np.asarray(g['vertices']), tris).sum())
    return block['areal_density_kg_m2'] * area / n_sim_verts


def phase_mass(cfg, g, n_sim_verts, welded):
    """봉제 단계는 cloth 블록, 용접 뒤 단계는 weld 블록에 질량 설정이 있으면 그것을 쓴다."""
    w = cfg.get('weld', {})
    if welded and ('areal_density_kg_m2' in w or 'mass' in w):
        return vertex_mass(w, g, n_sim_verts)
    return vertex_mass(cfg['cloth'], g, n_sim_verts)


def add_cloth(obj, cfg, frame_start, max_frames, mass_kg, rest_shape_key=None):
    cloth = obj.modifiers.new('Cloth', 'CLOTH')
    s = cloth.settings
    cc = cfg['cloth']
    s.quality = cc['quality']
    s.mass = mass_kg  # 정점당 질량
    s.air_damping = cc['air_damping']
    s.tension_stiffness = cc['tension_stiffness']
    s.compression_stiffness = cc['compression_stiffness']
    s.shear_stiffness = cc['shear_stiffness']
    s.bending_stiffness = cc['bending_stiffness']
    s.use_sewing_springs = cc['use_sewing_springs']
    s.sewing_force_max = cc['sewing_force_max']  # 0 = 제한 없음
    # 설정에 있을 때만 바꾸는 값(drape_v0에는 없음 -> Blender 5.2.3 기본값 사용).
    # tension_stiffness_max: 구조 강성 정점 그룹이 없을 때 봉제 스프링이 이 값을 쓴다(C2 E04에서 확인).
    for key in ('tension_stiffness_max', 'tension_damping', 'shrink_min'):
        if key in cc:
            setattr(s, key, cc[key])

    c = cloth.collision_settings
    col = cfg['cloth_collision']
    c.collision_quality = col['collision_quality']
    c.distance_min = col['distance_min']
    c.use_self_collision = col['use_self_collision']
    c.self_distance_min = col['self_distance_min']
    c.self_friction = col['self_friction']

    if rest_shape_key is not None:
        s.rest_shape_key = rest_shape_key

    cloth.point_cache.frame_start = frame_start
    cloth.point_cache.frame_end = max_frames

    sch = cfg['schedule']
    ew = s.effector_weights
    ew.gravity = 0.0
    ew.keyframe_insert('gravity', frame=1)
    ew.keyframe_insert('gravity', frame=sch['sew_frames'])
    ew.gravity = 1.0
    ew.keyframe_insert('gravity', frame=sch['sew_frames'] + sch['gravity_ramp_frames'])
    return cloth


def build_welded_garment(g, co, cfg, frame, max_frames):
    """봉제 쌍을 합친 메시를 만든다. 시작 형상 = 용접 시점 좌표(쌍 평균),
    휴지 형상(shape key) = 원래 패턴 변 길이에 맞춘 이완 형상. 반환: (객체, 원래->용접 정점 대응, 기록)."""
    wcfg = cfg['weld']
    rest = np.asarray(g['vertices'])
    tris, _ = metrics.triangles(g['faces'])
    vmap, n = weld.weld_map(len(rest), g['stitch_pairs'])
    wco, wtris, n_deg = weld.weld_mesh(co, tris, vmap, n)
    edges, L0 = weld.target_lengths(rest, tris, vmap)
    t0 = time.time()
    xrest = weld.relax_to_lengths(wco, edges, L0, wcfg['relax_iterations'], wcfg['relax_step'])
    record = {
        'frame': frame,
        'n_welded_verts': int(n),
        'n_degenerate_faces_dropped': n_deg,
        'rest_length_ratio_if_weld_time_shape': weld.length_ratio_stats(wco, edges, L0),
        'rest_length_ratio_used': weld.length_ratio_stats(xrest, edges, L0),
        'relax_seconds': round(time.time() - t0, 1),
    }
    obj = make_mesh('garment_welded', wco.tolist(), wtris.tolist())
    obj.data.shade_smooth()
    obj.color = tuple(cfg['preview']['garment_rgba'])
    obj.shape_key_add(name='Basis', from_mix=False)
    key = obj.shape_key_add(name='pattern_rest', from_mix=False)
    key.data.foreach_set('co', xrest.astype(np.float32).ravel())
    key.value = 0.0
    add_cloth(obj, cfg, frame, max_frames, phase_mass(cfg, g, n, welded=True), rest_shape_key=key)
    return obj, vmap, record


def evaluated_coords(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    me = obj.evaluated_get(dg).to_mesh()
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get('co', co)
    obj.evaluated_get(dg).to_mesh_clear()
    return co.reshape(-1, 3) @ np.asarray(obj.matrix_world)[:3, :3].T + np.asarray(obj.matrix_world)[:3, 3]


def simulate(garment, cfg, max_frames, g):
    """시뮬레이션. 프레임별 진단(최대 신장, 최대 봉제 벌어짐)은 기록만 하고 동작에 영향을 주지 않는다."""
    th, sch = cfg['thresholds'], cfg['schedule']
    window = th['stable_window_frames']
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, max_frames
    rest = np.asarray(g['vertices'])
    tris, _ = metrics.triangles(g['faces'])
    pairs = np.asarray(g['stitch_pairs'], dtype=int)
    wcfg = cfg.get('weld', {'enabled': False})
    weld_rec = {'enabled': bool(wcfg['enabled'])}
    vmap = None
    co_ref, ref_frame = None, None  # 중력 반응 진단 기준(용접 직후, 용접 없으면 봉제 끝 프레임)
    prev = None
    disp_hist, diag = [], {'stretch_max': [], 'seam_gap_max_m': []}
    t0 = time.time()
    last = 1
    for f in range(1, max_frames + 1):
        scene.frame_set(f)
        co = evaluated_coords(garment)
        if vmap is not None:
            co = co[vmap]  # 용접 메시 -> 원래 정점 번호로 되돌려 같은 기준으로 잰다
        if wcfg['enabled'] and vmap is None and f == wcfg['at_frame']:
            pre = metrics.seam_gaps(co, pairs)
            weld_rec['preweld_gaps_m'] = pre.tolist()
            if pre.max() > th['seam_gap_preweld_max_m']:
                # 용접 전 기준 초과: 용접으로 감추지 않고 여기서 멈춘다 -> DRAPE_INVALID
                weld_rec['skipped_reason'] = 'preweld seam gap over threshold'
                print(f'weld skipped at frame {f}: preweld gap max {pre.max() * 1000:.2f} mm', flush=True)
                prev, last = co, f
                break
            welded, vmap, rec = build_welded_garment(g, co, cfg, f, max_frames)
            weld_rec.update(rec)
            bpy.data.objects.remove(garment, do_unlink=True)
            garment = welded
            scene.frame_set(f)
            co_ref, ref_frame = evaluated_coords(garment)[vmap], f
            print(f'welded at frame {f}: preweld gap max {pre.max() * 1000:.2f} mm, '
                  f'rest length ratio {rec["rest_length_ratio_used"]}', flush=True)
        if co_ref is None and not wcfg['enabled'] and f == sch['sew_frames']:
            co_ref, ref_frame = co, f
        if prev is not None:
            disp_hist.append(metrics.frame_displacement_median(prev, co))
        prev = co
        last = f
        diag['stretch_max'].append(float(metrics.edge_stretch(rest, co, tris)[1].max()))
        diag['seam_gap_max_m'].append(float(metrics.seam_gaps(co, pairs).max()))
        if f % 10 == 0:
            print(f'frame {f}: median disp {disp_hist[-1] * 1000:.2f} mm, stretch max {diag["stretch_max"][-1]:.3f}, '
                  f'seam gap max {diag["seam_gap_max_m"][-1] * 1000:.1f} mm, {time.time() - t0:.1f} s', flush=True)
        if f >= sch['sew_frames'] + sch['min_frames_after_sew'] and len(disp_hist) >= window:
            if metrics.stable_median(disp_hist, window) <= th['stable_median_m_per_frame']:
                break
    weld_rec['_co_ref'], weld_rec['_ref_frame'] = co_ref, ref_frame
    return prev, disp_hist, last, time.time() - t0, diag, garment, weld_rec


def body_bvh(body):
    return BVHTree.FromPolygons([tuple(v) for v in body['vertices']], [tuple(fc) for fc in body['faces']])


def signed_distances(bvh, points):
    """점 -> 몸 표면 최근접점 부호 거리(법선 방향 +). 최근접점을 못 찾으면 NaN."""
    out = np.full(len(points), np.nan)
    for i, p in enumerate(points):
        loc, normal, _, _ = bvh.find_nearest(p)
        if loc is None:
            continue
        out[i] = float(np.dot(p - np.asarray(loc), np.asarray(normal)))
    return out


def setup_preview(cfg, out_png):
    pv = cfg['preview']
    scene = bpy.context.scene
    scene.render.engine = pv['engine']
    scene.display.shading.light = 'STUDIO'
    scene.display.shading.color_type = 'OBJECT'
    scene.render.resolution_x, scene.render.resolution_y = pv['resolution']
    scene.render.film_transparent = False
    scene.world = bpy.data.worlds.new('world')
    scene.world.color = tuple(pv['background_rgb'])

    cam_data = bpy.data.cameras.new('front_cam')
    cam_data.lens = pv['camera_lens_mm']
    cam = bpy.data.objects.new('front_cam', cam_data)
    scene.collection.objects.link(cam)
    cam.location = tuple(pv['camera_location_m'])  # 정면 -Y 쪽에서 +Y 방향을 본다
    cam.rotation_euler = tuple(math.radians(a) for a in pv['camera_rotation_deg'])
    scene.camera = cam
    scene.render.filepath = str(out_png)


def main():
    in_path, out_dir, cfg_path, max_frames_arg = parse_args()
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg, cfg_rec = load_config(cfg_path)
    th = cfg['thresholds']
    max_frames = th['max_frames'] if max_frames_arg is None else max_frames_arg
    if max_frames > th['max_frames']:
        raise ValueError(f'max_frames {max_frames} > thresholds.max_frames {th["max_frames"]}')
    data = json.loads(in_path.read_text(encoding='utf-8'))
    g, body = data['garment'], data['body']

    reset_scene()
    setup_body(body, cfg)
    garment = setup_garment(g, cfg, max_frames)

    # 초기 배치 침투(기록만). 오류 처리 기준은 아직 없다 -> C1 리뷰 '팀 합의 필요'.
    bvh = body_bvh(body)
    tris, _ = metrics.triangles(g['faces'])
    rest = np.asarray(g['vertices'])
    init_sd = signed_distances(bvh, metrics.face_centers(rest, tris))
    init_ratio, _, _ = metrics.penetration(metrics.tri_areas(rest, tris), init_sd,
                                           th['penetration_depth_m'], body['is_watertight'])

    co, disp_hist, last_frame, sim_s, diag, garment, weld_rec = simulate(garment, cfg, max_frames, g)
    sd = signed_distances(bvh, metrics.face_centers(co, tris))
    preweld = weld_rec.pop('preweld_gaps_m', None)
    co_ref, ref_frame = weld_rec.pop('_co_ref'), weld_rec.pop('_ref_frame')
    result = metrics.evaluate(rest, g['faces'], co, g['stitch_pairs'], disp_hist, sd,
                              body['is_watertight'], th, preweld_gaps=preweld)
    if weld_rec['enabled']:
        # [C 제안] 용접 휴지 형상의 패턴 대비 변 길이 편차 한도(설정에 있을 때만)
        used = weld_rec.get('rest_length_ratio_used')
        lim = th.get('weld_rest_length_ratio_dev_max')
        if lim is not None:
            result['weld_rest_ok'] = used is not None and max(1 - used['min'], used['max'] - 1) <= lim
            result['drape_ok'] = result['drape_ok'] and result['weld_rest_ok']
        if 'n_welded_verts' not in weld_rec:
            result['drape_ok'] = False
    failed = [k for k in ('stable_ok', 'penetration_ok', 'stretch_ok', 'seam_closed_ok', 'weld_rest_ok')
              if result.get(k) is False]
    if result['nan']:
        failed.append('nan')
    n_sim_verts = weld_rec.get('n_welded_verts', len(co))
    result.update({
        'error_code': None if result['drape_ok'] else 'DRAPE_INVALID',
        'failed_checks': failed,
        'initial_penetration_area_ratio': round(init_ratio, 4),
        'frames': last_frame,
        'max_frames': max_frames,
        'max_frames_overridden': max_frames_arg is not None,
        'sim_seconds': round(sim_s, 1),
        'blender': bpy.app.version_string,
        'n_garment_verts': len(co),
        'n_sim_verts': n_sim_verts,
        'vertex_mass_kg': phase_mass(cfg, g, n_sim_verts, welded='n_welded_verts' in weld_rec),
        'garment_total_mass_kg': round(phase_mass(cfg, g, n_sim_verts, 'n_welded_verts' in weld_rec)
                                       * n_sim_verts, 4),  # 마지막 단계 기준
        'weld': weld_rec,
        'gravity_response': None if co_ref is None else dict(
            reference_frame=ref_frame, note='진단용 기록. 판정에 쓰지 않음',
            **metrics.gravity_response(co_ref, co, metrics.free_edge_vertices(g['panels'], 'hem'),
                                       metrics.free_edge_vertices(g['panels'], 'cuff'))),
        'config': cfg_rec,
        'drape_input_sha256': sha256_file(in_path),
        'body_is_watertight': body['is_watertight'],
    })
    print(json.dumps(result, indent=2), flush=True)

    np.save(out_dir / 'garment_draped_co.npy', co)
    if co_ref is not None:
        np.save(out_dir / 'garment_ref_co.npy', co_ref)
    (out_dir / 'drape_trace.json').write_text(json.dumps(
        {'disp_median_m_per_frame': disp_hist, 'signed_dist_m': sd.tolist(),
         'stretch_max_per_frame': diag['stretch_max'], 'seam_gap_max_m_per_frame': diag['seam_gap_max_m'],
         'preweld_gaps_m': preweld}))

    setup_preview(cfg, out_dir / 'front.png')
    bpy.ops.render.render(write_still=True)

    bpy.ops.wm.save_as_mainfile(filepath=str(out_dir / 'drape.blend'))
    bpy.ops.object.select_all(action='DESELECT')
    garment.select_set(True)
    bpy.context.view_layer.objects.active = garment
    bpy.ops.wm.obj_export(filepath=str(out_dir / 'garment_draped.obj'), export_selected_objects=True,
                          apply_modifiers=True, forward_axis='Y', up_axis='Z', export_materials=False)
    (out_dir / 'drape_metrics.json').write_text(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
