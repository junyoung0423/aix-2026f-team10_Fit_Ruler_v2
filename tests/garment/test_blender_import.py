"""blender_drape.py가 Blender 안의 Python에서 패키지 import를 할 수 있는지 확인한다.

blender_drape.py는 `blender --background --python`으로 실행되어
저장소 루트를 sys.path에 직접 넣는다.
여기서는 main()은 돌리지 않고(run_name을 바꿔서) 모듈 머리의 import만 실행한다.

마커 blender: CI에서는 빠진다. 로컬 실행: pytest -m blender
Blender 경로는 환경변수 BLENDER_EXE로 바꾼다.
"""
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.blender

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "fitting" / "adapters" / "blender" / "blender_drape.py"
BLENDER = Path(
    os.environ.get("BLENDER_EXE")
    or r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
)

PROBE = (
    "import runpy, sys; "
    "g = runpy.run_path(sys.argv[sys.argv.index('--') + 1], run_name='blender_drape_probe'); "
    "print('PROBE_OK', g['metrics'].__name__, g['weld'].__name__, g['load_config'].__module__)"
)


def test_blender_drape_imports_inside_blender():
    p = subprocess.run(
        [str(BLENDER), "--background", "--factory-startup",
         "--python-expr", PROBE, "--", str(SCRIPT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    line = next((x for x in p.stdout.splitlines() if x.startswith("PROBE_OK")), None)
    assert line is not None, p.stdout + p.stderr
    assert line.split()[1:] == [
        "fitting.adapters.blender.metrics",
        "fitting.adapters.blender.weld",
        "fitting.adapters.blender.drape_config",
    ]
