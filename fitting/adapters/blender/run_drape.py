"""패턴 JSON + 몸 OBJ + 설정 -> export -> Blender 착장 -> 분석을 한 번에 실행한다.

가상환경 garmentcode의 python으로 실행한다:
    C:\\dev\\venvs\\garmentcode\\Scripts\\python.exe pipelines\\garment\\run_drape.py \\
        --spec pipelines\\garment\\fixtures\\tee_gc_default\\t-shirt__261103-16-24-09_specification.json \\
        --body C:\\dev\\GarmentCode\\assets\\bodies\\mean_all.obj \\
        --config pipelines\\garment\\configs\\drape_v0.json --name tee_base

출력: <out-root>\\<name>\\ (기본 C:\\dev\\fitruler_c\\runs\\C1). 이미 있으면 덮어쓰지 않고 멈춘다.
오류 코드(명세서 12장): export 실패 GARMENT_NOT_READY, 착장 검사 실패·시간 초과 DRAPE_INVALID.
같은 입력으로 자동 반복하지 않는다(명세서 11장).
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from drape_config import load_config, sha256_file  # noqa: E402

DEFAULT_GC_ROOT = Path(os.environ.get('FIT_RULER_GC_ROOT') or r'C:\dev\GarmentCode')
DEFAULT_BLENDER = Path(r'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe')
DEFAULT_OUT_ROOT = Path(r'C:\dev\fitruler_c\runs\C1')


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
    ap.add_argument('--out-root', default=str(DEFAULT_OUT_ROOT))
    ap.add_argument('--max-frames', type=int, help='짧은 시험용. thresholds.max_frames 이하만 허용')
    ap.add_argument('--gc-root', default=str(DEFAULT_GC_ROOT))
    ap.add_argument('--blender', default=str(DEFAULT_BLENDER))
    args = ap.parse_args()

    spec, body, cfg_path = (Path(p).resolve() for p in (args.spec, args.body, args.config))
    gc_root, blender = Path(args.gc_root).resolve(), Path(args.blender)
    out = (Path(args.out_root) / args.name).resolve()
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

    # 1) export (GarmentCode는 system.json 상대경로 때문에 cwd·PYTHONPATH를 GarmentCode 루트로)
    rec = run_step('export', [sys.executable, HERE / 'export_panels.py', spec, body, out, '--config', cfg_path],
                   out / 'export_log.txt', cwd=str(gc_root), extra_env={'PYTHONPATH': str(gc_root)})
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
    rec = run_step('analyze', [sys.executable, HERE / 'analyze_drape.py', out, '--config', cfg_path],
                   out / 'analyze_log.txt')
    summary['steps'].append(rec)
    if rec['returncode'] == 0:
        a = json.loads((out / 'analysis.json').read_text(encoding='utf-8'))
        summary['offline_matches_blender'] = a['matches_blender_metrics']

    if m['drape_ok']:
        finish('SUCCEEDED')
    finish('FAILED', 'DRAPE_INVALID', 'failed checks: ' + ', '.join(m['failed_checks']))


if __name__ == '__main__':
    main()
