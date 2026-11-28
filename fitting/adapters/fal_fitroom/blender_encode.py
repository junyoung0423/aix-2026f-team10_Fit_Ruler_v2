"""프레임 폴더(BMP·PNG) → 무음 H264 MP4 (Blender 내장 FFmpeg). 피팅룸 회전 영상의 맨 위
자르기(#10, `encode.py`)에 쓴다.

사용:
    blender --background --factory-startup --python-exit-code 1 \
        --python fitting/adapters/fal_fitroom/blender_encode.py -- <frames_dir> <out.mp4> <fps>

- 프레임은 이름 순서대로 쓴다(f00000.bmp ...). 화면 크기는 첫 프레임 크기다.
- 색은 바꾸지 않는다. 보기 변환 Standard·look 없음·노출 0·감마 1, 시퀀서와 프레임 색 공간 sRGB로
  렌더 클립(C5 `blender_render.color_management`)과 같다.
- 인코딩: MPEG4 컨테이너, H264, 소리 없음, CRF HIGH, GOOD 프리셋, GOP 24, B프레임 없음. 렌더 클립은
  정지 클립이라 PERC_LOSSLESS를 쓰지만, 움직이는 8초 영상은 HIGH로 Veo 원본과 크기가 비슷하다
  (q23: 원본 2.36 MB, 다시 쓴 영상 2.33 MB).
- 출력은 같은 폴더에 임시 이름으로 만든 뒤 `<out.mp4>`로 바꾼다. 실패하면 예외로 끝나고
  `--python-exit-code 1` 때문에 종료 코드가 1이 된다.
"""

import sys
from pathlib import Path

import bpy


def log(msg):
    print(f"[encode] {msg}", flush=True)


def main():
    frames_dir, out, fps = sys.argv[sys.argv.index("--") + 1 :][:3]
    # 그림 읽기는 상대 경로를 실행 위치(cwd) 기준으로 풀지 않으므로 절대 경로로 바꾼다
    folder = Path(frames_dir).resolve()
    frames = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".bmp", ".png"))
    if not frames:
        raise RuntimeError(f"프레임이 없습니다: {frames_dir}")
    out = Path(out).resolve()
    fps = float(fps)
    image = bpy.data.images.load(str(frames[0]))
    width, height = image.size
    bpy.data.images.remove(image)

    scene = bpy.data.scenes.new("fitroom_encode")
    r = scene.render
    r.resolution_x, r.resolution_y, r.resolution_percentage = width, height, 100
    r.fps = max(1, round(fps))
    r.fps_base = r.fps / fps
    scene.frame_start, scene.frame_end = 1, len(frames)
    scene.display_settings.display_device = "sRGB"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.view_settings.use_curve_mapping = False
    scene.sequencer_colorspace_settings.name = "sRGB"
    for prop in r.bl_rna.properties:  # 날짜·시간 같은 메타데이터를 넣지 않는다
        if prop.identifier.startswith("use_stamp") and not prop.is_readonly:
            setattr(r, prop.identifier, False)

    editor = scene.sequence_editor_create()
    strips = editor.strips if hasattr(editor, "strips") else editor.sequences
    strip = strips.new_image("frames", str(frames[0]), 1, 1)
    for frame in frames[1:]:
        strip.elements.append(frame.name)
    strip.colorspace_settings.name = "sRGB"
    r.use_sequencer, r.use_compositing = True, False
    im = r.image_settings
    if hasattr(im, "media_type"):
        im.media_type = "VIDEO"
    im.file_format = "FFMPEG"
    im.color_mode = "RGB"
    ff = r.ffmpeg
    ff.format, ff.codec, ff.audio_codec = "MPEG4", "H264", "NONE"
    ff.constant_rate_factor = "HIGH"
    ff.ffmpeg_preset = "GOOD"
    ff.gopsize = 24
    ff.use_max_b_frames = False

    prefix = f"{out.stem}_enc_"
    for old in out.parent.glob(prefix + "*"):
        old.unlink()
    r.filepath = str(out.parent / prefix)
    r.use_file_extension = True
    bpy.ops.render.render(animation=True, scene=scene.name)
    made = sorted(out.parent.glob(prefix + "*"))
    if not made:
        raise RuntimeError("FFmpeg 출력 파일이 없습니다")
    made[-1].replace(out)
    log(f"{out.name} frames={len(frames)} size={width}x{height} fps={fps:g}")


if __name__ == "__main__":
    main()
