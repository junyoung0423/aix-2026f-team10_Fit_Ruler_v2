"""D5: 실사 영상의 대표 프레임과 그 시각에 대응하는 렌더 프레임을 뽑는다 (fal 호출 없음, 비용 없음).

    python -m tools.match_frames --real <실사 영상.mp4> --render <렌더 클립.mp4>
    python -m tools.match_frames --run artifacts/realize/<이름>     # tools.realize_clip 결과 폴더

검증 어댑터(`fitting.adapters.media.validator`)와 같은 함수(`save_frame_pair`)를 쓴다. 매칭 방식은
`time`(기본, 같은 시각)과 `stretch`(전체 길이 비율)이다(`fitting/verify/frame_match.py`).

결과는 `--out-dir`(기본 `artifacts/frames/<시각>/`)에 남는다(커밋 금지).
- `frame.png`·`render_frame.png`: 실사 대표 프레임, 대응 렌더 프레임(각 영상의 원래 크기)
- `compare.png`: 두 프레임을 같은 높이로 나란히 놓은 확인 그림(왼쪽 렌더, 오른쪽 실사)
- `frames.json`: 두 영상 정보, 고른 프레임, 매칭 방식·시각 차이

종료 코드: 0 성공, 1 입력 오류.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from fitting.adapters.media.frames import save_frame_pair, write_image
from fitting.verify import MATCH_MODES

ROOT = Path(__file__).resolve().parents[1]
EXIT_OK, EXIT_INPUT = 0, 1


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="실사 대표 프레임과 대응 렌더 프레임을 뽑는다")
    parser.add_argument("--real", type=Path, help="실사 영상(.mp4)")
    parser.add_argument("--render", type=Path, help="렌더 클립(.mp4)")
    parser.add_argument(
        "--run", type=Path, help="tools.realize_clip 결과 폴더(artifacts/에 두 영상)"
    )
    parser.add_argument("--mode", choices=MATCH_MODES, default="time", help="매칭 방식")
    parser.add_argument("--method", choices=["sharpest", "middle"], default="sharpest")
    parser.add_argument("--out-dir", type=Path, help="결과 폴더")
    args = parser.parse_args(argv)
    if args.run is None and (args.real is None or args.render is None):
        parser.error("--real과 --render를 함께 주거나 --run을 주세요")
    return args


def _from_run(run: Path) -> tuple[Path, Path]:
    """realize_clip 결과 폴더의 `artifacts/{이름}_video.mp4`·`artifacts/{이름}_render_clip.mp4`."""
    videos = sorted((run / "artifacts").glob("*_video.mp4"))
    clips = sorted((run / "artifacts").glob("*_render_clip.mp4"))
    if len(videos) != 1 or len(clips) != 1:
        raise ValueError(f"실사 영상과 렌더 클립을 하나씩 찾지 못했습니다: {run / 'artifacts'}")
    return videos[0], clips[0]


def compare_image(render_path: Path, real_path: Path, height: int = 512) -> np.ndarray:
    """두 프레임을 같은 높이로 맞춰 나란히 놓는다(왼쪽 렌더, 오른쪽 실사, 사이 8 px 흰 띠)."""
    images = []
    for path in (render_path, real_path):
        image = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"그림을 읽지 못했습니다: {path}")
        width = round(image.shape[1] * height / image.shape[0])
        images.append(cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA))
    gap = np.full((height, 8, 3), 255, dtype=np.uint8)
    return np.hstack([images[0], gap, images[1]])


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        real, render = _from_run(args.run) if args.run else (args.real, args.render)
        for path in (real, render):
            if not path.is_file():
                raise ValueError(f"영상이 없습니다: {path}")
        out = args.out_dir or ROOT / "artifacts" / "frames" / datetime.now().strftime(
            "%Y%m%d-%H%M%S"
        )
        pair = save_frame_pair(
            real,
            render,
            out / "frame.png",
            out / "render_frame.png",
            method=args.method,
            mode=args.mode,
        )
    except (OSError, ValueError) as error:
        print(f"입력 오류: {error}", file=sys.stderr)
        return EXIT_INPUT

    record = {"real": real.as_posix(), "render": render.as_posix(), **pair.to_dict()}
    (out / "frames.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_image(out / "compare.png", compare_image(pair.render_path, pair.real.path))

    match, timing = pair.match, record["coverage"]
    ri, vi = pair.render_info, pair.real_info
    print(
        f"렌더: {ri.width}×{ri.height}, {ri.fps:g}fps, {ri.frame_count}프레임 · "
        f"실사: {vi.width}×{vi.height}, {vi.fps:g}fps, {vi.frame_count}프레임 "
        f"(길이 비 {timing['duration_ratio']:g})"
    )
    print(
        f"실사 대표 프레임 {match.real_index}번({match.real_time_s:g}초, {pair.real.method}) → "
        f"렌더 {match.render_index}번({match.render_time_s:g}초, {match.mode}"
        f"{', 마지막 프레임으로 자름' if match.clamped else ''})"
    )
    print(f"결과 폴더: {out}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
