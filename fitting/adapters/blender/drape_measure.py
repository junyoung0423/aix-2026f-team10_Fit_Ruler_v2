"""착장된 의류 메시에서 패턴 측정선을 다시 잰다 (순수 numpy). 진단용이며 판정 기준이 아니다.

패턴 측정기(pattern_measure.measure)가 낸 기준점(패널 안 2D, cm)을 같은 패널의 삼각형에서
무게중심 좌표로 찾아 착장 좌표(m)로 옮긴다.
  - 가슴: 4개 몸판의 가슴 측정선(겨드랑이 1 cm 아래 옆선 점 -> 중심선 수선의 발)을 따라 잰 천 위 길이의 합
    (줄자를 천에 붙여 두른 길이에 해당. 주름을 건너뛰는 실제 줄자와는 다를 수 있다)
  - 기장: 앞판 HPS -> 밑단 측정선의 천 위 길이, 그리고 HPS와 밑단 점의 높이(Z) 차
  - 어깨: 좌우 어깨점 사이 3D 직선 거리 (펼친 옷의 직선과 정의가 다르다)
"""
import numpy as np

N_SAMPLES = 200


def _bary(p, a, b, c):
    v0, v1, v2 = b - a, c - a, p - a
    d00, d01, d11 = v0 @ v0, v0 @ v1, v1 @ v1
    d20, d21 = v2 @ v0, v2 @ v1
    den = d00 * d11 - d01 * d01
    v = (d11 * d20 - d01 * d21) / den
    w = (d00 * d21 - d01 * d20) / den
    return np.array([1 - v - w, v, w])


def _closest_on_segment(p, a, b):
    ab = b - a
    t = np.clip(np.dot(p - a, ab) / max(np.dot(ab, ab), 1e-18), 0, 1)
    return a + t * ab


def _closest_on_triangle(p, a, b, c):
    cands = [_closest_on_segment(p, a, b), _closest_on_segment(p, b, c), _closest_on_segment(p, c, a)]
    return min(cands, key=lambda q: np.linalg.norm(q - p))


class PanelMap:
    """패널 이름 -> (그 패널의 삼각형, 2D 좌표)."""

    def __init__(self, garment):
        self.uv = np.asarray(garment['uv_cm'], float)
        self.faces = np.asarray(garment['faces'], int)
        self.pov = np.asarray(garment['panel_of_vertex'], int)
        self.names = [p['name'] for p in garment['panels']]

    def locate(self, panel, p2, tol=1e-6, max_snap_cm=1.0):
        """2D 점을 담은 삼각형과 무게중심 좌표. 곡선 경계를 현으로 근사한 메시 밖에 놓인 점은
        가장 가까운 삼각형 위 최근접점으로 옮긴다(max_snap_cm 초과면 오류). 최대 이동 거리를 self.max_snap_cm에 남긴다."""
        pi = self.names.index(panel)
        tris = self.faces[self.pov[self.faces[:, 0]] == pi]
        p2 = np.asarray(p2, float)
        best = None
        for t in tris:
            lam = _bary(p2, *self.uv[t])
            if lam.min() >= -tol:
                return t, lam
            q = _closest_on_triangle(p2, *self.uv[t])
            d = float(np.linalg.norm(q - p2))
            if best is None or d < best[0]:
                best = (d, t, q)
        d, t, q = best
        if d > max_snap_cm:
            raise ValueError(f'point {p2.tolist()} is {d:.3f} cm outside panel {panel}')
        self.max_snap_cm = max(getattr(self, 'max_snap_cm', 0.0), d)
        lam = np.clip(_bary(q, *self.uv[t]), 0, None)
        return t, lam / lam.sum()

    def to3d(self, panel, p2, coords):
        t, lam = self.locate(panel, p2)
        return lam @ np.asarray(coords, float)[t]

    def polyline(self, panel, a2, b2, coords, n=N_SAMPLES):
        a2, b2 = np.asarray(a2, float), np.asarray(b2, float)
        pts = [self.to3d(panel, a2 + (b2 - a2) * k / (n - 1), coords) for k in range(n)]
        return np.asarray(pts)


def _length(poly):
    return float(np.linalg.norm(np.diff(poly, axis=0), axis=1).sum())


def measure_draped(garment, landmarks, coords):
    """coords: 원래 정점 순서의 좌표(m). 반환: cm 단위 측정값과 세부."""
    pm = PanelMap(garment)
    out, detail = {}, {}
    chest = 0.0
    for side in ('left', 'right'):
        for k in ('f', 'b'):
            name = f'{side}_{k}torso'
            a, b = landmarks[f'{name}_chest_line']
            L = _length(pm.polyline(name, a, b, coords)) * 100
            detail[f'{name}_chest_half_cm'] = round(L, 3)
            chest += L
    out['chest_girth_surface_cm'] = chest
    lens, drops = [], []
    for side in ('left', 'right'):
        name = f'{side}_ftorso'
        hps, hem = landmarks[f'{name}_length_line']
        poly = pm.polyline(name, hps, hem, coords)
        lens.append(_length(poly) * 100)
        drops.append(float(poly[0, 2] - poly[-1, 2]) * 100)
        detail[f'{side}_length_surface_cm'] = round(lens[-1], 3)
        detail[f'{side}_length_z_drop_cm'] = round(drops[-1], 3)
    out['body_length_surface_cm'] = float(np.mean(lens))
    out['body_length_z_drop_cm'] = float(np.mean(drops))
    spl = pm.to3d('left_ftorso', landmarks['left_ftorso_shoulder_point'], coords)
    spr = pm.to3d('right_ftorso', landmarks['right_ftorso_shoulder_point'], coords)
    out['shoulder_width_straight_3d_cm'] = float(np.linalg.norm(spl - spr)) * 100
    detail['max_snap_to_mesh_cm'] = round(getattr(pm, 'max_snap_cm', 0.0), 4)
    return out, detail
