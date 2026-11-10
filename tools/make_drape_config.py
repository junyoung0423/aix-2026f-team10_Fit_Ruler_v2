"""기존 설정에서 값 하나(또는 몇 개)만 바꾼 새 설정 파일을 만든다. 원본 설정은 건드리지 않는다.

사용:
    python make_config.py <base.json> <out.json> --name drape_c2_e02 --version 0.2.0-e02 \
        --description "mass 0.3 -> 0.0001" --set cloth.mass=0.0001
값은 JSON으로 해석한다(숫자, true/false, 문자열은 "\"x\"").
"""
import argparse
import json
from pathlib import Path


def set_path(d, dotted, value):
    keys = dotted.split('.')
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('base')
    ap.add_argument('out')
    ap.add_argument('--name', required=True)
    ap.add_argument('--version', required=True)
    ap.add_argument('--description', required=True)
    ap.add_argument('--set', action='append', default=[], metavar='KEY=JSON')
    ap.add_argument('--unset', action='append', default=[], metavar='KEY')
    args = ap.parse_args()

    base_path = Path(args.base)
    cfg = json.loads(base_path.read_text(encoding='utf-8'))
    parent = {'name': cfg['name'], 'version': cfg['version'], 'file': base_path.name}
    cfg.update(name=args.name, version=args.version, status='experiment', description=args.description,
               parent=parent, changes=args.set + [f'unset {k}' for k in args.unset])
    for key in args.unset:
        keys = key.split('.')
        d = cfg
        for k in keys[:-1]:
            d = d[k]
        del d[keys[-1]]
    for item in args.set:
        key, val = item.split('=', 1)
        set_path(cfg, key, json.loads(val))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes((json.dumps(cfg, indent=2, ensure_ascii=False) + '\n').encode('utf-8'))
    print(f'wrote {out}')


if __name__ == '__main__':
    main()
