"""GarmentCode 좌표를 명세서 6장 규약으로 바꾼다 (순수 numpy, Blender 불필요).

명세서 규약: m 단위, Z축 위, 인체 정면 -Y, 바닥 Z=0, 몸 중심 X=0.
GarmentCode: 옷 cm, 몸 m, 둘 다 Y축 위, 정면 +Z.
X축 기준 +90도 회전으로 (x, y, z) -> (x, -z, y).

바닥 Z=0, 몸 중심 X=0 정렬은 아직 하지 않는다(GarmentCode 기준 몸은 이미 그 근처).
정렬 이동이 필요해지면 translation을 manifest에 같이 남긴다.
"""
import numpy as np

# 원본 축 설명. manifest(drape_input.json)에 그대로 남긴다.
SOURCE_AXES = {'up': '+Y', 'front': '+Z', 'garment_units': 'cm', 'body_units': 'm'}
TARGET_AXES = {'up': '+Z', 'front': '-Y', 'units': 'm'}

GC_TO_SPEC = np.array([
    [1.0, 0.0, 0.0],
    [0.0, 0.0, -1.0],
    [0.0, 1.0, 0.0],
])
GARMENT_SCALE = 0.01  # cm -> m
BODY_SCALE = 1.0      # 이미 m


def to_spec(v, scale):
    """(N, 3) 점을 scale 배 한 뒤 GC_TO_SPEC으로 회전한다."""
    return (np.asarray(v, dtype=float) * scale) @ GC_TO_SPEC.T


def transform_record():
    """manifest에 남길 변환 기록."""
    return {
        'source_axes': SOURCE_AXES,
        'target_axes': TARGET_AXES,
        'rotation': GC_TO_SPEC.tolist(),
        'garment_scale': GARMENT_SCALE,
        'body_scale': BODY_SCALE,
        'translation_m': [0.0, 0.0, 0.0],
    }
