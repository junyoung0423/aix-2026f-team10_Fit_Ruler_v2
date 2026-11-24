"""C6: 준비된 READY 의류를 한 벌씩 차례로 착장(·렌더)하고 의류별 결과를 표로 남긴다.

    python -m tools.drape_batch --prepared C:/dev/aihub/prepared --garments C:/dev/aihub/garments
        --out C:/dev/runs/c6-batch/v1_fabric --render          (한 줄로)

- 착장은 어댑터 `BlenderDraper`(`make_draper`와 같은 설정 확인), 렌더는 `BlenderRenderer`를 그대로
  쓴다. 착장·렌더 코드를 새로 쓰지 않는다. 동시에 Blender를 여러 개 띄우지 않는다.
- 준비 폴더(`--prepared`, 기본 `FIT_PREPARED_DIR`)에서 `prep.json`의 status가 READY인 의류만 돈다.
  의류 정의는 `--garments`(기본 `FIT_GARMENTS_DIR`)에서 읽는다.
- 옵션: `--config`(착장 설정, 기본 drape_v1), `--fabric-map`(원단 매핑 표, 기본은 어댑터와 같다,
  `off`면 끈다), `--body-obj`(기본 `FIT_DRAPE_BODY_OBJ`), `--only ID ...`, `--render`,
  `--force`, `--keep-blend`, `--blender`, `--timeout`.

결과 (`--out` 아래, 커밋 금지)
- `garments/{의류 ID}/result.json`: 의류별 기록. 이 파일이 있으면 끝난 의류로 보고 건너뛴다
  (`--force`일 때만 다시 한다). 중간에 멈춰도 같은 명령으로 이어서 돌릴 수 있다.
- `batch_summary.json`, `batch_summary.md`: `--out`의 모든 의류 기록을 모은 요약(매번 다시 쓴다).
- `batch_config.json`: 이 폴더에 쓴 착장 설정·원단 매핑·몸. 다른 설정으로 같은 폴더를 쓰면 멈춘다
  (결과가 섞이지 않게).
- `artifacts/`, `work/`: 어댑터 산출물·작업 폴더(job_id = 의류 ID).
- 디스크를 아끼려고 `--keep-blend`가 없으면 시연 2벌(`DEMO_GARMENT_IDS`) 밖 의류의 `.blend`는
  지표·미리보기·마스크를 남긴 뒤 지운다.

한 벌이 예외로 죽어도 기록(status ERROR)을 남기고 다음 벌로 넘어간다. 판정·검사 기준은 착장
설정의 `thresholds`를 그대로 쓰고 이 도구는 바꾸지 않는다.

종료 코드: 0 실행 끝(실패 의류가 있어도), 2 입력·설정 오류.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import statistics
import sys
import time
import traceback
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from fitting.domain import GarmentVariant
from fitting.ports import PortFailure, PortRequest

ROOT = Path(__file__).resolve().parents[1]
EXIT_OK, EXIT_SETUP = 0, 2
BATCH_VERSION = "0.1.0"
RESULT_FILE = "result.json"
SUMMARY_JSON, SUMMARY_MD, CONFIG_FILE = (
    "batch_summary.json",
    "batch_summary.md",
    "batch_config.json",
)
DEFAULT_CONFIG = ROOT / "config" / "drape" / "drape_v1.json"
DEFAULT_TIMEOUT_S = 600
# 시연 의류: .blend를 지우지 않는다(docs/decisions.md 1절 기준 사례)
DEMO_GARMENT_IDS = frozenset({"tee_regular_m_navy_v1", "tee_relaxed_l_black_v1"})

PASS, DRAPE_INVALID, NOT_READY, ERROR = "PASS", "DRAPE_INVALID", "GARMENT_NOT_READY", "ERROR"


# ------------------------------------------------------------------ 입력


def ready_ids(prepared_dir: Path) -> tuple[list[str], list[str]]:
    """(READY 의류 ID, READY가 아닌 의류 ID). prep.json을 읽지 못하면 READY가 아닌 쪽."""
    ready, other = [], []
    for folder in sorted(p for p in Path(prepared_dir).iterdir() if p.is_dir()):
        try:
            prep = json.loads((folder / "prep.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            other.append(folder.name)
            continue
        if prep.get("status") == "READY" and prep.get("garment_id") == folder.name:
            ready.append(folder.name)
        else:
            other.append(folder.name)
    return ready, other


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ 의류 하나


def _drape_details(job_dir: Path) -> dict[str, Any]:
    """착장 작업 폴더에서 판정·지표를 모은다(성공·실패 모두)."""
    record = _read_json(job_dir / "drape_record.json") or {}
    run_dir = job_dir / "drape"
    m = _read_json(run_dir / "drape_metrics.json") or {}
    summary = _read_json(run_dir / "run_summary.json") or {}
    weld = m.get("weld") or {}
    at_weld = weld.get("rest_length_ratio_if_weld_time_shape") or {}
    steps = {s.get("step"): s for s in summary.get("steps") or []}
    analyze = steps.get("analyze")
    analyze_ok: bool | None = None
    analyze_error = None
    if analyze is not None:
        analyze_ok = analyze.get("returncode") == 0 and (run_dir / "analysis.json").is_file()
        if not analyze_ok:
            analyze_error = _last_error_line(run_dir / "analyze_log.txt")
    elif (record.get("run") or {}).get("analyze_timed_out"):
        analyze_ok, analyze_error = False, "analyze time limit exceeded"
    fabric = record.get("fabric") or {}
    cfg = record.get("drape_config") or {}
    prep = record.get("prep") or {}
    return {
        "failed_checks": m.get("failed_checks", record.get("failed_checks")),
        "stretch_max": m.get("stretch_max"),
        "stretch_at_weld": at_weld.get("max"),
        "seam_gap_preweld_max_mm": m.get("seam_gap_preweld_max_mm"),
        "seam_closed_ok": m.get("seam_closed_ok"),
        "welded": weld.get("enabled"),
        "penetration_area_ratio": m.get("penetration_area_ratio"),
        "stable_last10_median_mm": m.get("stable_last10_median_mm"),
        "nan": m.get("nan"),
        "frames": m.get("frames"),
        "sim_seconds": m.get("sim_seconds"),
        "drape_seconds": (record.get("run") or {}).get("seconds"),
        "step_seconds": {k: v.get("seconds") for k, v in steps.items()},
        "timed_out": (record.get("run") or {}).get("timed_out"),
        "analyze_ok": analyze_ok,
        "analyze_error": analyze_error,
        "sample_id": prep.get("sample_id"),
        "fabric_stiffness_multiplier": fabric.get("stiffness_multiplier", 1.0 if fabric else None),
        "fabric_areal_density_kg_m2": fabric.get("areal_density_kg_m2"),
        "fabric_warnings": fabric.get("warnings"),
        "drape_config": f"{cfg.get('name')}@{cfg.get('version')}" if cfg else None,
        "drape_config_sha256": cfg.get("sha256"),
    }


def _last_error_line(log: Path) -> str | None:
    try:
        lines = [x.strip() for x in log.read_text(encoding="utf-8", errors="replace").splitlines()]
    except OSError:
        return None
    lines = [x for x in lines if x]
    return lines[-1][:300] if lines else None


def _read_mask(path: Path):
    """옷 마스크 PNG → 2차원 배열. cv2가 없으면 None."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    data = np.fromfile(str(path), dtype=np.uint8)  # 한글 경로 대비
    m = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if m is None:
        raise ValueError(f"마스크를 읽지 못했습니다: {path}")
    return m if m.ndim == 2 else m[:, :, 0]


RENDER_KEYS = (
    "garment_mask_ratio",
    "head_vertices_in_frame",
    "garment_vertices_out_of_frame",
    "top_limited_by",
    "garment_color_hex",
    "garment_median_hex",
    "garment_delta_e00",
    "render_seconds",
    "still_seconds",
)


def _render(renderer, garment: GarmentVariant, artifacts: Path, timeout: int) -> dict[str, Any]:
    from fitting.verify import mask_shape

    gid = garment.id
    request = PortRequest(
        job_id=gid,
        attempt_id=f"{gid}_render_1",
        input_artifact_ids=[f"{gid}_drape_result"],
        config_version="tools.drape_batch",
        timeout_seconds=timeout,
        trace_id=gid,
    )
    try:
        result = renderer.render(request, garment)
    except PortFailure as failure:
        return {
            "status": "FAILED",
            "error_code": failure.error.code.value,
            "safe_message": failure.error.safe_message,
            "record": failure.error.internal_details_ref,
        }
    m = result.metrics
    out: dict[str, Any] = {"status": "SUCCEEDED", **{k: m.get(k) for k in RENDER_KEYS}}
    mask_id = m.get("render_mask_artifact_id")
    if mask_id:
        mask_path = artifacts / f"{mask_id}.png"
        out["mask"] = str(mask_path)
        mask = _read_mask(mask_path)
        out["mask_shape"] = None if mask is None else mask_shape(mask)
        if mask is None:
            out["mask_shape_note"] = "cv2가 없어 마스크 형태 값을 재지 못함"
    return out


def _drop_blends(gid: str, artifacts: Path, work: Path) -> list[str]:
    removed = []
    for p in (artifacts / f"{gid}_drape_result.blend", work / gid / "drape" / "drape.blend"):
        if p.is_file():
            p.unlink()
            removed.append(str(p))
    return removed


def run_one(
    garment: GarmentVariant,
    draper,
    renderer,
    out: Path,
    *,
    timeout: int = DEFAULT_TIMEOUT_S,
    keep_blend: bool = False,
) -> dict[str, Any]:
    """의류 한 벌: 착장 → (통과하면) 렌더 → 기록. 예외는 status ERROR로 기록한다."""
    gid = garment.id
    artifacts, work = out / "artifacts", out / "work"
    t0 = time.time()
    rec: dict[str, Any] = {
        "garment_id": gid,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "chest_girth_cm": garment.measurements.chest_girth_cm,
    }
    request = PortRequest(
        job_id=gid,
        attempt_id=f"{gid}_drape_1",
        input_artifact_ids=[],
        config_version="tools.drape_batch",
        timeout_seconds=timeout,
        trace_id=gid,
    )
    try:
        try:
            result = draper.drape(request, garment)
            rec.update(status=PASS, error_code=None, safe_message=None)
            rec["drape_artifacts"] = list(result.output_artifact_ids)
        except PortFailure as failure:
            code = failure.error.code.value
            rec.update(
                status=NOT_READY if code == NOT_READY else DRAPE_INVALID,
                error_code=code,
                safe_message=failure.error.safe_message,
            )
        rec.update(_drape_details(work / gid))
        if rec["status"] == PASS and renderer is not None:
            rec["render"] = _render(renderer, garment, artifacts, timeout)
    except Exception as error:  # noqa: BLE001 - 한 벌이 죽어도 다음 벌로 넘어간다
        rec.update(
            status=ERROR,
            error_code=None,
            safe_message=f"{type(error).__name__}: {error}",
            traceback=traceback.format_exc(limit=8),
        )
    if not keep_blend and gid not in DEMO_GARMENT_IDS:
        rec["blend_removed"] = _drop_blends(gid, artifacts, work)
    rec["wall_seconds"] = round(time.time() - t0, 1)
    return rec


# ------------------------------------------------------------------ 일괄


def run_batch(
    garments: Iterable[GarmentVariant],
    draper,
    renderer,
    out: Path,
    *,
    force: bool = False,
    keep_blend: bool = False,
    timeout: int = DEFAULT_TIMEOUT_S,
    log: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    """의류를 차례로 돌린다. 끝난 의류(result.json 있음)는 force가 아니면 건너뛴다."""
    garments = list(garments)
    done = []
    for i, g in enumerate(garments, 1):
        folder = out / "garments" / g.id
        result_path = folder / RESULT_FILE
        if result_path.is_file() and not force:
            log(f"[{i}/{len(garments)}] {g.id}: 이미 끝남, 건너뜀")
            continue
        result_path.unlink(missing_ok=True)
        log(f"[{i}/{len(garments)}] {g.id}: 착장 중…")
        rec = run_one(g, draper, renderer, out, timeout=timeout, keep_blend=keep_blend)
        folder.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        render = rec.get("render") or {}
        log(
            f"[{i}/{len(garments)}] {g.id}: {rec['status']} "
            f"신장 {rec.get('stretch_max')} 벌어짐 {rec.get('seam_gap_preweld_max_mm')} mm "
            f"{rec['wall_seconds']} s" + (f" 렌더 {render.get('status')}" if render else "")
        )
        done.append(rec)
    return done


def load_results(out: Path) -> list[dict[str, Any]]:
    results = []
    for p in sorted((out / "garments").glob(f"*/{RESULT_FILE}")):
        rec = _read_json(p)
        if rec is not None:
            results.append(rec)
    return results


def _stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"n": 0, "median": None, "min": None, "max": None}
    return {
        "n": len(values),
        "median": round(statistics.median(values), 1),
        "min": round(min(values), 1),
        "max": round(max(values), 1),
    }


def summarize(results: list[dict[str, Any]], batch: dict[str, Any]) -> dict[str, Any]:
    status = Counter(r["status"] for r in results)
    checks = Counter(c for r in results for c in (r.get("failed_checks") or []))
    renders = [r["render"] for r in results if r.get("render")]
    ok_renders = [x for x in renders if x.get("status") == "SUCCEEDED"]
    ratios = [x["garment_mask_ratio"] for x in ok_renders if x.get("garment_mask_ratio") != ""]
    heads = [x["head_vertices_in_frame"] for x in ok_renders]
    return {
        "batch": batch,
        "counts": {
            "total": len(results),
            "pass": status.get(PASS, 0),
            "drape_invalid": status.get(DRAPE_INVALID, 0),
            "garment_not_ready": status.get(NOT_READY, 0),
            "error": status.get(ERROR, 0),
            "analyze_failed": sum(1 for r in results if r.get("analyze_ok") is False),
            "rendered": len(ok_renders),
            "render_failed": len(renders) - len(ok_renders),
        },
        "failed_check_counts": dict(sorted(checks.items())),
        "seconds": {
            "wall": _stats([r["wall_seconds"] for r in results if r.get("wall_seconds")]),
            "drape": _stats([r["drape_seconds"] for r in results if r.get("drape_seconds")]),
            "sim": _stats([r["sim_seconds"] for r in results if r.get("sim_seconds")]),
        },
        "render": {
            "mask_ratio_min": min(ratios) if ratios else None,
            "mask_ratio_max": max(ratios) if ratios else None,
            "head_vertices_in_frame_max": max(heads) if heads else None,
        },
        "garments": results,
    }


def _fmt(v: Any, nd: int = 3) -> str:
    if v is None or v == "":
        return "—"
    if isinstance(v, bool):
        return "예" if v else "아니오"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) or "—"
    return str(v)


def summary_markdown(summary: dict[str, Any]) -> str:
    b, c, s = summary["batch"], summary["counts"], summary["seconds"]
    lines = [
        f"# 일괄 착장 요약 ({b.get('name')})",
        "",
        f"- 착장 설정 `{b.get('config_name')}` · 원단 매핑 `{b.get('fabric_map')}` · "
        f"몸 `{Path(str(b.get('body_obj'))).name}` · 렌더 {_fmt(b.get('render'))}",
        f"- 의류 {c['total']}벌: 통과 {c['pass']}, DRAPE_INVALID {c['drape_invalid']}, "
        f"준비 안 됨 {c['garment_not_ready']}, 예외 {c['error']} · analyze 예외 "
        f"{c['analyze_failed']} · 렌더 성공 {c['rendered']}, 실패 {c['render_failed']}",
        f"- 실패 항목: {summary['failed_check_counts'] or '없음'}",
        f"- 시간(s) 전체 중앙값 {s['wall']['median']}·최대 {s['wall']['max']}, "
        f"시뮬 중앙값 {s['sim']['median']}·최대 {s['sim']['max']}",
        f"- 렌더: 마스크 비율 {summary['render']['mask_ratio_min']}~"
        f"{summary['render']['mask_ratio_max']}, 화면 안 머리 정점 최대 "
        f"{summary['render']['head_vertices_in_frame_max']}",
        "",
        "| 의류 | 가슴 cm | 견본 | 강성 배율 | 면밀도 | 판정 | 실패 항목 | 최대 신장 "
        "| 용접 시점 신장 | 용접 전 벌어짐 mm | 침투 | 시뮬 s | 착장 s | analyze "
        "| 마스크 비율 | 머리 정점 |",
        "|" + "---|" * 16,
    ]
    for r in summary["garments"]:
        render = r.get("render") or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    r["garment_id"],
                    _fmt(r.get("chest_girth_cm"), 1),
                    _fmt(r.get("sample_id")),
                    _fmt(r.get("fabric_stiffness_multiplier"), 2),
                    _fmt(r.get("fabric_areal_density_kg_m2"), 2),
                    r["status"],
                    _fmt(r.get("failed_checks")),
                    _fmt(r.get("stretch_max")),
                    _fmt(r.get("stretch_at_weld")),
                    _fmt(r.get("seam_gap_preweld_max_mm"), 2),
                    _fmt(r.get("penetration_area_ratio"), 4),
                    _fmt(r.get("sim_seconds"), 1),
                    _fmt(r.get("drape_seconds"), 1),
                    "—" if r.get("analyze_ok") is None else ("정상" if r["analyze_ok"] else "예외"),
                    _fmt(render.get("garment_mask_ratio"), 4),
                    _fmt(render.get("head_vertices_in_frame")),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def write_summary(out: Path, batch: dict[str, Any]) -> dict[str, Any]:
    summary = summarize(load_results(out), batch)
    (out / SUMMARY_JSON).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / SUMMARY_MD).write_text(summary_markdown(summary), encoding="utf-8")
    return summary


def check_batch_config(out: Path, batch: dict[str, Any]) -> str | None:
    """같은 폴더를 다른 설정으로 쓰려 하면 이유를, 아니면 None. 처음이면 기록한다."""
    path = out / CONFIG_FILE
    old = _read_json(path)
    keys = ("config_sha256", "fabric_map_sha256", "body_sha256", "render")
    if old is not None:
        diff = [k for k in keys if old.get(k) != batch.get(k)]
        if diff:
            return f"이 폴더는 다른 설정으로 돌린 결과가 있습니다({', '.join(diff)}): {path}"
        return None
    out.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
    return None


# ------------------------------------------------------------------ 명령


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="READY 의류 일괄 착장(·렌더)과 결과 표")
    p.add_argument("--prepared", type=Path, help="준비 폴더 (기본 FIT_PREPARED_DIR)")
    p.add_argument("--garments", type=Path, help="등록 의류 폴더 (기본 FIT_GARMENTS_DIR)")
    p.add_argument("--out", type=Path, required=True, help="결과 폴더")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="착장 설정 (기본 drape_v1)")
    p.add_argument("--fabric-map", help="원단 매핑 표 경로 또는 off (기본은 어댑터와 같다)")
    p.add_argument("--body-obj", type=Path, help="몸 OBJ (기본 FIT_DRAPE_BODY_OBJ)")
    p.add_argument("--only", nargs="+", metavar="ID", help="이 의류만")
    p.add_argument("--render", action="store_true", help="통과한 착장을 렌더하고 마스크를 잰다")
    p.add_argument("--force", action="store_true", help="끝난 의류도 다시 한다")
    p.add_argument("--keep-blend", action="store_true", help="AI Hub 의류 .blend도 남긴다")
    p.add_argument("--blender", type=Path, help="Blender 실행 파일 (기본 BLENDER_PATH)")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="단계 시간 제한(초)")
    return p.parse_args(argv)


def _env_dir(arg: Path | None, env_name: str, default: Path | None) -> Path | None:
    if arg is not None:
        return arg
    value = (os.environ.get(env_name) or "").strip()
    return Path(value) if value else default


def main(argv: list[str] | None = None) -> int:
    from fitting.adapters.blender import draper as dr
    from fitting.adapters.blender import fabric_map as fm
    from fitting.adapters.blender import renderer as rr
    from server.app import DEFAULT_GARMENTS_DIR, GARMENTS_DIR_ENV
    from server.garments import GarmentDirectory
    from server.wiring import AdapterConfigError, AdapterSettings

    args = parse_args(argv)
    prepared = _env_dir(args.prepared, dr.PREPARED_DIR_ENV, dr.DEFAULT_PREPARED_DIR)
    garments_dir = _env_dir(args.garments, GARMENTS_DIR_ENV, DEFAULT_GARMENTS_DIR)
    body = _env_dir(args.body_obj, dr.BODY_OBJ_ENV, None)
    if body is None:
        print("몸 OBJ가 없습니다(--body-obj 또는 FIT_DRAPE_BODY_OBJ)", file=sys.stderr)
        return EXIT_SETUP
    if not args.config.is_file():
        print(f"착장 설정이 없습니다: {args.config}", file=sys.stderr)
        return EXIT_SETUP
    if not prepared.is_dir():
        print(f"준비 폴더가 없습니다: {prepared}", file=sys.stderr)
        return EXIT_SETUP

    out = args.out.resolve()
    environ = dict(os.environ, FIT_WORK_DIR=str(out / "work"))
    environ[dr.PREPARED_DIR_ENV] = str(prepared)
    environ[dr.BODY_OBJ_ENV] = str(body)
    if args.fabric_map is not None:
        environ[dr.FABRIC_MAP_ENV] = args.fabric_map
    if args.blender is not None:
        environ["BLENDER_PATH"] = str(args.blender)
    try:
        settings = AdapterSettings.from_environ(artifacts_dir=out / "artifacts", environ=environ)
        draper = dataclasses.replace(
            dr.make_draper(settings, environ), drape_config=args.config.resolve()
        )
        renderer = rr.make_renderer(settings, environ) if args.render else None
    except AdapterConfigError as error:
        print(f"설정 오류: {error}", file=sys.stderr)
        return EXIT_SETUP

    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    table: fm.FabricMap | None = draper.fabric_table
    batch = {
        "name": out.name,
        "tool": f"tools.drape_batch@{BATCH_VERSION}",
        "config_path": str(args.config.resolve()),
        "config_name": f"{cfg.get('name')}@{cfg.get('version')}",
        "config_sha256": _sha256(args.config),
        "fabric_map": "off" if table is None else table.version,
        "fabric_map_sha256": None if table is None else table.sha256,
        "body_obj": str(body),
        "body_sha256": _sha256(body),
        "render": bool(args.render),
    }
    problem = check_batch_config(out, batch)
    if problem:
        print(problem, file=sys.stderr)
        return EXIT_SETUP

    ready, not_ready = ready_ids(prepared)
    if args.only:
        missing = sorted(set(args.only) - set(ready))
        if missing:
            print(f"READY가 아니거나 없는 의류(건너뜀): {', '.join(missing)}", file=sys.stderr)
        ready = [g for g in ready if g in set(args.only)]
    directory = GarmentDirectory(garments_dir)
    garments = []
    for gid in ready:
        g = directory.get(gid)
        if g is None:
            print(f"의류 정의가 없어 건너뜀: {gid} ({garments_dir})", file=sys.stderr)
            continue
        garments.append(g)
    print(
        f"{batch['name']}: {batch['config_name']} · 원단 {batch['fabric_map']} · READY "
        f"{len(garments)}벌(준비 안 됨 {len(not_ready)}) · 렌더 {'켬' if args.render else '끔'}"
    )
    run_batch(
        garments,
        draper,
        renderer,
        out,
        force=args.force,
        keep_blend=args.keep_blend,
        timeout=args.timeout,
    )
    summary = write_summary(out, batch)
    c = summary["counts"]
    print(
        f"요약: {c['total']}벌 통과 {c['pass']} · DRAPE_INVALID {c['drape_invalid']} · "
        f"예외 {c['error']} → {out / SUMMARY_MD}"
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
