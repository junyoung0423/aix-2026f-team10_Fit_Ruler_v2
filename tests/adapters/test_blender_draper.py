"""Blender 착장 어댑터 (#8). run_drape 하위 프로세스는 가짜 실행기로 바꾼다."""

import json
import os
import re
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from fitting.adapters.blender import draper as dr
from fitting.adapters.blender import fabric_map as fm
from fitting.domain import SCHEMA_VERSION, ErrorCode, GarmentVariant, Stage
from fitting.ports import DRAPE_OUTPUTS, Draper, PortFailure, PortRequest
from server.wiring import (
    ADAPTERS_CONFIG_DIR,
    AdapterConfigError,
    AdapterSettings,
    build_ports,
    load_adapter_config,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO = REPO_ROOT / "data" / "garments" / "tee_regular_m_navy_v1.json"
SAMPLE = REPO_ROOT / "data" / "samples" / "tee_gc_v1" / "tee_gc_regular_06"
JOB = "fit_abc123"


def _garment(**changes) -> GarmentVariant:
    data = json.loads(DEMO.read_text(encoding="utf-8"))
    for k, v in changes.items():
        data[k] = v
    return GarmentVariant.model_validate(data)


def _request(inputs=("fit_abc123_body_mesh",), timeout=600) -> PortRequest:
    return PortRequest(
        job_id=JOB,
        attempt_id=f"{JOB}_drape_1",
        input_artifact_ids=list(inputs),
        config_version="cfg_v1",
        timeout_seconds=timeout,
        trace_id=JOB,
    )


def _prepare(prepared: Path, garment: GarmentVariant, *, status="READY", sha=None):
    folder = prepared / garment.id
    folder.mkdir(parents=True)
    shutil.copyfile(SAMPLE / "specification.json", folder / "specification.json")
    spec = json.loads((folder / "specification.json").read_text(encoding="utf-8"))
    m = garment.measurements
    prep = {
        "schema": "garment_prep_v0",
        "garment_id": garment.id,
        "status": status,
        "failure_reason": None if status == "READY" else "OUT_OF_TOLERANCE",
        "sample_id": "tee_gc_regular_06",
        "targets_cm": {
            "chest_girth_cm": m.chest_girth_cm,
            "sleeve_length_cm": m.sleeve_length_cm,
            "body_length_cm": m.body_length_cm,
        },
        "shoulder": {"judged": False, "diff_cm": 11.543},
        "specification": "specification.json",
        "pattern_sha256": sha or dr.canonical_sha256(spec),
    }
    (folder / "prep.json").write_text(json.dumps(prep), encoding="utf-8")


class FakeRunDrape:
    """run_drape 대신 결과 폴더를 만든다."""

    def __init__(
        self,
        *,
        status="SUCCEEDED",
        error_code=None,
        failed=(),
        rc=0,
        timed_out=False,
        write_summary=True,
        analyze_timed_out=False,
    ):
        self.status, self.error_code, self.failed = status, error_code, list(failed)
        self.rc, self.timed_out, self.write_summary = rc, timed_out, write_summary
        self.analyze_timed_out = analyze_timed_out
        self.calls = []

    def __call__(self, cmd, cwd, env, timeout_s, log_path):
        self.calls.append({"cmd": cmd, "cwd": cwd, "timeout_s": timeout_s})
        if self.timed_out:
            return dr.RunOutcome(returncode=None, timed_out=True, seconds=timeout_s)
        args = dict(zip(cmd[3::2], cmd[4::2], strict=False))
        out = Path(args["--out-root"]) / args["--name"]
        out.mkdir(parents=True)
        metrics = {
            "drape_ok": self.status == "SUCCEEDED",
            "penetration_area_ratio": 0.0,
            "stretch_max": 1.441,
            "seam_gap_preweld_max_mm": 6.8,
            "frames": 110,
            "sim_seconds": 70.1,
            "blender": "5.2.3 LTS",
            "failed_checks": self.failed,
        }
        (out / "drape_metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
        if self.analyze_timed_out:
            (out / "drape.blend").write_bytes(b"BLEND")
            (out / "front.png").write_bytes(b"\x89PNG")
            return dr.RunOutcome(
                returncode=None, timed_out=False, seconds=1.0, analyze_timed_out=True
            )
        if self.write_summary:
            summary = {
                "status": self.status,
                "error_code": self.error_code,
                "reason": None,
                "drape_metrics": metrics,
                "versions": {"garmentcode_commit": "d449629", "python": "3.9.13"},
            }
            (out / "run_summary.json").write_text(json.dumps(summary), encoding="utf-8")
        (out / "drape.blend").write_bytes(b"BLEND")
        (out / "front.png").write_bytes(b"\x89PNG")
        return dr.RunOutcome(returncode=self.rc, timed_out=False, seconds=1.0)


@pytest.fixture
def env(tmp_path):
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    art = tmp_path / "art"
    art.mkdir()
    body = art / "fit_abc123_body_mesh.obj"
    body.write_text("v 0 0 0\n", encoding="utf-8")
    return {"prepared": prepared, "art": art, "work": tmp_path / "work", "tmp": tmp_path}


def _draper(env, runner, **kw) -> dr.BlenderDraper:
    return dr.BlenderDraper(
        artifacts_dir=env["art"],
        work_dir=env["work"],
        blender_path=Path("blender.exe"),
        gc_python=Path("python.exe"),
        gc_root=Path("GarmentCode"),
        prepared_dir=env["prepared"],
        fabric_table=kw.pop("fabric_table", fm.load_fabric_map()),
        runner=runner,
        **kw,
    )


def _failure(draper, garment, request=None) -> tuple[ErrorCode, str, str]:
    with pytest.raises(PortFailure) as info:
        draper.drape(request or _request(), garment)
    e = info.value.error
    assert e.stage is Stage.DRAPE and e.retryable is False
    return e.code, e.safe_message, e.internal_details_ref


def test_success_returns_blend_and_preview_in_drape_outputs_order(env):
    g = _garment()
    _prepare(env["prepared"], g)
    runner = FakeRunDrape()
    result = _draper(env, runner).drape(_request(), g)
    assert result.schema_version == SCHEMA_VERSION
    assert result.output_artifact_ids == [f"{JOB}_{role}" for role in DRAPE_OUTPUTS]
    assert (env["art"] / f"{JOB}_drape_result.blend").read_bytes() == b"BLEND"
    assert (env["art"] / f"{JOB}_drape_preview.png").read_bytes() == b"\x89PNG"
    m = result.metrics
    assert m["drape_ok"] == 1 and m["stretch_max"] == 1.441 and m["frames"] == 110
    assert m["sample_id"] == "tee_gc_regular_06" and m["shoulder_diff_cm"] == 11.543
    assert m["body_source"] == "artifact:fit_abc123_body_mesh"
    for key in (
        "penetration_area_ratio",
        "seam_gap_preweld_max_mm",
        "sim_seconds",
        "body_sha256",
        "drape_config",
        "drape_config_sha256",
        "fabric_stiffness_multiplier",
        "fabric_areal_density_kg_m2",
    ):
        assert key in m
    assert all(isinstance(v, int | float | str) and not isinstance(v, bool) for v in m.values())
    # 하위 프로세스: 저장소 루트에서 run_drape, 작업 폴더 work_dir/{job_id}/drape
    call = runner.calls[0]
    assert call["cmd"][1:3] == ["-m", "fitting.adapters.blender.run_drape"]
    assert call["cwd"] == dr.REPO_ROOT
    assert (env["work"] / JOB / "drape" / "run_summary.json").is_file()
    assert json.loads((env["work"] / JOB / "drape_record.json").read_text("utf-8"))["status"] == (
        "SUCCEEDED"
    )



def test_success_metric_keys_match_the_draper_docstring(env):
    """`Draper` docstring "지표:" 단락의 키 = 성공 때 어댑터가 내는 지표 키."""
    paragraph = Draper.__doc__.split("지표:", 1)[1].split("\n\n", 1)[0]
    documented = set(re.findall(r"`([a-z][a-z0-9_]*)`", paragraph))
    g = _garment()
    _prepare(env["prepared"], g)
    result = _draper(env, FakeRunDrape()).drape(_request(), g)
    assert set(result.metrics) == documented

def test_demo_fabric_keeps_drape_v1(env):
    """시연 의류(면 100 %, contain little)는 배율 1.0·면밀도 0.18이라 drape_v1을 그대로 쓴다."""
    g = _garment()
    _prepare(env["prepared"], g)
    runner = FakeRunDrape()
    result = _draper(env, runner).drape(_request(), g)
    args = dict(zip(runner.calls[0]["cmd"][3::2], runner.calls[0]["cmd"][4::2], strict=False))
    assert Path(args["--config"]) == dr.DEFAULT_DRAPE_CONFIG
    assert result.metrics["drape_config"] == "drape_v1@1.0.0"
    assert result.metrics["fabric_stiffness_multiplier"] == 1.0


def test_other_fabric_writes_a_derived_config(env):
    g = _garment(fabric={"fibers": ["polyester"], "composition": None, "stretch": "contain"})
    _prepare(env["prepared"], g)
    runner = FakeRunDrape()
    result = _draper(env, runner).drape(_request(), g)
    args = dict(zip(runner.calls[0]["cmd"][3::2], runner.calls[0]["cmd"][4::2], strict=False))
    derived = json.loads(Path(args["--config"]).read_text(encoding="utf-8"))
    assert derived["parent"]["name"] == "drape_v1"
    assert derived["cloth"]["tension_stiffness"] == pytest.approx(15.0 * 0.8)
    assert derived["weld"]["areal_density_kg_m2"] == 0.15
    base = json.loads(dr.DEFAULT_DRAPE_CONFIG.read_text(encoding="utf-8"))
    assert derived["thresholds"] == base["thresholds"]  # 검사 기준은 그대로
    assert result.metrics["drape_config"] == "drape_v1_fabric@1.0.0+fabric_map_v0"


def test_fabric_map_off_uses_drape_v1(env):
    g = _garment(fabric={"fibers": ["polyester"], "composition": None, "stretch": "contain"})
    _prepare(env["prepared"], g)
    result = _draper(env, FakeRunDrape(), fabric_table=None).drape(_request(), g)
    assert result.metrics["fabric_map"] == "off"
    assert result.metrics["drape_config"] == "drape_v1@1.0.0"


def test_timeout_is_the_smaller_of_request_and_config(env):
    g = _garment()
    _prepare(env["prepared"], g)
    runner = FakeRunDrape()
    _draper(env, runner).drape(_request(timeout=120), g)
    assert runner.calls[0]["timeout_s"] == 120
    runner = FakeRunDrape()
    shutil.rmtree(env["work"])
    _draper(env, runner).drape(_request(timeout=5000), g)
    assert runner.calls[0]["timeout_s"] == 600


def test_no_prep_record_is_garment_not_ready(env):
    code, message, ref = _failure(_draper(env, FakeRunDrape()), _garment())
    assert code is ErrorCode.GARMENT_NOT_READY
    assert str(env["tmp"]) not in message
    assert Path(ref).is_file()


def test_failed_prep_is_garment_not_ready(env):
    g = _garment()
    _prepare(env["prepared"], g, status="FAILED")
    runner = FakeRunDrape()
    assert _failure(_draper(env, runner), g)[0] is ErrorCode.GARMENT_NOT_READY
    assert runner.calls == []


def test_pattern_hash_mismatch_is_garment_not_ready(env):
    g = _garment()
    _prepare(env["prepared"], g, sha="0" * 64)
    assert _failure(_draper(env, FakeRunDrape()), g)[0] is ErrorCode.GARMENT_NOT_READY


def test_missing_pattern_file_is_garment_not_ready(env):
    g = _garment()
    _prepare(env["prepared"], g)
    (env["prepared"] / g.id / "specification.json").unlink()
    assert _failure(_draper(env, FakeRunDrape()), g)[0] is ErrorCode.GARMENT_NOT_READY


def test_prep_for_other_measurements_is_garment_not_ready(env):
    _prepare(env["prepared"], _garment())
    changed = json.loads(DEMO.read_text(encoding="utf-8"))
    changed["measurements"]["chest_girth_cm"] = 112.0
    g = GarmentVariant.model_validate(changed)
    assert _failure(_draper(env, FakeRunDrape()), g)[0] is ErrorCode.GARMENT_NOT_READY


def test_failed_checks_are_drape_invalid(env):
    g = _garment()
    _prepare(env["prepared"], g)
    runner = FakeRunDrape(status="FAILED", error_code="DRAPE_INVALID", failed=["stretch_ok"], rc=1)
    code, message, ref = _failure(_draper(env, runner), g)
    assert code is ErrorCode.DRAPE_INVALID
    assert "stretch_ok" in Path(ref).read_text(encoding="utf-8")
    assert not (env["art"] / f"{JOB}_drape_preview.png").exists()


def test_timeout_is_drape_invalid(env):
    g = _garment()
    _prepare(env["prepared"], g)
    code, message, _ = _failure(_draper(env, FakeRunDrape(timed_out=True)), g)
    assert code is ErrorCode.DRAPE_INVALID and "시간 제한" in message


def test_abnormal_exit_is_drape_invalid(env):
    g = _garment()
    _prepare(env["prepared"], g)
    runner = FakeRunDrape(rc=3, write_summary=False)
    assert _failure(_draper(env, runner), g)[0] is ErrorCode.DRAPE_INVALID


def test_export_failure_keeps_run_drape_code(env):
    g = _garment()
    _prepare(env["prepared"], g)
    runner = FakeRunDrape(status="FAILED", error_code="GARMENT_NOT_READY", rc=1)
    assert _failure(_draper(env, runner), g)[0] is ErrorCode.GARMENT_NOT_READY


def test_missing_body_mesh_is_drape_invalid(env):
    g = _garment()
    _prepare(env["prepared"], g)
    runner = FakeRunDrape()
    code, _, _ = _failure(_draper(env, runner), g, _request(inputs=("fit_abc123_nobody",)))
    assert code is ErrorCode.DRAPE_INVALID
    assert runner.calls == []


def test_dev_body_replaces_the_input(env):
    g = _garment()
    _prepare(env["prepared"], g)
    dev = env["tmp"] / "mean_all.obj"
    dev.write_text("v 1 1 1\n", encoding="utf-8")
    runner = FakeRunDrape()
    result = _draper(env, runner, dev_body_obj=dev).drape(_request(inputs=()), g)
    args = dict(zip(runner.calls[0]["cmd"][3::2], runner.calls[0]["cmd"][4::2], strict=False))
    assert Path(args["--body"]) == dev
    assert result.metrics["body_source"] == "dev_env:FIT_DRAPE_BODY_OBJ"


def test_canonical_hash_matches_pattern_measure():
    pm = pytest.importorskip(
        "fitting.adapters.garmentcode.pattern_measure", reason="garment 선택 의존성 필요"
    )
    spec = json.loads((SAMPLE / "specification.json").read_text(encoding="utf-8"))
    sample = json.loads((SAMPLE / "sample.json").read_text(encoding="utf-8"))
    assert dr.canonical_sha256(spec) == pm.canonical_sha256(spec) == sample["pattern_sha256"]


# --- 팩토리 ---


def _factory_env(tmp_path):
    blender = tmp_path / "blender.exe"
    blender.write_bytes(b"")
    gc_python = tmp_path / "python.exe"
    gc_python.write_bytes(b"")
    gc_root = tmp_path / "GarmentCode"
    (gc_root / "assets" / "garment_programs").mkdir(parents=True)
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    settings = AdapterSettings.from_environ(
        artifacts_dir=tmp_path / "art", environ={"BLENDER_PATH": str(blender)}
    )
    environ = {
        "FIT_GC_PYTHON": str(gc_python),
        "FIT_RULER_GC_ROOT": str(gc_root),
        "FIT_PREPARED_DIR": str(prepared),
    }
    return settings, environ


def test_factory_builds_a_draper(tmp_path):
    settings, environ = _factory_env(tmp_path)
    d = dr.make_draper(settings, environ)
    assert isinstance(d, dr.BlenderDraper) and d.dev_body_obj is None
    assert d.fabric_table is not None and d.fabric_table.version == "fabric_map_v0"


@pytest.mark.parametrize(
    "change, fragment",
    [
        ({"blender": None}, "Blender 경로"),
        ({"FIT_GC_PYTHON": "nope.exe"}, "FIT_GC_PYTHON"),
        ({"FIT_RULER_GC_ROOT": "nope"}, "FIT_RULER_GC_ROOT"),
        ({"FIT_PREPARED_DIR": "nope"}, "FIT_PREPARED_DIR"),
        ({"FIT_DRAPE_BODY_OBJ": "nope.obj"}, "FIT_DRAPE_BODY_OBJ"),
        ({"FIT_DRAPE_FABRIC_MAP": "nope.yaml"}, "FIT_DRAPE_FABRIC_MAP"),
    ],
)
def test_factory_stops_when_something_is_missing(tmp_path, change, fragment):
    settings, environ = _factory_env(tmp_path)
    if "blender" in change:
        settings = AdapterSettings.from_environ(artifacts_dir=tmp_path / "art", environ={})
    else:
        environ.update({k: str(tmp_path / v) for k, v in change.items()})
    with pytest.raises(AdapterConfigError, match=fragment):
        dr.make_draper(settings, environ)


def test_factory_dev_body_and_fabric_off(tmp_path):
    settings, environ = _factory_env(tmp_path)
    body = tmp_path / "mean_all.obj"
    body.write_text("v 0 0 0\n", encoding="utf-8")
    environ.update(FIT_DRAPE_BODY_OBJ=str(body), FIT_DRAPE_FABRIC_MAP="off")
    d = dr.make_draper(settings, environ)
    assert d.dev_body_obj == body and d.fabric_table is None


def test_drape_blender_yaml_uses_the_draper_only_for_drape():
    raw = yaml.safe_load((ADAPTERS_CONFIG_DIR / "drape_blender.yaml").read_text(encoding="utf-8"))
    assert raw == {
        "version": "adapters_v0",
        "ports": {
            "body": "fake",
            "drape": "fitting.adapters.blender.draper:make_draper",
            "render": "fake",
            "realize": "fake",
            "validate": "fake",
        },
    }
    assert load_adapter_config("drape_blender").ports["drape"].endswith(":make_draper")


def test_drape_blender_yaml_builds_with_the_wiring(tmp_path, monkeypatch):
    from fitting.fit import RuleFitCalculator

    settings, environ = _factory_env(tmp_path)
    for k, v in environ.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("FIT_DRAPE_BODY_OBJ", raising=False)
    monkeypatch.delenv("FIT_DRAPE_FABRIC_MAP", raising=False)
    ports = build_ports(load_adapter_config("drape_blender"), settings, fit=RuleFitCalculator())
    assert isinstance(ports.drape, dr.BlenderDraper)


def test_default_yaml_still_uses_the_fake_draper():
    assert load_adapter_config("default").ports["drape"] == "fake"


# --- 시간 제한 (analyze는 따로) ---


def test_analyze_timeout_is_judged_from_blender_metrics(env):
    g = _garment()
    _prepare(env["prepared"], g)
    result = _draper(env, FakeRunDrape(analyze_timed_out=True)).drape(_request(), g)
    assert result.metrics["drape_ok"] == 1
    record = json.loads((env["work"] / JOB / "drape_record.json").read_text("utf-8"))
    assert record["run"]["analyze_timed_out"] is True


def test_analyze_timeout_with_failed_checks_is_drape_invalid(env):
    g = _garment()
    _prepare(env["prepared"], g)
    runner = FakeRunDrape(status="FAILED", failed=["stretch_ok"], analyze_timed_out=True)
    assert _failure(_draper(env, runner), g)[0] is ErrorCode.DRAPE_INVALID


def _script(tmp_path, body: str) -> list[str]:
    p = tmp_path / "fake_run_drape.py"
    p.write_text("import sys, time\n" + body, encoding="utf-8")
    return [sys.executable, str(p)]


def test_runner_returns_the_exit_code(tmp_path):
    cmd = _script(tmp_path, "print('[drape] rc=0', flush=True)\nsys.exit(1)\n")
    outcome = dr.subprocess_runner(cmd, tmp_path, dict(os.environ), 30, tmp_path / "log.txt")
    assert (outcome.returncode, outcome.timed_out, outcome.analyze_timed_out) == (1, False, False)


def test_runner_stops_a_drape_that_is_too_slow(tmp_path):
    cmd = _script(tmp_path, "print('[export] rc=0', flush=True)\ntime.sleep(60)\n")
    outcome = dr.subprocess_runner(cmd, tmp_path, dict(os.environ), 2, tmp_path / "log.txt")
    assert outcome.timed_out and not outcome.analyze_timed_out
    assert outcome.returncode is None and outcome.seconds < 30


def test_runner_gives_analyze_its_own_limit(tmp_path):
    # 착장이 1.5초 걸려 한도(2초) 안에 끝나고, analyze가 한도를 넘는다
    body = "time.sleep(1.5)\nprint('[drape] rc=0', flush=True)\ntime.sleep(60)\n"
    outcome = dr.subprocess_runner(
        _script(tmp_path, body), tmp_path, dict(os.environ), 2, tmp_path / "log.txt"
    )
    assert outcome.analyze_timed_out and not outcome.timed_out
    assert outcome.seconds >= 3
