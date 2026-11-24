"""Blender 정면 렌더 (C5, 명세서 6장 "렌더 항목").

착장 결과 .blend(C4)를 열고
  1) 몸·옷 객체를 이름으로 찾고(config `objects`), 옷은 현재 프레임의 착장 형상을 고정 메시로
     만든다.
  2) 옷 재질(Principled BSDF, 기본 색 = 의류 color_hex, sRGB → 선형)과 몸 재질(회색 마네킹)을
     입힌다.
  3) 정면 고정 원근 카메라를 프레이밍 규칙(render_framing.py)으로 놓고, 조명·배경·색 관리를
     설정한다.
  4) 한 번 렌더해 정지 프레임(render_still.png)을 만들고, 같은 카메라의 Cycles 발광 패스(1샘플)로
     옷 마스크(garment_mask.png, 옷 255·나머지 0, 8비트 흑백)를 만든다. GPU는 쓰지 않는다.
  5) 정지 프레임 121장을 Blender 내장 FFmpeg로 무음 H264 MP4(render_clip.mp4)로 만든다.
     몸과 카메라가 정지해 있어 모든 프레임이 같다(정지 클립).
  6) 만든 MP4를 MovieClip으로 다시 열어 크기·fps·프레임 수를 기록한다.
  7) render_metrics.json(지표)과 render_settings.json(쓴 카메라·조명·재질·엔진·색 관리)을 쓴다.

판정(클립 크기·마스크 비율·머리 정점)은 어댑터(renderer.py)가 한다. 이 스크립트는 객체를 못 찾으면
status FAILED(failure=objects_not_found)를 쓰고 종료 코드 3으로 끝난다.

사용:
    blender --background <drape.blend> --python-exit-code 1 \
        --python fitting/adapters/blender/blender_render.py -- <render_job.json>

render_job.json: {"out_dir", "config", "garment_color_hex",
                  "background_srgb_hex"·"body_srgb_hex"(선택: 어댑터가 옷 색으로 고른
                  배경·마네킹 색, 없으면 설정 값),
                  "engine_override"(선택: "CYCLES"·"BLENDER_EEVEE", 엔진 비교용)}
"""

import hashlib
import json
import math
import sys
import time
from pathlib import Path

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

# blender_drape.py와 같은 이유로 저장소 루트를 import 경로에 넣는다
# (Blender Python에는 저장소가 설치되어 있지 않다).
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from fitting.adapters.blender import render_color as rc  # noqa: E402
from fitting.adapters.blender import render_framing as rf  # noqa: E402

STILL, MASK, CLIP = "render_still.png", "garment_mask.png", "render_clip.mp4"
METRICS, SETTINGS = "render_metrics.json", "render_settings.json"
EXIT_OBJECTS = 3
MASK_ERODE_PX = 5  # 색 중앙값을 잴 때 마스크 안쪽으로 줄이는 픽셀(명세서 7장 색 비교와 같다)


def log(msg):
    print(f"[render] {msg}", flush=True)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def world_co(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    co = np.empty(len(me.vertices) * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    mw = np.array(ev.matrix_world)
    ev.to_mesh_clear()
    return co @ mw[:3, :3].T + mw[:3, 3]


def find_objects(cfg):
    names = cfg["objects"]
    body = bpy.data.objects.get(names["body"])
    garment = None
    for name in names["garment"]:
        obj = bpy.data.objects.get(name)
        if obj is not None and obj.type == "MESH":
            garment = obj
            break
    if body is None or body.type != "MESH":
        body = None
    return body, garment


def freeze_garment(garment):
    """현재 프레임의 착장 형상(천 모디파이어 적용)을 고정 메시 객체로 만든다.

    원래 객체는 렌더에서 뺀다.
    """
    dg = bpy.context.evaluated_depsgraph_get()
    ev = garment.evaluated_get(dg)
    mesh = bpy.data.meshes.new_from_object(ev)
    obj = bpy.data.objects.new("garment_render", mesh)
    obj.matrix_world = garment.matrix_world.copy()
    bpy.context.scene.collection.objects.link(obj)
    for poly in mesh.polygons:
        poly.use_smooth = True
    base = np.empty(len(garment.data.vertices) * 3)
    garment.data.vertices.foreach_get("co", base)
    frozen = np.empty(len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", frozen)
    moved = None
    if base.size == frozen.size:
        moved = float(np.median(np.linalg.norm((frozen - base).reshape(-1, 3), axis=1)) * 1000)
    garment.hide_render = True
    return obj, moved


def principled(name, srgb_hex, roughness, spec_level, sheen=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    lin = rc.hex_to_linear(srgb_hex)
    bsdf.inputs["Base Color"].default_value = (*lin, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Specular IOR Level"].default_value = spec_level
    bsdf.inputs["Sheen Weight"].default_value = sheen
    return mat, lin


def set_material(obj, mat):
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    for poly in obj.data.polygons:
        poly.material_index = 0


def setup_world(cfg, background_hex=None):
    w = cfg["world"]
    background_hex = background_hex or w["background_srgb_hex"]
    world = bpy.data.worlds.new("render_world")
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    path = nt.nodes.new("ShaderNodeLightPath")
    bg_cam = nt.nodes.new("ShaderNodeBackground")
    bg_amb = nt.nodes.new("ShaderNodeBackground")
    mix = nt.nodes.new("ShaderNodeMixShader")
    bg_cam.inputs["Color"].default_value = (*rc.hex_to_linear(background_hex), 1.0)
    bg_cam.inputs["Strength"].default_value = 1.0
    bg_amb.inputs["Color"].default_value = (*rc.hex_to_linear(w["ambient_srgb_hex"]), 1.0)
    bg_amb.inputs["Strength"].default_value = w["ambient_strength"]
    nt.links.new(path.outputs["Is Camera Ray"], mix.inputs["Fac"])
    nt.links.new(bg_amb.outputs["Background"], mix.inputs[1])
    nt.links.new(bg_cam.outputs["Background"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    bpy.context.scene.world = world
    return world


def setup_lights(cfg, center):
    rec = []
    for item in cfg["lights"]["items"]:
        data = bpy.data.lights.new(f"render_{item['name']}", item["type"])
        data.energy = item["energy_w"]
        data.color = rc.hex_to_linear(item["color_srgb_hex"])
        if item["type"] == "AREA":
            data.size = item["size_m"]
        obj = bpy.data.objects.new(f"render_{item['name']}", data)
        bpy.context.scene.collection.objects.link(obj)
        loc = Vector(center) + Vector(item["offset_m"])
        obj.location = loc
        obj.rotation_euler = (Vector(center) - loc).to_track_quat("-Z", "Y").to_euler()
        rec.append({**item, "location_m": [round(v, 4) for v in loc]})
    return rec


def setup_camera(cfg, fr):
    cam_cfg = cfg["camera"]
    data = bpy.data.cameras.new("render_cam")
    data.type = "PERSP"
    data.lens = cam_cfg["lens_mm"]
    data.sensor_fit = cam_cfg["sensor_fit"]
    data.sensor_height = cam_cfg["sensor_height_mm"]
    data.shift_x, data.shift_y = fr.shift_x, fr.shift_y
    data.clip_start, data.clip_end = cam_cfg["clip_start_m"], cam_cfg["clip_end_m"]
    cam = bpy.data.objects.new("render_cam", data)
    bpy.context.scene.collection.objects.link(cam)
    cam.location = fr.camera_location_m
    cam.rotation_euler = (math.radians(90.0), 0.0, 0.0)  # -y 쪽에서 +y 방향을 수평으로 본다
    bpy.context.scene.camera = cam
    return cam


def color_management(scene, cm):
    scene.display_settings.display_device = cm["display_device"]
    scene.view_settings.view_transform = cm["view_transform"]
    scene.view_settings.look = cm["look"]
    scene.view_settings.exposure = cm["exposure"]
    scene.view_settings.gamma = cm["gamma"]
    scene.view_settings.use_curve_mapping = False
    scene.sequencer_colorspace_settings.name = cm["sequencer_colorspace"]


def no_metadata(scene):
    """PNG에 날짜·시간·렌더 시간 같은 메타데이터를 넣지 않는다(재현성: 같은 입력이면 같은 파일)."""
    r = scene.render
    for p in r.bl_rna.properties:
        if p.identifier.startswith("use_stamp") and not p.is_readonly:
            setattr(r, p.identifier, False)


def png_settings(scene, color_mode):
    im = scene.render.image_settings
    if hasattr(im, "media_type"):
        im.media_type = "IMAGE"
    im.file_format = "PNG"
    im.color_mode = color_mode
    im.color_depth = "8"
    im.compression = 15


def setup_engine(scene, eng, override):
    name = override or eng["name"]
    scene.render.engine = name
    if name == "CYCLES":
        c = scene.cycles
        c.device = eng["device"]
        c.samples = eng["samples"]
        c.seed = eng["seed"]
        c.use_animated_seed = False
        c.use_adaptive_sampling = eng["use_adaptive_sampling"]
        c.use_denoising = eng["denoise"]
        c.denoiser = eng["denoiser"]
        c.max_bounces = eng["max_bounces"]
        c.filter_width = eng["filter_width_px"]
        samples = eng["samples"]
    else:
        scene.eevee.taa_render_samples = eng["eevee_taa_render_samples"]
        samples = eng["eevee_taa_render_samples"]
    scene.render.filter_size = eng["filter_width_px"]
    return name, samples


def read_pixels(path):
    img = bpy.data.images.load(str(path), check_existing=False)
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return px.reshape(h, w, 4)  # 아래 줄부터


def resave_png(src, dst):
    """8비트 PNG의 픽셀만 메타데이터 없이 다시 쓴다(색 변환 없음). src는 지운다."""
    img = bpy.data.images.load(str(src), check_existing=False)
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    new = bpy.data.images.new("still_out", w, h, alpha=False)
    new.pixels.foreach_set(px)
    new.filepath_raw = str(dst)
    new.file_format = "PNG"
    new.save()
    bpy.data.images.remove(new)
    Path(src).unlink()


def erode(mask, k):
    out = mask.copy()
    for s in range(1, k + 1):
        out[s:, :] &= mask[:-s, :]
        out[:-s, :] &= mask[s:, :]
        out[:, s:] &= mask[:, :-s]
        out[:, :-s] &= mask[:, s:]
    out[:k, :] = out[-k:, :] = False
    out[:, :k] = out[:, -k:] = False
    return out


def emission(name, value):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (value, value, value, 1.0)
    em.inputs["Strength"].default_value = 1.0
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    return mat


def render_mask(scene, garment_obj, body_obj, raw_path, mask_path):
    """옷 마스크 패스(Cycles CPU): 옷 흰색 발광, 몸·배경 검정, 1샘플, 픽셀 필터 최소(가운데 한 점).

    Workbench(GPU)는 쓰지 않는다. 이 PC(Intel 내장 그래픽)에서 Workbench 패스 뒤 Blender가 끝날
    때 그래픽 드라이버에서 비정상 종료한 일이 있었다(docs/render.md). 임계값 0.5로 0/255.
    """
    scene.render.engine = "CYCLES"
    c = scene.cycles
    c.device = "CPU"
    c.samples = 1
    c.seed = 0
    c.use_animated_seed = False
    c.use_adaptive_sampling = False
    c.use_denoising = False
    c.max_bounces = 0
    c.filter_width = 0.01
    world = bpy.data.worlds.new("mask_world")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    scene.world = world
    set_material(garment_obj, emission("mask_garment", 1.0))
    set_material(body_obj, emission("mask_body", 0.0))
    png_settings(scene, "RGB")
    scene.render.filepath = str(raw_path)
    bpy.ops.render.render(write_still=True)
    px = read_pixels(raw_path)
    m = px[:, :, 0] > 0.5
    h, w = m.shape
    img = bpy.data.images.new("garment_mask", w, h, alpha=False)
    v = m.astype(np.float32)
    img.pixels.foreach_set(np.stack([v, v, v, np.ones_like(v)], axis=-1).ravel())
    png_settings(scene, "BW")
    img.save_render(str(mask_path), scene=scene)
    bpy.data.images.remove(img)
    Path(raw_path).unlink()
    return m


def make_clip(cfg, still_path, clip_path):
    """정지 프레임 하나로 121프레임 무음 H264 MP4. 시퀀서 이미지 스트립을 늘려 쓴다."""
    c = cfg["clip"]
    scene = bpy.data.scenes.new("render_clip")
    scene.render.resolution_x, scene.render.resolution_y = c["width"], c["height"]
    scene.render.resolution_percentage = 100
    scene.render.fps, scene.render.fps_base = c["fps"], 1.0
    scene.frame_start, scene.frame_end = 1, c["frames"]
    color_management(scene, cfg["color_management"])
    no_metadata(scene)
    ed = scene.sequence_editor_create()
    strips = ed.strips if hasattr(ed, "strips") else ed.sequences
    strip = strips.new_image("still", str(still_path), 1, 1)
    strip.colorspace_settings.name = cfg["color_management"]["sequencer_colorspace"]
    strip.frame_final_duration = c["frames"]
    r = scene.render
    r.use_sequencer, r.use_compositing = True, False
    im = r.image_settings
    if hasattr(im, "media_type"):
        im.media_type = "VIDEO"
    im.file_format = "FFMPEG"
    im.color_mode = "RGB"
    ff = r.ffmpeg
    ff.format, ff.codec, ff.audio_codec = c["container"], c["codec"], "NONE"
    ff.constant_rate_factor = c["constant_rate_factor"]
    ff.ffmpeg_preset = c["ffmpeg_preset"]
    ff.gopsize = c["gop_size"]
    ff.use_max_b_frames = False
    tmp = clip_path.parent / "clip_out_"
    r.filepath = str(tmp)
    r.use_file_extension = True
    bpy.ops.render.render(animation=True, scene=scene.name)
    made = sorted(clip_path.parent.glob("clip_out_*"))
    if not made:
        raise RuntimeError("FFmpeg 출력 파일이 없습니다")
    made[-1].replace(clip_path)
    clip = bpy.data.movieclips.load(str(clip_path))
    probe = {
        "width": int(clip.size[0]),
        "height": int(clip.size[1]),
        "fps": float(clip.fps),
        "frames": int(clip.frame_duration),
    }
    bpy.data.movieclips.remove(clip)
    return probe, {
        "container": ff.format,
        "codec": ff.codec,
        "audio_codec": ff.audio_codec,
        "constant_rate_factor": ff.constant_rate_factor,
        "ffmpeg_preset": ff.ffmpeg_preset,
        "gop_size": ff.gopsize,
    }


def main():
    t_all = time.time()
    job_path = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
    job = json.loads(job_path.read_text(encoding="utf-8"))
    out = Path(job["out_dir"]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    cfg_path = Path(job["config"]).resolve()
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    scene = bpy.context.scene
    metrics = {
        "status": "FAILED",
        "blender": bpy.app.version_string,
        "blend_file": bpy.data.filepath,
        "config": {"name": cfg["name"], "version": cfg["version"], "sha256": sha256_file(cfg_path)},
    }
    body, garment = find_objects(cfg)
    if body is None or garment is None:
        metrics.update(
            failure="objects_not_found",
            body_found=body is not None,
            garment_found=garment is not None,
            objects=[o.name for o in bpy.data.objects],
        )
        write_json(out / METRICS, metrics)
        log("objects not found")
        sys.exit(EXIT_OBJECTS)

    frame = scene.frame_current
    garment_obj, moved_mm = freeze_garment(garment)
    for obj in scene.objects:
        if obj not in (body, garment_obj) and obj.type in (
            "MESH",
            "LIGHT",
            "CURVE",
            "SURFACE",
            "META",
            "FONT",
        ):
            obj.hide_render = True
    g_co = world_co(garment_obj)
    b_co = world_co(body)

    c, fcfg = cfg["clip"], cfg["framing"]
    fr = rf.solve_framing(
        g_co,
        b_co,
        width=c["width"],
        height=c["height"],
        lens_mm=cfg["camera"]["lens_mm"],
        sensor_height_mm=cfg["camera"]["sensor_height_mm"],
        top_margin_cm=fcfg["top_margin_cm"],
        bottom_margin_cm=fcfg["bottom_margin_cm"],
        side_margin_cm=fcfg["side_margin_cm"],
        head_min_above_collar_cm=fcfg["head_min_above_collar_cm"],
        head_clearance_px=fcfg["head_clearance_px"],
        distance_search_m=tuple(cfg["camera"]["distance_search_m"]),
        iterations=cfg["camera"]["distance_search_iterations"],
    )
    cam = setup_camera(cfg, fr)

    # 재질
    gm = cfg["garment_material"]
    g_hex = job["garment_color_hex"]
    g_mat, g_lin = principled(
        "garment_render", g_hex, gm["roughness"], gm["specular_ior_level"], gm["sheen_weight"]
    )
    set_material(garment_obj, g_mat)
    bm = cfg["body_material"]
    body_hex = job.get("body_srgb_hex") or bm["base_srgb_hex"]
    b_mat, _ = principled("body_render", body_hex, bm["roughness"], bm["specular_ior_level"])
    set_material(body, b_mat)
    if bm.get("shade_smooth"):
        for poly in body.data.polygons:  # 보기만 바꾼다(정점은 그대로)
            poly.use_smooth = True

    center = (
        (g_co[:, 0].min() + g_co[:, 0].max()) / 2,
        (g_co[:, 1].min() + g_co[:, 1].max()) / 2,
        (g_co[:, 2].min() + g_co[:, 2].max()) / 2,
    )
    background_hex = job.get("background_srgb_hex") or cfg["world"]["background_srgb_hex"]
    setup_world(cfg, background_hex)
    lights = setup_lights(cfg, center)
    color_management(scene, cfg["color_management"])
    no_metadata(scene)
    scene.render.resolution_x, scene.render.resolution_y = c["width"], c["height"]
    scene.render.resolution_percentage = 100
    scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1.0
    scene.render.film_transparent = False
    scene.render.use_compositing = False
    scene.render.use_sequencer = False
    engine, samples = setup_engine(scene, cfg["engine"], job.get("engine_override"))

    # 정지 프레임. Cycles는 렌더 시간 같은 메타데이터를 PNG에 넣으므로 픽셀만 다시 저장한다(재현성).
    png_settings(scene, "RGB")
    still = out / STILL
    raw = out / "still_raw.png"
    scene.render.filepath = str(raw)
    t0 = time.time()
    bpy.ops.render.render(write_still=True)
    still_s = time.time() - t0
    log(f"still {engine} {still_s:.1f}s")
    resave_png(raw, still)

    # 마스크 (같은 카메라)
    t0 = time.time()
    mask = render_mask(scene, garment_obj, body, out / "mask_raw.png", out / MASK)
    mask_s = time.time() - t0

    # 머리 정점·옷 정점 화면 검사 (Blender 투영)
    head = rf.head_vertices(b_co, float(g_co[:, 2].max()), fcfg["head_min_above_collar_cm"] / 100.0)

    def in_frame(points):
        n = 0
        for p in points:
            v = world_to_camera_view(scene, cam, Vector(p))
            if 0.0 <= v.x <= 1.0 and 0.0 <= v.y <= 1.0 and v.z > 0.0:
                n += 1
        return n

    head_in = in_frame(head)
    garment_in = in_frame(g_co)

    # 옷 색: 마스크 안쪽 5픽셀 줄인 영역의 sRGB 중앙값
    px = read_pixels(still)
    rgb8 = np.round(px[:, :, :3] * 255).astype(np.uint8)
    inner = erode(mask, MASK_ERODE_PX)
    median_hex, de = "", ""
    if inner.any():
        med = np.median(px[:, :, :3][inner], axis=0)
        median_hex = rc.srgb_to_hex(tuple(float(v) for v in med))
        de = round(rc.delta_e_hex(median_hex, g_hex), 2)

    # 클립
    t0 = time.time()
    probe, ffmpeg_rec = make_clip(cfg, still, out / CLIP)
    clip_s = time.time() - t0
    log(f"clip {clip_s:.1f}s probe={probe}")

    h, w = mask.shape
    metrics.update(
        {
            "status": "SUCCEEDED",
            "engine": engine,
            "samples": samples,
            "still_seconds": round(still_s, 2),
            "mask_seconds": round(mask_s, 2),
            "clip_seconds": round(clip_s, 2),
            "blender_seconds": round(time.time() - t_all, 2),
            "width": w,
            "height": h,
            "clip_probe": probe,
            "static_clip": True,
            "static_note": "정지 클립, 모든 프레임 동일(한 번 렌더한 정지 프레임을 반복)",
            "garment_mask_ratio": round(float(mask.mean()), 6),
            "garment_mask_pixels": int(mask.sum()),
            "head_vertices": int(len(head)),
            "head_vertices_in_frame": head_in,
            "garment_vertices": int(len(g_co)),
            "garment_vertices_out_of_frame": int(len(g_co) - garment_in),
            "top_limited_by": fr.top_limited_by,
            "garment_median_hex": median_hex,
            "garment_delta_e00": de,
            "garment_object": garment.name,
            "drape_frame": frame,
            "garment_moved_from_rest_median_mm": None if moved_mm is None else round(moved_mm, 2),
            "still_pixel_sha256": hashlib.sha256(rgb8.tobytes()).hexdigest(),
            "still_file_sha256": sha256_file(still),
            "mask_file_sha256": sha256_file(out / MASK),
        }
    )
    settings = {
        "config": metrics["config"],
        "blender": bpy.app.version_string,
        "camera": {
            "type": "PERSP",
            "location_m": list(fr.camera_location_m),
            "rotation_deg": [90.0, 0.0, 0.0],
            "lens_mm": cam.data.lens,
            "sensor_fit": cam.data.sensor_fit,
            "sensor_height_mm": cam.data.sensor_height,
            "shift_x": cam.data.shift_x,
            "shift_y": cam.data.shift_y,
            "framing": fr.to_dict(),
            "framing_rule": cfg["framing"],
        },
        "lights": lights,
        "world": {**cfg["world"], "used_background_srgb_hex": background_hex},
        "garment_material": {
            "base_srgb_hex": g_hex,
            "base_linear": [round(v, 6) for v in g_lin],
            **gm,
        },
        "body_material": {**bm, "used_base_srgb_hex": body_hex},
        "engine": {
            "name": engine,
            "samples": samples,
            **({k: v for k, v in cfg["engine"].items() if not k.startswith("_")}),
        },
        "color_management": {
            "display_device": scene.display_settings.display_device,
            "view_transform": scene.view_settings.view_transform,
            "look": scene.view_settings.look,
            "exposure": scene.view_settings.exposure,
            "gamma": scene.view_settings.gamma,
        },
        "clip": {**{k: v for k, v in c.items() if not k.startswith("_")}, "ffmpeg": ffmpeg_rec},
        "mask": {
            "method": "cycles_cpu_emission_1spp_filter_0.01px",
            "threshold": 0.5,
            "format": "PNG 8-bit BW",
            "erode_px_for_color": MASK_ERODE_PX,
        },
    }
    write_json(out / SETTINGS, settings)
    write_json(out / METRICS, metrics)
    log("done")


if __name__ == "__main__":
    main()
