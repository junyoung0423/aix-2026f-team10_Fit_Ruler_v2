"""실제 Blender 렌더 1회 (#10, C5). 마커 blender, CI에서 빠짐. 로컬: pytest -m blender

착장 결과 대신 Blender로 작은 장면을 만든다(몸 = 원기둥 + 머리 구, 옷 = 원기둥 `garment_welded`).
렌더 어댑터로 클립·마스크·정지 프레임을 만들고, 클립을 cv2로 다시 열어 크기·fps·프레임 수를 본다.
1분 안팎 걸린다. Blender 경로: BLENDER_PATH > BLENDER_EXE > 기본 설치 경로.
"""

import os
import subprocess
from pathlib import Path

import pytest

from fitting.adapters.blender import renderer as rr
from fitting.domain import GarmentVariant
from fitting.ports import PortRequest
from server.wiring import AdapterSettings

pytestmark = pytest.mark.blender

REPO_ROOT = Path(__file__).resolve().parents[2]
BLENDER = (
    os.environ.get("BLENDER_PATH")
    or os.environ.get("BLENDER_EXE")
    or r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
)
JOB = "fit_blenderrender"

SCENE = """
import bpy, sys
bpy.ops.wm.read_factory_settings(use_empty=True)

def add(op, name, **kw):
    op(**kw)
    obj = bpy.context.active_object
    obj.name = name
    return obj

body = add(bpy.ops.mesh.primitive_cylinder_add, 'body', vertices=48, radius=0.14, depth=1.36,
           location=(0, 0, 0.68))  # 몸통 위(1.36)는 옷 위(1.40)보다 낮다
head = add(bpy.ops.mesh.primitive_uv_sphere_add, 'head', radius=0.1, location=(0, 0, 1.58))
bpy.ops.object.select_all(action='DESELECT')
head.select_set(True)
body.select_set(True)
bpy.context.view_layer.objects.active = body
bpy.ops.object.join()
add(bpy.ops.mesh.primitive_cylinder_add, 'garment_welded', vertices=64, radius=0.18, depth=0.30,
    location=(0, 0, 1.25), end_fill_type='NOTHING')
bpy.ops.wm.save_as_mainfile(filepath=sys.argv[sys.argv.index('--') + 1])
"""


@pytest.mark.skipif(not Path(BLENDER).is_file(), reason="Blender 없음")
def test_real_render_once(tmp_path):
    cv2 = pytest.importorskip("cv2")
    art = tmp_path / "art"
    art.mkdir()
    script = tmp_path / "make_scene.py"
    script.write_text(SCENE, encoding="utf-8")
    blend = art / f"{JOB}_drape_result.blend"
    subprocess.run(
        [BLENDER, "--background", "--factory-startup", "--python-exit-code", "1",
         "--python", str(script), "--", str(blend)],
        check=True,
        capture_output=True,
        timeout=120,
    )  # fmt: skip
    assert blend.is_file()

    settings = AdapterSettings.from_environ(
        artifacts_dir=art,
        environ={"BLENDER_PATH": BLENDER, "FIT_WORK_DIR": str(tmp_path / "work")},
    )
    renderer = rr.make_renderer(settings)
    garment = GarmentVariant.model_validate_json(
        (REPO_ROOT / "data" / "garments" / "tee_relaxed_l_black_v1.json").read_text("utf-8")
    )
    request = PortRequest(
        job_id=JOB,
        attempt_id=f"{JOB}_render_1",
        input_artifact_ids=[f"{JOB}_drape_result"],
        config_version="cfg_v1",
        timeout_seconds=300,
        trace_id=JOB,
    )
    result = renderer.render(request, garment)
    m = result.metrics
    assert result.output_artifact_ids == [f"{JOB}_render_clip"]
    assert m["head_vertices_in_frame"] == 0 and m["garment_vertices_out_of_frame"] == 0
    assert m["garment_color_source"] == "color_hex" and m["garment_color_hex"] == "#222326"
    assert 0.05 <= m["garment_mask_ratio"] <= 0.6

    clip = art / f"{JOB}_render_clip.mp4"
    capture = cv2.VideoCapture(str(clip))
    try:
        size = (capture.get(cv2.CAP_PROP_FRAME_WIDTH), capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = capture.get(cv2.CAP_PROP_FPS)
        count = 0
        while capture.grab():
            count += 1
    finally:
        capture.release()
    assert size == (768, 1024) and fps == pytest.approx(24.0) and count == 121

    mask = cv2.imread(str(art / f"{JOB}_render_mask.png"), cv2.IMREAD_UNCHANGED)
    assert mask.shape == (1024, 768) and mask.dtype.name == "uint8"
    assert set(int(v) for v in set(mask.ravel().tolist())) == {0, 255}
    assert (mask > 0).mean() == pytest.approx(m["garment_mask_ratio"], abs=1e-6)
    still = cv2.imread(str(art / f"{JOB}_render_still.png"))
    assert still.shape == (1024, 768, 3)
