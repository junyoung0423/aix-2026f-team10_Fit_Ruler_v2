"""D4: 렌더 클립 하나를 fal 실사화 어댑터로 실사 영상으로 바꿔 저장한다(서버 없이, 같은 어댑터).

    python -m tools.realize_clip --input path/to/render.mp4           # 드라이런(비용 없음)
    python -m tools.realize_clip --input path/to/render.mp4 --yes     # 실제 1회 호출(비용 발생)
    python -m tools.realize_clip --name <이전 이름> --yes              # 이어 받기(다시 제출 안 함)

기본은 드라이런이다. 보낼 설정과 예상 비용만 계산하고 fal은 부르지 않는다. `--yes`를 붙여야 실제로
부르며 비용이 든다(D1 기준 3초 클립 약 $0.07). FAL_KEY는 환경변수나 저장소 맨 위 `.env`에서 읽는다.

결과는 `artifacts/realize/<이름>/`에 남는다(커밋 금지).
- `artifacts/<이름>_render_clip.mp4`(입력 사본), `artifacts/<이름>_video.mp4`(실사 영상)
- `work/<이름>/realize/`: `plan.json`·`request.json`·`response.json`·`error.json`·`realize.json`
- `frame_render.png`·`frame_real.png`: 렌더·실사 대표 프레임(비교용)

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

from fitting.adapters.fal_ltx import ENDPOINT, FalRealizer, RealizeOptions
from fitting.adapters.fal_ltx.factory import make_realizer
from fitting.adapters.media.frames import save_representative_frame
from fitting.ports import PortFailure, PortRequest
from server.app import DEFAULT_GARMENTS_DIR, GARMENTS_DIR_ENV
from server.garments import GarmentDirectory
from server.wiring import AdapterConfigError, AdapterSettings

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GARMENT_ID = "tee_regular_m_navy_v1"
EXIT_OK, EXIT_SETUP, EXIT_FAILED = 0, 2, 3
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


class _NoCalls:
    """드라이런용 클라이언트. 부르면 바로 알 수 있게 예외를 낸다."""

    def __getattr__(self, name: str):
        raise RuntimeError("드라이런에서는 fal을 부르지 않습니다")


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


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="렌더 클립을 fal로 실사화한다 (기본은 드라이런)")
    parser.add_argument("--input", type=Path, help="렌더 클립(.mp4). 이어 받기면 생략")
    parser.add_argument("--name", help="작업 이름(결과 폴더). 기본은 지금 시각")
    parser.add_argument("--garment-id", default=DEFAULT_GARMENT_ID, help="프롬프트에 쓸 의류 ID")
    parser.add_argument(
        "--garment-text", help="프롬프트의 옷 설명(영어). 예: 'plain black cotton T-shirt'"
    )
    parser.add_argument("--garments-dir", type=Path, help="등록 의류 폴더 (기본 FIT_GARMENTS_DIR)")
    parser.add_argument("--resolution", choices=["480p", "720p"], default="720p")
    parser.add_argument("--intensity", choices=["light", "strong", "strong-v2"], default="light")
    parser.add_argument("--max-cost-usd", type=float, default=0.5, help="예상 비용 상한")
    parser.add_argument("--timeout", type=int, default=600, help="대기 상한(초)")
    parser.add_argument("--out-dir", type=Path, help="결과 상위 폴더 (기본 artifacts/realize)")
    parser.add_argument("--yes", action="store_true", help="실제로 fal을 호출한다 (비용 발생)")
    return parser.parse_args(argv)


def _garments_dir(arg: Path | None) -> Path:
    if arg is not None:
        return arg
    env = os.environ.get(GARMENTS_DIR_ENV)
    return Path(env) if env else DEFAULT_GARMENTS_DIR


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    name = args.name or datetime.now().strftime("realize_%Y%m%d_%H%M%S")
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

    options = RealizeOptions(
        intensity=args.intensity,
        resolution=args.resolution,
        max_cost_usd=args.max_cost_usd,
        garment_text=args.garment_text,
    )
    request = PortRequest(
        job_id=name,
        attempt_id=f"{name}_realize_1",
        input_artifact_ids=[clip.stem],
        config_version="tools.realize_clip",
        timeout_seconds=args.timeout,
        trace_id=name,
    )

    dry = FalRealizer(client=_NoCalls(), artifacts_dir=artifacts, work_dir=work, options=options)
    plan = dry.plan(request, garment)
    plan.pop("settings")
    (out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), "utf-8")
    render_frame = save_representative_frame(clip, out / "frame_render.png")
    estimate = plan["estimate"]
    inp = plan["input"]
    print(f"{name}: {ENDPOINT}")
    print(
        f"입력: {inp['width']}×{inp['height']}, {inp['fps']:g}fps, {inp['frame_count']}프레임 · "
        f"의류 {garment.id}"
    )
    print(f"프롬프트: {plan['arguments_preview']['prompt']}")
    print(
        f"설정: {options.intensity}, {options.resolution}, {estimate['frames']}프레임, 오디오 끔 · "
        f"예상 비용(상한) ${estimate['usd']:.4f} / 한도 ${options.max_cost_usd:.2f}"
    )
    print(f"렌더 대표 프레임: {render_frame.index}번 → {render_frame.path.name}")

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
        realizer = make_realizer(settings)
    except AdapterConfigError as error:
        print(f"설정 오류: {error}", file=sys.stderr)
        return EXIT_SETUP
    realizer.options = options

    print("\nfal 호출 중… (대기와 생성에 몇 분 걸릴 수 있습니다)")
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

    video = artifacts / f"{result.output_artifact_ids[0]}.mp4"
    real_frame = save_representative_frame(video, out / "frame_real.png")
    m = result.metrics
    print(f"완료: request_id={m.get('external_request_id')} · 재시도 {m.get('retries', 0)}회")
    print(
        f"시간: 업로드 {m.get('upload_s')}초 · 대기 {m.get('queue_s')}초 · "
        f"생성 {m.get('processing_s')}초 · 내려받기 {m.get('download_s')}초"
    )
    print(f"비용(출력 기준): ${m.get('cost_usd')} (예상 상한 ${m.get('estimated_cost_usd')})")
    print(f"실사 영상: {video}")
    print(f"실사 대표 프레임: {real_frame.index}번 → {real_frame.path.name}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
