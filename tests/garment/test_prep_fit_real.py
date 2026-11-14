"""실제 GarmentCode로 견본 복사본 맞춤 1회 (#6).

마커 garmentcode, CI에서 빠짐. 로컬: pytest -m garmentcode

목표를 견본 `tee_gc_slim_01` 자신의 치수(가슴·소매, 기장 없음)로 주면 맞춤이 바로 끝나야 하고
(잔여 오차 1.0 cm 이하), 견본집 파일은 그대로여야 한다. 맞춤·생성에 1~3분 걸린다.
"""

import hashlib
import json
import os
from pathlib import Path

import pytest

from fitting.catalog import optimize as opt
from tools import prepare_garments as pg

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG = REPO_ROOT / "data" / "samples" / "tee_gc_v1"
GC_ROOT = Path(os.environ.get("FIT_RULER_GC_ROOT") or r"C:\dev\GarmentCode")
GC_PYTHON = Path(
    os.environ.get("FIT_GC_PYTHON")
    or os.environ.get("FIT_RULER_GC_PYTHON")
    or r"C:\dev\venvs\garmentcode\Scripts\python.exe"
)

pytestmark = [
    pytest.mark.garmentcode,
    pytest.mark.skipif(
        not (GC_PYTHON.exists() and (GC_ROOT / "pygarment" / "__init__.py").is_file()),
        reason="GarmentCode not installed (FIT_RULER_GC_ROOT, FIT_GC_PYTHON)",
    ),
]


def _hashes(folder: Path) -> dict[str, str]:
    return {
        p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


def test_fit_copy_of_sample_keeps_original(tmp_path):
    sid = "tee_gc_slim_01"
    before = _hashes(CATALOG)
    sample = json.loads((CATALOG / sid / "sample.json").read_text(encoding="utf-8"))
    targets = {
        "chest_girth_cm": sample["measurements_cm"]["chest_girth_cm"],
        "sleeve_length_cm": sample["measurements_cm"]["sleeve_length_cm"],
        "body_length_cm": None,  # AI Hub처럼 기장 없음: 견본 기장 유지
    }
    job = {
        "targets": targets,
        "sample_design_yaml": str(CATALOG / sid / "design.yaml"),
        "fit_config": str(REPO_ROOT / "config" / "pattern" / "pattern_fit_v0.json"),
        "gc_root": str(GC_ROOT),
    }
    job_path = tmp_path / "job.json"
    job_path.write_text(json.dumps(job), encoding="utf-8")
    out = tmp_path / "out"
    rc, err = pg.subprocess_runner(GC_PYTHON, GC_ROOT, timeout_s=1800)(job_path, out)
    assert rc == 0, err
    fit = json.loads((out / "fit_result.json").read_text(encoding="utf-8"))
    status, reason, detail = opt.judge(
        targets, fit["measured_cm"], 1.0, self_intersecting=fit["self_intersecting"]
    )
    assert status == opt.READY, detail
    assert "shirt.length" not in fit["fitted_params"]
    assert fit["measured_cm"]["body_length_cm"] == pytest.approx(
        sample["measurements_cm"]["body_length_cm"], abs=0.01
    )
    assert _hashes(CATALOG) == before
