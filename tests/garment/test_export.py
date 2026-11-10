"""GarmentCode 패턴 export 시험. GarmentCode가 필요하므로 cwd/PYTHONPATH를 맞춰 하위 프로세스로 돌린다."""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from drape_config import load_config

HERE = Path(__file__).resolve().parents[1]
GC_ROOT = Path(os.environ.get('FIT_RULER_GC_ROOT') or r'C:\dev\GarmentCode')
SPEC = HERE / 'fixtures' / 'tee_gc_default' / 't-shirt__261103-16-24-09_specification.json'
BODY = GC_ROOT / 'assets' / 'bodies' / 'mean_all.obj'
CONFIG = HERE / 'configs' / 'drape_v0.json'

# 실행은 GarmentCode 루트를 PYTHONPATH에 넣어 import한다(편집 설치 .pth만으로는 import되지 않음)
_HAS_PYGARMENT = (GC_ROOT / 'pygarment' / '__init__.py').is_file()
pytestmark = pytest.mark.skipif(not (BODY.exists() and _HAS_PYGARMENT),
                                reason='GarmentCode not installed (FIT_RULER_GC_ROOT, pygarment)')

PROBE = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from export_panels import export_garment
box, gv, gf, pov, panels, pairs = export_garment(sys.argv[2], float(sys.argv[3]))
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
    env = dict(os.environ, PYTHONPATH=str(GC_ROOT), PYTHONIOENCODING='utf-8')
    p = subprocess.run([sys.executable, '-c', PROBE, str(HERE), str(SPEC), str(res)], cwd=str(GC_ROOT),
                       env=env, capture_output=True, text=True, encoding='utf-8', check=True)
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
    env = dict(os.environ, PYTHONPATH=str(GC_ROOT), PYTHONIOENCODING='utf-8')
    subprocess.run([sys.executable, str(HERE / 'export_panels.py'), str(SPEC), str(BODY), str(tmp_path),
                    '--config', str(CONFIG)], cwd=str(GC_ROOT), env=env, check=True, capture_output=True)
    d = json.loads((tmp_path / 'drape_input.json').read_text(encoding='utf-8'))
    assert d['units'] == 'm' and d['axes'] == {'up': '+Z', 'front': '-Y'}
    assert d['config']['name'] == 'drape_v0' and len(d['config']['sha256']) == 64
    assert d['source']['garment_spec_sha256'] and d['source']['body_sha256']
    assert d['body']['is_watertight'] is True
    bv = np.asarray(d['body']['vertices'])
    # 몸: Z 위(키 ~1.7 m), 바닥 근처 Z~0
    assert bv[:, 2].max() - bv[:, 2].min() > 1.5
    assert abs(bv[:, 2].min()) < 0.1
