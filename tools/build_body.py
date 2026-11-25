"""D3: 입력 치수로 SMPL 몸을 만들어 A자세 OBJ와 기록을 남긴다 (서버 없이, 서버와 같은 어댑터 경로).

    python -m tools.build_body --case body1
    python -m tools.build_body --case body2
    python -m tools.build_body --body 175,100,80,44,60 --name team_example

`SMPL_MODEL_DIR`(또는 `--model-dir`)에 SMPL 중성 모델 폴더가 있어야 한다(models/README.md).
결과는 `artifacts/body_fit/<이름>/`에 남는다(`artifacts/`는 커밋하지 않는다).

- `<이름>_body_mesh.obj`  A자세 몸 메시(미터). 서버 산출물과 같은 이름 규칙이고 C4 착장 입력이다
- `body_fit.json`         입력값·생성값·오차·형상 계수·크기·시작점별 결과(서버의 기록과 같은 내용)
- `debug_front.png`       정면 투영 위에 가슴·허리 높이와 어깨 끝점(D2 확인 그림과 같은 방식)

종료 코드: 0 통과, 3 허용 오차 초과(`BODY_FIT_NOT_CONVERGED`), 1 설정 오류.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from fitting.adapters.smpl import BODY_FIT_RECORD, SmplBodyBuilder
from fitting.adapters.smpl.factory import make_body_builder
from fitting.body.fit import FIELD_LABEL, FIELDS
from fitting.domain import BodyInput
from fitting.ports import PortFailure, PortRequest
from server.wiring import AdapterConfigError, AdapterSettings
from tools.console import use_utf8_output

ROOT = Path(__file__).resolve().parents[1]

# 기준 신체 (docs/decisions.md "1주차 기준 [제안]" 1절). 키, 가슴, 허리, 어깨, 팔 (cm)
CASES: dict[str, tuple[float, float, float, float, float]] = {
    "body1": (175.0, 100.0, 80.0, 40.0, 56.0),  # 신체 1 (전체 실행용)
    "body2": (166.0, 97.5, 81.0, 36.0, 52.0),  # 신체 2 (D2 SMPL 여성 평균을 잰 값)
    "team_example": (175.0, 100.0, 80.0, 44.0, 60.0),  # 지금 팀 예시 입력(#22 1절 비교용)
}
EXIT_PASSED, EXIT_SETUP, EXIT_NOT_CONVERGED = 0, 1, 3
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


def parse_body(text: str) -> tuple[float, float, float, float, float]:
    """`175,100,80,40,56` → (키, 가슴, 허리, 어깨, 팔)."""
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != len(FIELDS):
        raise argparse.ArgumentTypeError("키,가슴,허리,어깨,팔 다섯 값을 쉼표로 적으세요")
    try:
        values = tuple(float(p) for p in parts)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"숫자가 아닌 값이 있습니다: {text}") from error
    return values  # type: ignore[return-value]


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="입력 치수로 SMPL 몸을 만들어 OBJ와 기록을 남긴다")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--case", choices=sorted(CASES), help="기준 신체 이름")
    source.add_argument("--body", type=parse_body, help="키,가슴,허리,어깨,팔 (cm)")
    parser.add_argument("--name", help="결과 폴더 이름 (기본: --case 이름 또는 custom)")
    parser.add_argument("--model-dir", type=Path, help="SMPL 모델 폴더 (기본: SMPL_MODEL_DIR)")
    parser.add_argument("--out-dir", type=Path, help="결과의 상위 폴더 (기본: artifacts/body_fit)")
    parser.add_argument("--timeout", type=int, default=600, help="최적화 시간 한도(초)")
    parser.add_argument("--no-image", action="store_true", help="확인 그림을 만들지 않는다")
    return parser.parse_args(argv)


def _settings(args: argparse.Namespace, out_root: Path, name: str) -> AdapterSettings:
    model_dir = args.model_dir or (os.environ.get("SMPL_MODEL_DIR") or "").strip() or None
    return AdapterSettings(
        artifacts_dir=out_root / name,
        work_dir=out_root,  # 기록은 work_dir/{이름}/body_fit.json → 같은 폴더에 모인다
        blender_path=None,
        smpl_model_dir=Path(model_dir) if model_dir else None,
        fal_key_present=False,
        repo_root=ROOT,
    )


def _save_image(builder: SmplBodyBuilder, record: dict, path: Path) -> None:
    import numpy as np

    from tools.smpl_body import save_debug_image

    betas, scale = np.array(record["betas"]), float(record["scale"])
    vertices, _ = builder.body_model.mesh(betas, scale)
    measured, debug = builder.body_model.measure(betas, scale)
    save_debug_image(vertices, measured, debug, path)


def _print_summary(record: dict, out_dir: Path) -> None:
    print(f"{'치수':<8}{'입력':>8}{'생성':>8}{'오차':>8}{'허용':>6}")
    for name in FIELDS:
        error, limit = record["error_cm"][name], record["tolerance_cm"][name]
        passed = error is not None and abs(error) <= limit + 1e-9
        error_text = "—" if error is None else f"{error:+.1f}"
        print(
            f"{FIELD_LABEL[name]:<8}{record['input_cm'][name]:>8.1f}"
            f"{record['fitted_cm'][name]:>8.1f}{error_text:>8}{limit:>6.1f}"
            + ("" if passed else " ✗")
        )
    optimization = record["optimization"]
    print(
        f"형상 계수 {len(record['betas'])}개, 크기 {record['scale']:.4f}, "
        f"측정 {optimization['evaluations']}회, {optimization['seconds']}초, "
        f"시작점 {len(optimization['starts'])}개 사용({optimization['stopped_by']})"
    )
    print(f"결과 폴더: {out_dir}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    values = CASES[args.case] if args.case else args.body
    name = args.name or args.case or "custom"
    if not _NAME.fullmatch(name):
        print(f"이름에는 영문·숫자·_·-만 씁니다: {name}", file=sys.stderr)
        return EXIT_SETUP
    out_root = args.out_dir or ROOT / "artifacts" / "body_fit"

    try:
        builder = make_body_builder(_settings(args, out_root, name))
    except AdapterConfigError as error:
        print(f"설정 오류: {error}", file=sys.stderr)
        return EXIT_SETUP

    body = BodyInput(
        **dict(zip(FIELDS, values, strict=True)), measurement_protocol_version="measure_v1"
    )
    request = PortRequest(
        job_id=name,
        attempt_id=f"{name}_body_fit_1",
        config_version="tools.build_body",
        timeout_seconds=args.timeout,
        trace_id=name,
    )
    print(f"{name}: {' / '.join(f'{v:g}' for v in values)} cm, 모델 {builder.model_name}")
    started = time.monotonic()
    try:
        builder.build_body(request, body)
        code = EXIT_PASSED
    except PortFailure as failure:
        print(f"실패 {failure.error.code.value}: {failure.error.safe_message}")
        code = EXIT_NOT_CONVERGED
    out_dir = out_root / name
    record = json.loads((out_dir / BODY_FIT_RECORD).read_text(encoding="utf-8"))
    if not args.no_image:
        _save_image(builder, record, out_dir / "debug_front.png")
    print(f"{'통과' if code == EXIT_PASSED else '실패'} ({time.monotonic() - started:.0f}초)")
    _print_summary(record, out_dir)
    return code


if __name__ == "__main__":
    use_utf8_output()
    sys.exit(main())
