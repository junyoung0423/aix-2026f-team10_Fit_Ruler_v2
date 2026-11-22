"""C5: 착장 결과 .blend 하나를 정면 렌더 클립·옷 마스크로 만든다(서버 없이, 같은 렌더 어댑터).

    python -m tools.render_clip --blend path/to/xxx_drape_result.blend
        --garment-id tee_regular_m_navy_v1   (한 줄로)

D에게 넘길 클립을 서버 없이 만들 때 쓴다.
Blender 경로는 `--blender` > `BLENDER_PATH` > `BLENDER_EXE`.

결과는 `artifacts/render/<이름>/`에 남는다(커밋 금지).
- `render_clip.mp4`(768×1024, 24fps, 121프레임, 무음 H264), `garment_mask.png`(옷 255·나머지 0),
  `render_still.png`(정지 프레임), `render_metrics.json`(어댑터 지표 + cv2 확인 + Blender 지표),
  `render_settings.json`(쓴 카메라·조명·재질·엔진·색 관리)
- `artifacts/`(어댑터 산출물 이름 그대로), `work/<이름>/`(작업 폴더·기록·로그)

만든 MP4는 cv2로 다시 열어 크기·fps·프레임 수를 확인한다(D가 cv2로 읽는다). cv2가 없으면 확인을
건너뛰고 알린다(`pip install -e ".[body]"` 등에 들어 있다).

종료 코드: 0 성공, 3 어댑터 실패(`PortFailure`) 또는 cv2 확인 불일치, 2 입력·설정 오류.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from fitting.adapters.blender import renderer as rr
from fitting.ports import PortFailure, PortRequest
from server.app import DEFAULT_GARMENTS_DIR, GARMENTS_DIR_ENV
from server.garments import GarmentDirectory
from server.wiring import AdapterConfigError, AdapterSettings

ROOT = Path(__file__).resolve().parents[1]
EXIT_OK, EXIT_SETUP, EXIT_FAILED = 0, 2, 3
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="착장 결과 .blend를 정면 렌더 클립·옷 마스크로")
    parser.add_argument("--blend", type=Path, required=True, help="착장 결과 .blend (C4 산출물)")
    parser.add_argument("--garment-id", required=True, help="옷 색을 가져올 의류 ID")
    parser.add_argument("--garments-dir", type=Path, help="등록 의류 폴더 (기본 FIT_GARMENTS_DIR)")
    parser.add_argument("--name", help="작업 이름(결과 폴더, job_id). 기본은 의류 ID와 지금 시각")
    parser.add_argument("--out-dir", type=Path, help="결과 상위 폴더 (기본 artifacts/render)")
    parser.add_argument("--blender", type=Path, help="Blender 실행 파일 (기본 BLENDER_PATH)")
    parser.add_argument("--timeout", type=int, default=300, help="시간 제한(초), 설정값과 작은 값")
    return parser.parse_args(argv)


def _garments_dir(arg: Path | None) -> Path:
    if arg is not None:
        return arg
    env = os.environ.get(GARMENTS_DIR_ENV)
    return Path(env) if env else DEFAULT_GARMENTS_DIR


def cv2_probe(clip: Path) -> dict | None:
    """cv2로 다시 연 클립 정보. cv2가 없으면 None."""
    try:
        from fitting.adapters.media.frames import read_video_info
    except ImportError:
        return None
    return read_video_info(clip).to_dict()


def clip_matches(probe: dict, metrics: dict) -> bool:
    return (
        probe["width"] == metrics["width"]
        and probe["height"] == metrics["height"]
        and abs(probe["fps"] - metrics["fps"]) < 1e-3
        and probe["frame_count"] == metrics["frames"]
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    name = args.name or f"{args.garment_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if not _NAME.fullmatch(name):
        print(f"이름에는 영문·숫자·_·-만 씁니다: {name}", file=sys.stderr)
        return EXIT_SETUP
    if not args.blend.is_file():
        print(f"착장 결과 파일이 없습니다: {args.blend}", file=sys.stderr)
        return EXIT_SETUP
    garment = GarmentDirectory(_garments_dir(args.garments_dir)).get(args.garment_id)
    if garment is None:
        print(f"의류를 찾을 수 없습니다: {args.garment_id}", file=sys.stderr)
        return EXIT_SETUP

    out = (args.out_dir or ROOT / "artifacts" / "render") / name
    artifacts, work = out / "artifacts", out / "work"
    environ = dict(os.environ, FIT_WORK_DIR=str(work))
    if args.blender is not None:
        environ["BLENDER_PATH"] = str(args.blender)
    try:
        renderer = rr.make_renderer(
            AdapterSettings.from_environ(artifacts_dir=artifacts, environ=environ)
        )
    except AdapterConfigError as error:
        print(f"설정 오류: {error}", file=sys.stderr)
        return EXIT_SETUP

    drape_id = f"{name}_drape_result"
    artifacts.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.blend, artifacts / f"{drape_id}.blend")
    request = PortRequest(
        job_id=name,
        attempt_id=f"{name}_render_1",
        input_artifact_ids=[drape_id],
        config_version="tools.render_clip",
        timeout_seconds=args.timeout,
        trace_id=name,
    )
    print(f"{name}: {args.blend.name} · 의류 {garment.id} · Blender 렌더 중…")
    try:
        result = renderer.render(request, garment)
    except PortFailure as failure:
        error = failure.error
        print(f"실패 {error.code.value}: {error.safe_message}", file=sys.stderr)
        print(f"기록: {error.internal_details_ref}", file=sys.stderr)
        return EXIT_FAILED

    m = result.metrics
    clip_id, mask_id, still_id = renderer.artifact_ids(name)
    run_dir = work / name / rr.RUN_NAME
    shutil.copyfile(artifacts / f"{clip_id}.mp4", out / rr.CLIP)
    shutil.copyfile(artifacts / f"{mask_id}.png", out / rr.MASK)
    shutil.copyfile(artifacts / f"{still_id}.png", out / rr.STILL)
    shutil.copyfile(run_dir / rr.SETTINGS, out / rr.SETTINGS)
    probe = cv2_probe(out / rr.CLIP)
    summary = {
        "name": name,
        "blend": str(args.blend),
        "garment_id": garment.id,
        "port_metrics": m,
        "tool_versions": result.tool_versions,
        "cv2_probe": probe,
        "blender_metrics": json.loads((run_dir / rr.METRICS).read_text(encoding="utf-8")),
    }
    (out / rr.METRICS).write_text(json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")

    print(
        f"완료: Blender {m['render_seconds']}초(정지 프레임 {m['still_seconds']}초, "
        f"{m['engine']} {m['samples']}샘플)"
    )
    print(
        f"클립: {m['width']}×{m['height']}, {m['fps']}fps, {m['frames']}프레임, {m['codec']}, "
        "정지 클립(모든 프레임 동일)"
    )
    print(
        f"마스크 비율 {m['garment_mask_ratio']} · "
        f"화면 안 머리 정점 {m['head_vertices_in_frame']} · "
        f"화면 밖 옷 정점 {m['garment_vertices_out_of_frame']}"
    )
    print(
        f"옷 색 {m['garment_color_hex']}({m['garment_color_source']}) → 렌더 중앙값 "
        f"{m['garment_median_hex']}, CIEDE2000 {m['garment_delta_e00']}"
    )
    print(f"결과 폴더: {out}")
    if probe is None:
        print("cv2가 없어 클립을 다시 열어 확인하지 못했습니다.")
        return EXIT_OK
    print(
        f"cv2 확인: {probe['width']}×{probe['height']}, {probe['fps']:g}fps, "
        f"{probe['frame_count']}프레임"
    )
    if not clip_matches(probe, m):
        print("cv2로 읽은 클립이 설정과 다릅니다.", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
