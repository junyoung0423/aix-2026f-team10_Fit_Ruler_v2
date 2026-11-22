"""Blender 정면 렌더 어댑터 (#10, C5). `Renderer` 포트를 구현한다.

연결: `config/adapters/drape_render_blender.yaml`의
`render: "fitting.adapters.blender.renderer:make_renderer"`.

흐름 (작업 1개)
1. 입력: `request.input_artifact_ids[0]`의 `artifacts_dir/{id}.blend`(C4 착장 결과).
2. 옷 색: `garment.color_hex` > `COLOR_HEX[garment.color]`(AI Hub 색 표) > 설정의 중립 회색
   (경고 기록).
3. Blender를 하위 프로세스로 돌린다(`blender_render.py`). 작업 폴더는 `work_dir/{job_id}/render/`.
   시간 제한은 `request.timeout_seconds`와 설정 `time_limits_s.render` 중 작은 값이고, 넘으면
   프로세스 트리를 끝낸다(착장 어댑터와 같은 방식).
4. 결과 검사: 출력 파일, 클립 크기·fps·프레임 수(Blender가 MP4를 다시 열어 잰 값), 옷 마스크 비율,
   머리 정점이 화면 안에 있는지(얼굴 처리, docs/decisions.md 2).
5. 성공하면 `artifacts_dir`에 `{job_id}_render_clip.mp4`(`RENDER_OUTPUTS`),
   `{job_id}_render_mask.png`, `{job_id}_render_still.png`를 쓴다. 마스크·정지 프레임은 계약 출력이
   아니어서 ID를 지표(`render_mask_artifact_id`, `render_still_artifact_id`)로 알린다.

실패는 모두 `PortFailure(RENDER_FAILED, retryable=False)`다. 같은 입력이면 같은 결과가 나오기
때문이다(같은 입력 두 번 렌더에서 픽셀 같음, docs/render.md). `safe_message`에는 경로·로그를
넣지 않고, 자세한 기록은 `work_dir/{job_id}/render_record.json`(`internal_details_ref`)에 둔다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fitting.adapters.aihub.tables import COLOR_HEX
from fitting.adapters.blender.draper import _kill_tree, _num, _sha256_file
from fitting.domain import SCHEMA_VERSION, ErrorCode, GarmentVariant, Stage
from fitting.ports import RENDER_OUTPUTS, PortError, PortFailure, PortRequest, PortResult

REPO_ROOT = Path(__file__).resolve().parents[3]
RENDERER_VERSION = "0.1.0"
DEFAULT_RENDER_CONFIG = REPO_ROOT / "config" / "render" / "render_v1.json"
SCRIPT = Path(__file__).with_name("blender_render.py")

RUN_NAME = "render"  # work_dir/{job_id}/render/
RECORD_FILE = "render_record.json"
JOB_FILE = "render_job.json"
LOG_FILE = "render_log.txt"
# blender_render.py가 쓰는 파일
STILL, MASK, CLIP = "render_still.png", "garment_mask.png", "render_clip.mp4"
METRICS, SETTINGS = "render_metrics.json", "render_settings.json"
OUTPUT_FILES = (STILL, MASK, CLIP, METRICS, SETTINGS)
# 계약 밖 산출물 역할(지표로 ID를 알린다). 계약 출력은 RENDER_OUTPUTS.
MASK_ROLE, STILL_ROLE = "render_mask", "render_still"

COLOR_SOURCES = ("color_hex", "color_table", "default")


@dataclass(frozen=True)
class RunOutcome:
    returncode: int | None
    timed_out: bool
    seconds: float


# (명령, cwd, 환경, 시간 제한 s, 로그 파일) -> 결과. 테스트는 가짜로 바꾼다.
Runner = Callable[[list[str], Path, dict[str, str], float, Path], RunOutcome]


def subprocess_runner(
    cmd: list[str], cwd: Path, env: dict[str, str], timeout_s: float, log_path: Path
) -> RunOutcome:
    """Blender를 돌리고, 시간 제한을 넘으면 프로세스 트리를 끝낸다."""
    t0 = time.time()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timed_out = False
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        kwargs: dict[str, Any] = {}
        if os.name != "nt":
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(
            cmd, cwd=str(cwd), env=env, stdout=log, stderr=subprocess.STDOUT, **kwargs
        )
        try:
            rc: int | None = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            rc, timed_out = None, True
    return RunOutcome(returncode=rc, timed_out=timed_out, seconds=round(time.time() - t0, 1))


def choose_color(garment: GarmentVariant, default_hex: str) -> tuple[str, str, list[str]]:
    """옷 색을 고른다. (색, 출처, 경고). 출처는 `COLOR_SOURCES` 중 하나."""
    if garment.color_hex:
        return garment.color_hex.lower(), "color_hex", []
    key = (garment.color or "").strip().lower()
    if key in COLOR_HEX:
        return COLOR_HEX[key].lower(), "color_table", []
    return (
        default_hex.lower(),
        "default",
        [f"color_hex가 없고 색 키 '{garment.color}'를 색 표에서 찾지 못해 기본 회색을 썼다"],
    )


def load_render_config(path: Path) -> dict[str, Any]:
    """렌더 설정을 읽고 어댑터가 쓰는 값이 있는지 본다. 틀리면 ValueError."""
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        for key in ("name", "version"):
            if not isinstance(cfg[key], str):
                raise ValueError(f"{key}가 문자열이 아닙니다")
        clip = cfg["clip"]
        for key in ("width", "height", "fps", "frames"):
            if not isinstance(clip[key], int) or clip[key] <= 0:
                raise ValueError(f"clip.{key}가 양의 정수가 아닙니다")
        mask = cfg["mask"]
        if not 0 <= mask["ratio_min"] < mask["ratio_max"] <= 1:
            raise ValueError("mask.ratio_min·ratio_max가 0~1 안의 범위가 아닙니다")
        if cfg["time_limits_s"]["render"] <= 0:
            raise ValueError("time_limits_s.render가 양수가 아닙니다")
        cfg["garment_material"]["default_srgb_hex"]
    except (KeyError, TypeError) as error:
        raise ValueError(f"렌더 설정에 필요한 값이 없습니다: {error}") from None
    return cfg


@dataclass
class BlenderRenderer:
    artifacts_dir: Path
    work_dir: Path
    blender_path: Path
    render_config: Path = DEFAULT_RENDER_CONFIG
    runner: Runner = subprocess_runner

    def _fail(self, message: str, ref: Path, record: dict[str, Any]) -> PortFailure:
        record.update(
            status="FAILED", error_code=ErrorCode.RENDER_FAILED.value, safe_message=message
        )
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        return PortFailure(
            PortError(
                code=ErrorCode.RENDER_FAILED,
                stage=Stage.RENDER,
                retryable=False,
                safe_message=message,
                internal_details_ref=str(ref),
            )
        )

    def artifact_ids(self, job_id: str) -> tuple[str, str, str]:
        """(클립, 마스크, 정지 프레임) 산출물 ID.

        클립은 가짜 어댑터와 같은 `{job_id}_render_clip`이다.
        """
        (clip_role,) = RENDER_OUTPUTS
        return f"{job_id}_{clip_role}", f"{job_id}_{MASK_ROLE}", f"{job_id}_{STILL_ROLE}"

    def render(self, request: PortRequest, garment: GarmentVariant) -> PortResult:
        job_dir = self.work_dir / request.job_id
        out = job_dir / RUN_NAME
        ref = job_dir / RECORD_FILE
        clip_id, mask_id, still_id = self.artifact_ids(request.job_id)
        targets = {
            CLIP: self.artifacts_dir / f"{clip_id}.mp4",
            MASK: self.artifacts_dir / f"{mask_id}.png",
            STILL: self.artifacts_dir / f"{still_id}.png",
        }
        record: dict[str, Any] = {
            "renderer_version": RENDERER_VERSION,
            "job_id": request.job_id,
            "attempt_id": request.attempt_id,
            "garment_id": garment.id,
        }
        # 같은 작업의 예전 결과가 남아 새 결과처럼 보이지 않게 지운다.
        for path in [*targets.values(), *(out / name for name in OUTPUT_FILES)]:
            path.unlink(missing_ok=True)

        cfg = load_render_config(self.render_config)
        cfg_sha = _sha256_file(self.render_config)
        record["render_config"] = {
            "path": str(self.render_config),
            "name": cfg["name"],
            "version": cfg["version"],
            "sha256": cfg_sha,
        }

        if not request.input_artifact_ids:
            raise self._fail("렌더에 쓸 착장 결과가 없습니다.", ref, record)
        drape_id = request.input_artifact_ids[0]
        blend = self.artifacts_dir / f"{drape_id}.blend"
        record["drape_artifact_id"] = drape_id
        if not blend.is_file():
            raise self._fail("렌더에 쓸 착장 결과가 없습니다.", ref, record)
        record["blend_sha256"] = _sha256_file(blend)

        color_hex, color_source, warnings = choose_color(
            garment, cfg["garment_material"]["default_srgb_hex"]
        )
        record["garment_color"] = {
            "hex": color_hex,
            "source": color_source,
            "garment_color": garment.color,
            "garment_color_hex": garment.color_hex,
            "warnings": warnings,
        }

        timeout_s = min(float(request.timeout_seconds), float(cfg["time_limits_s"]["render"]))
        out.mkdir(parents=True, exist_ok=True)
        job = {
            "out_dir": str(out),
            "config": str(self.render_config),
            "garment_color_hex": color_hex,
            "engine_override": None,
        }
        (out / JOB_FILE).write_text(json.dumps(job, indent=2, ensure_ascii=False), "utf-8")
        cmd = [
            str(self.blender_path),
            "--background",
            "--factory-startup",
            str(blend),
            "--python-exit-code",
            "1",
            "--python",
            str(SCRIPT),
            "--",
            str(out / JOB_FILE),
        ]
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        record.update(timeout_s=timeout_s, out_dir=str(out), log=str(job_dir / LOG_FILE))
        outcome = self.runner(cmd, REPO_ROOT, env, timeout_s, job_dir / LOG_FILE)
        record["run"] = {
            "returncode": outcome.returncode,
            "timed_out": outcome.timed_out,
            "seconds": outcome.seconds,
        }
        if outcome.timed_out:
            raise self._fail(
                f"렌더가 시간 제한({int(timeout_s)}초) 안에 끝나지 않았습니다.", ref, record
            )
        try:
            raw = json.loads((out / METRICS).read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("지표가 객체가 아닙니다")
        except (OSError, ValueError):
            raise self._fail("렌더 프로그램이 비정상 종료했습니다.", ref, record) from None
        record["blender_metrics"] = raw
        if raw.get("failure") == "objects_not_found":
            raise self._fail("착장 결과에서 몸이나 옷을 찾지 못했습니다.", ref, record)
        if outcome.returncode != 0 or raw.get("status") != "SUCCEEDED":
            raise self._fail("렌더 프로그램이 비정상 종료했습니다.", ref, record)
        if not all((out / name).is_file() for name in (STILL, MASK, CLIP)):
            raise self._fail("렌더 결과 파일이 없습니다.", ref, record)

        c = cfg["clip"]
        probe = raw.get("clip_probe") or {}
        expected = {
            "width": c["width"],
            "height": c["height"],
            "fps": c["fps"],
            "frames": c["frames"],
        }
        found = {k: probe.get(k) for k in expected}
        still_size = {"width": raw.get("width"), "height": raw.get("height")}
        record["clip_check"] = {"expected": expected, "found": found, "still": still_size}
        if any(not _same_number(found[k], v) for k, v in expected.items()) or still_size != {
            "width": c["width"],
            "height": c["height"],
        }:
            raise self._fail("렌더 클립의 크기·fps·프레임 수가 설정과 다릅니다.", ref, record)

        ratio = raw.get("garment_mask_ratio")
        lo, hi = cfg["mask"]["ratio_min"], cfg["mask"]["ratio_max"]
        if not isinstance(ratio, int | float) or ratio <= 0:
            raise self._fail("옷 마스크가 비었습니다.", ref, record)
        if not lo <= ratio <= hi:
            raise self._fail("옷 마스크 비율이 기준 범위 밖입니다.", ref, record)
        head_in = raw.get("head_vertices_in_frame")
        if not isinstance(head_in, int) or head_in != 0:
            raise self._fail(
                "머리가 화면 안에 들어와 얼굴 처리 기준을 지키지 못했습니다.", ref, record
            )

        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        for name in (MASK, STILL, CLIP):  # 계약 출력(클립)을 마지막에 쓴다
            shutil.copyfile(out / name, targets[name])

        metrics: dict[str, Any] = {
            "width": c["width"],
            "height": c["height"],
            "fps": c["fps"],
            "frames": c["frames"],
            "duration_s": round(c["frames"] / c["fps"], 3),
            "codec": str(c["codec"]),
            "static_clip": 1,
            "render_seconds": outcome.seconds,
            "still_seconds": _num(raw.get("still_seconds")),
            "engine": _num(raw.get("engine")),
            "samples": _num(raw.get("samples")),
            "garment_mask_ratio": ratio,
            "head_vertices_in_frame": head_in,
            "garment_vertices_out_of_frame": _num(raw.get("garment_vertices_out_of_frame")),
            "top_limited_by": _num(raw.get("top_limited_by")),
            "garment_color_hex": color_hex,
            "garment_color_source": color_source,
            "garment_color_warnings": _num(warnings),
            "garment_median_hex": _num(raw.get("garment_median_hex")),
            "garment_delta_e00": _num(raw.get("garment_delta_e00")),
            "still_pixel_sha256": _num(raw.get("still_pixel_sha256")),
            "render_config": f"{cfg['name']}@{cfg['version']}",
            "render_config_sha256": cfg_sha,
            "render_mask_artifact_id": mask_id,
            "render_still_artifact_id": still_id,
        }
        record.update(
            status="SUCCEEDED",
            output_artifact_ids=[clip_id],
            extra_artifact_ids={MASK_ROLE: mask_id, STILL_ROLE: still_id},
            metrics=metrics,
        )
        ref.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        return PortResult(
            schema_version=SCHEMA_VERSION,
            output_artifact_ids=[clip_id],
            metrics=metrics,
            tool_versions={
                "renderer": RENDERER_VERSION,
                "blender": str(raw.get("blender") or ""),
            },
        )


def _same_number(found: Any, expected: int) -> bool:
    return (
        isinstance(found, int | float)
        and not isinstance(found, bool)
        and abs(found - expected) < 1e-6
    )


def make_renderer(settings, environ: Mapping[str, str] | None = None) -> BlenderRenderer:
    """팩토리 (`docs/adapters.md`). 필요한 것이 없으면 `AdapterConfigError`.

    - Blender: `settings.blender_path`(BLENDER_PATH, 없으면 BLENDER_EXE)
    - 렌더 설정: `config/render/render_v1.json`
    """
    from server.wiring import AdapterConfigError  # 어댑터 본체가 server를 import하지 않게 여기서만

    del environ  # 착장 팩토리와 같은 모양. 지금은 읽는 환경변수가 없다
    problems = []
    blender = settings.blender_path
    if blender is None:
        problems.append("Blender 경로가 없습니다(BLENDER_PATH 또는 BLENDER_EXE)")
    elif not Path(blender).is_file():
        problems.append(f"Blender 실행 파일이 없습니다: {blender}")
    try:
        load_render_config(DEFAULT_RENDER_CONFIG)
    except (OSError, ValueError) as error:
        problems.append(f"렌더 설정을 읽지 못했습니다: {DEFAULT_RENDER_CONFIG} ({error})")
    if problems:
        raise AdapterConfigError("; ".join(problems))
    return BlenderRenderer(
        artifacts_dir=Path(settings.artifacts_dir),
        work_dir=Path(settings.work_dir),
        blender_path=Path(blender),
    )


__all__ = [
    "BlenderRenderer",
    "COLOR_SOURCES",
    "RunOutcome",
    "choose_color",
    "load_render_config",
    "make_renderer",
]
