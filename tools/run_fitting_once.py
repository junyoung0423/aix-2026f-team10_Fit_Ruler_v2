"""피팅 한 번을 서버 없이 한 프로세스 안에서 끝까지 돌린다 (#12 #13). 통합 실행 명령(B6)의 초안이다.

    python -m tools.run_fitting_once                       # 기준 사례, 기본 어댑터, 실사화 미동의
    python -m tools.run_fitting_once --adapters fake --consent
    python -m tools.run_fitting_once --garment-id tee_regular_m_navy_v1 --body 175,100,80,40,56

기본값은 기준 사례(신체 1 × 티셔츠 1)다: 키 175 · 가슴 100 · 허리 80 · 어깨 40 · 팔 56 cm와
`tee_regular_m_navy_v1`(`docs/decisions.md`의 "1주차 기준 [제안]" 1절).

어댑터는 서버와 같은 설정(`config/adapters/`)으로 고른다: `--adapters` > 환경변수
`FIT_ADAPTERS` > `default`. 단계가 바뀔 때마다 한 줄씩 출력하고, 끝나면 요약(핏 리포트·단계별 상태·
영상 경로 결과·산출물 파일 위치와 있는지)을 출력해 `summary.txt`에도 쓴다.
결과 폴더(`--out`, 기본 `artifacts/runs/{job_id}/`)에는 `request.json`, `report.json`,
`status.json`, `summary.txt`가 생기고, 어댑터가 쓰는 산출물은 그 안의 `artifacts/`에 쓴다.

실사화 포트가 실제 어댑터(가짜 아님)이고 `--consent`이면 외부 서비스 비용이 들 수 있어서 `--yes`가
있어야 진행한다. 없으면 아무것도 실행하지 않고 안내만 한다.

종료 코드: 0 작업 `SUCCEEDED`, 3 `PARTIAL_SUCCEEDED`(핏 리포트는 있고 영상 경로 실패), 2 `FAILED`,
1 설정·입력 오류(한국어 한 줄을 표준 오류에). 영상 경로가 실패하면 요약 첫 줄에 "영상 경로 실패"가
나오고 종료 코드는 0이 아니다. 대체 결과를 성공으로 보여 주지 않는다.
"""

import argparse
import json
import os
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

from pydantic import BaseModel, ValidationError

from fitting.domain import (
    FitReport,
    FittingRequest,
    FittingStatus,
    GarmentVariant,
    JobStatus,
    Mode,
    Stage,
    StageStatus,
    VisualPart,
)
from fitting.fit import RuleFitCalculator
from fitting.services.fitting import run_fitting
from server.app import DEFAULT_GARMENTS_DIR, GARMENTS_DIR_ENV
from server.artifacts import ArtifactDirectory
from server.errors import input_invalid_error
from server.garments import GarmentDirectory
from server.wiring import (
    FAKE,
    AdapterConfig,
    AdapterConfigError,
    AdapterSettings,
    build_ports,
    describe,
    load_adapter_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNS_DIR = REPO_ROOT / "artifacts" / "runs"
DEFAULT_GARMENT_ID = "tee_regular_m_navy_v1"
DEFAULT_BODY = "175,100,80,40,56"  # 키, 가슴, 허리, 어깨, 팔 (기준 신체 1)

EXIT_OK = 0
EXIT_INPUT_ERROR = 1
EXIT_FAILED = 2
EXIT_PARTIAL = 3

_BODY_FIELDS = ("height_cm", "chest_cm", "waist_cm", "shoulder_cm", "arm_length_cm")
_ALL_STAGES = tuple(Stage)

_REGION_KO = {
    "chest": "가슴",
    "shoulder": "어깨",
    "waist": "허리",
    "sleeve": "소매",
    "body_length": "총장",
}
# 상태의 산출물 → (이름, 상태 필드, 그 산출물을 만드는 포트)
_ARTIFACTS = (
    ("3D 착장 미리보기", "drape_preview_artifact_id", "drape"),
    ("정면 렌더 클립", "render_artifact_id", "render"),
    ("실사 영상", "video_artifact_id", "realize"),
    ("대표 프레임", "frame_artifact_id", "validate"),
    ("대응 렌더 프레임", "render_frame_artifact_id", "validate"),
)


class _Parser(argparse.ArgumentParser):
    """사용법 오류도 종료 코드 1로 끝낸다(argparse 기본값 2는 '작업 실패'와 겹친다)."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(EXIT_INPUT_ERROR, f"오류: {message}\n")


class _InputError(Exception):
    """명령에 준 값이 틀렸다. 메시지는 한국어다."""


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description=__doc__.splitlines()[0])
    parser.add_argument("--garment-id", help=f"의류 ID (기본 {DEFAULT_GARMENT_ID})")
    parser.add_argument("--body", help=f"키,가슴,허리,어깨,팔 (cm). 기본 {DEFAULT_BODY}")
    parser.add_argument(
        "--request", type=Path, help="FittingRequest JSON 파일 (--body 등과 함께 못 씀)"
    )
    parser.add_argument("--garments-dir", type=Path, help="등록 의류 폴더 (기본 FIT_GARMENTS_DIR)")
    parser.add_argument(
        "--adapters", help="어댑터 설정 이름이나 파일 (기본 FIT_ADAPTERS, 없으면 default)"
    )
    parser.add_argument("--mode", choices=[m.value for m in Mode], help="실행 방식 (기본 full)")
    parser.add_argument("--consent", action="store_true", help="실사화(외부 전송)에 동의한다")
    parser.add_argument("--yes", action="store_true", help="실제 실사화 어댑터의 비용을 감수한다")
    parser.add_argument("--out", type=Path, help="결과 폴더 (기본 artifacts/runs/{job_id}/)")
    return parser


# --- 입력 ---------------------------------------------------------------------------------------


def _field_lines(error: ValidationError) -> str:
    """검증 오류를 서버와 같은 한국어 문구로. 틀린 필드마다 한 줄이다."""
    fields = input_invalid_error(error.errors()).fields or []
    return "\n".join(f"  - {item.message}" for item in fields)


def _parse_body(text: str) -> dict[str, float]:
    parts = [p.strip() for p in text.split(",")]
    try:
        numbers = [float(p) for p in parts]
    except ValueError:
        numbers = []
    if len(numbers) != len(_BODY_FIELDS):
        raise _InputError(f"--body는 키,가슴,허리,어깨,팔 다섯 숫자(cm)여야 합니다: {text!r}")
    return dict(zip(_BODY_FIELDS, numbers, strict=True))


def _build_request(args: argparse.Namespace) -> FittingRequest:
    try:
        if args.request is not None:
            conflicts = [
                flag
                for flag, given in (
                    ("--body", args.body is not None),
                    ("--garment-id", args.garment_id is not None),
                    ("--mode", args.mode is not None),
                    ("--consent", args.consent),
                )
                if given
            ]
            if conflicts:
                raise _InputError(f"--request는 {', '.join(conflicts)}와 함께 쓸 수 없습니다")
            try:
                text = args.request.read_text(encoding="utf-8")
            except OSError as error:
                raise _InputError(
                    f"요청 파일을 읽을 수 없습니다: {args.request} ({error.strerror})"
                ) from error
            return FittingRequest.model_validate_json(text)
        return FittingRequest(
            garment_variant_id=DEFAULT_GARMENT_ID if args.garment_id is None else args.garment_id,
            mode=Mode(Mode.FULL.value if args.mode is None else args.mode),
            realize_consent=args.consent,
            measurements=_parse_body(DEFAULT_BODY if args.body is None else args.body),
            measurement_protocol_version="measure_v1",
        )
    except ValidationError as error:
        raise _InputError(f"입력한 값을 확인해 주세요:\n{_field_lines(error)}") from error


def _garments_dir(arg: Path | None) -> Path:
    if arg is not None:
        return arg
    env = os.environ.get(GARMENTS_DIR_ENV)
    return Path(env) if env else DEFAULT_GARMENTS_DIR


def _needs_yes(config: AdapterConfig, request: FittingRequest) -> bool:
    """실제 실사화 어댑터를 동의로 돌리면 비용이 든다."""
    return request.mode is Mode.FULL and request.realize_consent and config.ports["realize"] != FAKE


# --- 진행 출력 ----------------------------------------------------------------------------------


class _ProgressPrinter:
    """상태가 바뀔 때마다 단계가 바뀐 줄만 한 줄씩 출력한다.

    예: `[DRAPE] RUNNING`, `[DRAPE] SUCCEEDED 12.3s` (걸린 시간은 RUNNING을 본 단계만).
    """

    def __init__(self) -> None:
        self._seen = {stage: StageStatus.WAITING for stage in _ALL_STAGES}
        self._started: dict[Stage, float] = {}

    def __call__(self, status: FittingStatus) -> None:
        now = time.monotonic()
        for item in status.stages:
            if item.status is self._seen[item.stage]:
                continue
            self._seen[item.stage] = item.status
            line = f"[{item.stage.value}] {item.status.value}"
            if item.status is StageStatus.RUNNING:
                self._started[item.stage] = now
            elif item.status in (StageStatus.SUCCEEDED, StageStatus.FAILED):
                started = self._started.get(item.stage)
                if started is not None:
                    line += f" {now - started:.1f}s"
            print(line, flush=True)


# --- 요약 ---------------------------------------------------------------------------------------


def _headline(status: FittingStatus) -> str:
    visual = status.visual
    if status.status is JobStatus.FAILED:
        code = status.report.error_code.value if status.report.error_code else "알 수 없음"
        return f"피팅 실패 — 핏 리포트를 만들 수 없습니다 ({code})"
    if visual.status is StageStatus.FAILED:
        stage = visual.stage.value if visual.stage else "알 수 없음"
        code = visual.error_code.value if visual.error_code else "알 수 없음"
        return f"영상 경로 실패 — {stage} 단계, 오류 코드 {code} (핏 리포트는 있습니다)"
    if visual.status is StageStatus.SKIPPED:
        return "핏 리포트만 완료 (영상 경로는 요청하지 않았습니다)"
    if visual.skip_reason is not None:
        return "성공 — 렌더까지 완료 (실사화는 동의하지 않아 건너뜀)"
    return "성공 — 실사화·검증까지 완료"


def _region_lines(report: FitReport) -> list[str]:
    lines = []
    for region in report.regions:
        name = _REGION_KO.get(region.region.value, region.region.value)
        if region.ease_cm is not None:
            ease = f"{region.ease_cm:+.1f} cm"
        else:
            ease = "판정 안 함" if region.label.value == "NOT_EVALUATED" else "비교 안 함"
        detail = []
        if region.body_cm is not None:
            detail.append(f"몸 {region.body_cm:g}")
        if region.garment_cm is not None:
            detail.append(f"옷 {region.garment_cm:g}")
        suffix = f" ({', '.join(detail)})" if detail else ""
        lines.append(f"  {name}: {ease} · {region.label.value}{suffix}")
    return lines


def _visual_line(visual: VisualPart) -> str:
    parts = [f"상태 {visual.status.value}"]
    if visual.stage is not None:
        parts.append(f"마지막 단계 {visual.stage.value}")
    if visual.skip_reason is not None:
        parts.append(f"건너뜀 이유 {visual.skip_reason.value}")
    if visual.error_code is not None:
        parts.append(f"오류 코드 {visual.error_code.value}")
    if visual.quality_status is not None:
        quality = f"검증 {visual.quality_status.value}"
        if visual.quality_metrics:
            metrics = ", ".join(f"{k} {v:g}" for k, v in visual.quality_metrics.items())
            quality += f" ({metrics})"
        parts.append(quality)
    return " · ".join(parts)


def _artifact_lines(visual: VisualPart, config: AdapterConfig, artifacts_dir: Path) -> list[str]:
    """산출물 ID마다 두 줄: ID, 그리고 실제 파일 위치와 있는지."""
    directory = ArtifactDirectory(artifacts_dir)
    lines = []
    for label, field, port in _ARTIFACTS:
        artifact_id = getattr(visual, field)
        if artifact_id is None:
            continue
        found = directory.find(artifact_id)
        if found is not None:
            where = f"{found} — 있음 ({found.stat().st_size} bytes)"
        elif config.ports[port] == FAKE:
            where = f"{artifacts_dir / artifact_id}.* — 없음(가짜)"
        else:
            where = f"{artifacts_dir / artifact_id}.* — 없음 (어댑터가 이 이름으로 쓰지 않았습니다)"
        lines += [f"  {label}: {artifact_id}", f"      {where}"]
    return lines or ["  (산출물 없음)"]


def build_summary(
    *,
    request: FittingRequest,
    garment: GarmentVariant,
    status: FittingStatus,
    report: FitReport | None,
    config: AdapterConfig,
    artifacts_dir: Path,
    out_dir: Path,
) -> str:
    """사람이 읽는 요약. 첫 줄이 결과(영상 경로가 실패하면 "영상 경로 실패")다."""
    body = request.measurements
    consent = "실사화 동의" if request.realize_consent else "실사화 동의 안 함"
    demo = " (시연용 의류)" if garment.is_demo else ""
    adapters = " · ".join(f"{name}={spec}" for name, spec in describe(config).items())
    lines = [
        f"결과: {_headline(status)}",
        f"작업: {status.job_id} · 상태 {status.status.value}",
        f"의류: {garment.id}{demo}",
        f"신체(cm): 키 {body.height_cm:g} · 가슴 {body.chest_cm:g} · 허리 {body.waist_cm:g}"
        f" · 어깨 {body.shoulder_cm:g} · 팔 {body.arm_length_cm:g}",
        f"실행: 모드 {request.mode.value} · {consent}",
        f"어댑터: {adapters}",
        f"결과 폴더: {out_dir}",
        "",
    ]
    if report is not None:
        lines += [f"[핏 리포트] {report.rules_version}", f"요약: {report.summary}"]
        lines += _region_lines(report)
        if report.warnings:
            lines.append(f"경고: {', '.join(report.warnings)}")
    else:
        lines.append("[핏 리포트] 없음")
    lines += ["", "[단계]"]
    lines += [f"  {item.stage.value:<14} {item.status.value}" for item in status.stages]
    lines += ["", f"[영상 경로] {_visual_line(status.visual)}", "", "[산출물]"]
    lines += _artifact_lines(status.visual, config, artifacts_dir)
    return "\n".join(lines) + "\n"


def exit_code(status: FittingStatus) -> int:
    """작업 상태 → 종료 코드. 영상 경로가 실패하면 0이 아니다."""
    if status.status is JobStatus.SUCCEEDED:
        return EXIT_OK
    if status.status is JobStatus.PARTIAL_SUCCEEDED:
        return EXIT_PARTIAL
    return EXIT_FAILED


# --- 실행 ---------------------------------------------------------------------------------------


def _dump(model: BaseModel) -> str:
    return json.dumps(model.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as stop:  # argparse가 사용법 오류(1)나 --help(0)로 끝낸 경우
        return stop.code if isinstance(stop.code, int) else EXIT_OK

    try:
        request = _build_request(args)
        garment = GarmentDirectory(_garments_dir(args.garments_dir)).get(request.garment_variant_id)
        if garment is None:
            raise _InputError(
                f"의류를 찾을 수 없습니다: {request.garment_variant_id} "
                f"(폴더: {_garments_dir(args.garments_dir)})"
            )
        config = load_adapter_config(args.adapters)
        if _needs_yes(config, request) and not args.yes:
            raise _InputError(
                f"실사화 어댑터({config.ports['realize']})는 실제 외부 서비스를 부르고 비용이 "
                "들 수 있습니다. 동의(--consent)로 실행하려면 --yes를 함께 주세요. "
                "아무것도 실행하지 않았습니다."
            )
        job_id = f"fit_{uuid.uuid4().hex[:12]}"
        out_dir = (args.out or DEFAULT_RUNS_DIR / job_id).expanduser()
        artifacts_dir = out_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        settings = AdapterSettings.from_environ(artifacts_dir=artifacts_dir)
        ports = build_ports(config, settings, fit=RuleFitCalculator())
    except (_InputError, AdapterConfigError) as error:
        print(f"오류: {error}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    (out_dir / "request.json").write_text(_dump(request), encoding="utf-8")
    adapters = " · ".join(f"{name}={spec}" for name, spec in describe(config).items())
    print(f"피팅 시작: {job_id} · 어댑터 {adapters}", flush=True)
    try:
        outcome = run_fitting(
            request,
            garment,
            ports,
            job_id=job_id,
            created_at=datetime.now(timezone.utc),
            on_progress=_ProgressPrinter(),
        )
    except Exception as error:  # 어댑터가 PortFailure 대신 예상 밖 예외를 던졌다 (처리 완성은 B5)
        traceback.print_exc()
        print(
            f"오류: 피팅 실행 중 예상하지 못한 예외가 났습니다 ({type(error).__name__}: {error})",
            file=sys.stderr,
        )
        return EXIT_FAILED

    (out_dir / "status.json").write_text(_dump(outcome.status), encoding="utf-8")
    if outcome.report is not None:
        (out_dir / "report.json").write_text(_dump(outcome.report), encoding="utf-8")
    summary = build_summary(
        request=request,
        garment=garment,
        status=outcome.status,
        report=outcome.report,
        config=config,
        artifacts_dir=artifacts_dir,
        out_dir=out_dir,
    )
    (out_dir / "summary.txt").write_text(summary, encoding="utf-8")
    print()
    print(summary, end="")
    return exit_code(outcome.status)


if __name__ == "__main__":
    sys.exit(main())
