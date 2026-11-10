"""버전 있는 착장 설정 파일을 읽는다 (Blender 안팎 공용, 표준 라이브러리만 사용)."""
import hashlib
import json
from pathlib import Path

REQUIRED_BLOCKS = ('name', 'version', 'thresholds', 'time_limits_s', 'export', 'schedule',
                   'cloth', 'cloth_collision', 'body_collision')
REQUIRED_THRESHOLDS = ('max_frames', 'stable_window_frames', 'stable_median_m_per_frame',
                       'penetration_depth_m', 'penetration_area_ratio_max', 'stretch_ratio_max')


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def load_config(path):
    """설정을 읽고 (설정 dict, 기록용 dict)를 돌려준다. 빠진 블록·값은 채우지 않고 오류로 낸다."""
    path = Path(path).resolve()
    cfg = json.loads(path.read_text(encoding='utf-8'))
    missing = [k for k in REQUIRED_BLOCKS if k not in cfg]
    missing += ['thresholds.' + k for k in REQUIRED_THRESHOLDS if k not in cfg.get('thresholds', {})]
    if missing:
        raise ValueError(f'config {path.name}: missing {missing}')
    for block in REQUIRED_BLOCKS[2:]:
        for k, v in cfg[block].items():
            if v is None:
                raise ValueError(f'config {path.name}: {block}.{k} is null')
    record = {'file': path.name, 'path': str(path), 'name': cfg['name'],
              'version': cfg['version'], 'sha256': sha256_file(path)}
    return cfg, record
