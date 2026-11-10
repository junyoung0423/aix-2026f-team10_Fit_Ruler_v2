"""패턴 JSON + 몸 OBJ + 설정 -> export -> Blender 착장 -> 분석을 한 번에 실행한다.

저장소 루트에서 가상환경 garmentcode의 python으로 모듈로 실행한다:
    C:\\dev\\venvs\\garmentcode\\Scripts\\python.exe -m fitting.adapters.blender.run_drape \\
        --spec tests\\fixtures\\garment\\tee_gc_default\\t-shirt__261103-16-24-09_specification.json \\
        --body C:\\dev\\GarmentCode\\assets\\bodies\\mean_all.obj \\
        --config config\\drape\\drape_v0.json --name tee_base

출력: <out-root>\\<name>\\. <out-root>는 --out-root, 없으면 환경변수 FIT_RULER_RUNS_DIR이고,
둘 다 없으면 실행하지 않고 멈춘다. 출력 폴더가 이미 있으면 덮어쓰지 않고 멈춘다.
오류 코드(명세서 12장): export 실패 GARMENT_NOT_READY, 착장 검사 실패·시간 초과 DRAPE_INVALID.
같은 입력으로 자동 반복하지 않는다(명세서 11장).
Blender 실행 파일은 --blender, 없으면 환경변수 BLENDER_EXE, 그것도 없으면 Blender 5.2 기본 설치 경로.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from fitting.adapters.blender.drape_config import load_config, sha256_file

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]  # fitting/adapters/blender/ -> 저장소 루트

DEFAULT_GC_ROOT = Path(os.environ.get('FIT_RULER_GC_ROOT') or r'C:\dev\GarmentCode')
DEFAULT_BLENDER = Path(os.environ.get('BLENDER_EXE')
                       or r'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe')
RUNS_DIR_ENV = 'FIT_RULER_RUNS_DIR'


def resolve_out_root(out_root_arg, environ=None):
    """출력 루트: --out-root > 환경변수 FIT_RULER_RUNS_DIR. 둘 다 없으면 None(호출한 쪽이 멈춘다)."""
    if out_root_arg:
        return Path(out_root_arg)
    env_value = (os.environ if environ is None else environ).get(RUNS_DIR_ENV)
    return Path(env_value) if env_value else None


def git_head(path):
    try:
        return subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def run_step(name, cmd, log_path, timeout=None, **kw):
    t0 = time.time()
    env = dict(os.environ, PYTHONIOENCODING='utf-8', **kw.pop('extra_env', {}))
    with open(log_path, 'w', encoding='utf-8', errors='replace') as log:
        log.write('$ ' + ' '.join(f'"{c}"' if ' ' in str(c) else str(c) for c in cmd) + '\n')
        log.flush()
        try:
            p = subprocess.run([str(c) for c in cmd], stdout=log, stderr=subprocess.STDOUT,
                               timeout=timeout, env=env, **kw)
            rc, timed_out = p.returncode, False
        except subprocess.TimeoutExpired:
            rc, timed_out = None, True
    rec = {'step': name, 'returncode': rc, 'timed_out': timed_out, 'timeout_s': timeout,
           'seconds': round(time.time() - t0, 1), 'log': str(log_path)}
    print(f'[{name}] rc={rc} timed_out={timed_out} {rec["seconds"]} s', flush=True)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--spec', required=True, help='GarmentCode *_specification.json')
    ap.add_argument('--body', required=True, help='body OBJ (GarmentCode m, Y up)')
    ap.add_argument('--config', required=True)
    ap.add_argument('--name', required=True)
    ap.add_argument('--out-root', help=f'없으면 환경변수 {RUNS_DIR_ENV}')
    ap.add_argument('--max-frames', type=int, help='짧은 시험용. thresholds.max_frames 이하만 허용')
    ap.add_argument('--gc-root', default=str(DEFAULT_GC_ROOT))
    ap.add_argument('--blender', default=str(DEFAULT_BLENDER))
    args = ap.parse_args()

    spec, body, cfg_path = (Path(p).resolve() for p in (args.spec, args.body, args.config))
    gc_root, blender = Path(args.gc_root).resolve(), Path(args.blender)
    out_root = resolve_out_root(args.out_root)
    if out_root is None:
        sys.exit(f'output root not set: pass --out-root or set {RUNS_DIR_ENV} '
                 f'(--out-root나 {RUNS_DIR_ENV}를 지정하라)')
    out = (out_root / args.name).resolve()
    if out.exists() and any(out.iterdir()):
        sys.exit(f'output exists, not overwriting: {out}')
    out.mkdir(parents=True, exist_ok=True)
    cfg, cfg_rec = load_config(cfg_path)

    summary = {
        'name': args.name,
        'out_dir': str(out),
        'config': cfg_rec,
        'inputs': {'spec': str(spec), 'spec_sha256': sha256_file(spec),
                   'body': str(body), 'body_sha256': sha256_file(body)},
        'versions': {'repo_commit': git_head(HERE), 'garmentcode_commit': git_head(gc_root),
                     'python': sys.version.split()[0], 'blender_exe': str(blender)},
        'max_frames_override': args.max_frames,
        'steps': [],
        'status': None,
        'error_code': None,
    }

    def finish(status, error_code=None, reason=None):
        summary.update(status=status, error_code=error_code, reason=reason)
        (out / 'run_summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                                              encoding='utf-8')
        print(json.dumps({k: summary[k] for k in ('name', 'status', 'error_code', 'reason')}, ensure_ascii=False))
        sys.exit(0 if status == 'SUCCEEDED' else 1)

    # 1) export (GarmentCode는 system.json 상대경로 때문에 cwd·PYTHONPATH를 GarmentCode 루트로.
    #    export_panels는 패키지 모듈이라 저장소 루트도 PYTHONPATH 뒤에 붙인다)
    rec = run_step('export', [sys.executable, '-m', 'fitting.adapters.garmentcode.export_panels',
                              spec, body, out, '--config', cfg_path],
                   out / 'export_log.txt', cwd=str(gc_root),
                   extra_env={'PYTHONPATH': os.pathsep.join([str(gc_root), str(REPO_ROOT)])})
    summary['steps'].append(rec)
    if rec['returncode'] != 0:
        finish('FAILED', 'GARMENT_NOT_READY', 'export failed (see export_log.txt)')
    summary['inputs']['drape_input_sha256'] = sha256_file(out / 'drape_input.json')

    # 2) Blender 착장 (명세서 12장 착장 10분)
    cmd = [blender, '--background', '--python', HERE / 'blender_drape.py', '--',
           out / 'drape_input.json', out, cfg_path]
    if args.max_frames is not None:
        cmd.append(args.max_frames)
    rec = run_step('drape', cmd, out / 'blender_log.txt', timeout=cfg['time_limits_s']['drape'])
    summary['steps'].append(rec)
    if rec['timed_out']:
        finish('FAILED', 'DRAPE_INVALID', f'drape time limit {cfg["time_limits_s"]["drape"]} s exceeded')
    metrics_path = out / 'drape_metrics.json'
    if rec['returncode'] != 0 or not metrics_path.exists():
        finish('FAILED', 'DRAPE_INVALID', 'blender drape failed (see blender_log.txt)')
    m = json.loads(metrics_path.read_text(encoding='utf-8'))
    summary['drape_metrics'] = m

    # 3) Blender 없이 지표 재계산 + 패널·봉제선별 요약
    # analyze_drape는 패키지 모듈이라 cwd를 저장소 루트로 두고 -m으로 실행한다(입출력 경로는 절대경로)
    rec = run_step('analyze', [sys.executable, '-m', 'fitting.adapters.blender.analyze_drape',
                               out, '--config', cfg_path],
                   out / 'analyze_log.txt', cwd=str(REPO_ROOT))
    summary['steps'].append(rec)
    if rec['returncode'] == 0:
        a = json.loads((out / 'analysis.json').read_text(encoding='utf-8'))
        summary['offline_matches_blender'] = a['matches_blender_metrics']

    if m['drape_ok']:
        finish('SUCCEEDED')
    finish('FAILED', 'DRAPE_INVALID', 'failed checks: ' + ', '.join(m['failed_checks']))


if __name__ == '__main__':
    main()
