"""D1 스파이크: fal / LTX-2.3 render-to-real 1회 호출.

기본은 드라이런이다. 요청 내용과 예상 비용만 계산해 저장하고 fal은 부르지 않는다.
--yes를 붙여야 실제로 호출하며, 이때 비용이 든다.

    # 드라이런 (C의 렌더 클립이 없으면 --make-test-clip으로 임시 클립을 만든다)
    python -m tools.spike_fal_ltx --make-test-clip
    python -m tools.spike_fal_ltx --input path/to/render.mp4

    # 실제 1회 호출 (FAL_KEY가 환경변수나 .env에 있어야 한다)
    python -m tools.spike_fal_ltx --input path/to/render.mp4 --yes

    # 생성은 끝났는데 내려받기에서 멈춘 기록 폴더를 다시 생성하지 않고 마무리한다
    python -m tools.spike_fal_ltx --resume artifacts/spikes/fal_ltx/<시각>

결과는 artifacts/spikes/fal_ltx/<시각>/ 에 남는다(커밋 금지).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from fitting.adapters.fal_ltx import (
    ENDPOINT,
    PRICE_CHECKED_ON,
    PRICE_PER_MEGAPIXEL_USD,
    RenderToRealError,
    RenderToRealSettings,
    estimate_cost,
    frames_for_clip,
    output_size,
    run_render_to_real,
)
from fitting.adapters.fal_ltx.render_to_real import download
from fitting.adapters.media.frames import read_video_info, save_representative_frame

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "artifacts" / "spikes" / "fal_ltx"

DEFAULT_GARMENT = "plain black short-sleeve crew-neck cotton T-shirt"
PROMPT_TEMPLATE = (
    "3DREAL. A realistic photo of a person wearing a {garment}, front view from the neck down, "
    "plain light gray studio background, soft even lighting, natural fabric folds. "
    "Keep the garment color and shape exactly as in the render."
)


def write_test_clip(
    path: Path,
    seconds: float = 3.0,
    fps: float = 24.0,
    size: tuple[int, int] = (540, 720),
) -> Path:
    """C의 렌더 클립 대신 쓰는 임시 클립: 목 아래만 보이는 마네킹이 검정 반팔 티를 입고 좌우로 살짝 흔들린다.

    얼굴 처리 결정(턱 아래로 잘라 프레임 밖)에 맞춰 머리는 그리지 않는다.
    """
    width, height = size
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    if not writer.isOpened():
        raise OSError(f"임시 클립을 만들 수 없습니다: {path}")
    background = (
        np.linspace(232, 200, height, dtype=np.uint8)[:, None, None]
        .repeat(width, 1)
        .repeat(3, 2)
    )
    skin, shirt, shade = (150, 178, 214), (34, 34, 36), (60, 60, 64)
    frames = round(seconds * fps)
    for i in range(frames):
        dx = int(8 * math.sin(2 * math.pi * i / frames))
        frame = background.copy()
        cx = width // 2 + dx

        def pts(points: list[tuple[float, float]], cx: int = cx) -> np.ndarray:
            return np.array(
                [(cx + x * width, y * height) for x, y in points], dtype=np.int32
            )

        cv2.rectangle(
            frame, (cx - 34, 0), (cx + 34, int(0.12 * height)), skin, -1
        )  # 목
        cv2.fillPoly(
            frame,
            [pts([(-0.40, 0.30), (-0.30, 0.18), (-0.24, 0.80), (-0.36, 0.82)])],
            skin,
        )  # 왼팔
        cv2.fillPoly(
            frame, [pts([(0.40, 0.30), (0.30, 0.18), (0.24, 0.80), (0.36, 0.82)])], skin
        )  # 오른팔
        body = [
            (-0.13, 0.08),
            (0.13, 0.08),
            (0.31, 0.15),
            (0.42, 0.38),
            (0.30, 0.43),
            (0.25, 0.33),
            (0.25, 0.95),
            (-0.25, 0.95),
            (-0.25, 0.33),
            (-0.30, 0.43),
            (-0.42, 0.38),
            (-0.31, 0.15),
        ]
        cv2.fillPoly(frame, [pts(body)], shirt)  # 티셔츠
        cv2.ellipse(
            frame,
            (cx, int(0.085 * height)),
            (int(0.11 * width), int(0.05 * height)),
            0,
            0,
            180,
            skin,
            -1,
        )  # 목둘레
        for x in (-0.12, 0.05, 0.16):  # 주름 음영
            cv2.line(
                frame,
                (int(cx + x * width), int(0.45 * height)),
                (int(cx + (x + 0.03) * width), int(0.9 * height)),
                shade,
                3,
            )
        writer.write(frame)
    writer.release()
    return path


def load_env_file(path: Path) -> None:
    """KEY=VALUE 형식의 .env를 읽어 환경변수에 없는 값만 채운다. 값은 출력하지 않는다."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="fal / LTX-2.3 render-to-real 1회 호출 스파이크 (기본은 드라이런)"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--input", type=Path, help="렌더 클립 경로 (C의 정면 렌더 클립)"
    )
    source.add_argument(
        "--make-test-clip",
        action="store_true",
        help="임시 마네킹 클립(3초)을 만들어 쓴다",
    )
    source.add_argument(
        "--resume",
        type=Path,
        help="생성 후 내려받기에서 멈춘 기록 폴더를 마무리한다 (다시 생성하지 않음, 비용 없음)",
    )
    parser.add_argument("--resolution", choices=["480p", "720p"], default="720p")
    parser.add_argument(
        "--intensity", choices=["light", "strong", "strong-v2"], default="light"
    )
    parser.add_argument("--fps", type=float, default=24.0, help="출력 fps")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--garment", default=DEFAULT_GARMENT, help="프롬프트에 넣을 옷 설명(영어)"
    )
    parser.add_argument(
        "--max-cost-usd",
        type=float,
        default=0.5,
        help="예상 비용이 이 값을 넘으면 호출하지 않는다",
    )
    parser.add_argument(
        "--timeout", type=float, default=600.0, help="호출 대기 상한(초)"
    )
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument(
        "--yes", action="store_true", help="실제로 fal을 호출한다 (비용 발생)"
    )
    return parser.parse_args(argv)


def finish_record(
    out_dir: Path,
    plan: dict[str, Any],
    result: dict[str, Any],
    output_path: Path,
) -> dict[str, Any]:
    """내려받은 결과 영상을 재고, 실사 대표 프레임과 record.json을 남긴다."""
    output_info = read_video_info(output_path)
    actual = estimate_cost(
        output_info.width, output_info.height, output_info.frame_count
    )
    real_frame = save_representative_frame(output_path, out_dir / "frame_real.png")
    record = {
        **plan,
        "result": result,
        "output": output_info.to_dict(),
        "cost_from_output": {"megapixels": actual.megapixels, "usd": actual.usd},
        "real_frame": real_frame.to_dict(),
    }
    write_json(out_dir / "record.json", record)

    durations = result["durations"]
    print(f"완료: request_id={result['request_id']}")
    print(
        f"출력: {output_info.width}×{output_info.height}, {output_info.fps:g}fps, {output_info.frame_count}프레임"
    )
    print(
        f"시간: 업로드 {durations['upload_s']}초 · 대기 {durations['queue_s']}초 · 생성 {durations['processing_s']}초 · 전체 {durations['total_s']}초"
    )
    print(
        f"출력 기준 비용: {actual.megapixels}MP → 약 ${actual.usd:.4f} (fal 대시보드 청구액과 비교해 기록)"
    )
    print(f"실사 대표 프레임: {real_frame.index}번 → {real_frame.path.name}")
    return record


def resume(out_dir: Path, downloader: Callable[[str, Path], None] = download) -> int:
    """response.json의 결과 주소에서 영상만 다시 내려받아 기록을 마무리한다."""
    try:
        plan = json.loads((out_dir / "plan.json").read_text(encoding="utf-8"))
        response = json.loads((out_dir / "response.json").read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        print(f"이어서 할 기록이 없습니다: {error.filename}", file=sys.stderr)
        return 2
    error_path = out_dir / "error.json"
    first = (
        json.loads(error_path.read_text(encoding="utf-8"))
        if error_path.exists()
        else {}
    )
    durations = dict(first.get("durations") or {})

    output_path = out_dir / "output.mp4"
    started = time.monotonic()
    downloader(response["video"]["url"], output_path)
    durations["download_s"] = round(time.monotonic() - started, 2)
    if error_path.exists():
        error_path.replace(out_dir / "error_first_attempt.json")

    result = {
        "endpoint": ENDPOINT,
        "request_id": first.get("request_id"),
        "output_url": response["video"]["url"],
        "output_path": output_path.as_posix(),
        "seed": response.get("seed"),
        "prompt_used": response.get("prompt"),
        "durations": durations,
        "resumed_after": first.get("stage"),
    }
    finish_record(out_dir, plan, result, output_path)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.resume:
        return resume(args.resume)
    out_dir = args.out_dir or DEFAULT_OUT / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.make_test_clip:
        input_path = write_test_clip(out_dir / "input_test_clip.mp4")
    else:
        input_path = args.input
        if not input_path.exists():
            print(f"입력 클립이 없습니다: {input_path}", file=sys.stderr)
            return 2
        shutil.copy2(input_path, out_dir / f"input{input_path.suffix}")

    info = read_video_info(input_path)
    settings = RenderToRealSettings(
        prompt=PROMPT_TEMPLATE.format(garment=args.garment),
        intensity=args.intensity,
        resolution=args.resolution,
        num_frames=frames_for_clip(info.frame_count, info.fps, args.fps),
        frames_per_second=args.fps,
        seed=args.seed,
    )
    settings.check()
    width, height = output_size(
        info.width, info.height, settings.resolution, settings.enable_detail_refine
    )
    estimate = estimate_cost(width, height, settings.num_frames)
    render_frame = save_representative_frame(input_path, out_dir / "frame_render.png")

    plan = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "endpoint": ENDPOINT,
        "input": {"path": input_path.as_posix(), **info.to_dict()},
        "arguments_preview": settings.to_arguments("<업로드 후 fal 주소>"),
        "estimate": {
            "output_width": estimate.width,
            "output_height": estimate.height,
            "frames": estimate.frames,
            "megapixels": estimate.megapixels,
            "usd": estimate.usd,
            "price_per_megapixel_usd": PRICE_PER_MEGAPIXEL_USD,
            "price_checked_on": PRICE_CHECKED_ON,
        },
        "render_frame": render_frame.to_dict(),
    }
    write_json(out_dir / "plan.json", plan)

    print(f"엔드포인트: {ENDPOINT}")
    print(
        f"입력: {info.width}×{info.height}, {info.fps:g}fps, {info.frame_count}프레임 ({info.duration_s:.2f}초)"
    )
    print(
        f"설정: intensity={settings.intensity}, {settings.resolution}, {settings.num_frames}프레임 @ {settings.frames_per_second:g}fps, 오디오 끔, seed={settings.seed}"
    )
    print(
        f"예상 출력(상한): {estimate.width}×{estimate.height}, {estimate.megapixels}MP → 약 ${estimate.usd:.4f} (단가 {PRICE_CHECKED_ON} 기준)"
    )
    print(
        f"렌더 대표 프레임: {render_frame.index}번 ({render_frame.time_s}초) → {render_frame.path.name}"
    )
    print(f"기록 폴더: {out_dir}")

    if not args.yes:
        print(
            "\n드라이런이라 fal을 부르지 않았습니다. 실제로 호출하려면 --yes를 붙이세요."
        )
        return 0

    if estimate.usd > args.max_cost_usd:
        print(
            f"예상 비용 ${estimate.usd:.4f}이 상한 ${args.max_cost_usd:.2f}을 넘어 호출하지 않습니다.",
            file=sys.stderr,
        )
        return 2

    load_env_file(ROOT / ".env")
    if not os.environ.get("FAL_KEY"):
        print(
            "FAL_KEY가 없습니다. 환경변수나 저장소 맨 위 .env 파일에 FAL_KEY=... 를 넣어 주세요.",
            file=sys.stderr,
        )
        return 2

    import fal_client  # 실제 호출할 때만 불러온다

    print("\nfal 호출 중… (대기와 생성에 몇 분 걸릴 수 있습니다)")
    try:
        result = run_render_to_real(
            input_path, settings, out_dir, fal_client, timeout_s=args.timeout
        )
    except RenderToRealError as error:
        print(f"실패: {error}\n실패 기록: {out_dir / 'error.json'}", file=sys.stderr)
        return 1

    finish_record(out_dir, plan, result.to_dict(), result.output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
