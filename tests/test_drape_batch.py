"""C6 일괄 착장 도구 (tools/drape_batch.py). 가짜 draper·renderer로 Blender 없이 확인한다."""

import json
from pathlib import Path

import numpy as np
import pytest

from fitting.domain import SCHEMA_VERSION, ErrorCode, GarmentVariant, Stage
from fitting.ports import PortError, PortFailure, PortResult
from tools import drape_batch as db

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "data" / "garments" / "tee_regular_m_navy_v1.json"


def garment(gid: str) -> GarmentVariant:
    data = json.loads(DEMO.read_text(encoding="utf-8"))
    data["id"] = gid
    return GarmentVariant.model_validate(data)


def write_prep(prepared: Path, gid: str, status: str = "READY", folder: str | None = None):
    d = prepared / (folder or gid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "prep.json").write_text(json.dumps({"garment_id": gid, "status": status}), "utf-8")


class FakeDraper:
    """의류 ID로 결과를 정한다: 'bad' → DRAPE_INVALID(신장), 'boom' → 예외, 나머지 통과."""

    def __init__(self, work: Path, artifacts: Path):
        self.work, self.artifacts, self.calls = work, artifacts, []

    def drape(self, request, garment):
        gid = garment.id
        self.calls.append(gid)
        if "boom" in gid:
            raise RuntimeError("Blender가 죽었다")
        job = self.work / request.job_id
        run = job / "drape"
        run.mkdir(parents=True, exist_ok=True)
        ok = "bad" not in gid
        metrics = {
            "drape_ok": ok,
            "stretch_max": 1.4 if ok else 1.62,
            "seam_gap_preweld_max_mm": 6.5,
            "seam_closed_ok": True,
            "penetration_area_ratio": 0.0,
            "failed_checks": [] if ok else ["stretch"],
            "frames": 110,
            "sim_seconds": 80.0,
            "weld": {"enabled": True, "rest_length_ratio_if_weld_time_shape": {"max": 1.39}},
        }
        (run / "drape_metrics.json").write_text(json.dumps(metrics), "utf-8")
        steps = [{"step": "analyze", "returncode": 0 if ok else 1, "seconds": 30.0}]
        (run / "run_summary.json").write_text(json.dumps({"steps": steps}), "utf-8")
        if ok:
            (run / "analysis.json").write_text("{}", "utf-8")
        else:
            (run / "analyze_log.txt").write_text("Traceback\nValueError: outside panel\n", "utf-8")
        (run / "drape.blend").write_bytes(b"blend")
        record = {
            "prep": {"sample_id": "tee_gc_regular_06"},
            "fabric": {"stiffness_multiplier": 0.8, "areal_density_kg_m2": 0.18, "warnings": []},
            "drape_config": {"name": "drape_v1", "version": "1.0.0", "sha256": "x"},
            "run": {"seconds": 150.0, "timed_out": False},
        }
        (job / "drape_record.json").write_text(json.dumps(record), "utf-8")
        if not ok:
            raise PortFailure(
                PortError(
                    code=ErrorCode.DRAPE_INVALID,
                    stage=Stage.DRAPE,
                    retryable=False,
                    safe_message="착장 결과가 검사 기준을 통과하지 못했습니다.",
                )
            )
        self.artifacts.mkdir(parents=True, exist_ok=True)
        (self.artifacts / f"{gid}_drape_result.blend").write_bytes(b"blend")
        ids = [f"{gid}_drape_result", f"{gid}_drape_preview"]
        return PortResult(
            schema_version=SCHEMA_VERSION, output_artifact_ids=ids, metrics={}, tool_versions={}
        )


class FakeRenderer:
    def __init__(self, artifacts: Path):
        self.artifacts, self.calls = artifacts, []

    def render(self, request, garment):
        self.calls.append(garment.id)
        assert (self.artifacts / f"{request.input_artifact_ids[0]}.blend").is_file()
        mask = np.zeros((40, 30), dtype=np.uint8)
        mask[10:30, 5:25] = 255
        mask_id = f"{request.job_id}_render_mask"
        import cv2

        cv2.imwrite(str(self.artifacts / f"{mask_id}.png"), mask)
        return PortResult(
            schema_version=SCHEMA_VERSION,
            output_artifact_ids=[f"{request.job_id}_render_clip"],
            metrics={
                "garment_mask_ratio": float((mask == 255).mean()),
                "head_vertices_in_frame": 0,
                "render_mask_artifact_id": mask_id,
                "render_seconds": 40.0,
            },
            tool_versions={},
        )


@pytest.fixture
def setup(tmp_path):
    out = tmp_path / "out"
    draper = FakeDraper(out / "work", out / "artifacts")
    return out, draper


def test_ready_ids_filters_status_and_folder_mismatch(tmp_path):
    p = tmp_path / "prepared"
    write_prep(p, "a")
    write_prep(p, "b", status="FAILED")
    write_prep(p, "c", folder="c_other")  # 폴더 이름과 기록의 ID가 다르다
    (p / "d").mkdir()  # 기록 없음
    ready, other = db.ready_ids(p)
    assert ready == ["a"]
    assert other == ["b", "c_other", "d"]


def test_batch_records_pass_fail_and_continues_after_exception(setup):
    out, draper = setup
    gs = [garment("g1_boom"), garment("g2_bad"), garment("g3")]
    done = db.run_batch(gs, draper, None, out, log=lambda _: None)
    assert [r["status"] for r in done] == [db.ERROR, db.DRAPE_INVALID, db.PASS]
    err = done[0]
    assert "RuntimeError" in err["safe_message"] and "traceback" in err
    bad = done[1]
    assert bad["failed_checks"] == ["stretch"]
    assert bad["stretch_max"] == 1.62
    assert bad["analyze_ok"] is False and "outside panel" in bad["analyze_error"]
    ok = done[2]
    assert ok["stretch_at_weld"] == 1.39
    assert ok["sample_id"] == "tee_gc_regular_06"
    assert ok["fabric_stiffness_multiplier"] == 0.8
    assert ok["drape_seconds"] == 150.0
    assert ok["analyze_ok"] is True
    for g in gs:
        assert (out / "garments" / g.id / db.RESULT_FILE).is_file()


def test_skip_done_and_force(setup):
    out, draper = setup
    gs = [garment("g1"), garment("g2")]
    db.run_batch(gs, draper, None, out, log=lambda _: None)
    assert draper.calls == ["g1", "g2"]
    logs = []
    assert db.run_batch(gs, draper, None, out, log=logs.append) == []
    assert draper.calls == ["g1", "g2"]
    assert all("건너뜀" in x for x in logs)
    db.run_batch(gs[:1], draper, None, out, force=True, log=lambda _: None)
    assert draper.calls == ["g1", "g2", "g1"]


def test_resume_after_interrupt(setup):
    out, draper = setup
    gs = [garment("g1"), garment("g2"), garment("g3")]
    db.run_batch(gs[:1], draper, None, out, log=lambda _: None)  # 첫 벌 뒤 멈췄다고 본다
    db.run_batch(gs, draper, None, out, log=lambda _: None)
    assert draper.calls == ["g1", "g2", "g3"]


def test_render_only_passing_and_mask_shape(setup):
    pytest.importorskip("cv2")
    out, draper = setup
    renderer = FakeRenderer(out / "artifacts")
    gs = [garment("g1_bad"), garment("g2")]
    done = db.run_batch(gs, draper, renderer, out, log=lambda _: None)
    assert renderer.calls == ["g2"]
    assert "render" not in done[0]
    r = done[1]["render"]
    assert r["status"] == "SUCCEEDED"
    assert r["head_vertices_in_frame"] == 0
    assert r["mask_shape"]["area_px"] == 400
    assert r["mask_shape"]["bbox_px"] == [5, 10, 24, 29]


def test_blend_removed_except_demo_and_keep(setup):
    out, draper = setup
    demo = "tee_regular_m_navy_v1"
    db.run_batch([garment("g1"), garment(demo)], draper, None, out, log=lambda _: None)
    art = out / "artifacts"
    assert not (art / "g1_drape_result.blend").exists()
    assert not (out / "work" / "g1" / "drape" / "drape.blend").exists()
    assert (art / f"{demo}_drape_result.blend").is_file()
    db.run_batch([garment("g3")], draper, None, out, keep_blend=True, log=lambda _: None)
    assert (art / "g3_drape_result.blend").is_file()


def test_summary_keys_and_markdown(setup):
    out, draper = setup
    db.run_batch(
        [garment("g1_boom"), garment("g2_bad"), garment("g3")], draper, None, out, log=print
    )
    batch = {"name": "t", "config_name": "drape_v1@1.0.0", "fabric_map": "off", "body_obj": "b"}
    s = db.write_summary(out, batch)
    assert set(s) == {"batch", "counts", "failed_check_counts", "seconds", "render", "garments"}
    assert s["counts"] == {
        "total": 3,
        "pass": 1,
        "drape_invalid": 1,
        "garment_not_ready": 0,
        "error": 1,
        "analyze_failed": 1,
        "rendered": 0,
        "render_failed": 0,
    }
    assert s["failed_check_counts"] == {"stretch": 1}
    assert s["seconds"]["drape"]["n"] == 2
    assert set(s["seconds"]["wall"]) == {"n", "median", "min", "max"}
    md = (out / db.SUMMARY_MD).read_text(encoding="utf-8")
    assert md.count("\n| g") == 3
    assert json.loads((out / db.SUMMARY_JSON).read_text(encoding="utf-8"))["counts"]["pass"] == 1


def test_batch_config_mismatch_is_refused(tmp_path):
    batch = {"config_sha256": "a", "fabric_map_sha256": None, "body_sha256": "b", "render": True}
    assert db.check_batch_config(tmp_path, batch) is None
    assert db.check_batch_config(tmp_path, dict(batch)) is None
    problem = db.check_batch_config(tmp_path, {**batch, "config_sha256": "z"})
    assert problem and "config_sha256" in problem


def test_main_requires_body(tmp_path, monkeypatch):
    monkeypatch.delenv("FIT_DRAPE_BODY_OBJ", raising=False)
    assert db.main(["--out", str(tmp_path / "o"), "--prepared", str(tmp_path)]) == db.EXIT_SETUP
