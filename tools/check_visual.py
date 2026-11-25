"""D6: 실사 영상을 렌더 클립과 11프레임에서 비교해 색·실루엣을 판정한다 (fal 호출 없음, 비용 없음).

    python -m tools.check_visual --real <실사 영상.mp4> --render <렌더 클립.mp4> --mask <마스크.png>
    python -m tools.check_visual --run artifacts/realize/<이름>      # tools.realize_clip 결과 폴더
    python -m tools.check_visual --real ... --render ... --render-mask-dark 80   # 임시 클립용
    python -m tools.check_visual --run artifacts/realize/<이름> --mask <마스크.png>
        # tools.realize_fitroom 결과 폴더: 핏 옮기기 프레임이 있어 피팅룸 검사를 한다

검증 어댑터(`fitting.adapters.media.validator`)와 같은 측정·판정(`check_videos`)과 같은 설정
(`config/validate/visual_check_v1.json`, `--config`로 바꿀 수 있음)을 쓴다.

피팅룸 검사: `--fit-frame`(또는 `--run` 폴더의 `artifacts/*_realize_fit_frame.png`)이 있으면 검증
어댑터처럼 11프레임 대신 피팅룸 검사(`check_fitroom`, `config/validate/fitroom_check_v1.json`)를
한다. 렌더 옷 마스크 파일이 필요하다(`--render-mask-dark`는 못 쓴다). 결과 폴더에는
`fitroom_sheet.png`(렌더 · 핏 옮기기 프레임 · 영상 1초 간격), `frame.png`(영상 첫 프레임),
`render_frame.png`(렌더 첫 프레임), `check.json`, `masks/fit_frame.png`가 남는다.

렌더 옷 마스크
- `--mask`: C5 렌더 어댑터가 낸 `{job_id}_render_mask.png`(정지 클립용 한 장)
- `--run`: 폴더의 `artifacts/*_render_mask.png`를 찾는다
  (없으면 `--mask`나 `--render-mask-dark`가 필요하다)
- `--render-mask-dark MAX`: 마스크 파일이 없는 어두운 옷 렌더용(D1 임시 클립). 렌더 프레임마다
  R·G·B가 모두 MAX 이하인 픽셀을 옷으로 본다

결과는 `--out-dir`(기본 `artifacts/visual_check/<시각>/`)에 남는다(커밋 금지).
- `contact_sheet.png`: 11프레임 접촉 시트(위 렌더, 아래 실사, 초록 = 렌더 마스크, 자주 = 실사 옷)
- `frame.png`·`render_frame.png`: 대표 프레임과 대응 렌더 프레임(원래 크기)
- `check.json`: 기준·프레임별 측정값·판정
- `masks/real_XXX.png`: 프레임별 실사 옷 영역

종료 코드: 0 판정함(PASS·FAIL·UNVERIFIABLE 모두), 1 입력 오류.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from fitting.adapters.media.fitroom_measure import (
    DEFAULT_FITROOM_CONFIG,
    check_fitroom,
    fitroom_sheet,
    load_fitroom_config,
)
from fitting.adapters.media.frames import read_image, write_image
from fitting.adapters.media.visual_measure import (
    DEFAULT_CHECK_CONFIG,
    MaskSource,
    as_mask,
    check_videos,
    contact_sheet,
    load_check_config,
)
from fitting.verify import MATCH_MODES

ROOT = Path(__file__).resolve().parents[1]
EXIT_OK, EXIT_INPUT = 0, 1


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="실사 영상의 색·실루엣을 렌더 클립과 비교한다")
    parser.add_argument("--real", type=Path, help="실사 영상(.mp4)")
    parser.add_argument("--render", type=Path, help="렌더 클립(.mp4)")
    parser.add_argument("--mask", type=Path, help="렌더 옷 마스크(.png, 정지 클립용 한 장)")
    parser.add_argument(
        "--render-mask-dark",
        type=int,
        metavar="MAX",
        help="마스크 파일 대신 렌더 프레임마다 R·G·B가 모두 MAX 이하인 픽셀을 옷으로 본다",
    )
    parser.add_argument(
        "--run", type=Path, help="tools.realize_clip·tools.realize_fitroom 결과 폴더"
    )
    parser.add_argument(
        "--fit-frame",
        type=Path,
        help="피팅룸 실사화의 핏 옮기기 프레임(.png). 주면 11프레임 대신 피팅룸 검사를 한다",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CHECK_CONFIG, help="검증 설정")
    parser.add_argument(
        "--fitroom-config", type=Path, default=DEFAULT_FITROOM_CONFIG, help="피팅룸 검증 설정"
    )
    parser.add_argument("--mode", choices=MATCH_MODES, default="time", help="대응 프레임 방식")
    parser.add_argument("--out-dir", type=Path, help="결과 폴더")
    args = parser.parse_args(argv)
    if args.run is None and (args.real is None or args.render is None):
        parser.error("--real과 --render를 함께 주거나 --run을 주세요")
    if args.mask is not None and args.render_mask_dark is not None:
        parser.error("--mask와 --render-mask-dark는 함께 쓸 수 없습니다")
    return args


def _one(directory: Path, pattern: str, required: bool = True) -> Path | None:
    found = sorted(directory.glob(pattern))
    if len(found) > 1 or (required and not found):
        raise ValueError(f"{pattern}을(를) 하나 찾지 못했습니다: {directory}")
    return found[0] if found else None


def resolve_inputs(args: argparse.Namespace) -> tuple[Path, Path, Path | None, Path | None]:
    """(실사 영상, 렌더 클립, 마스크 파일 또는 None, 핏 옮기기 프레임 또는 None)."""
    if args.run is not None:
        directory = args.run / "artifacts"
        real = _one(directory, "*_video.mp4")
        render = _one(directory, "*_render_clip.mp4")
        mask = args.mask or _one(directory, "*_render_mask.png", required=False)
        fit_frame = args.fit_frame or _one(directory, "*_realize_fit_frame.png", required=False)
    else:
        real, render, mask, fit_frame = args.real, args.render, args.mask, args.fit_frame
    assert real is not None and render is not None
    for path in (real, render, mask, fit_frame):
        if path is not None and not path.is_file():
            raise ValueError(f"파일이 없습니다: {path}")
    return real, render, mask, fit_frame


def mask_source(mask: Path | None, dark_max: int | None) -> MaskSource:
    if dark_max is not None:
        limit = int(dark_max)

        def dark(_index: int, frame: np.ndarray) -> np.ndarray:
            return frame.max(axis=2) <= limit

        return dark
    if mask is None:
        raise ValueError("렌더 옷 마스크가 없습니다. --mask나 --render-mask-dark를 주세요")
    return as_mask(read_image(mask, cv2.IMREAD_GRAYSCALE))


def _out_dir(args: argparse.Namespace) -> Path:
    return args.out_dir or ROOT / "artifacts" / "visual_check" / datetime.now().strftime(
        "%Y%m%d-%H%M%S"
    )


def _fitroom(
    args: argparse.Namespace, real: Path, render: Path, mask: Path | None, fit_frame: Path
) -> int:
    """피팅룸 검사(검증 어댑터의 `_validate_fitroom`과 같은 측정·판정)."""
    try:
        if mask is None or args.render_mask_dark is not None:
            raise ValueError("피팅룸 검사에는 렌더 옷 마스크 파일(--mask)이 필요합니다")
        render_mask = as_mask(read_image(mask, cv2.IMREAD_GRAYSCALE))
        config = load_check_config(args.config)
        fitroom = load_fitroom_config(args.fitroom_config)
        run = check_fitroom(render, render_mask, fit_frame, real, config, fitroom)
    except (OSError, ValueError) as error:
        print(f"입력 오류: {error}", file=sys.stderr)
        return EXIT_INPUT

    out = _out_dir(args)
    write_image(out / "frame.png", run.video_frames[0])
    write_image(out / "render_frame.png", run.render_frame)
    write_image(out / "fitroom_sheet.png", fitroom_sheet(run))
    if run.fit_mask is not None:
        write_image(out / "masks" / "fit_frame.png", run.fit_mask.astype(np.uint8) * 255)
    record = {
        "real": real.as_posix(),
        "render": render.as_posix(),
        "mask": mask.as_posix(),
        "fit_frame": fit_frame.as_posix(),
        "config": config.to_record(),
        "fitroom_config": fitroom.to_record(),
        **run.to_record(),
    }
    (out / "check.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )

    result = run.result
    print(f"피팅룸 검사 판정: {result.status} (설정 {config.source}, {fitroom.source})")
    for reason in result.reasons():
        print(f"  - {reason}")
    if result.fit is not None:
        m = result.fit.measure
        print(
            f"  핏 옮기기 프레임 {result.fit.status}: IoU {m.iou} 가슴 {m.chest_change} "
            f"밑단 {m.hem_change} 색 {m.delta_e} 면적비 {m.area_ratio}"
        )
    video = result.video
    print(
        f"  영상: 첫·끝 차이 {video.end_frame_gap} · 옷 밝기 범위 {video.lightness_range} · "
        f"옷 못 찾은 시점 {video.garment_missing} · 검은 화면 {video.black}"
    )
    print(f"지표: {json.dumps(result.metrics(), ensure_ascii=False)}")
    print(f"결과 폴더: {out}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        real, render, mask, fit_frame = resolve_inputs(args)
    except (OSError, ValueError) as error:
        print(f"입력 오류: {error}", file=sys.stderr)
        return EXIT_INPUT
    if fit_frame is not None:
        return _fitroom(args, real, render, mask, fit_frame)
    try:
        source = mask_source(mask, args.render_mask_dark)
        config = load_check_config(args.config)
        run = check_videos(real, render, source, config, mode=args.mode)
    except (OSError, ValueError) as error:
        print(f"입력 오류: {error}", file=sys.stderr)
        return EXIT_INPUT

    out = _out_dir(args)
    real_index, render_index = run.representative_pair()
    write_image(out / "frame.png", run.real_frames[real_index])
    write_image(out / "render_frame.png", run.render_frames[render_index])
    write_image(out / "contact_sheet.png", contact_sheet(run))
    for fraction, real_mask in zip(config.criteria.sample_fractions, run.real_masks, strict=False):
        if real_mask is not None:
            name = f"real_{round(fraction * 100):03d}.png"
            write_image(out / "masks" / name, real_mask.astype(np.uint8) * 255)
    record = {
        "real": real.as_posix(),
        "render": render.as_posix(),
        "mask": None if mask is None else mask.as_posix(),
        "render_mask_dark": args.render_mask_dark,
        "config": config.to_record(),
        "representative_frames": {"real_index": real_index, "render_index": render_index},
        **run.to_record(),
    }
    (out / "check.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )

    result = run.result
    print(f"판정: {result.status} (설정 {config.source})")
    for reason in result.reasons():
        print(f"  - {reason}")
    for frame in result.frames:
        m = frame.measure
        print(
            f"  {m.fraction * 100:3.0f}% 실사 {m.real_index:3d} ↔ 렌더 {m.render_index:3d}  "
            f"{frame.status:12s} IoU {m.iou} 가슴 {m.chest_change} 밑단 {m.hem_change} "
            f"색 {m.delta_e} 면적비 {m.area_ratio}"
        )
    print(f"지표: {json.dumps(result.metrics(), ensure_ascii=False)}")
    print(f"결과 폴더: {out}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
