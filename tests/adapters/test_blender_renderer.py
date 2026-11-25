"""Blender 렌더 어댑터 (#10, C5). Blender 하위 프로세스는 가짜 실행기로 바꾼다."""

import json
import os
import re
import sys
from pathlib import Path

import pytest
import yaml

from fitting.adapters.aihub.tables import COLOR_HEX
from fitting.adapters.blender import draper as dr
from fitting.adapters.blender import renderer as rr
from fitting.domain import SCHEMA_VERSION, ErrorCode, GarmentVariant, Stage
from fitting.ports import RENDER_OUTPUTS, PortFailure, PortRequest, Renderer
from server.wiring import (
    ADAPTERS_CONFIG_DIR,
    AdapterConfigError,
    AdapterSettings,
    build_ports,
    load_adapter_config,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO = REPO_ROOT / "data" / "garments" / "tee_regular_m_navy_v1.json"
JOB = "fit_abc123"
DRAPE_ID = f"{JOB}_drape_result"
CFG = rr.load_render_config(rr.DEFAULT_RENDER_CONFIG)


def _garment(**changes) -> GarmentVariant:
    data = json.loads(DEMO.read_text(encoding="utf-8"))
    data.update(changes)
    return GarmentVariant.model_validate(data)


def _request(inputs=(DRAPE_ID,), timeout=300) -> PortRequest:
    return PortRequest(
        job_id=JOB,
        attempt_id=f"{JOB}_render_1",
        input_artifact_ids=list(inputs),
        config_version="cfg_v1",
        timeout_seconds=timeout,
        trace_id=JOB,
    )


class FakeBlender:
    """blender_render.py 대신 결과 폴더(render_job.json의 out_dir)에 파일과 지표를 쓴다."""

    def __init__(
        self,
        *,
        returncode=0,
        timed_out=False,
        write_metrics=True,
        missing=(),
        failure=None,
        **metric_changes,
    ):
        self.returncode = returncode
        self.timed_out = timed_out
        self.write_metrics = write_metrics
        self.missing = set(missing)
        self.failure = failure
        self.metric_changes = metric_changes
        self.calls: list[dict] = []

    def __call__(self, cmd, cwd, env, timeout_s, log_path):
        job = json.loads(Path(cmd[-1]).read_text(encoding="utf-8"))
        self.calls.append({"cmd": cmd, "cwd": cwd, "timeout_s": timeout_s, "job": job})
        out = Path(job["out_dir"])
        if self.timed_out:
            return rr.RunOutcome(returncode=None, timed_out=True, seconds=timeout_s)
        files = {rr.STILL: b"\x89PNG still", rr.MASK: b"\x89PNG mask", rr.CLIP: b"MP4"}
        if self.failure is None:
            for name, data in files.items():
                if name not in self.missing:
                    (out / name).write_bytes(data)
        if self.write_metrics:
            c = CFG["clip"]
            metrics = {
                "status": "SUCCEEDED" if self.failure is None else "FAILED",
                "blender": "5.2.3 LTS",
                "engine": "CYCLES",
                "samples": 32,
                "still_seconds": 22.3,
                "width": c["width"],
                "height": c["height"],
                "clip_probe": {
                    "width": c["width"],
                    "height": c["height"],
                    "fps": float(c["fps"]),
                    "frames": c["frames"],
                },
                "garment_mask_ratio": 0.2959,
                "head_vertices_in_frame": 0,
                "garment_vertices_out_of_frame": 4,
                "top_limited_by": "head",
                "garment_median_hex": "#283248",
                "garment_delta_e00": 2.63,
                "still_pixel_sha256": "ab" * 32,
            }
            if self.failure is not None:
                metrics["failure"] = self.failure
            for key, value in self.metric_changes.items():
                if key.startswith("probe_"):
                    metrics["clip_probe"][key[len("probe_") :]] = value
                else:
                    metrics[key] = value
            (out / rr.METRICS).write_text(json.dumps(metrics), encoding="utf-8")
        return rr.RunOutcome(returncode=self.returncode, timed_out=False, seconds=41.5)


@pytest.fixture
def env(tmp_path):
    art, work = tmp_path / "art", tmp_path / "work"
    art.mkdir()
    (art / f"{DRAPE_ID}.blend").write_bytes(b"BLEND")
    return {"art": art, "work": work, "tmp": tmp_path}


def _renderer(env, runner) -> rr.BlenderRenderer:
    return rr.BlenderRenderer(
        artifacts_dir=env["art"],
        work_dir=env["work"],
        blender_path=Path("blender.exe"),
        runner=runner,
    )


def _failure(renderer, garment=None, request=None) -> tuple[str, str]:
    with pytest.raises(PortFailure) as info:
        renderer.render(request or _request(), garment or _garment())
    e = info.value.error
    assert e.code is ErrorCode.RENDER_FAILED
    assert e.stage is Stage.RENDER and e.retryable is False
    assert e.external_request_id is None
    # 화면에 보이는 문장에는 경로·로그가 없다. 자세한 것은 작업 폴더 기록에.
    assert "/" not in e.safe_message and "\\" not in e.safe_message
    assert str(renderer.work_dir) not in e.safe_message
    record = json.loads(Path(e.internal_details_ref).read_text(encoding="utf-8"))
    assert record["status"] == "FAILED" and record["error_code"] == "RENDER_FAILED"
    assert Path(e.internal_details_ref) == renderer.work_dir / JOB / rr.RECORD_FILE
    return e.safe_message, e.internal_details_ref


# --- 성공 ---


def test_success_writes_clip_mask_and_still_with_fixed_names(env):
    runner = FakeBlender()
    result = _renderer(env, runner).render(_request(), _garment())
    assert result.schema_version == SCHEMA_VERSION
    assert result.output_artifact_ids == [f"{JOB}_{role}" for role in RENDER_OUTPUTS]
    assert result.output_artifact_ids == [f"{JOB}_render_clip"]
    assert (env["art"] / f"{JOB}_render_clip.mp4").read_bytes() == b"MP4"
    assert (env["art"] / f"{JOB}_render_mask.png").read_bytes() == b"\x89PNG mask"
    assert (env["art"] / f"{JOB}_render_still.png").read_bytes() == b"\x89PNG still"
    m = result.metrics
    assert m["render_mask_artifact_id"] == f"{JOB}_render_mask"
    assert m["render_still_artifact_id"] == f"{JOB}_render_still"
    assert (m["width"], m["height"], m["fps"], m["frames"]) == (768, 1024, 24, 121)
    assert m["duration_s"] == round(121 / 24, 3) and m["codec"] == "H264"
    assert m["static_clip"] == 1 and m["render_seconds"] == 41.5
    assert m["engine"] == "CYCLES" and m["samples"] == 32
    assert m["garment_mask_ratio"] == 0.2959 and m["head_vertices_in_frame"] == 0
    assert m["render_config"] == "render_v1@1.1.0"
    assert all(isinstance(v, int | float | str) and not isinstance(v, bool) for v in m.values())
    assert result.tool_versions == {"renderer": rr.RENDERER_VERSION, "blender": "5.2.3 LTS"}
    record = json.loads((env["work"] / JOB / rr.RECORD_FILE).read_text("utf-8"))
    assert record["status"] == "SUCCEEDED" and record["output_artifact_ids"] == [
        f"{JOB}_render_clip"
    ]


def test_blender_is_called_with_the_drape_blend_and_the_render_script(env):
    runner = FakeBlender()
    _renderer(env, runner).render(_request(), _garment())
    call = runner.calls[0]
    cmd = call["cmd"]
    assert cmd[0] == "blender.exe" and "--background" in cmd and "--factory-startup" in cmd
    assert str(env["art"] / f"{DRAPE_ID}.blend") in cmd
    assert cmd[cmd.index("--python") + 1] == str(rr.SCRIPT)
    assert cmd[cmd.index("--python-exit-code") + 1] == "1"
    assert cmd[-2] == "--"
    assert call["cwd"] == rr.REPO_ROOT
    assert Path(call["job"]["out_dir"]) == env["work"] / JOB / "render"
    assert Path(call["job"]["config"]) == rr.DEFAULT_RENDER_CONFIG


def test_success_metric_keys_match_the_renderer_docstring(env):
    """`Renderer` docstring "지표:" 단락의 키 = 성공 때 어댑터가 내는 지표 키."""
    paragraph = Renderer.__doc__.split("지표:", 1)[1].split("\n\n", 1)[0]
    documented = set(re.findall(r"`([a-z][a-z0-9_]*)`", paragraph))
    result = _renderer(env, FakeBlender()).render(_request(), _garment())
    assert set(result.metrics) == documented


@pytest.mark.parametrize(("request_s", "expected"), [(120, 120.0), (300, 300.0), (900, 300.0)])
def test_timeout_is_the_smaller_of_request_and_config(env, request_s, expected):
    runner = FakeBlender()
    _renderer(env, runner).render(_request(timeout=request_s), _garment())
    assert CFG["time_limits_s"]["render"] == 300
    assert runner.calls[0]["timeout_s"] == expected


# --- 색 고르기 ---


def test_color_hex_is_used_first(env):
    runner = FakeBlender()
    result = _renderer(env, runner).render(_request(), _garment(color_hex="#24324f"))
    assert runner.calls[0]["job"]["garment_color_hex"] == "#24324f"
    assert result.metrics["garment_color_hex"] == "#24324f"
    assert result.metrics["garment_color_source"] == "color_hex"
    assert result.metrics["garment_color_warnings"] == ""


def test_color_key_is_looked_up_in_the_color_table(env):
    runner = FakeBlender()
    result = _renderer(env, runner).render(_request(), _garment(color="black", color_hex=None))
    assert runner.calls[0]["job"]["garment_color_hex"] == COLOR_HEX["black"]
    assert result.metrics["garment_color_source"] == "color_table"


def test_unknown_color_uses_default_gray_and_warns(env):
    runner = FakeBlender()
    result = _renderer(env, runner).render(_request(), _garment(color="teal", color_hex=None))
    default = CFG["garment_material"]["default_srgb_hex"]
    assert runner.calls[0]["job"]["garment_color_hex"] == default
    assert result.metrics["garment_color_source"] == "default"
    assert "teal" in result.metrics["garment_color_warnings"]
    record = json.loads((env["work"] / JOB / rr.RECORD_FILE).read_text("utf-8"))
    assert record["garment_color"]["warnings"]


def test_choose_color_sources():
    assert rr.choose_color(_garment(color_hex="#abcdef"), "#808080") == ("#abcdef", "color_hex", [])
    assert rr.choose_color(_garment(color="Navy", color_hex=None), "#808080")[:2] == (
        COLOR_HEX["navy"],
        "color_table",
    )
    hex_, source, warnings = rr.choose_color(_garment(color="mint", color_hex=None), "#808080")
    assert (hex_, source, len(warnings)) == ("#808080", "default", 1)
    assert set(rr.COLOR_SOURCES) == {"color_hex", "color_table", "default"}


# --- 배경 (옷 색이 기본 배경과 가까우면 다른 배경) ---

WORLD = CFG["world"]


def test_shipped_background_rule():
    assert WORLD["background_srgb_hex"] == "#808080"
    assert WORLD["background_name"] == "medium_gray"
    rule = WORLD["background_rule"]
    assert rule["min_delta_e00"] == 15.0
    assert [a["srgb_hex"] for a in rule["alternatives"]] == ["#d9d9d9", "#3a3a3a"]


def test_only_gray_in_the_color_table_changes_the_background():
    chosen = {key: rr.choose_background(hex_, WORLD) for key, hex_ in COLOR_HEX.items()}
    changed = {key: b.name for key, b in chosen.items() if b.changed}
    assert changed == {"gray": "dark_gray"}
    assert all(b.srgb_hex == "#808080" for key, b in chosen.items() if key != "gray")
    gray = chosen["gray"]
    assert gray.default_delta_e00 < 15 <= gray.delta_e00
    # 바뀐 뒤에도 기준보다 멀고, 바꾸지 않은 색은 모두 기본 배경과 기준 이상 떨어져 있다
    assert all(b.delta_e00 >= 15 for b in chosen.values())


def test_background_boundary_keeps_the_default_at_exactly_the_threshold():
    hex_ = COLOR_HEX["brown"]
    de = rr.choose_background(hex_, WORLD).default_delta_e00
    at = {**WORLD, "background_rule": {**WORLD["background_rule"], "min_delta_e00": de}}
    above = {**WORLD, "background_rule": {**WORLD["background_rule"], "min_delta_e00": de + 0.01}}
    assert not rr.choose_background(hex_, at).changed
    assert rr.choose_background(hex_, above).changed


def test_background_picks_the_farthest_alternative():
    assert rr.choose_background("#f4f4f2", {**WORLD, "background_srgb_hex": "#f0f0f0"}).name == (
        "dark_gray"
    )
    assert rr.choose_background("#222326", {**WORLD, "background_srgb_hex": "#202020"}).name == (
        "light_gray"
    )


def test_without_a_rule_the_default_background_is_kept():
    world = {k: v for k, v in WORLD.items() if k != "background_rule"}
    b = rr.choose_background(COLOR_HEX["gray"], world)
    assert (b.srgb_hex, b.name, b.changed) == ("#808080", "medium_gray", False)


def test_only_gray_in_the_color_table_changes_the_mannequin():
    body = CFG["body_material"]
    assert body["base_srgb_hex"] == "#9a9a9a"
    chosen = {key: rr.choose_body_color(hex_, body) for key, hex_ in COLOR_HEX.items()}
    assert {k: b.name for k, b in chosen.items() if b.changed} == {"gray": "light_gray_mannequin"}
    assert all(b.delta_e00 >= 15 for b in chosen.values())


def test_gray_garment_gets_colors_apart_from_each_other():
    """회색 옷: 배경(어두운)과 마네킹(밝은)이 옷과도, 서로도 떨어져 있다."""
    gray = COLOR_HEX["gray"]
    bg = rr.choose_background(gray, WORLD).srgb_hex
    body = rr.choose_body_color(gray, CFG["body_material"]).srgb_hex
    assert (bg, body) == ("#3a3a3a", "#d9d9d9")
    assert rr.delta_e_hex(bg, body) >= 15


def test_renderer_passes_the_chosen_background_and_reports_it(env):
    runner = FakeBlender()
    result = _renderer(env, runner).render(_request(), _garment(color_hex=COLOR_HEX["gray"]))
    assert runner.calls[0]["job"]["background_srgb_hex"] == "#3a3a3a"
    m = result.metrics
    assert (m["background_srgb_hex"], m["background_name"]) == ("#3a3a3a", "dark_gray")
    assert m["background_delta_e00"] >= 15
    assert runner.calls[0]["job"]["body_srgb_hex"] == "#d9d9d9"
    assert (m["body_srgb_hex"], m["body_color_name"]) == ("#d9d9d9", "light_gray_mannequin")
    record = json.loads((env["work"] / JOB / rr.RECORD_FILE).read_text("utf-8"))
    assert record["background"]["changed"] is True
    assert record["background"]["default_delta_e00"] < 15
    assert record["body_color"]["changed"] is True


def test_demo_navy_keeps_the_default_background(env):
    runner = FakeBlender()
    result = _renderer(env, runner).render(_request(), _garment())
    assert runner.calls[0]["job"]["background_srgb_hex"] == "#808080"
    assert result.metrics["background_name"] == "medium_gray"
    assert runner.calls[0]["job"]["body_srgb_hex"] == "#9a9a9a"
    assert result.metrics["body_color_name"] == "gray_mannequin"


# --- 실패 (모두 RENDER_FAILED, retryable=false) ---


def test_no_input_artifact_fails(env):
    runner = FakeBlender()
    message, _ = _failure(_renderer(env, runner), request=_request(inputs=()))
    assert "착장 결과" in message and runner.calls == []


def test_missing_drape_blend_fails(env):
    (env["art"] / f"{DRAPE_ID}.blend").unlink()
    runner = FakeBlender()
    message, _ = _failure(_renderer(env, runner))
    assert "착장 결과" in message and runner.calls == []


def test_objects_not_found_fails(env):
    message, _ = _failure(_renderer(env, FakeBlender(returncode=3, failure="objects_not_found")))
    assert "몸이나 옷" in message


def test_timeout_fails_with_the_limit_in_the_message(env):
    message, ref = _failure(_renderer(env, FakeBlender(timed_out=True)))
    assert "300초" in message
    assert json.loads(Path(ref).read_text("utf-8"))["run"]["timed_out"] is True


def test_abnormal_exit_without_metrics_fails(env):
    message, _ = _failure(_renderer(env, FakeBlender(returncode=1, write_metrics=False)))
    assert "비정상 종료" in message


def test_nonzero_exit_with_success_metrics_still_fails(env):
    message, _ = _failure(_renderer(env, FakeBlender(returncode=1)))
    assert "비정상 종료" in message


@pytest.mark.parametrize("name", [rr.CLIP, rr.MASK, rr.STILL])
def test_missing_output_file_fails(env, name):
    message, _ = _failure(_renderer(env, FakeBlender(missing=[name])))
    assert "결과 파일" in message


@pytest.mark.parametrize(
    "change",
    [
        {"probe_width": 720},
        {"probe_height": 960},
        {"probe_fps": 25.0},
        {"probe_frames": 120},
        {"probe_frames": None},
        {"width": 512},
    ],
)
def test_clip_size_fps_or_frames_different_from_config_fails(env, change):
    message, ref = _failure(_renderer(env, FakeBlender(**change)))
    assert "크기·fps·프레임 수" in message
    assert "clip_check" in json.loads(Path(ref).read_text("utf-8"))


@pytest.mark.parametrize("ratio", [0, 0.0, None])
def test_empty_garment_mask_fails(env, ratio):
    message, _ = _failure(_renderer(env, FakeBlender(garment_mask_ratio=ratio)))
    assert "마스크가 비었습니다" in message


def test_mask_ratio_boundaries():
    lo, hi = CFG["mask"]["ratio_min"], CFG["mask"]["ratio_max"]
    assert 0 < lo < hi <= 1


@pytest.mark.parametrize(("offset", "ok"), [(-1e-4, False), (0.0, True), (1e-4, True)])
def test_mask_ratio_just_around_the_lower_bound(env, offset, ok):
    ratio = CFG["mask"]["ratio_min"] + offset
    renderer = _renderer(env, FakeBlender(garment_mask_ratio=ratio))
    if ok:
        assert renderer.render(_request(), _garment()).metrics["garment_mask_ratio"] == ratio
    else:
        assert "범위 밖" in _failure(renderer)[0]


@pytest.mark.parametrize(("offset", "ok"), [(-1e-4, True), (0.0, True), (1e-4, False)])
def test_mask_ratio_just_around_the_upper_bound(env, offset, ok):
    ratio = CFG["mask"]["ratio_max"] + offset
    renderer = _renderer(env, FakeBlender(garment_mask_ratio=ratio))
    if ok:
        renderer.render(_request(), _garment())
    else:
        assert "범위 밖" in _failure(renderer)[0]


@pytest.mark.parametrize("count", [1, 250, None])
def test_head_in_frame_fails(env, count):
    message, _ = _failure(_renderer(env, FakeBlender(head_vertices_in_frame=count)))
    assert "얼굴 처리" in message


def test_failure_removes_stale_outputs_of_the_same_job(env):
    _renderer(env, FakeBlender()).render(_request(), _garment())
    assert (env["art"] / f"{JOB}_render_clip.mp4").is_file()
    _failure(_renderer(env, FakeBlender(head_vertices_in_frame=3)))
    for suffix in ("render_clip.mp4", "render_mask.png", "render_still.png"):
        assert not (env["art"] / f"{JOB}_{suffix}").exists()


# --- 설정 ---


def test_shipped_render_config_matches_the_spec_clip():
    c = CFG["clip"]
    assert (c["width"], c["height"], c["fps"], c["frames"]) == (768, 1024, 24, 121)
    assert c["codec"] == "H264" and c["audio"] is False and c["static"] is True
    assert (c["frames"] - 1) % 8 == 0  # 실사화 프레임 수 규칙 8k + 1
    assert CFG["color_management"]["view_transform"] == "Standard"
    assert CFG["time_limits_s"]["render"] == 300


@pytest.mark.parametrize(
    "breaks",
    [
        lambda c: c.pop("clip"),
        lambda c: c["clip"].update(frames=0),
        lambda c: c["mask"].update(ratio_min=0.7),
        lambda c: c["time_limits_s"].update(render=0),
        lambda c: c["garment_material"].pop("default_srgb_hex"),
        lambda c: c["world"]["background_rule"].update(min_delta_e00=0),
        lambda c: c["world"]["background_rule"].update(alternatives=[]),
        lambda c: c["world"]["background_rule"]["alternatives"][0].update(srgb_hex="gray"),
        lambda c: c["world"].update(background_srgb_hex="#80808"),
        lambda c: c["body_material"]["contrast_rule"].update(min_delta_e00=-1),
        lambda c: c["body_material"]["contrast_rule"]["alternatives"][0].pop("name"),
    ],
)
def test_broken_render_config_is_rejected(tmp_path, breaks):
    cfg = json.loads(rr.DEFAULT_RENDER_CONFIG.read_text(encoding="utf-8"))
    breaks(cfg)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(ValueError):
        rr.load_render_config(path)


# --- 팩토리와 어댑터 설정 파일 ---


def _settings(tmp_path, blender: str | None = "blender.exe") -> AdapterSettings:
    environ = {"FIT_WORK_DIR": str(tmp_path / "work")}
    if blender is not None:
        exe = tmp_path / blender
        exe.write_bytes(b"")
        environ["BLENDER_PATH"] = str(exe)
    return AdapterSettings.from_environ(artifacts_dir=tmp_path / "art", environ=environ)


def test_factory_builds_a_renderer(tmp_path):
    r = rr.make_renderer(_settings(tmp_path))
    assert isinstance(r, rr.BlenderRenderer)
    assert r.blender_path == tmp_path / "blender.exe"
    assert r.artifacts_dir == tmp_path / "art" and r.work_dir == tmp_path / "work"


def test_factory_without_blender_path_stops(tmp_path):
    with pytest.raises(AdapterConfigError, match="Blender 경로"):
        rr.make_renderer(_settings(tmp_path, blender=None))


def test_factory_with_missing_blender_file_stops(tmp_path):
    settings = AdapterSettings.from_environ(
        artifacts_dir=tmp_path / "art", environ={"BLENDER_PATH": str(tmp_path / "nope.exe")}
    )
    with pytest.raises(AdapterConfigError, match="Blender 실행 파일"):
        rr.make_renderer(settings)


def test_drape_render_blender_yaml_uses_blender_for_drape_and_render_only():
    path = ADAPTERS_CONFIG_DIR / "drape_render_blender.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw == {
        "version": "adapters_v0",
        "ports": {
            "body": "fake",
            "drape": "fitting.adapters.blender.draper:make_draper",
            "render": "fitting.adapters.blender.renderer:make_renderer",
            "realize": "fake",
            "validate": "fake",
        },
    }
    assert load_adapter_config("drape_render_blender").ports["render"].endswith(":make_renderer")


def test_drape_render_blender_yaml_builds_with_the_wiring(tmp_path, monkeypatch):
    from fitting.fit import RuleFitCalculator

    gc_python, gc_root, prepared = tmp_path / "python.exe", tmp_path / "gc", tmp_path / "prep"
    gc_python.write_bytes(b"")
    (gc_root / "assets" / "garment_programs").mkdir(parents=True)
    prepared.mkdir()
    monkeypatch.setenv("FIT_GC_PYTHON", str(gc_python))
    monkeypatch.setenv("FIT_RULER_GC_ROOT", str(gc_root))
    monkeypatch.setenv("FIT_PREPARED_DIR", str(prepared))
    monkeypatch.delenv("FIT_DRAPE_BODY_OBJ", raising=False)
    monkeypatch.delenv("FIT_DRAPE_FABRIC_MAP", raising=False)
    ports = build_ports(
        load_adapter_config("drape_render_blender"), _settings(tmp_path), fit=RuleFitCalculator()
    )
    assert isinstance(ports.drape, dr.BlenderDraper)
    assert isinstance(ports.render, rr.BlenderRenderer)


def test_default_and_drape_blender_yaml_keep_the_fake_renderer():
    assert load_adapter_config("default").ports["render"] == "fake"
    assert load_adapter_config("drape_blender").ports["render"] == "fake"


# --- 하위 프로세스 실행기 ---


def _script(tmp_path, body: str) -> list[str]:
    p = tmp_path / "fake_blender.py"
    p.write_text("import sys, time\n" + body, encoding="utf-8")
    return [sys.executable, str(p)]


def test_runner_returns_the_exit_code(tmp_path):
    cmd = _script(tmp_path, "print('[render] done', flush=True)\nsys.exit(3)\n")
    outcome = rr.subprocess_runner(cmd, tmp_path, dict(os.environ), 30, tmp_path / "log.txt")
    assert (outcome.returncode, outcome.timed_out) == (3, False)
    assert "[render] done" in (tmp_path / "log.txt").read_text(encoding="utf-8")


def test_runner_stops_a_render_that_is_too_slow(tmp_path):
    cmd = _script(tmp_path, "time.sleep(60)\n")
    outcome = rr.subprocess_runner(cmd, tmp_path, dict(os.environ), 2, tmp_path / "log.txt")
    assert outcome.timed_out and outcome.returncode is None and outcome.seconds < 30
