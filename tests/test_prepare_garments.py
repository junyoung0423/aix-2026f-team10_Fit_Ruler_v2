"""의류 준비 명령 (#6). GarmentCode 하위 프로세스는 가짜 실행기로 바꾼다."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from fitting.catalog import optimize as opt
from tools import prepare_garments as pg

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG = REPO_ROOT / "data" / "samples" / "tee_gc_v1"
DEMO = REPO_ROOT / "data" / "garments" / "tee_regular_m_navy_v1.json"


def _tree_hashes(folder: Path) -> dict[str, str]:
    return {
        p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


class FakeRunner:
    """prep_fit 대신 목표에 `offset`을 더한 치수를 '다시 잰 값'으로 쓴다."""

    def __init__(self, offset=0.0, rc=0, self_intersecting=False):
        self.offset, self.rc, self.self_intersecting = offset, rc, self_intersecting
        self.jobs = []

    def __call__(self, job_path: Path, out: Path):
        job = json.loads(job_path.read_text(encoding="utf-8"))
        self.jobs.append(job)
        if self.rc != 0:
            return self.rc, "boom"
        sample_design = Path(job["sample_design_yaml"])
        sample = json.loads((sample_design.parent / "sample.json").read_text(encoding="utf-8"))
        measured = dict(sample["measurements_cm"])
        for f, t in job["targets"].items():
            if t is not None:
                measured[f] = t + self.offset
        (out / "design.yaml").write_text("design: {}\n", encoding="utf-8")
        shutil.copyfile(sample_design.parent / "specification.json", out / "specification.json")
        fit = {
            "fit_status": "FITTED",
            "measured_cm": {k: v for k, v in measured.items() if v is not None},
            "pattern_sha256": "f" * 64,
            "design_params": {"shirt.width": 1.0},
            "fitted_params": sorted(f for f, t in job["targets"].items() if t is not None),
            "params_at_bound": {"shirt.width": False},
            "self_intersecting": self.self_intersecting,
            "optimizer": {"nfev": 3},
            "body_yaml": "mean_all.yaml",
            "body_yaml_sha256": "0" * 64,
        }
        (out / "fit_result.json").write_text(json.dumps(fit), encoding="utf-8")
        return 0, ""


@pytest.fixture
def garments(tmp_path):
    d = tmp_path / "garments"
    d.mkdir()
    shutil.copyfile(DEMO, d / DEMO.name)
    return d


def _run(garments, out, runner, *extra):
    return pg.main(
        ["--garments", str(garments), "--out", str(out), *extra], runner=runner, environ={}
    )


def test_demo_garment_is_prepared(garments, tmp_path):
    out = tmp_path / "prepared"
    assert _run(garments, out, FakeRunner()) == 0
    prep = json.loads((out / "tee_regular_m_navy_v1" / "prep.json").read_text(encoding="utf-8"))
    assert prep["status"] == opt.READY and prep["failure_reason"] is None
    assert prep["sample_id"] == "tee_gc_regular_06"  # docs/decisions.md B3 기대 결과
    assert prep["search_score_cm"] == pytest.approx(3.86, abs=0.005)
    assert prep["targets_cm"] == {
        "chest_girth_cm": 108.0,
        "sleeve_length_cm": 22.0,
        "body_length_cm": 70.0,
    }
    assert prep["residuals_cm"] == {
        "chest_girth_cm": 0.0,
        "sleeve_length_cm": 0.0,
        "body_length_cm": 0.0,
    }
    assert prep["shoulder"]["judged"] is False
    assert prep["shoulder"]["diff_cm"] == pytest.approx(46.0 - 34.457)
    assert prep["garment_sha256"] == hashlib.sha256(DEMO.read_bytes()).hexdigest()
    assert prep["specification"] == "specification.json" and prep["pattern_sha256"]
    for key in ("pattern_fit", "sample_search", "catalog"):
        assert set(prep["configs"][key]) == {"path", "name", "version", "sha256"}
    assert (out / "tee_regular_m_navy_v1" / "specification.json").is_file()
    assert (out / "tee_regular_m_navy_v1" / "design.yaml").is_file()


def test_sample_catalog_is_not_changed(garments, tmp_path):
    before = _tree_hashes(CATALOG)
    assert _run(garments, tmp_path / "prepared", FakeRunner()) == 0
    assert _tree_hashes(CATALOG) == before


def test_garment_file_is_not_changed(garments, tmp_path):
    before = _tree_hashes(garments)
    _run(garments, tmp_path / "prepared", FakeRunner(offset=5.0))
    assert _tree_hashes(garments) == before


def test_over_tolerance_is_prep_failure(garments, tmp_path):
    out = tmp_path / "prepared"
    assert _run(garments, out, FakeRunner(offset=1.01)) == 0
    prep = json.loads((out / "tee_regular_m_navy_v1" / "prep.json").read_text(encoding="utf-8"))
    assert prep["status"] == opt.FAILED
    assert prep["failure_reason"] == opt.FailureReason.OUT_OF_TOLERANCE


def test_at_tolerance_is_ready(garments, tmp_path):
    out = tmp_path / "prepared"
    assert _run(garments, out, FakeRunner(offset=0.999)) == 0
    prep = json.loads((out / "tee_regular_m_navy_v1" / "prep.json").read_text(encoding="utf-8"))
    assert prep["status"] == opt.READY


def test_subprocess_failure_is_recorded(garments, tmp_path):
    out = tmp_path / "prepared"
    assert _run(garments, out, FakeRunner(rc=1)) == 0
    prep = json.loads((out / "tee_regular_m_navy_v1" / "prep.json").read_text(encoding="utf-8"))
    assert (prep["status"], prep["failure_reason"]) == (opt.FAILED, opt.FailureReason.FIT_ERROR)


def test_missing_length_keeps_sample_length(garments, tmp_path):
    data = json.loads(DEMO.read_text(encoding="utf-8"))
    data["id"] = "tee_no_length"
    data["measurements"]["body_length_cm"] = None
    (garments / "tee_no_length.json").write_text(json.dumps(data), encoding="utf-8")
    runner = FakeRunner()
    out = tmp_path / "prepared"
    assert _run(garments, out, runner, "--only", "tee_no_length") == 0
    assert runner.jobs[0]["targets"]["body_length_cm"] is None
    prep = json.loads((out / "tee_no_length" / "prep.json").read_text(encoding="utf-8"))
    assert prep["status"] == opt.READY
    assert prep["residuals_cm"]["body_length_cm"] is None
    assert any("기장 목표 없음" in n for n in prep["notes"])


def test_no_shoulder_means_search_failure(garments, tmp_path):
    data = json.loads(DEMO.read_text(encoding="utf-8"))
    data.update(id="tee_no_shoulder", status="DRAFT")
    data["measurements"]["shoulder_width_cm"] = None
    (garments / "tee_no_shoulder.json").write_text(json.dumps(data), encoding="utf-8")
    runner = FakeRunner()
    out = tmp_path / "prepared"
    assert _run(garments, out, runner, "--only", "tee_no_shoulder") == 0
    assert runner.jobs == []
    prep = json.loads((out / "tee_no_shoulder" / "prep.json").read_text(encoding="utf-8"))
    assert prep["failure_reason"] == opt.FailureReason.SAMPLE_SEARCH_FAILED


def test_same_inputs_are_skipped_unless_forced(garments, tmp_path):
    out = tmp_path / "prepared"
    first = FakeRunner()
    assert _run(garments, out, first) == 0
    again = FakeRunner()
    assert _run(garments, out, again) == 0
    assert again.jobs == []
    forced = FakeRunner()
    assert _run(garments, out, forced, "--force") == 0
    assert len(forced.jobs) == 1


def test_changed_garment_is_prepared_again(garments, tmp_path):
    out = tmp_path / "prepared"
    assert _run(garments, out, FakeRunner()) == 0
    data = json.loads((garments / DEMO.name).read_text(encoding="utf-8"))
    data["measurements"]["sleeve_length_cm"] = 23.0
    (garments / DEMO.name).write_text(json.dumps(data), encoding="utf-8")
    runner = FakeRunner()
    assert _run(garments, out, runner) == 0
    assert len(runner.jobs) == 1


def test_jobs_option_prepares_all(garments, tmp_path):
    for i in range(3):
        data = json.loads(DEMO.read_text(encoding="utf-8"))
        data["id"] = f"tee_copy_{i}"
        (garments / f"tee_copy_{i}.json").write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "prepared"
    runner = FakeRunner()
    assert _run(garments, out, runner, "--jobs", "3") == 0
    assert len(runner.jobs) == 4
    assert sorted(p.name for p in out.iterdir()) == [
        "tee_copy_0",
        "tee_copy_1",
        "tee_copy_2",
        "tee_regular_m_navy_v1",
    ]


def test_bad_garments_dir_is_a_config_error(tmp_path):
    assert _run(tmp_path / "nope", tmp_path / "out", FakeRunner()) == pg.EXIT_CONFIG


def test_prepared_dir_comes_from_environment(garments, tmp_path):
    out = tmp_path / "from_env"
    code = pg.main(
        ["--garments", str(garments)], runner=FakeRunner(), environ={"FIT_PREPARED_DIR": str(out)}
    )
    assert code == 0
    assert (out / "tee_regular_m_navy_v1" / "prep.json").is_file()
