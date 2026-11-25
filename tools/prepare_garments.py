"""등록 의류를 3D 착장용으로 준비한다: 견본 검색 → 견본 복사본 최적화 → 준비 기록 (#6, C4).

    python -m tools.prepare_garments [--garments DIR] [--out DIR] [--jobs N] [--force]
        [--only ID ...]

- 의류 폴더: `--garments` > 환경변수 `FIT_GARMENTS_DIR` > `data/garments/` (서버와 같다).
- 준비 폴더: `--out` > 환경변수 `FIT_PREPARED_DIR` > `data/prepared/`. 의류마다
  `{out}/{garment_id}/`에 `prep.json`·`design.yaml`·`specification.json`을 쓴다.
  키는 `data/prepared/README.md`.
- 의류마다: 견본 검색(`config/sample_search_v0.yaml`, 견본집 `data/samples/tee_gc_v1`)
  → 고른 견본의 design.yaml 사본을 의류의 가슴·소매·기장에 맞춤(GarmentCode용 Python 3.9
  하위 프로세스 `fitting.adapters.garmentcode.prep_fit`, 생성용 몸 mean_all)
  → 다시 잰 잔여 오차를 허용 오차(1.0 cm)로 판정(`fitting.catalog.optimize`).
  견본집 파일은 읽기만 한다.
- 의류·견본·설정 해시가 기존 준비 기록과 같으면 건너뛴다. `--force`일 때만 다시 만든다.
- `--jobs N`: 의류 N벌을 동시에 맞춘다(하위 프로세스 N개). 기본 1.
- GarmentCode용 Python: `--gc-python` > 환경변수 `FIT_GC_PYTHON`
  > `C:\\dev\\venvs\\garmentcode\\Scripts\\python.exe`.
- GarmentCode 루트: `--gc-root` > 환경변수 `FIT_RULER_GC_ROOT` > `C:\\dev\\GarmentCode`.
- 준비 실패는 3D 착장만 못 한다는 뜻이다. 의류 파일(`status` 포함)은 바꾸지 않는다.

종료 코드: 0 모든 의류를 처리함(준비 실패가 있어도), 1 처리 중 오류가 난 의류가 있음,
2 설정·입력 오류.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from fitting.catalog import optimize as opt
from fitting.catalog.search import (
    DEFAULT_SEARCH_RULES_PATH,
    SampleCandidate,
    SampleSearchError,
    load_search_rules,
    search_sample,
)
from fitting.domain import GarmentVariant
from server.env_file import load_env_file
from tools.console import use_utf8_output

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_VERSION = "0.1.0"
GARMENTS_DIR_ENV = "FIT_GARMENTS_DIR"
DEFAULT_GARMENTS_DIR = REPO_ROOT / "data" / "garments"
DEFAULT_CATALOG = REPO_ROOT / "data" / "samples" / "tee_gc_v1"
DEFAULT_FIT_CONFIG = REPO_ROOT / "config" / "pattern" / "pattern_fit_v0.json"
GC_PYTHON_ENV = "FIT_GC_PYTHON"
DEFAULT_GC_PYTHON = Path(r"C:\dev\venvs\garmentcode\Scripts\python.exe")
GC_ROOT_ENV = "FIT_RULER_GC_ROOT"
DEFAULT_GC_ROOT = Path(r"C:\dev\GarmentCode")
FIT_TIMEOUT_S = 3600  # 의류 1벌 맞춤 한도. 견본 맞춤이 6~16분 걸렸다(C3)

EXIT_OK, EXIT_FAILED, EXIT_CONFIG = 0, 1, 2

# (job.json, out_dir) -> (returncode, stderr 끝부분). 테스트는 가짜로 바꾼다.
FitRunner = Callable[[Path, Path], tuple[int, str]]


def _env_path(name: str, default: Path, environ: Mapping[str, str]) -> Path:
    value = (environ.get(name) or "").strip()
    return Path(value) if value else default


def _rel(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def config_record(path: Path, name: str, version: str) -> dict[str, str]:
    return {"path": _rel(path), "name": name, "version": version, "sha256": opt.sha256_file(path)}


@dataclass(frozen=True)
class Context:
    """한 번의 실행에서 모든 의류가 같이 쓰는 입력."""

    catalog_dir: Path
    catalog: dict[str, Any]
    samples: list[SampleCandidate]
    rules: Any
    search_config: dict[str, str]
    fit_config_path: Path
    fit_config: dict[str, Any]
    fit_config_record: dict[str, str]
    gc_root: Path
    out_dir: Path

    @property
    def tolerance_cm(self) -> float:
        return float(self.fit_config["tolerance_cm"])


def load_context(
    catalog_dir: Path, fit_config_path: Path, search_rules_path: Path, gc_root: Path, out_dir: Path
) -> Context:
    catalog = json.loads((catalog_dir / "catalog.json").read_text(encoding="utf-8"))
    samples = [SampleCandidate.model_validate(s) for s in catalog["samples"]]
    rules = load_search_rules(search_rules_path)
    fit_cfg = json.loads(fit_config_path.read_text(encoding="utf-8"))
    catalog_sha = opt.sha256_file(catalog_dir / "catalog.json")
    return Context(
        catalog_dir=catalog_dir,
        catalog={**catalog, "_sha256": catalog_sha},
        samples=samples,
        rules=rules,
        search_config=config_record(search_rules_path, search_rules_path.stem, rules.version),
        fit_config_path=fit_config_path,
        fit_config=fit_cfg,
        fit_config_record=config_record(fit_config_path, fit_cfg["name"], fit_cfg["version"]),
        gc_root=gc_root,
        out_dir=out_dir,
    )


def subprocess_runner(gc_python: Path, gc_root: Path, timeout_s: int = FIT_TIMEOUT_S) -> FitRunner:
    """GarmentCode용 Python으로 prep_fit을 돌리는 실행기."""

    def run(job_path: Path, out_dir: Path) -> tuple[int, str]:
        env = dict(
            os.environ,
            PYTHONIOENCODING="utf-8",
            PYTHONPATH=os.pathsep.join([str(REPO_ROOT), str(gc_root)]),
        )
        cmd = [
            str(gc_python),
            "-m",
            "fitting.adapters.garmentcode.prep_fit",
            str(job_path),
            str(out_dir),
        ]
        try:
            p = subprocess.run(
                cmd,
                cwd=str(REPO_ROOT),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return -1, f"prep_fit timed out after {timeout_s} s"
        (Path(out_dir) / "prep_fit_log.txt").write_text(
            p.stdout + "\n--- stderr ---\n" + p.stderr, encoding="utf-8"
        )
        return p.returncode, p.stderr.strip()[-1000:]

    return run


def _base_record(garment_id: str, garment_file: Path, garment_sha: str) -> dict[str, Any]:
    return {
        "schema": opt.PREP_SCHEMA,
        "garment_id": garment_id,
        "garment_file": garment_file.name,
        "garment_sha256": garment_sha,
        "status": None,
        "failure_reason": None,
        "failure_detail": None,
    }


def _search_record(result) -> dict[str, Any]:
    top = result.ranking[0]
    return {
        "score_cm": result.score_cm,
        "exact_match": result.exact_match,
        "tie_broken": result.tie_broken,
        "compared": [c.model_dump() for c in top.compared],
        "skipped": [s.dimension for s in top.skipped],
        "runner_up": (
            None
            if len(result.ranking) < 2
            else {"sample_id": result.ranking[1].sample_id, "score_cm": result.ranking[1].score_cm}
        ),
    }


def _write_prep(folder: Path, record: dict[str, Any]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    text = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    (folder / opt.PREP_FILE).write_bytes(text.encode("utf-8"))


def prepare_one(
    garment_path: Path,
    ctx: Context,
    runner: FitRunner,
    *,
    force: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """의류 1벌. 반환: 준비 기록(건너뛰었으면 기존 기록에 `"_skipped": true`)."""
    raw = garment_path.read_bytes()
    garment_sha = opt.sha256_bytes(raw)
    try:
        garment = GarmentVariant.model_validate(json.loads(raw.decode("utf-8")))
    except (ValueError, ValidationError) as error:
        gid = garment_path.stem
        record = _base_record(gid, garment_path, garment_sha)
        record.update(
            status=opt.FAILED,
            failure_reason=opt.FailureReason.GARMENT_INVALID,
            failure_detail=f"의류 파일을 읽지 못했습니다: {type(error).__name__}",
        )
        record["created_at"] = date.today().isoformat()
        _write_prep(ctx.out_dir / gid, record)
        return record

    folder = ctx.out_dir / garment.id
    record = _base_record(garment.id, garment_path, garment_sha)
    measurements = garment.measurements.model_dump()
    targets = opt.fit_targets(measurements)
    record["targets_cm"] = targets
    record["notes"] = opt.notes_for(targets)
    record["tolerance_cm"] = ctx.tolerance_cm
    record["configs"] = {
        "pattern_fit": ctx.fit_config_record,
        "sample_search": ctx.search_config,
        "catalog": {
            "path": _rel(ctx.catalog_dir / "catalog.json"),
            "name": ctx.catalog["catalog"],
            "version": ctx.catalog["version"],
            "sha256": ctx.catalog["_sha256"],
        },
    }
    record["tool"] = {"name": "tools.prepare_garments", "version": TOOL_VERSION}

    try:
        result = search_sample(garment.measurements, ctx.samples, ctx.rules)
    except SampleSearchError as error:
        inputs = {
            "garment_sha256": garment_sha,
            "configs": record["configs"],
            "tool_version": TOOL_VERSION,
        }
        key = opt.inputs_key(inputs)
        if not force and opt.is_up_to_date(opt.read_prep(folder), key):
            return {**opt.read_prep(folder), "_skipped": True}
        record.update(
            status=opt.FAILED,
            failure_reason=opt.FailureReason.SAMPLE_SEARCH_FAILED,
            failure_detail=f"{error.code}: {error}",
        )
        record.update(inputs_key=key, created_at=date.today().isoformat())
        _replace_folder(folder, record, None)
        log(f"[{garment.id}] FAILED {record['failure_reason']}")
        return record

    sample_id = result.selected_sample_id
    sample_dir = ctx.catalog_dir / sample_id
    sample = json.loads((sample_dir / "sample.json").read_text(encoding="utf-8"))
    sample_design = sample_dir / sample["files"]["design"]
    inputs = {
        "garment_sha256": garment_sha,
        "sample_id": sample_id,
        "sample_pattern_sha256": sample["pattern_sha256"],
        "sample_design_sha256": opt.sha256_file(sample_design),
        "configs": record["configs"],
        "tool_version": TOOL_VERSION,
    }
    key = opt.inputs_key(inputs)
    existing = opt.read_prep(folder)
    if not force and opt.is_up_to_date(existing, key):
        log(f"[{garment.id}] 건너뜀 (같은 입력의 준비 기록 있음)")
        return {**existing, "_skipped": True}

    sample_meas = sample["measurements_cm"]
    record.update(
        sample_id=sample_id,
        search_score_cm=result.score_cm,
        search=_search_record(result),
        sample_pattern_sha256=sample["pattern_sha256"],
        sample_measurements_cm=sample_meas,
        shoulder=opt.shoulder_record(measurements.get(opt.SHOULDER), sample_meas.get(opt.SHOULDER)),
        inputs_key=key,
    )
    log(f"[{garment.id}] 견본 {sample_id} (점수 {result.score_cm} cm) 맞춤 시작")
    t0 = time.time()
    work = Path(tempfile.mkdtemp(prefix=f"prep_{garment.id}_", dir=ctx.out_dir))
    try:
        job = {
            "targets": targets,
            "sample_design_yaml": str(sample_design.resolve()),
            "fit_config": str(ctx.fit_config_path.resolve()),
            "gc_root": str(ctx.gc_root),
        }
        job_path = work / "job.json"
        job_path.write_text(json.dumps(job, indent=2), encoding="utf-8")
        out = work / "out"
        out.mkdir()
        rc, err = runner(job_path, out)
        fit_path = out / "fit_result.json"
        if rc != 0 or not fit_path.is_file():
            record.update(
                status=opt.FAILED,
                failure_reason=opt.FailureReason.FIT_ERROR,
                failure_detail=f"맞춤 하위 프로세스 실패 (rc={rc}): {err[-300:]}",
            )
            record.update(seconds=round(time.time() - t0, 1), created_at=date.today().isoformat())
            _replace_folder(folder, record, None)
            log(f"[{garment.id}] FAILED FIT_ERROR rc={rc}")
            return record
        fit = json.loads(fit_path.read_text(encoding="utf-8"))
        measured = fit["measured_cm"]
        status, reason, detail = opt.judge(
            targets, measured, ctx.tolerance_cm, self_intersecting=bool(fit["self_intersecting"])
        )
        record.update(
            status=status,
            failure_reason=reason,
            failure_detail=detail,
            fitted_measurements_cm={f: round(float(measured[f]), 3) for f in opt.MEASURE_FIELDS},
            residuals_cm=opt.residuals(targets, measured),
            design_params=fit["design_params"],
            fitted_params=fit["fitted_params"],
            params_at_bound=fit["params_at_bound"],
            any_param_at_bound=any(fit["params_at_bound"].values()),
            self_intersecting=bool(fit["self_intersecting"]),
            optimizer=fit["optimizer"],
            generation_body={"body_yaml": fit["body_yaml"], "sha256": fit["body_yaml_sha256"]},
            design=opt.DESIGN_FILE,
            specification=opt.SPEC_FILE,
            pattern_sha256=fit["pattern_sha256"],
            seconds=round(time.time() - t0, 1),
            created_at=date.today().isoformat(),
        )
        _replace_folder(folder, record, out)
        log(
            f"[{garment.id}] {status} 잔여 {record['residuals_cm']} 어깨 차이 "
            f"{record['shoulder']['diff_cm']} {record['seconds']} s"
        )
        return record
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _replace_folder(folder: Path, record: dict[str, Any], out: Path | None) -> None:
    """의류 준비 폴더를 새 결과로 바꾼다. 다른 파일은 남기지 않는다."""
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    if out is not None:
        for name in (opt.DESIGN_FILE, opt.SPEC_FILE):
            shutil.copyfile(out / name, folder / name)
    _write_prep(folder, record)


def garment_files(garments_dir: Path, only: list[str] | None) -> list[Path]:
    files = sorted(p for p in garments_dir.glob("*.json") if p.is_file())
    if only:
        wanted = set(only)
        files = [p for p in files if p.stem in wanted]
    return files


def summarize(records: list[dict[str, Any]]) -> str:
    statuses = Counter(r.get("status") for r in records)
    reasons = Counter(r.get("failure_reason") for r in records if r.get("status") == opt.FAILED)
    skipped = sum(1 for r in records if r.get("_skipped"))
    lines = [
        f"의류 {len(records)}벌: READY {statuses.get(opt.READY, 0)}, "
        f"FAILED {statuses.get(opt.FAILED, 0)} (건너뜀 {skipped})"
    ]
    for reason, n in sorted(reasons.items()):
        lines.append(f"  실패 {reason}: {n}")
    return "\n".join(lines)


def main(
    argv: list[str] | None = None,
    *,
    runner: FitRunner | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    env = os.environ if environ is None else environ
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--garments", type=Path, help="의류 폴더 (기본: FIT_GARMENTS_DIR > data/garments)"
    )
    ap.add_argument("--out", type=Path, help="준비 폴더 (기본: FIT_PREPARED_DIR > data/prepared)")
    ap.add_argument("--samples", type=Path, default=DEFAULT_CATALOG, help="견본집 폴더")
    ap.add_argument("--fit-config", type=Path, default=DEFAULT_FIT_CONFIG)
    ap.add_argument("--search-config", type=Path, default=DEFAULT_SEARCH_RULES_PATH)
    ap.add_argument("--gc-root", type=Path)
    ap.add_argument("--gc-python", type=Path)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--force", action="store_true", help="같은 입력이어도 다시 만든다")
    ap.add_argument("--only", nargs="+", metavar="GARMENT_ID", help="이 의류만")
    a = ap.parse_args(argv)

    garments_dir = a.garments or _env_path(GARMENTS_DIR_ENV, DEFAULT_GARMENTS_DIR, env)
    out_dir = (a.out or opt.prepared_dir(env)).resolve()
    gc_root = (a.gc_root or _env_path(GC_ROOT_ENV, DEFAULT_GC_ROOT, env)).resolve()
    gc_python = a.gc_python or _env_path(GC_PYTHON_ENV, DEFAULT_GC_PYTHON, env)
    if a.jobs < 1:
        print("--jobs는 1 이상이어야 합니다", file=sys.stderr)
        return EXIT_CONFIG
    if not garments_dir.is_dir():
        print(f"의류 폴더가 없습니다: {garments_dir}", file=sys.stderr)
        return EXIT_CONFIG
    if runner is None:
        if not gc_python.is_file():
            print(f"GarmentCode용 Python이 없습니다({GC_PYTHON_ENV}): {gc_python}", file=sys.stderr)
            return EXIT_CONFIG
        if not (gc_root / "assets" / "garment_programs").is_dir():
            print(f"GarmentCode 루트가 아닙니다({GC_ROOT_ENV}): {gc_root}", file=sys.stderr)
            return EXIT_CONFIG
        runner = subprocess_runner(gc_python, gc_root)
    try:
        ctx = load_context(
            a.samples.resolve(), a.fit_config.resolve(), a.search_config, gc_root, out_dir
        )
    except (OSError, ValueError, KeyError) as error:
        print(f"설정 오류: {error}", file=sys.stderr)
        return EXIT_CONFIG
    files = garment_files(garments_dir, a.only)
    if not files:
        print(f"준비할 의류가 없습니다: {garments_dir}", file=sys.stderr)
        return EXIT_CONFIG
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"의류 {len(files)}벌 → {out_dir} (jobs {a.jobs})", flush=True)

    errors = []

    def one(path: Path) -> dict[str, Any] | None:
        try:
            return prepare_one(path, ctx, runner, force=a.force, log=lambda s: print(s, flush=True))
        except Exception as error:  # 한 벌의 예상 밖 오류로 나머지를 멈추지 않는다
            errors.append(f"{path.name}: {type(error).__name__}: {error}")
            print(f"[{path.stem}] 오류: {type(error).__name__}: {error}", flush=True)
            return None

    with ThreadPoolExecutor(max_workers=a.jobs) as ex:
        records = [r for r in ex.map(one, files) if r is not None]
    print(summarize(records))
    for e in errors:
        print(f"  처리 오류 {e}", file=sys.stderr)
    return EXIT_FAILED if errors else EXIT_OK


if __name__ == "__main__":
    use_utf8_output()
    load_env_file()  # 저장소 맨 위 .env: 비어 있는 환경변수만 채운다(server/env_file.py)
    sys.exit(main())
