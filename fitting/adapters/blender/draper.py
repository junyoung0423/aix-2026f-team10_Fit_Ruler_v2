"""Blender 착장 어댑터 (#8, C4). `Draper` 포트를 구현한다.

연결: `config/adapters/drape_blender.yaml`의 `drape: "fitting.adapters.blender.draper:make_draper"`.

흐름 (의류 1벌, 작업 1개)
1. 준비 기록: `{준비 폴더}/{garment.id}/prep.json`(`tools/prepare_garments.py`)과
   `specification.json`. 기록이 없거나 준비 실패이거나 패턴 해시가 다르면 `GARMENT_NOT_READY`.
2. 몸: `request.input_artifact_ids[0]`의 `artifacts_dir/{id}.obj`(D3 몸 메시, y 위·z 앞·미터).
   개발용: 환경변수 `FIT_DRAPE_BODY_OBJ`가 있으면 입력 대신 그 OBJ를 쓴다(D3 전 시연용,
   가짜 몸은 메시가 없다).
3. 원단 → 물성(`fabric_map.py`)으로 작업 폴더에 파생 설정을 쓴다. 바뀌는 값이 없으면
   `drape_v1.json` 그대로.
4. GarmentCode용 Python으로 `run_drape`를 하위 프로세스로 돌린다. 패턴을 만든 몸(준비 기록의
   `generation_body`, 보통 `mean_all`)을 `--ref-body`로 넘겨, export가 옷 패널 첫 위치를 입력 몸의
   어깨에 맞추게 한다(`garmentcode/placement.py`). 작업 폴더는
   `work_dir/{job_id}/drape/`. 시간 제한은 `request.timeout_seconds`와 설정
   `time_limits_s.drape` 중 작은 값이고, 넘으면 프로세스 트리를 끝낸다.
   D3가 몸 메시 옆에 팔 내리는 길(`{job_id}_body_arms_path.npy`)을 썼으면 `--arms-path`로 넘긴다.
   Blender 착장이 A자세 착장 뒤 팔을 내려 보고, 검사 기준을 넘으면 A자세 상태로 저장한다
   (설정 `arms_down`, `arms_down.py`). 판정·지표는 A자세 결과이고, 팔을 내렸는지는 기록
   (`drape_record.json`의 `arms_down`)과 .blend 장면 값에 남는다. 개발용 몸에는 길이 없다.
   착장 뒤 analyze(Blender 없이 지표 재계산, 판정에 쓰지 않음)는 기본으로 건너뛴다
   (`--skip-analyze`). 판정은 Blender 지표로 하므로 결과는 같고, 서버 착장 단계가 analyze
   시간(C3 실측 40~430초)만큼 짧아진다. 켜려면 `analyze=True`(`FIT_DRAPE_ANALYZE=on`).
5. 성공하면 `drape.blend` → `{job_id}_drape_result.blend`, `front.png` →
   `{job_id}_drape_preview.png`를 `artifacts_dir`에 복사하고 `DRAPE_OUTPUTS` 순서로 돌려준다.

실패는 `PortFailure`로 던진다. `safe_message`에는 경로·로그를 넣지 않고, 자세한 기록은 작업
폴더의 `drape_record.json`(`internal_details_ref`)에 둔다. `retryable`은 기존 어댑터와 같이
False다(같은 입력으로 다시 돌려도 같은 결과: 착장은 결정적이다, C2 확인).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fitting.adapters.blender import fabric_map as fm
from fitting.domain import SCHEMA_VERSION, ErrorCode, GarmentVariant, Stage
from fitting.ports import DRAPE_OUTPUTS, PortError, PortFailure, PortRequest, PortResult

REPO_ROOT = Path(__file__).resolve().parents[3]
DRAPER_VERSION = "0.1.0"

GC_PYTHON_ENV = "FIT_GC_PYTHON"
DEFAULT_GC_PYTHON = Path(r"C:\dev\venvs\garmentcode\Scripts\python.exe")
GC_ROOT_ENV = "FIT_RULER_GC_ROOT"
DEFAULT_GC_ROOT = Path(r"C:\dev\GarmentCode")
PREPARED_DIR_ENV = "FIT_PREPARED_DIR"
DEFAULT_PREPARED_DIR = REPO_ROOT / "data" / "prepared"
BODY_OBJ_ENV = "FIT_DRAPE_BODY_OBJ"
FABRIC_MAP_ENV = "FIT_DRAPE_FABRIC_MAP"  # 매핑 표 경로, "off"면 끈다
ANALYZE_ENV = "FIT_DRAPE_ANALYZE"  # "on"이면 착장 뒤 analyze까지 돌린다(기본 off)
DEFAULT_DRAPE_CONFIG = REPO_ROOT / "config" / "drape" / "drape_v1.json"

RUN_NAME = "drape"  # work_dir/{job_id}/drape/
# D3(smpl/builder.py `ARMS_PATH_ROLE`)가 몸 메시 옆에 쓰는 팔 내리는 길 {job_id}_body_arms_path.npy
ARMS_PATH_ROLE = "body_arms_path"
RECORD_FILE = "drape_record.json"


@dataclass(frozen=True)
class RunOutcome:
    returncode: int | None
    timed_out: bool  # export + Blender 착장이 시간 제한을 넘어 끝냈다
    seconds: float
    analyze_timed_out: bool = False  # 착장은 끝났고 뒤의 analyze가 따로 둔 한도를 넘어 끝냈다


# (명령, cwd, 환경, 시간 제한 s, 로그 파일) -> 결과. 테스트는 가짜로 바꾼다.
Runner = Callable[[list[str], Path, dict[str, str], float, Path], RunOutcome]

# run_drape가 Blender 착장 단계를 끝내면 찍는 줄. 이 줄이 나오면 착장 시간 제한이 끝나고
# analyze(Blender 없이 지표 재계산, 판정에 쓰지 않음)의 한도가 시작된다.
DRAPE_DONE_MARK = "[drape] rc="
POLL_S = 1.0


def _kill_tree(proc: subprocess.Popen) -> None:
    """하위 프로세스(run_drape → python → blender)까지 끝낸다."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()


def _drape_done(log_path: Path) -> bool:
    try:
        return DRAPE_DONE_MARK in log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def subprocess_runner(
    cmd: list[str], cwd: Path, env: dict[str, str], timeout_s: float, log_path: Path
) -> RunOutcome:
    """run_drape를 돌린다. 시간 제한은 export + Blender 착장에 건다.

    [제안] run_drape는 착장 뒤에 analyze(Blender 없이 지표 재계산, 판정에 쓰지 않음)를 돌리고,
    C3에서 analyze만 40~430초 걸렸다. 전체에 600초를 걸면 정상 착장도 끊긴다. 그래서 착장 끝
    줄(`DRAPE_DONE_MARK`)이 나오면 analyze에 같은 길이의 한도를 따로 주고, 넘으면 끝내고
    `analyze_timed_out`으로 알린다(판정은 Blender의 drape_metrics.json으로 한다).
    """
    t0 = time.time()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timed_out = analyze_timed_out = False
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        kwargs: dict[str, Any] = {}
        if os.name != "nt":
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(
            cmd, cwd=str(cwd), env=env, stdout=log, stderr=subprocess.STDOUT, **kwargs
        )
        deadline, analyzing = t0 + timeout_s, False
        while True:
            try:
                rc = proc.wait(timeout=POLL_S)
                break
            except subprocess.TimeoutExpired:
                pass
            if not analyzing:
                log.flush()
                if _drape_done(log_path):
                    analyzing, deadline = True, time.time() + timeout_s
            if time.time() > deadline:
                _kill_tree(proc)
                rc = None
                if analyzing:
                    analyze_timed_out = True
                else:
                    timed_out = True
                break
    return RunOutcome(
        returncode=rc,
        timed_out=timed_out,
        seconds=round(time.time() - t0, 1),
        analyze_timed_out=analyze_timed_out,
    )


def _summary_from_drape_metrics(out: Path) -> dict[str, Any]:
    """analyze를 끝내 run_summary.json이 없을 때, run_drape와 같은 판정을 Blender 지표로 한다."""
    m = json.loads((out / "drape_metrics.json").read_text(encoding="utf-8"))
    ok = bool(m["drape_ok"])
    return {
        "status": "SUCCEEDED" if ok else "FAILED",
        "error_code": None if ok else ErrorCode.DRAPE_INVALID.value,
        "reason": "analyze time limit exceeded (judged from drape_metrics.json)",
        "drape_metrics": m,
        "versions": {},
    }


def canonical_sha256(spec: Mapping[str, Any]) -> str:
    """패턴 정규형 sha256. `pattern_measure.canonical_sha256`과 같은 계산이다(테스트로 확인).

    pattern_measure는 numpy·svgpathtools를 import해서, 서버 환경에 garment 의존성이 없어도 되게
    옮겨 적었다.
    """
    p = spec["pattern"] if "pattern" in spec else spec
    stitches = sorted(
        json.dumps(sorted(s, key=lambda x: (x["panel"], x["edge"])), sort_keys=True)
        for s in p["stitches"]
    )
    canon = {
        "panels": p["panels"],
        "stitches": stitches,
        "panel_order": sorted(p.get("panel_order", [])),
    }
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _num(value: Any) -> float | int | str:
    """지표 값: 숫자나 문자열만. 불리언은 1/0, 목록은 쉼표로 이은 문자열, None은 빈 문자열."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float | str):
        return value
    if value is None:
        return ""
    if isinstance(value, list | tuple):
        return ",".join(str(v) for v in value)
    return str(value)


@dataclass
class BlenderDraper:
    artifacts_dir: Path
    work_dir: Path
    blender_path: Path
    gc_python: Path
    gc_root: Path
    prepared_dir: Path
    drape_config: Path = DEFAULT_DRAPE_CONFIG
    fabric_table: fm.FabricMap | None = None  # None이면 매핑하지 않음(drape_v1 그대로)
    dev_body_obj: Path | None = None
    analyze: bool = False  # True면 착장 뒤 analyze까지 돌린다(개발·일괄 점검용, 판정에는 안 씀)
    runner: Runner = subprocess_runner

    # --- 실패 ---

    def _fail(
        self,
        code: ErrorCode,
        message: str,
        ref: Path | str | None,
        record: dict[str, Any] | None = None,
    ) -> PortFailure:
        if record is not None and ref is not None:
            record.update(status="FAILED", error_code=code.value, safe_message=message)
            Path(ref).parent.mkdir(parents=True, exist_ok=True)
            Path(ref).write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        return PortFailure(
            PortError(
                code=code,
                stage=Stage.DRAPE,
                retryable=False,
                safe_message=message,
                internal_details_ref=None if ref is None else str(ref),
            )
        )

    # --- 준비 기록 ---

    def _prepared(self, garment: GarmentVariant, ref: Path, record: dict) -> tuple[dict, Path]:
        folder = self.prepared_dir / garment.id
        record["prepared_dir"] = str(folder)
        try:
            prep = json.loads((folder / "prep.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise self._fail(
                ErrorCode.GARMENT_NOT_READY,
                "이 의류는 3D 착장 준비 기록이 없어 착장할 수 없습니다.",
                ref,
                record,
            ) from None
        record["prep"] = {
            k: prep.get(k)
            for k in ("status", "failure_reason", "sample_id", "pattern_sha256", "garment_sha256")
        }
        if prep.get("garment_id") != garment.id:
            raise self._fail(
                ErrorCode.GARMENT_NOT_READY,
                "준비 기록이 다른 의류의 것이라 착장할 수 없습니다.",
                ref,
                record,
            )
        if prep.get("status") != "READY":
            raise self._fail(
                ErrorCode.GARMENT_NOT_READY,
                "이 의류는 3D 착장 준비에 실패해 착장할 수 없습니다. "
                "핏 리포트는 그대로 볼 수 있습니다.",
                ref,
                record,
            )
        targets = prep.get("targets_cm") or {}
        m = garment.measurements
        for f in ("chest_girth_cm", "sleeve_length_cm", "body_length_cm"):
            if targets.get(f) != getattr(m, f):
                raise self._fail(
                    ErrorCode.GARMENT_NOT_READY,
                    "준비 기록이 지금 의류 치수와 달라 착장할 수 없습니다.",
                    ref,
                    record,
                )
        spec_path = folder / str(prep.get("specification") or "specification.json")
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            sha = canonical_sha256(spec)
        except (OSError, ValueError, KeyError, TypeError):
            raise self._fail(
                ErrorCode.GARMENT_NOT_READY,
                "이 의류의 패턴 파일이 없어 착장할 수 없습니다.",
                ref,
                record,
            ) from None
        if sha != prep.get("pattern_sha256"):
            record["pattern_sha256_found"] = sha
            raise self._fail(
                ErrorCode.GARMENT_NOT_READY,
                "이 의류의 패턴 파일이 준비 기록과 달라 착장할 수 없습니다.",
                ref,
                record,
            )
        return prep, spec_path

    def _body(self, request: PortRequest, ref: Path, record: dict) -> tuple[Path, str]:
        if self.dev_body_obj is not None:
            return self.dev_body_obj, "dev_env:FIT_DRAPE_BODY_OBJ"
        if not request.input_artifact_ids:
            raise self._fail(ErrorCode.DRAPE_INVALID, "착장에 쓸 몸 메시가 없습니다.", ref, record)
        body_id = request.input_artifact_ids[0]
        path = self.artifacts_dir / f"{body_id}.obj"
        if not path.is_file():
            record["body_artifact_id"] = body_id
            raise self._fail(ErrorCode.DRAPE_INVALID, "착장에 쓸 몸 메시가 없습니다.", ref, record)
        return path, f"artifact:{body_id}"

    def _arms_path(self, request: PortRequest, body_source: str) -> Path | None:
        """D3가 쓴 팔 내리는 길. 개발용 몸이거나 파일이 없으면 None(A자세로만 착장)."""
        if not body_source.startswith("artifact:"):
            return None
        path = self.artifacts_dir / f"{request.job_id}_{ARMS_PATH_ROLE}.npy"
        return path if path.is_file() else None

    # --- 포트 ---

    def drape(self, request: PortRequest, garment: GarmentVariant) -> PortResult:
        job_dir = self.work_dir / request.job_id
        out = job_dir / RUN_NAME
        ref = job_dir / RECORD_FILE
        record: dict[str, Any] = {
            "draper_version": DRAPER_VERSION,
            "job_id": request.job_id,
            "attempt_id": request.attempt_id,
            "garment_id": garment.id,
        }
        prep, spec_path = self._prepared(garment, ref, record)
        body, body_source = self._body(request, ref, record)
        body_sha = _sha256_file(body)
        record.update(body=str(body), body_source=body_source, body_sha256=body_sha)

        # 원단 → 물성
        base_cfg = json.loads(self.drape_config.read_text(encoding="utf-8"))
        cfg_path, cfg_sha = self.drape_config, _sha256_file(self.drape_config)
        props = None
        if self.fabric_table is not None:
            props = fm.properties_for(garment.fabric, self.fabric_table)
            derived = fm.derive_config(base_cfg, self.drape_config.name, props, self.fabric_table)
            if derived is not None:
                cfg_path = job_dir / "drape_config_fabric.json"
                cfg_sha = fm.write_config(derived, cfg_path)
                base_cfg = derived
        cfg_name, cfg_version = base_cfg["name"], base_cfg["version"]
        record["drape_config"] = {
            "path": str(cfg_path),
            "name": cfg_name,
            "version": cfg_version,
            "sha256": cfg_sha,
        }
        record["fabric"] = (
            None
            if props is None
            else {
                "map_version": self.fabric_table.version,
                "map_sha256": self.fabric_table.sha256,
                "stiffness_multiplier": props.stiffness_multiplier,
                "areal_density_kg_m2": props.areal_density_kg_m2,
                "stretch_value": props.stretch_value,
                "density_source": props.density_source,
                "warnings": props.warnings,
            }
        )

        timeout_s = min(float(request.timeout_seconds), float(base_cfg["time_limits_s"]["drape"]))
        cmd = [
            str(self.gc_python),
            "-m",
            "fitting.adapters.blender.run_drape",
            "--spec",
            str(spec_path),
            "--body",
            str(body),
            "--config",
            str(cfg_path),
            "--name",
            RUN_NAME,
            "--out-root",
            str(job_dir),
            "--gc-root",
            str(self.gc_root),
            "--blender",
            str(self.blender_path),
        ]
        ref_body = _generation_body_obj(prep)
        if ref_body is not None:
            cmd += ["--ref-body", str(ref_body)]
        record["placement_reference_body"] = None if ref_body is None else str(ref_body)
        arms = self._arms_path(request, body_source)
        if arms is not None:
            cmd += ["--arms-path", str(arms)]
        record["arms_path"] = (
            None if arms is None else {"path": str(arms), "sha256": _sha256_file(arms)}
        )
        if not self.analyze:
            cmd.append("--skip-analyze")
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        record.update(timeout_s=timeout_s, out_dir=str(out), analyze=self.analyze)
        job_dir.mkdir(parents=True, exist_ok=True)
        outcome = self.runner(cmd, REPO_ROOT, env, timeout_s, job_dir / "run_drape_log.txt")
        record["run"] = {
            "returncode": outcome.returncode,
            "timed_out": outcome.timed_out,
            "seconds": outcome.seconds,
        }
        if outcome.timed_out:
            raise self._fail(
                ErrorCode.DRAPE_INVALID,
                f"착장이 시간 제한({int(timeout_s)}초) 안에 끝나지 않았습니다.",
                ref,
                record,
            )
        record["run"]["analyze_timed_out"] = outcome.analyze_timed_out
        try:
            if outcome.analyze_timed_out:
                summary = _summary_from_drape_metrics(out)
            else:
                summary = json.loads((out / "run_summary.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError):
            raise self._fail(
                ErrorCode.DRAPE_INVALID, "착장 프로그램이 비정상 종료했습니다.", ref, record
            ) from None
        metrics_raw = summary.get("drape_metrics") or {}
        record["run_summary"] = {k: summary.get(k) for k in ("status", "error_code", "reason")}
        ok_rc = 0 if not outcome.analyze_timed_out else None
        if summary.get("status") != "SUCCEEDED" or outcome.returncode != ok_rc:
            code = summary.get("error_code")
            if code == ErrorCode.GARMENT_NOT_READY.value:
                raise self._fail(
                    ErrorCode.GARMENT_NOT_READY,
                    "이 의류의 패턴을 착장용으로 바꾸지 못했습니다.",
                    ref,
                    record,
                )
            if metrics_raw.get("failed_checks"):
                record["failed_checks"] = metrics_raw["failed_checks"]
                raise self._fail(
                    ErrorCode.DRAPE_INVALID,
                    "착장 결과가 검사 기준을 통과하지 못했습니다.",
                    ref,
                    record,
                )
            raise self._fail(
                ErrorCode.DRAPE_INVALID, "착장 프로그램이 비정상 종료했습니다.", ref, record
            )

        blend, png = out / "drape.blend", out / "front.png"
        if not blend.is_file() or not png.is_file():
            raise self._fail(ErrorCode.DRAPE_INVALID, "착장 결과 파일이 없습니다.", ref, record)
        ids = [f"{request.job_id}_{role}" for role in DRAPE_OUTPUTS]
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(blend, self.artifacts_dir / f"{ids[0]}.blend")
        shutil.copyfile(png, self.artifacts_dir / f"{ids[1]}.png")

        shoulder = prep.get("shoulder") or {}
        fabric = record["fabric"] or {}
        metrics = {
            "drape_ok": _num(metrics_raw.get("drape_ok")),
            "penetration_area_ratio": _num(metrics_raw.get("penetration_area_ratio")),
            "stretch_max": _num(metrics_raw.get("stretch_max")),
            "seam_gap_preweld_max_mm": _num(metrics_raw.get("seam_gap_preweld_max_mm")),
            "frames": _num(metrics_raw.get("frames")),
            "sim_seconds": _num(metrics_raw.get("sim_seconds")),
            "sample_id": _num(prep.get("sample_id")),
            "shoulder_diff_cm": _num(shoulder.get("diff_cm")),
            "body_source": body_source,
            "body_sha256": body_sha,
            "drape_config": f"{cfg_name}@{cfg_version}",
            "drape_config_sha256": cfg_sha,
            "fabric_stiffness_multiplier": _num(fabric.get("stiffness_multiplier", 1.0)),
            "fabric_areal_density_kg_m2": _num(
                fabric.get(
                    "areal_density_kg_m2", base_cfg.get("weld", {}).get("areal_density_kg_m2")
                )
            ),
            "fabric_map": _num(fabric.get("map_version", "off")),
            "fabric_warnings": _num(fabric.get("warnings", [])),
        }
        # 팔을 내렸는지(렌더가 쓰는 상태). 판정·지표는 A자세 결과다
        record["arms_down"] = metrics_raw.get("arms_down")
        record.update(status="SUCCEEDED", output_artifact_ids=ids, metrics=metrics)
        ref.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        versions = summary.get("versions") or {}
        tool_versions = {
            "draper": DRAPER_VERSION,
            "blender": str(metrics_raw.get("blender") or ""),
            "garmentcode_commit": str(versions.get("garmentcode_commit") or ""),
            "python_gc": str(versions.get("python") or ""),
        }
        return PortResult(
            schema_version=SCHEMA_VERSION,
            output_artifact_ids=ids,
            metrics=metrics,
            tool_versions=tool_versions,
        )


def _generation_body_obj(prep: Mapping[str, Any]) -> Path | None:
    """준비 기록의 패턴 생성 몸(`generation_body.body_yaml`)과 같은 이름의 OBJ.

    없으면 None이고, 그때는 run_drape 기본값(`<gc-root>/assets/bodies/mean_all.obj`)을 쓴다.
    """
    body_yaml = (prep.get("generation_body") or {}).get("body_yaml")
    if not body_yaml:
        return None
    obj = Path(str(body_yaml)).with_suffix(".obj")
    return obj if obj.is_file() else None


def _env_path(environ: Mapping[str, str], name: str) -> Path | None:
    value = (environ.get(name) or "").strip()
    return Path(value) if value else None


def make_draper(settings, environ: Mapping[str, str] | None = None) -> BlenderDraper:
    """팩토리 (`docs/adapters.md`). 필요한 것이 없으면 `AdapterConfigError`.

    - Blender: `settings.blender_path`(BLENDER_PATH, 없으면 BLENDER_EXE)
    - GarmentCode용 Python: `FIT_GC_PYTHON`, 기본 `C:\\dev\\venvs\\garmentcode\\Scripts\\python.exe`
    - GarmentCode 루트: `FIT_RULER_GC_ROOT`, 기본 `C:\\dev\\GarmentCode`
    - 준비 폴더: `FIT_PREPARED_DIR`, 기본 저장소 `data/prepared/`
    - 개발용 몸: `FIT_DRAPE_BODY_OBJ` (있으면 입력 몸 대신 이 OBJ, 개발·시연용)
    - 원단 매핑 표: `FIT_DRAPE_FABRIC_MAP`, 기본 `config/drape/fabric_map_v0.yaml`, `off`면 끈다
    - 착장 뒤 analyze: `FIT_DRAPE_ANALYZE`, `on`이면 돌린다. 기본은 건너뜀(판정에 쓰지 않음)
    """
    from server.wiring import AdapterConfigError  # 어댑터 본체가 server를 import하지 않게 여기서만

    env = os.environ if environ is None else environ
    problems = []
    blender = settings.blender_path
    if blender is None:
        problems.append("Blender 경로가 없습니다(BLENDER_PATH 또는 BLENDER_EXE)")
    elif not Path(blender).is_file():
        problems.append(f"Blender 실행 파일이 없습니다: {blender}")
    gc_python = _env_path(env, GC_PYTHON_ENV) or DEFAULT_GC_PYTHON
    if not gc_python.is_file():
        problems.append(f"GarmentCode용 Python이 없습니다({GC_PYTHON_ENV}): {gc_python}")
    gc_root = _env_path(env, GC_ROOT_ENV) or DEFAULT_GC_ROOT
    if not (gc_root / "assets" / "garment_programs").is_dir():
        problems.append(f"GarmentCode 루트가 아닙니다({GC_ROOT_ENV}): {gc_root}")
    prepared = _env_path(env, PREPARED_DIR_ENV) or DEFAULT_PREPARED_DIR
    if not prepared.is_dir():
        problems.append(f"의류 준비 폴더가 없습니다({PREPARED_DIR_ENV}): {prepared}")
    body = _env_path(env, BODY_OBJ_ENV)
    if body is not None and not body.is_file():
        problems.append(f"개발용 몸 OBJ가 없습니다({BODY_OBJ_ENV}): {body}")
    table = None
    map_value = (env.get(FABRIC_MAP_ENV) or "").strip()
    if map_value.lower() != "off":
        map_path = Path(map_value) if map_value else fm.DEFAULT_FABRIC_MAP
        try:
            table = fm.load_fabric_map(map_path)
        except (OSError, ValueError) as error:
            problems.append(
                f"원단 매핑 표를 읽지 못했습니다({FABRIC_MAP_ENV}): {map_path} ({error})"
            )
    analyze_value = (env.get(ANALYZE_ENV) or "off").strip().lower()
    if analyze_value not in ("on", "off"):
        problems.append(f"{ANALYZE_ENV}는 on이나 off여야 합니다: {analyze_value}")
    if problems:
        raise AdapterConfigError("; ".join(problems))
    return BlenderDraper(
        artifacts_dir=Path(settings.artifacts_dir),
        work_dir=Path(settings.work_dir),
        blender_path=Path(blender),
        gc_python=gc_python,
        gc_root=gc_root,
        prepared_dir=prepared,
        fabric_table=table,
        dev_body_obj=body,
        analyze=analyze_value == "on",
    )


__all__ = ["BlenderDraper", "RunOutcome", "make_draper", "canonical_sha256"]
