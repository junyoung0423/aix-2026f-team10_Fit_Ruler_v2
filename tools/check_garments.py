"""등록 의류 폴더를 점검한다 (#3, C3). 필수 치수 누락·단위 오류·ID 중복을 찾는다.

    python -m tools.check_garments [폴더] [--min-ready N]

- 폴더 기본값: 환경변수 `FIT_GARMENTS_DIR`, 없으면 `data/garments` (서버와 같다).
- 폴더 안의 `*.json`마다 `GarmentVariant`로 검증한다.
- 요약: 파일 수, 상태별 개수, 검증 실패, READY인데 가슴·어깨가 없는 의류, 단위가 의심스러운 값,
  ID 중복(파일 이름과 `id`가 다른 것 포함).
- 의류 개별 값은 출력하지 않고 파일 이름·필드·규칙만 출력한다(AI Hub 변환 의류를 보고에 옮길 때
  값이 섞이지 않게).

종료 코드: 0 통과, 1 검증을 통과한 READY 의류가 `--min-ready`(기본 0)보다 적음,
2 폴더를 읽을 수 없음.
문제 의류가 있어도 READY 개수가 충분하면 0이다. 문제는 요약에 나온다.
"""

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from fitting.domain import GarmentStatus, GarmentVariant
from tools.console import use_utf8_output

REPO_ROOT = Path(__file__).resolve().parents[1]
GARMENTS_DIR_ENV = "FIT_GARMENTS_DIR"
DEFAULT_GARMENTS_DIR = REPO_ROOT / "data" / "garments"

EXIT_OK = 0
EXIT_TOO_FEW_READY = 1
EXIT_UNREADABLE = 2

# 받는 범위(cm, 경계 포함). AI Hub 71501 변환 규칙과 같다(decisions.md 2026-11-11 [제안],
# fitting/adapters/aihub/tables.py). 기장은 앞길이 범위를 쓴다. 범위 밖은 mm 단위·입력 오류로 본다.
UNIT_RANGES_CM = {
    "chest_girth_cm": (60.0, 200.0),
    "waist_girth_cm": (50.0, 200.0),
    "shoulder_width_cm": (25.0, 80.0),
    "sleeve_length_cm": (5.0, 45.0),
    "body_length_cm": (35.0, 100.0),
}

# 단면 너비가 둘레 칸에 그대로 들어간 듯한 값 [제안].
# - 성인 반팔의 가슴·허리 단면은 40~65 cm 정도다(docs/spikes/aihub-71501.md).
#   둘레가 70 cm 미만이면 단면일 수 있다. AI Hub 반팔 라운드넥 가슴 1% 값은 64 cm라
#   정상 의류도 드물게 걸린다(의심 표시만 한다).
# - 둘레는 어깨너비의 약 2배다(시연 의류 108/46 = 2.3). 1.6배보다 작으면 단면일 수 있다.
FLAT_WIDTH_GIRTH_MAX_CM = 70.0
FLAT_WIDTH_GIRTH_TO_SHOULDER_MIN = 1.6
GIRTH_FIELDS = ("chest_girth_cm", "waist_girth_cm")


@dataclass
class GarmentReport:
    files: int = 0
    status_counts: Counter = field(default_factory=Counter)
    invalid: list = field(default_factory=list)  # (파일, 이유)
    ready_missing_required: list = field(default_factory=list)  # (파일, [필드])
    suspicious: list = field(default_factory=list)  # (파일, 필드, 규칙)
    duplicate_ids: dict = field(default_factory=dict)  # id -> [파일]
    id_name_mismatch: list = field(default_factory=list)  # 파일
    ready_ids: list = field(default_factory=list)

    @property
    def ready(self) -> int:
        return len(self.ready_ids)

    @property
    def problem_files(self) -> set:
        files = {f for f, _ in self.invalid} | {f for f, _ in self.ready_missing_required}
        files |= {f for f, _, _ in self.suspicious} | set(self.id_name_mismatch)
        for names in self.duplicate_ids.values():
            files |= set(names)
        return files


def _suspicious(m: dict) -> list[tuple[str, str]]:
    """(필드, 규칙) 목록. m은 measurements dict(검증 전일 수 있다)."""
    out = []
    for name, (lo, hi) in UNIT_RANGES_CM.items():
        v = m.get(name)
        if isinstance(v, int | float) and not isinstance(v, bool) and not lo <= v <= hi:
            out.append((name, f"범위 {lo:g}~{hi:g} cm 밖"))
    shoulder = m.get("shoulder_width_cm")
    for name in GIRTH_FIELDS:
        v = m.get(name)
        if not isinstance(v, int | float) or isinstance(v, bool):
            continue
        if v < FLAT_WIDTH_GIRTH_MAX_CM:
            out.append((name, f"{FLAT_WIDTH_GIRTH_MAX_CM:g} cm 미만(단면 너비 의심)"))
        elif (
            isinstance(shoulder, int | float)
            and shoulder > 0
            # 곱셈의 부동소수 꼬리(1.6 × 46 = 73.600…01)로 경계가 흔들리지 않게 반올림한다
            and v < round(FLAT_WIDTH_GIRTH_TO_SHOULDER_MIN * shoulder, 6)
        ):
            out.append(
                (name, f"어깨너비의 {FLAT_WIDTH_GIRTH_TO_SHOULDER_MIN:g}배 미만(단면 너비 의심)")
            )
    return out


def check_garments(directory: Path) -> GarmentReport:
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(f"폴더가 없습니다: {directory}")
    report = GarmentReport()
    ids: dict[str, list[str]] = {}
    for path in sorted(directory.glob("*.json")):
        report.files += 1
        name = path.name
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            report.invalid.append((name, f"JSON을 읽을 수 없음 ({type(error).__name__})"))
            continue
        if not isinstance(raw, dict):
            report.invalid.append((name, "JSON 객체가 아님"))
            continue
        m = raw.get("measurements") if isinstance(raw.get("measurements"), dict) else {}
        if raw.get("status") == GarmentStatus.READY.value:
            missing = [f for f in ("chest_girth_cm", "shoulder_width_cm") if m.get(f) is None]
            if missing:
                report.ready_missing_required.append((name, missing))
        report.suspicious.extend((name, f, rule) for f, rule in _suspicious(m))
        if isinstance(raw.get("id"), str):
            ids.setdefault(raw["id"], []).append(name)
            if path.stem != raw["id"]:
                report.id_name_mismatch.append(name)
        try:
            garment = GarmentVariant.model_validate(raw)
        except ValidationError as error:
            fields = sorted(
                {".".join(str(p) for p in e["loc"]) or "(모델)" for e in error.errors()}
            )
            report.invalid.append((name, f"GarmentVariant 검증 실패: {', '.join(fields)}"))
            continue
        report.status_counts[garment.status.value] += 1
        if garment.status is GarmentStatus.READY:
            report.ready_ids.append(garment.id)
    report.duplicate_ids = {i: names for i, names in ids.items() if len(names) > 1}
    return report


def format_report(directory: Path, report: GarmentReport) -> str:
    lines = [f"등록 의류 점검: {directory}"]
    lines.append(f"  파일 {report.files}개, 검증 통과 {sum(report.status_counts.values())}개")
    statuses = ", ".join(f"{k} {v}" for k, v in sorted(report.status_counts.items())) or "없음"
    lines.append(f"  상태별: {statuses}")
    lines.append(f"  문제 의류 {len(report.problem_files)}개")
    sections = [
        ("검증 실패", [f"{f}: {why}" for f, why in report.invalid]),
        (
            "READY인데 가슴·어깨 없음",
            [f"{f}: {', '.join(fs)}" for f, fs in report.ready_missing_required],
        ),
        ("단위 의심", [f"{f}: {field_} {rule}" for f, field_, rule in report.suspicious]),
        ("ID 중복", [f"{i}: {', '.join(n)}" for i, n in sorted(report.duplicate_ids.items())]),
        ("파일 이름과 id가 다름", list(report.id_name_mismatch)),
    ]
    for title, rows in sections:
        lines.append(f"  {title}: {len(rows)}")
        lines.extend(f"    - {row}" for row in rows)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "directory",
        nargs="?",
        type=Path,
        help=f"등록 의류 폴더 (기본: 환경변수 {GARMENTS_DIR_ENV}, 없으면 data/garments)",
    )
    parser.add_argument(
        "--min-ready",
        type=int,
        default=0,
        help="검증을 통과한 READY 의류가 이보다 적으면 1로 끝난다",
    )
    args = parser.parse_args(argv)
    directory = args.directory or Path(os.environ.get(GARMENTS_DIR_ENV) or DEFAULT_GARMENTS_DIR)
    try:
        report = check_garments(directory)
    except FileNotFoundError as error:
        print(f"오류: {error}", file=sys.stderr)
        return EXIT_UNREADABLE
    print(format_report(directory, report))
    if report.ready < args.min_ready:
        print(
            f"READY 의류 {report.ready}개가 --min-ready {args.min_ready}보다 적습니다",
            file=sys.stderr,
        )
        return EXIT_TOO_FEW_READY
    print(f"READY 의류 {report.ready}개 (--min-ready {args.min_ready})")
    return EXIT_OK


if __name__ == "__main__":
    use_utf8_output()
    sys.exit(main())
