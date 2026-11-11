"""GarmentCode 패턴 export 시험.

GarmentCode가 필요하므로 cwd/PYTHONPATH를 맞춰 하위 프로세스로 돌린다.
마커 garmentcode: CI에서는 빠진다. 로컬 실행: pytest -m garmentcode
하위 프로세스는 GarmentCode용 가상환경(Python 3.9)의 python으로 돌린다.
이 저장소는 Python 3.10 이상이라 pytest와 같은 인터프리터로는 pygarment를 쓸 수 없다.
경로는 환경변수 FIT_RULER_GC_PYTHON으로 바꾼다.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

np = pytest.importorskip("numpy", reason="garment 선택 의존성 필요: pip install -e .[garment]")

from fitting.adapters.blender.drape_config import load_config  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
GC_ROOT = Path(os.environ.get('FIT_RULER_GC_ROOT') or r'C:\dev\GarmentCode')
GC_PYTHON = Path(os.environ.get('FIT_RULER_GC_PYTHON')
                 or r'C:\dev\venvs\garmentcode\Scripts\python.exe')
SPEC = (REPO_ROOT / 'tests' / 'fixtures' / 'garment' / 'tee_gc_default'
        / 't-shirt__261103-16-24-09_specification.json')
BODY = GC_ROOT / 'assets' / 'bodies' / 'mean_all.obj'
CONFIG = REPO_ROOT / 'config' / 'drape' / 'drape_v0.json'

# 실행은 GarmentCode 루트를 PYTHONPATH에 넣어 import한다(편집 설치 .pth만으로는 import되지 않음).
# export_panels는 패키지 모듈이라 저장소 루트도 PYTHONPATH 뒤에 붙인다.
_HAS_PYGARMENT = (GC_ROOT / 'pygarment' / '__init__.py').is_file()
pytestmark = [
    pytest.mark.garmentcode,
    pytest.mark.skipif(not (BODY.exists() and _HAS_PYGARMENT and GC_PYTHON.exists()),
                       reason='GarmentCode not installed '
                              '(FIT_RULER_GC_ROOT, FIT_RULER_GC_PYTHON, pygarment)'),
]
GC_ENV = dict(os.environ, PYTHONPATH=os.pathsep.join([str(GC_ROOT), str(REPO_ROOT)]),
              PYTHONIOENCODING='utf-8')

PROBE = r'''
import json, sys
from fitting.adapters.garmentcode.export_panels import export_garment
box, gv, gf, pov, panels, pairs = export_garment(sys.argv[1], float(sys.argv[2]))
counts = []
for sid, st in enumerate(box.stitches):
    r1, r2 = box._swap_stitch_ranges(st)
    counts.append([len(r1), len(r2), sum(1 for p in pairs if p[2] == sid),
                   int(st.panel_1 == st.panel_2 and any(a == b for a, b in zip(r1, r2)))])
print(json.dumps({'n_verts': len(gv), 'n_faces': len(gf), 'n_panels': len(panels),
                  'n_stitches': len(box.stitches), 'n_pairs': len(pairs), 'counts': counts}))
'''


@pytest.fixture(scope='module')
def probe():
    res = load_config(CONFIG)[0]['export']['mesh_resolution_cm']
    p = subprocess.run([str(GC_PYTHON), '-c', PROBE, str(SPEC), str(res)], cwd=str(GC_ROOT),
                       env=GC_ENV, capture_output=True, text=True, encoding='utf-8', check=True)
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_baseline_counts(probe):
    # runs/tee_test/sew80 기준 값
    assert (probe['n_verts'], probe['n_faces'], probe['n_panels'], probe['n_stitches'], probe['n_pairs']) == \
        (2001, 3516, 8, 16, 183)


def test_stitch_pairs_equal_both_sides(probe):
    for n1, n2, n_pairs, self_skip in probe['counts']:
        assert n1 == n2
        if not self_skip:
            assert n_pairs == n1


def test_export_file(tmp_path):
    subprocess.run([str(GC_PYTHON), '-m', 'fitting.adapters.garmentcode.export_panels', str(SPEC),
                    str(BODY), str(tmp_path), '--config', str(CONFIG)],
                   cwd=str(GC_ROOT), env=GC_ENV, check=True, capture_output=True)
    d = json.loads((tmp_path / 'drape_input.json').read_text(encoding='utf-8'))
    assert d['units'] == 'm' and d['axes'] == {'up': '+Z', 'front': '-Y'}
    assert d['config']['name'] == 'drape_v0' and len(d['config']['sha256']) == 64
    assert d['source']['garment_spec_sha256'] and d['source']['body_sha256']
    assert d['body']['is_watertight'] is True
    bv = np.asarray(d['body']['vertices'])
    # 몸: Z 위(키 ~1.7 m), 바닥 근처 Z~0
    assert bv[:, 2].max() - bv[:, 2].min() > 1.5
    assert abs(bv[:, 2].min()) < 0.1
