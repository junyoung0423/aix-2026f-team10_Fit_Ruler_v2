"""렌더 클립 하나를 피팅룸 실사화 어댑터로 회전 영상으로 만든다(서버 없이, 같은 어댑터).

    python -m tools.realize_fitroom --input path/to/render_clip.mp4         # 드라이런(비용 없음)
    python -m tools.realize_fitroom --input path/to/render_clip.mp4 --yes   # 실제 호출(약 $2.1)
    python -m tools.realize_fitroom --name <이전 이름> --yes                 # 이어 받기(끝난 단계
    건너뜀)

기본은 드라이런이다. 단계별 모델·문장·예상 비용만 보여 주고 fal은 부르지 않는다. `--yes`를 붙여야
실제로
부른다. FAL_KEY는 환경변수나 저장소 맨 위 `.env`에서 읽는다.

결과는 `artifacts/realize/<이름>/`에 남는다(커밋 금지).
- `artifacts/<이름>_render_clip.mp4`(입력 사본), `<이름>_realize_photo.png`(① 사진),
  `<이름>_realize_fit_frame.png`(② 핏 옮긴 프레임), `<이름>_realize_pose.png`(③ 자세),
  `<이름>_video.mp4`(④ 회전 영상)
- `work/<이름>/realize/`: `plan.json`, 단계별 `*_request.json`·`*_response.json`, `realize.json`
- `strip.png`: 렌더 첫 프레임 · ① · ② · ③ · 영상 0~8초 프레임을 나란히 놓은 확인 그림

종료 코드: 0 성공(드라이런 포함), 3 어댑터 실패(`PortFailure`), 2 입력·설정 오류.
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

import cv2
import numpy as np

from fitting.adapters.fal_fitroom.factory import make_fitroom_realizer
from fitting.adapters.fal_fitroom.realizer import FitRoomOptions, FitRoomRealizer
from fitting.adapters.fal_fitroom.steps import first_frame, read_image, sample_frames, write_png
from fitting.ports import PortFailure, PortRequest
from server.app import DEFAULT_GARMENTS_DIR, GARMENTS_DIR_ENV
from server.garments import GarmentDirectory
from server.wiring import AdapterConfigError, AdapterSettings
from tools.realize_clip import load_env_file, seed_value

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GARMENT_ID = "tee_regular_m_navy_v1"
EXIT_OK, EXIT_SETUP, EXIT_FAILED = 0, 2, 3
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


class _NoCalls:
    def __getattr__(self, name: str):
        raise RuntimeError("드라이런에서는 fal을 부르지 않습니다")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="렌더 클립으로 피팅룸 회전 영상을 만든다(기본 드라이런)"
    )
    parser.add_argument("--input", type=Path, help="렌더 클립(.mp4). 이어 받기면 생략")
    parser.add_argument("--name", help="작업 이름(결과 폴더). 기본은 지금 시각")
    parser.add_argument("--garment-id", default=DEFAULT_GARMENT_ID, help="프롬프트에 쓸 의류 ID")
    parser.add_argument("--garment-text", help="프롬프트의 옷 설명(영어, 시험용)")
    parser.add_argument("--garments-dir", type=Path, help="등록 의류 폴더 (기본 FIT_GARMENTS_DIR)")
    parser.add_argument(
        "--seed",
        type=seed_value,
        default=42,
        help="②·④ 시드(0 이상 정수). random이면 보내지 않는다",
    )
    parser.add_argument(
        "--duration", type=int, choices=[4, 6, 8], default=8, help="회전 영상 길이(초)"
    )
    parser.add_argument("--max-cost-usd", type=float, default=3.0, help="4단계 예상 비용 합 상한")
    parser.add_argument("--timeout", type=int, default=900, help="4단계 전체 대기 상한(초)")
    parser.add_argument("--out-dir", type=Path, help="결과 상위 폴더 (기본 artifacts/realize)")
    parser.add_argument("--yes", action="store_true", help="실제로 fal을 호출한다 (비용 발생)")
    parser.add_argument(
        "--resubmit",
        action="store_true",
        help="접수 여부를 모르는 이전 요청이 있어도 다시 제출한다 (비용이 다시 들 수 있음)",
    )
    return parser.parse_args(argv)


def _garments_dir(arg: Path | None) -> Path:
    if arg is not None:
        return arg
    env = os.environ.get(GARMENTS_DIR_ENV)
    return Path(env) if env else DEFAULT_GARMENTS_DIR


def strip_image(clip: Path, pictures: list[Path], video: Path, height: int = 360) -> np.ndarray:
    """렌더 첫 프레임 · 중간 사진들 · 영상 1초 간격 프레임을 같은 높이로 나란히 놓는다."""
    tiles = [first_frame(clip)] + [read_image(p) for p in pictures]
    frames, info = sample_frames(video, count=9)
    tiles += frames
    resized = [cv2.resize(t, (round(t.shape[1] * height / t.shape[0]), height)) for t in tiles]
    gap = np.full((height, 6, 3), 255, np.uint8)
    out = resized[0]
    for tile in resized[1:]:
        out = np.hstack([out, gap, tile])
    return out


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    name = args.name or datetime.now().strftime("fitroom_%Y%m%d_%H%M%S")
    if not _NAME.fullmatch(name):
        print(f"이름에는 영문·숫자·_·-만 씁니다: {name}", file=sys.stderr)
        return EXIT_SETUP
    out = (args.out_dir or ROOT / "artifacts" / "realize") / name
    artifacts, work = out / "artifacts", out / "work"
    clip = artifacts / f"{name}_render_clip.mp4"
    if args.input is not None:
        if not args.input.is_file():
            print(f"입력 클립이 없습니다: {args.input}", file=sys.stderr)
            return EXIT_SETUP
        artifacts.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.input, clip)
    elif not clip.is_file():
        print("--input이 필요합니다(이어 받을 작업도 없습니다)", file=sys.stderr)
        return EXIT_SETUP

    garment = GarmentDirectory(_garments_dir(args.garments_dir)).get(args.garment_id)
    if garment is None:
        print(f"의류를 찾을 수 없습니다: {args.garment_id}", file=sys.stderr)
        return EXIT_SETUP

    options = FitRoomOptions(
        seed=args.seed,
        max_cost_usd=args.max_cost_usd,
        garment_text=args.garment_text,
        allow_resubmit=args.resubmit,
        turn_duration_s=args.duration,
    )
    request = PortRequest(
        job_id=name,
        attempt_id=f"{name}_realize_1",
        input_artifact_ids=[clip.stem],
        config_version="tools.realize_fitroom",
        timeout_seconds=args.timeout,
        trace_id=name,
    )
    dry = FitRoomRealizer(
        client=_NoCalls(), artifacts_dir=artifacts, work_dir=work, options=options
    )
    try:
        plan = dry.plan(request, garment)
    except ValueError as error:
        print(f"설정 오류: {error}", file=sys.stderr)
        return EXIT_SETUP
    (out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), "utf-8")
    inp, cost = plan["input"], plan["estimate_usd"]
    print(f"{name}: 피팅룸 실사화 4단계 · 의류 {garment.id} · 바지 {plan['prompts']['pants']}")
    print(f"입력: {inp['width']}×{inp['height']}, {inp['fps']:g}fps, {inp['frame_count']}프레임")
    for step, endpoint in plan["endpoints"].items():
        print(f"  {step:8s} {endpoint}  예상 ${cost[step]:.4f}")
    print(f"예상 비용 합 ${cost['total']:.4f} / 한도 ${options.max_cost_usd:.2f}")

    if not args.yes:
        print("\n드라이런이라 fal을 부르지 않았습니다. 실제로 호출하려면 --yes를 붙이세요.")
        print(f"결과 폴더: {out}")
        return EXIT_OK

    load_env_file(ROOT / ".env")
    settings = AdapterSettings(
        artifacts_dir=artifacts,
        work_dir=work,
        blender_path=None,
        smpl_model_dir=None,
        fal_key_present=bool((os.environ.get("FAL_KEY") or "").strip()),
        repo_root=ROOT,
    )
    try:
        realizer = make_fitroom_realizer(settings)
    except AdapterConfigError as error:
        print(f"설정 오류: {error}", file=sys.stderr)
        return EXIT_SETUP
    realizer.options = options

    print("\nfal 호출 중… (4단계, 모두 6~8분쯤 걸립니다)")
    try:
        result = realizer.realize(request, garment)
    except PortFailure as failure:
        error = failure.error
        print(f"실패 {error.code.value}: {error.safe_message}", file=sys.stderr)
        if error.external_request_id:
            print(
                f"요청 ID {error.external_request_id} — 같은 --name으로 다시 실행하면 이어 받습니다"
            )
        print(f"기록: {work / name / 'realize'}", file=sys.stderr)
        return EXIT_FAILED

    m = result.metrics
    outputs = realizer.outputs(name)
    write_png(
        out / "strip.png",
        strip_image(
            clip, [outputs["photo"], outputs["transfer"], outputs["pose"]], outputs["turn"]
        ),
    )
    print(
        f"완료: 비용 약 ${m.get('cost_usd')} (예상 ${m.get('estimated_cost_usd')}) · 재시도 "
        f"{m.get('retries', 0)}회"
    )
    print(
        f"시간: 사진 {m.get('photo_s')}초 · 핏 옮기기 {m.get('transfer_s')}초 · "
        f"자세 {m.get('pose_s')}초 · 회전 {m.get('turn_s')}초"
    )
    print(f"회전 영상: {outputs['turn']} ({m.get('video_width')}×{m.get('video_height')})")
    print(f"확인 그림: {out / 'strip.png'}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
