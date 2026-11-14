"""GarmentCode 티셔츠 패턴을 목표 실측에 맞춘다 (명세서 6장 '패턴 최적화', #4 #6).

이전 저장소 `pipelines/garment/pattern_fit.py`(C2·C3)의 generate·fit을 옮겼다(C3).
알고리즘·기본값·판정은 그대로다. 바뀐 것:
  - 함수로 부를 수 있게 했다: `fit_design(targets, base_design, body_yaml, fit_cfg)`,
    `generate_pattern(design_yaml, body_yaml, out_dir)`. C3 견본집 생성과 C4 견본 최적화가 다시 쓴다.
  - 목표는 설정의 targets_json(이전 저장소 경로) 대신 명령행 `--targets`(GarmentVariant 모양 JSON)로 받는다.
  - sensitivity는 옮기지 않았다.

- 패턴 생성용 몸은 설정의 body_yaml 하나로 고정한다(mean_all). 사용자 몸으로 다시 만들지 않는다(R06).
- 디자인 파라미터(몸 치수 대비 비율)만 yaml range 안에서 바꾼다. 조정 파라미터는 설정의 fit_params
  (shirt.width -> 가슴, shirt.length -> 기장, sleeve.length -> 소매). 어깨를 바꾸는 파라미터는 없다.
- 목표의 null 항목은 맞추지 않는다(0으로 채우지 않음).
- 설정 파일의 상대경로 body_yaml·design_yaml은 GarmentCode 루트 기준이다(--gc-root). cwd 기준으로 풀지 않는다.
- GarmentCode 루트: --gc-root, 없으면 환경변수 FIT_RULER_GC_ROOT, 없으면 C:\\dev\\GarmentCode (run_drape와 같다).
  GarmentCode의 system.json이 상대경로라 실행 중 cwd를 GarmentCode 루트로 옮기고 sys.path에 넣는다.
  명령행의 다른 경로는 옮기기 전에 절대경로로 바꾼다.
- 판정: 항목마다 |패턴 재측정 - 목표| <= tolerance_cm (명세서 6장 초기 목표 1.0 cm). 기준을 바꾸지 않는다.
- 패턴 자기 교차는 실패.
- 이 파일은 GarmentCode용 Python 3.9에서 돈다. 3.10 문법(X | Y, match, zip(strict=))을 쓰지 않는다.

실행 (저장소 루트에서, GarmentCode용 가상환경의 python):
    python -m fitting.adapters.garmentcode.pattern_fit fit --targets <garment.json> <out_dir> \\
        [--config config/pattern/pattern_fit_v0.json] [--gc-root <GarmentCode>]
    python -m fitting.adapters.garmentcode.pattern_fit generate <design.yaml> <body.yaml> <out_dir> \\
        [--gc-root <GarmentCode>]

고정 패턴은 fit 프로세스 안에서 만든 패턴을 쓰지 않고, 고정한 디자인 yaml로 새 프로세스에서 generate 한다.
한 프로세스에서 패턴을 여러 번 만들면 봉제선 목록 순서가 바뀔 수 있다(기하·봉제선 집합은 같음, C3에서 확인).
"""
import argparse
import copy
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import yaml
from scipy.optimize import least_squares

from fitting.adapters.garmentcode import pattern_measure as pm

FIELDS = ('chest_girth_cm', 'shoulder_width_cm', 'sleeve_length_cm', 'body_length_cm')
REPO_ROOT = Path(__file__).resolve().parents[3]  # fitting/adapters/garmentcode/ -> 저장소 루트
DEFAULT_GC_ROOT = Path(os.environ.get('FIT_RULER_GC_ROOT') or r'C:\dev\GarmentCode')
DEFAULT_FIT_CONFIG = REPO_ROOT / 'config' / 'pattern' / 'pattern_fit_v0.json'
GC_PATH_KEYS = ('body_yaml', 'design_yaml')  # GarmentCode 루트 기준


def resolve_path(p, root):
    """설정 파일의 경로를 푼다. 절대경로는 그대로, 상대경로는 root 기준."""
    q = Path(p)
    return q if q.is_absolute() else (Path(root) / q).resolve()


def resolve_config(cfg, gc_root=DEFAULT_GC_ROOT):
    """경로 키를 절대경로로 바꾼 사본. 빈 값·누락은 조용히 채우지 않고 오류로 낸다."""
    out = dict(cfg)
    for k in GC_PATH_KEYS:
        if not cfg.get(k):
            raise ValueError(f'config {k} is missing or empty')
        out[k] = str(resolve_path(cfg[k], gc_root))
    return out


def sha256_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def load_fit_config(path=DEFAULT_FIT_CONFIG, gc_root=DEFAULT_GC_ROOT):
    """설정을 읽어 (경로를 푼 설정, 기록용 {path, name, version, sha256})를 돌려준다."""
    path = Path(path).resolve()
    cfg = resolve_config(json.loads(path.read_text(encoding='utf-8')), gc_root=gc_root)
    try:
        shown = path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        shown = str(path)
    rec = {'path': shown, 'name': cfg['name'], 'version': cfg['version'], 'sha256': sha256_file(path)}
    return cfg, rec


def enter_gc_root(gc_root):
    """GarmentCode 루트로 cwd를 옮기고 import 경로에 넣는다(system.json이 상대경로). 없으면 오류.
    저장소 루트도 import 경로에 넣어 cwd를 옮긴 뒤에도 fitting 패키지를 찾게 한다."""
    gc_root = Path(gc_root).resolve()
    if not (gc_root / 'assets' / 'garment_programs').is_dir():
        sys.exit(f'GarmentCode root not found (--gc-root / FIT_RULER_GC_ROOT): {gc_root}')
    os.chdir(gc_root)
    for p in (str(REPO_ROOT), str(gc_root)):
        if p not in sys.path:
            sys.path.insert(0, p)
    return gc_root


def get_param(design, dotted):
    d = design
    for k in dotted.split('.'):
        d = d[k]
    return d


def set_value(design, dotted, value):
    p = get_param(design, dotted)
    p['v'] = int(round(value)) if p.get('type') == 'int' else float(value)


def load_design(design_yaml):
    """GarmentCode 디자인 yaml의 'design' 부분."""
    return yaml.safe_load(Path(design_yaml).read_text(encoding='utf-8'))['design']


def design_yaml_text(design):
    """디자인 dict를 yaml 파일 내용으로 (이전 fit의 design_fitted.yaml과 같은 형식)."""
    return yaml.safe_dump({'design': design}, allow_unicode=True, sort_keys=False)


class Generator:
    """GarmentCode MetaGarment로 패턴을 메모리에서 만든다. 같은 입력이면 같은 패턴(C3에서 확인).
    enter_gc_root를 먼저 불러야 한다."""

    def __init__(self, body_yaml, base_design):
        from assets.bodies.body_params import BodyParameters
        from assets.garment_programs.meta_garment import MetaGarment
        self.MetaGarment = MetaGarment
        self.body = BodyParameters(str(body_yaml))
        self.base = copy.deepcopy(base_design)
        self.calls = 0

    def design_with(self, values):
        d = copy.deepcopy(self.base)
        for k, v in values.items():
            set_value(d, k, v)
        return d

    def generate(self, values):
        self.calls += 1
        design = self.design_with(values)
        garment = self.MetaGarment('t-shirt', self.body, design)
        pattern = garment.assembly()
        selfint = bool(garment.is_self_intersecting()) or bool(pattern.is_self_intersecting())
        return pattern, design, selfint

    def measure(self, values):
        pattern, design, selfint = self.generate(values)
        m, detail = pm.measure(pattern.pattern)
        return m, detail, selfint, pattern, design


def normalize_targets(targets):
    """목표 dict -> FIELDS 키 모두 있는 dict. 없는 키는 None(맞추지 않음)."""
    return {f: targets.get(f) for f in FIELDS}


def fit_design(targets, base_design, body_yaml, fit_cfg, generator=None):
    """목표 실측에 맞게 디자인 파라미터를 조정한다(이전 cmd_fit의 계산 부분).

    targets: {측정 항목: cm 또는 None}. base_design: 디자인 dict(yaml의 'design').
    fit_cfg: tolerance_cm, fit_params({dotted: 측정 항목}), diff_step, max_nfev.
    generator: 같은 몸·기본 디자인의 Generator를 다시 쓸 때. 없으면 새로 만든다.
    반환: status(FITTED / FIT_FAILED), design(맞춘 디자인 dict), measured_cm(맞춘 패턴 실측),
    measurements(항목별 목표·패턴·오차·통과), fitted_params, params_at_bound, self_intersecting 등.
    """
    t0 = time.time()
    targets = normalize_targets(targets)
    tol = fit_cfg['tolerance_cm']
    gen = generator if generator is not None else Generator(body_yaml, base_design)
    params = fit_cfg['fit_params']  # {dotted: 맞추는 측정 항목}
    names = list(params)
    bounds = [get_param(gen.base, n)['range'] for n in names]
    x0 = np.array([get_param(gen.base, n)['v'] for n in names], float)
    fitted_fields = [f for f in params.values() if targets[f] is not None]

    base_m, _, base_si, _, _ = gen.measure({})
    history = []

    # zip(strict=)는 Python 3.10부터라 쓰지 않는다(이 파일은 GarmentCode용 3.9에서 돈다). 길이는 같다.
    def resid(x):
        m, _, si, _, _ = gen.measure(dict(zip(names, x)))  # noqa: B905
        r = np.array([m[f] - targets[f] for f in fitted_fields])
        history.append({'x': x.round(6).tolist(), 'resid': r.round(4).tolist(), 'self_intersecting': si})
        return r

    calls0 = gen.calls
    sol = least_squares(resid, x0, bounds=(np.array([b[0] for b in bounds]), np.array([b[1] for b in bounds])),
                        diff_step=fit_cfg['diff_step'], xtol=1e-10, ftol=1e-10, gtol=1e-10,
                        max_nfev=fit_cfg['max_nfev'])
    values = dict(zip(names, [float(v) for v in sol.x]))  # noqa: B905
    m, detail, selfint, pattern, design = gen.measure(values)

    report = {}
    for f in FIELDS:
        tgt = targets[f]
        if tgt is None:
            report[f] = {'target': None, 'pattern': round(m[f], 3), 'error': None, 'ok': None,
                         'reason': 'target null'}
            continue
        err = m[f] - tgt
        ok = abs(err) <= tol
        report[f] = {'target': tgt, 'pattern': round(m[f], 3), 'default_pattern': round(base_m[f], 3),
                     'error': round(err, 3), 'ok': ok,
                     'controlled_by': [n for n, ff in params.items() if ff == f] or None}
    at_bound = {n: (abs(values[n] - b[0]) < 1e-6 or abs(values[n] - b[1]) < 1e-6) for n, b in zip(names, bounds)}  # noqa: B905
    all_ok = all(r['ok'] for r in report.values() if r['ok'] is not None) and not selfint
    return {
        'status': 'FITTED' if all_ok else 'FIT_FAILED',
        'tolerance_cm': tol,
        'measurements': report,
        'measured_cm': m,
        'default_measured_cm': base_m,
        'fitted_params': values,
        'params_at_bound': at_bound,
        'self_intersecting': selfint,
        'default_self_intersecting': base_si,
        'design': design,
        'detail': detail,
        'optimizer': {'method': 'scipy.optimize.least_squares (trf, bounds = yaml range)',
                      'nfev': int(sol.nfev), 'status': int(sol.status), 'message': sol.message,
                      'generator_calls': gen.calls - calls0 + 1, 'seconds': round(time.time() - t0, 1)},
        'history': history,
    }


def load_targets(sample_path):
    s = json.loads(Path(sample_path).read_text(encoding='utf-8'))
    meas = s['measurements']
    return {f: meas.get(f) for f in FIELDS}, s


def target_meta(sample):
    """목표 sample의 출처·시연용 여부와 허리(읽기만, 맞춤 대상 아님)를 기록용으로 뽑는다. 없는 값은 None."""
    meta = sample.get('measurement_meta', {})
    return {
        'garment_id': sample.get('id'),
        'schema_version': sample.get('schema_version'),
        'status': sample.get('status'),
        'asset_version': sample.get('asset_version'),
        'source_ref': sample.get('source_ref'),
        'source': meta.get('source'),
        'is_measured': meta.get('is_measured'),
        'measurement_protocol_version': meta.get('measurement_protocol_version'),
        'chest_from_flat_width': meta.get('chest_from_flat_width'),
        'waist_girth_cm': sample.get('measurements', {}).get('waist_girth_cm'),
        'waist_position_matched': meta.get('waist_position_matched'),
        'waist_fitted': False,
    }


def cmd_fit(cfg, cfg_rec, targets_path, out):
    targets, sample = load_targets(targets_path)
    res = fit_design(targets, load_design(cfg['design_yaml']), cfg['body_yaml'], cfg)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'design_fitted.yaml').write_text(design_yaml_text(res['design']), encoding='utf-8')
    result = {k: res[k] for k in ('status', 'tolerance_cm', 'measurements', 'fitted_params', 'params_at_bound',
                                  'self_intersecting', 'default_self_intersecting', 'detail', 'optimizer')}
    result['fit_config'] = cfg_rec
    result['inputs'] = {'body_yaml': cfg['body_yaml'], 'body_yaml_sha256': sha256_file(cfg['body_yaml']),
                        'design_yaml': cfg['design_yaml'], 'design_yaml_sha256': sha256_file(cfg['design_yaml']),
                        'targets_json': str(targets_path), 'targets_json_sha256': sha256_file(targets_path),
                        'targets_source': sample.get('measurement_meta', {}).get('source'),
                        'targets_is_measured': sample.get('measurement_meta', {}).get('is_measured'),
                        'targets_meta': target_meta(sample)}
    result['history'] = res['history']
    (out / 'fit_result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('status', 'measurements', 'fitted_params', 'params_at_bound',
                                              'self_intersecting')}, indent=2, ensure_ascii=False))
    print('optimizer', result['optimizer'])
    return result


def generate_pattern(design_yaml, body_yaml, out):
    """고정 디자인 yaml + 몸 yaml -> 패턴 파일(새 프로세스에서 한 번). 측정값도 남긴다.
    out 안에 pattern_specification.json과 generate_result.json을 쓴다."""
    out = Path(out)
    gen = Generator(body_yaml, load_design(design_yaml))
    pattern, _, selfint = gen.generate({})
    m, detail = pm.measure(pattern.pattern)
    pattern.name = 'pattern'
    pattern.serialize(str(out), tag='', to_subfolder=False, with_3d=False, with_text=False,
                      view_ids=False, with_printable=False)
    res = {'measurements_cm': m, 'detail': detail, 'self_intersecting': selfint,
           'design_yaml': str(design_yaml), 'design_yaml_sha256': sha256_file(design_yaml),
           'body_yaml': str(body_yaml), 'body_yaml_sha256': sha256_file(body_yaml),
           'pattern_sha256': {p.name: sha256_file(p) for p in sorted(out.glob('pattern_*'))}}
    (out / 'generate_result.json').write_text(json.dumps(res, indent=2), encoding='utf-8')
    return res


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == 'generate':
        ap = argparse.ArgumentParser()
        ap.add_argument('cmd')
        ap.add_argument('design_yaml')
        ap.add_argument('body_yaml')
        ap.add_argument('out_dir')
        ap.add_argument('--gc-root', default=str(DEFAULT_GC_ROOT))
        a = ap.parse_args(argv)
        out = Path(a.out_dir).resolve()
        design_yaml, body_yaml = Path(a.design_yaml).resolve(), Path(a.body_yaml).resolve()
        out.mkdir(parents=True, exist_ok=True)
        enter_gc_root(a.gc_root)
        print(json.dumps(generate_pattern(design_yaml, body_yaml, out), indent=2))
        return
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['fit'])
    ap.add_argument('out_dir')
    ap.add_argument('--targets', required=True, help='목표 실측 JSON (GarmentVariant 모양, measurements 사용)')
    ap.add_argument('--config', default=str(DEFAULT_FIT_CONFIG))
    ap.add_argument('--gc-root', default=str(DEFAULT_GC_ROOT))
    a = ap.parse_args(argv)
    gc_root = Path(a.gc_root).resolve()
    cfg, cfg_rec = load_fit_config(a.config, gc_root=gc_root)
    targets_path = Path(a.targets).resolve()
    out = Path(a.out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    enter_gc_root(gc_root)
    cmd_fit(cfg, cfg_rec, targets_path, out)


if __name__ == '__main__':
    main()
