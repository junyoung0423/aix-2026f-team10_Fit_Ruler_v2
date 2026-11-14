"""견본 복사본을 의류 치수에 맞춘다 (#6, C4). GarmentCode용 Python 3.9 하위 프로세스.

`tools/prepare_garments.py`(저장소 .venv)가 의류마다 이 모듈을 새 프로세스로 부른다.

    <venv>\\Scripts\\python.exe -m fitting.adapters.garmentcode.prep_fit <job.json> <out_dir>

job.json:
    {"targets": {"chest_girth_cm": 108.0, "sleeve_length_cm": 22.0,
                 "body_length_cm": 70.0 또는 null},
     "sample_design_yaml": "<견본 폴더>/design.yaml",   # 읽기만 한다(원본 보존)
     "fit_config": "<repo>/config/pattern/pattern_fit_v0.json",
     "gc_root": "C:/dev/GarmentCode"}

1. 견본 design.yaml을 읽어 메모리의 사본에서 시작한다(파일은 쓰지 않는다).
2. `pattern_fit.fit_design`으로 목표가 있는 치수의 파라미터만 맞춘다. 목표가 없는 치수
   (AI Hub 기장)의 파라미터는 맞춤에서 빼서 견본 값이 그대로 남는다. 어깨는 목표로 넘기지
   않는다(맞추지 않음).
   생성용 몸은 맞춤 설정의 body_yaml(mean_all)이다.
3. 맞춘 디자인을 `{out}/design.yaml`로 쓰고, 새 프로세스에서 패턴을 만든다(`pattern_fit generate`,
   C3 견본집과 같은 이유: 한 프로세스에서 여러 번 만들면 봉제선 순서가 바뀔 수 있다).
4. 만든 패턴을 `{out}/specification.json`으로 두고 다시 잰다. 결과는 `{out}/fit_result.json`.

판정(허용 오차)은 하지 않는다. 부른 쪽이 `fitting.catalog.optimize.judge`로 한다.
종료 코드: 0 결과를 씀, 1 실패(이유는 stderr 마지막 줄).
이 파일은 Python 3.9에서 돈다. 3.10 문법을 쓰지 않는다.
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from fitting.adapters.garmentcode import pattern_fit as pf
from fitting.adapters.garmentcode import pattern_measure as pm

REPO_ROOT = Path(__file__).resolve().parents[3]


def write_text(path, text):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


def fit_config_for(fit_cfg, targets):
    """목표가 없는 치수의 파라미터를 뺀 맞춤 설정 사본."""
    cfg = copy.deepcopy(fit_cfg)
    cfg['fit_params'] = {p: f for p, f in fit_cfg['fit_params'].items()
                         if targets.get(f) is not None}
    return cfg


def generate_in_new_process(design_yaml, body_yaml, work, gc_root):
    env = dict(os.environ, PYTHONIOENCODING='utf-8',
               PYTHONPATH=os.pathsep.join([str(REPO_ROOT), str(gc_root)]))
    cmd = [sys.executable, '-m', 'fitting.adapters.garmentcode.pattern_fit', 'generate',
           str(design_yaml), str(body_yaml), str(work), '--gc-root', str(gc_root)]
    p = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    if p.returncode != 0:
        tail = p.stderr.strip()[-2000:]
        raise RuntimeError(f'generate failed (rc={p.returncode}): {tail}')
    spec = Path(work) / 'pattern_specification.json'
    if not spec.is_file():
        raise RuntimeError('generate wrote no pattern_specification.json')
    return spec


def run(job, out):
    t0 = time.time()
    gc_root = Path(job['gc_root']).resolve()
    fit_cfg, fit_rec = pf.load_fit_config(Path(job['fit_config']).resolve(), gc_root=gc_root)
    sample_design_yaml = Path(job['sample_design_yaml']).resolve()
    targets = {f: job['targets'].get(f) for f in pf.FIELDS}
    targets['shoulder_width_cm'] = None  # 어깨는 맞추지 않는다
    cfg = fit_config_for(fit_cfg, targets)
    body_yaml = Path(cfg['body_yaml'])
    base = pf.load_design(sample_design_yaml)
    pf.enter_gc_root(gc_root)
    if cfg['fit_params']:
        res = pf.fit_design(targets, base, body_yaml, cfg)
    else:  # 맞출 목표가 하나도 없으면 견본 그대로
        res = {'status': 'FITTED', 'design': base, 'fitted_params': {}, 'params_at_bound': {},
               'self_intersecting': None, 'optimizer': None}
    out.mkdir(parents=True, exist_ok=True)
    write_text(out / 'design.yaml', pf.design_yaml_text(res['design']))
    work = Path(tempfile.mkdtemp(prefix='prep_fit_'))
    try:
        spec_tmp = generate_in_new_process(out / 'design.yaml', body_yaml, work, gc_root)
        shutil.copyfile(spec_tmp, out / 'specification.json')
        gen = json.loads((work / 'generate_result.json').read_text(encoding='utf-8'))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    spec = json.loads((out / 'specification.json').read_text(encoding='utf-8'))
    measured, _ = pm.measure(spec)
    design_params = {}
    for name in fit_cfg['fit_params']:
        design_params[name] = round(float(pf.get_param(res['design'], name)['v']), 6)
    result = {
        'fit_status': res['status'],
        'measured_cm': {k: float(v) for k, v in measured.items()},
        'pattern_sha256': pm.canonical_sha256(spec),
        'design_params': design_params,
        'fitted_params': sorted(cfg['fit_params']),
        'params_at_bound': res['params_at_bound'],
        'self_intersecting': bool(gen.get('self_intersecting')) or bool(res['self_intersecting']),
        'optimizer': None if res['optimizer'] is None else {
            k: res['optimizer'][k] for k in ('method', 'nfev', 'status', 'message', 'seconds')},
        'pattern_fit_config': fit_rec,
        'body_yaml': str(body_yaml),
        'body_yaml_sha256': pf.sha256_file(body_yaml),
        'seconds': round(time.time() - t0, 1),
    }
    write_text(out / 'fit_result.json', json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    return result


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print('usage: python -m fitting.adapters.garmentcode.prep_fit <job.json> <out_dir>',
              file=sys.stderr)
        return 2
    job = json.loads(Path(argv[0]).read_text(encoding='utf-8'))
    out = Path(argv[1]).resolve()
    try:
        r = run(job, out)
    except Exception as e:  # 부른 쪽이 준비 실패(FIT_ERROR)로 기록한다
        print(f'prep_fit failed: {type(e).__name__}: {e}', file=sys.stderr)
        return 1
    print(json.dumps({k: r[k] for k in ('fit_status', 'measured_cm', 'pattern_sha256', 'seconds')}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
