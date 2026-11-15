"""커밋된 견본집의 design.yaml로 패턴을 다시 만들면 기록한 패턴 해시·치수와 같다 (#4).

GarmentCode가 필요하다(마커 garmentcode, CI에서 빠짐). 로컬 실행: pytest -m garmentcode
하위 프로세스는 GarmentCode용 가상환경(Python 3.9)의 python으로 돈다(경로: FIT_RULER_GC_PYTHON).
견본 하나에 30초~1분 걸려 첫 견본과 마지막 견본만 다시 만든다.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("numpy", reason="garment 선택 의존성 필요: pip install -e .[garment]")
pytest.importorskip("svgpathtools", reason="garment 선택 의존성 필요: pip install -e .[garment]")

from fitting.adapters.garmentcode import pattern_measure as pm  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG = REPO_ROOT / "data" / "samples" / "tee_gc_v1"
GC_ROOT = Path(os.environ.get("FIT_RULER_GC_ROOT") or r"C:\dev\GarmentCode")
GC_PYTHON = Path(
    os.environ.get("FIT_RULER_GC_PYTHON") or r"C:\dev\venvs\garmentcode\Scripts\python.exe"
)
BODY_YAML = GC_ROOT / "assets" / "bodies" / "mean_all.yaml"
_HAS_PYGARMENT = (GC_ROOT / "pygarment" / "__init__.py").is_file()

pytestmark = [
    pytest.mark.garmentcode,
    pytest.mark.skipif(
        not (BODY_YAML.exists() and _HAS_PYGARMENT and GC_PYTHON.exists()),
        reason="GarmentCode not installed (FIT_RULER_GC_ROOT, FIT_RULER_GC_PYTHON, pygarment)",
    ),
]


def _sample_ids() -> list[str]:
    path = CATALOG / "catalog.json"
    if not path.is_file():
        return []
    ids = [s["sample_id"] for s in json.loads(path.read_text("utf-8"))["samples"]]
    return [ids[0], ids[-1]]


@pytest.mark.parametrize("sample_id", _sample_ids())
def test_regenerated_pattern_matches_record(tmp_path, sample_id):
    sample = json.loads((CATALOG / sample_id / "sample.json").read_text(encoding="utf-8"))
    env = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join([str(REPO_ROOT), str(GC_ROOT)]),
        PYTHONIOENCODING="utf-8",
    )
    cmd = [
        str(GC_PYTHON), "-m", "fitting.adapters.garmentcode.pattern_fit", "generate",
        str(CATALOG / sample_id / "design.yaml"), str(BODY_YAML), str(tmp_path),
        "--gc-root", str(GC_ROOT),
    ]  # fmt: skip
    p = subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=600)
    assert p.returncode == 0, p.stderr[-2000:]
    spec = json.loads((tmp_path / "pattern_specification.json").read_text(encoding="utf-8"))
    assert pm.canonical_sha256(spec) == sample["pattern_sha256"]
    m, _ = pm.measure(spec)
    for name, recorded in sample["measurements_cm"].items():
        if recorded is not None:
            assert abs(m[name] - recorded) <= 0.001, name
