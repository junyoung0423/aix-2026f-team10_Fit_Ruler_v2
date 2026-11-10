"""GarmentCode 패턴(specification JSON)에서 의류 실측 4개를 잰다 (numpy + svgpathtools, GarmentCode 불필요).

정의는 garments/MEASURING.md(명세서 3장 + [C 제안])를 패턴에 옮긴 것이다. 단위 cm.
그림·수식은 review C3.md "측정 정의" 절에 있다.

전제(GarmentCode 반소매 티셔츠 구조): 패널 이름 {left,right}_{ftorso,btorso,sleeve_f,sleeve_b}.
  - 몸판 앞 중심선 = 좌우 앞판을 잇는 봉제선의 앞판 모서리(뒤판도 같은 방식).
  - 같은 쪽 앞판-뒤판 봉제선 2개 중 3D에서 높은 것 = 어깨선, 낮은 것 = 옆선.
  - 같은 쪽 앞소매-뒤소매 봉제선 2개 중 3D에서 높은 것 = 소매 윗선(바깥선), 낮은 것 = 소매 밑선.
구조가 다르면 PatternMeasureError를 낸다(조용히 0이나 기본값으로 채우지 않는다).

패널 안 좌표계(패널마다): 중심선 위 방향 u(밑단 쪽 끝 -> 목 쪽 끝), 중심선에서 옆선 쪽 수직 방향 w.
"""
import numpy as np
import svgpathtools as svgpath
from scipy.spatial.transform import Rotation

SIDES = ('left', 'right')
CHEST_BELOW_ARMPIT_CM = 1.0  # [C 제안] MEASURING.md: 겨드랑이 봉제점 아래 1 cm


class PatternMeasureError(ValueError):
    pass


def _c(p):
    return complex(p[0], p[1])


def rel_to_abs_2d(start, end, rel):
    """GarmentCode 상대 곡률 좌표 -> 절대 좌표 (pygarment.pattern.utils.rel_to_abs_2d와 같은 식)."""
    start, end = np.asarray(start, float), np.asarray(end, float)
    edge = end - start
    perp = np.array([-edge[1], edge[0]])
    return start + rel[0] * edge + rel[1] * perp


def edge_curve(vertices, edge):
    """모서리 하나를 svgpathtools 곡선으로. 해석은 pygarment meshgen(boxmeshgen.Edge.init_curve)과 같다
    (착장 메시와 같은 기하). 곡선 길이는 control point의 좌우 뒤집기와 무관하다."""
    start = np.asarray(vertices[edge['endpoints'][0]], float)
    end = np.asarray(vertices[edge['endpoints'][1]], float)
    cv = edge.get('curvature')
    if cv is None:
        return svgpath.Line(_c(start), _c(end))
    if isinstance(cv, list) or cv['type'] == 'quadratic':
        rel = cv if isinstance(cv, list) else cv['params'][0]
        return svgpath.QuadraticBezier(_c(start), _c(rel_to_abs_2d(start, end, rel)), _c(end))
    if cv['type'] == 'circle':
        radius, large_arc, right = cv['params']
        return svgpath.Arc(_c(start), complex(radius, radius), 0, bool(large_arc), bool(right), _c(end))
    if cv['type'] == 'cubic':
        cps = [_c(rel_to_abs_2d(start, end, p)) for p in cv['params']]
        return svgpath.CubicBezier(_c(start), *cps, _c(end))
    raise PatternMeasureError(f'unknown curvature type {cv["type"]}')


class Panel:
    def __init__(self, name, d):
        self.name = name
        self.v = np.asarray(d['vertices'], float)
        self.edges = d['edges']
        self.curves = [edge_curve(self.v, e) for e in self.edges]
        self.R = Rotation.from_euler('xyz', d.get('rotation', [0, 0, 0]), degrees=True).as_matrix()
        self.t = np.asarray(d.get('translation', [0, 0, 0]), float)

    def to3d(self, p2):
        return self.R @ np.array([p2[0], p2[1], 0.0]) + self.t

    def edge_mid3d(self, ei):
        z = self.curves[ei].point(0.5)
        return self.to3d([z.real, z.imag])

    def endpoints(self, ei):
        a, b = self.edges[ei]['endpoints']
        return self.v[a], self.v[b]


def _stitch_edges(spec, p1, p2):
    """패널 p1-p2 사이 봉제선마다 (p1 모서리 번호, p2 모서리 번호)."""
    out = []
    for st in spec['stitches']:
        sides = {s['panel']: s['edge'] for s in st}
        if p1 in sides and p2 in sides and len(st) == 2:
            out.append((sides[p1], sides[p2]))
    return out


def _frame(panel, center_ei, toward):
    """패널 좌표계: 원점 = 중심선 밑단 쪽 끝, u = 중심선 위 방향, w = 중심선에 수직이며 toward 점 쪽."""
    a, b = panel.endpoints(center_ei)
    # 목 쪽 끝 = toward(어깨선 위 점)에 가까운 끝
    if np.linalg.norm(a - toward) < np.linalg.norm(b - toward):
        a, b = b, a
    u = (b - a) / np.linalg.norm(b - a)
    w = np.array([-u[1], u[0]])
    if np.dot(toward - a, w) < 0:
        w = -w
    return a, u, w


def _uw(p, frame):
    o, u, w = frame
    return float(np.dot(p - o, u)), float(np.dot(p - o, w))


def _point_at_arclen_from(curve, from_start, s):
    """곡선 위에서 한쪽 끝(from_start=True면 시작점)부터 호 길이 s인 점."""
    L = curve.length()
    if s > L:
        raise PatternMeasureError(f'arc length {s} > edge length {L}')
    t = curve.ilength(s if from_start else L - s)
    z = curve.point(t)
    return np.array([z.real, z.imag])


def _torso_side(spec, panels, side, kind):
    """한쪽(left/right) 몸판(kind='f' 앞, 'b' 뒤)의 기준점과 좌표계."""
    other = 'right' if side == 'left' else 'left'
    name = f'{side}_{kind}torso'
    mate = f'{side}_{"b" if kind == "f" else "f"}torso'
    for n in (name, mate, f'{other}_{kind}torso'):
        if n not in panels:
            raise PatternMeasureError(f'panel {n} missing')
    P = panels[name]
    center = _stitch_edges(spec, name, f'{other}_{kind}torso')
    fb = _stitch_edges(spec, name, mate)
    if len(center) != 1 or len(fb) != 2:
        raise PatternMeasureError(f'{name}: expected 1 center seam and 2 front-back seams, got {len(center)}, {len(fb)}')
    e_center = center[0][0]
    e_a, e_b = fb[0][0], fb[1][0]
    # 3D에서 높은 쪽(Y 위) = 어깨선
    if P.edge_mid3d(e_a)[1] >= P.edge_mid3d(e_b)[1]:
        e_shoulder, e_sideseam = e_a, e_b
    else:
        e_shoulder, e_sideseam = e_b, e_a
    sh_pts = P.endpoints(e_shoulder)
    frame = _frame(P, e_center, np.mean(sh_pts, axis=0))
    # 어깨선 두 끝: 중심선에서 먼 끝 = 어깨점(SP), 가까운 끝 = HPS
    sp, hps = sorted(sh_pts, key=lambda p: -_uw(p, frame)[1])
    # 옆선 두 끝: 높은 끝(u 큰 쪽) = 겨드랑이점
    s0, s1 = P.endpoints(e_sideseam)
    armpit_is_start = _uw(s0, frame)[0] > _uw(s1, frame)[0]
    armpit = s0 if armpit_is_start else s1
    # 밑단: 봉제되지 않은 모서리 중 중점이 가장 낮은 것
    stitched = {s['edge'] for st in spec['stitches'] for s in st if s['panel'] == name}
    free = [i for i in range(len(P.edges)) if i not in stitched]
    if not free:
        raise PatternMeasureError(f'{name}: no free edge for hem')
    hem = min(free, key=lambda i: _uw(np.array([P.curves[i].point(0.5).real, P.curves[i].point(0.5).imag]), frame)[0])
    return {'panel': P, 'name': name, 'frame': frame, 'e_center': e_center, 'e_shoulder': e_shoulder,
            'e_sideseam': e_sideseam, 'sp': sp, 'hps': hps, 'armpit': armpit,
            'armpit_is_start': armpit_is_start, 'e_hem': hem}


def _vertical_drop_to_curve(panel, curve, p, frame):
    """점 p에서 중심선과 평행하게(-u) 내려 곡선과 만나는 점까지 거리와 그 점."""
    o, u, w = frame
    far = p - u * 1e4
    line = svgpath.Line(_c(p), _c(far))
    hits = line.intersect(curve)
    if not hits:
        raise PatternMeasureError(f'{panel.name}: vertical line from HPS does not hit hem')
    pts = [line.point(t1) for t1, _ in hits]
    z = min(pts, key=lambda q: abs(q - _c(p)))
    return float(abs(z - _c(p))), np.array([z.real, z.imag])


def _foot_on_center(p, frame):
    """점 p에서 중심선에 내린 수선의 발."""
    o, u, w = frame
    return o + u * float(np.dot(p - o, u))


def measure(spec):
    """spec: specification JSON 전체 또는 그 'pattern' 부분. 반환: 실측 dict(cm)와 세부 기록."""
    if 'pattern' in spec:
        spec = spec['pattern']
    panels = {n: Panel(n, d) for n, d in spec['panels'].items()}
    detail = {}
    chest_parts, length_parts, sleeve_parts, sp_uw = [], [], [], {}
    lm = {}  # 착장 후 측정용 기준점(패널 안 2D 좌표, cm)
    for side in SIDES:
        F = _torso_side(spec, panels, side, 'f')
        B = _torso_side(spec, panels, side, 'b')
        for T in (F, B):
            P = T['panel']
            curve = P.curves[T['e_sideseam']]
            p = _point_at_arclen_from(curve, T['armpit_is_start'], CHEST_BELOW_ARMPIT_CM)
            w = _uw(p, T['frame'])[1]
            chest_parts.append(w)
            detail[f'{T["name"]}_chest_half_width'] = round(w, 3)
            lm[f'{T["name"]}_chest_line'] = [p.tolist(), _foot_on_center(p, T['frame']).tolist()]
        # 기장: 앞판 HPS에서 중심선과 평행하게 밑단까지
        Fp = F['panel']
        L, hem_pt = _vertical_drop_to_curve(Fp, Fp.curves[F['e_hem']], F['hps'], F['frame'])
        length_parts.append(L)
        lm[f'{F["name"]}_length_line'] = [F['hps'].tolist(), hem_pt.tolist()]
        lm[f'{F["name"]}_shoulder_point'] = F['sp'].tolist()
        detail[f'{side}_body_length'] = round(L, 3)
        sp_uw[side] = _uw(F['sp'], F['frame'])
        detail[f'{side}_shoulder_point_uw'] = [round(x, 3) for x in sp_uw[side]]
        # 소매: 앞소매-뒤소매 봉제선 중 높은 것 = 윗선. 길이 = 두 모서리 길이 평균
        sf, sb = f'{side}_sleeve_f', f'{side}_sleeve_b'
        if sf not in panels or sb not in panels:
            raise PatternMeasureError(f'sleeve panels for {side} missing')
        ss = _stitch_edges(spec, sf, sb)
        if len(ss) != 2:
            raise PatternMeasureError(f'{sf}-{sb}: expected 2 seams, got {len(ss)}')
        top = max(ss, key=lambda e: panels[sf].edge_mid3d(e[0])[1])
        lf, lb = panels[sf].curves[top[0]].length(), panels[sb].curves[top[1]].length()
        sleeve_parts.append((lf + lb) / 2)
        detail[f'{side}_sleeve_top_seam_f_b'] = [round(lf, 3), round(lb, 3)]
    # 어깨너비: 펼친 앞판에서 좌우 중심선을 겹친 뒤 두 어깨점 사이 직선
    (ul, wl), (ur, wr) = sp_uw['left'], sp_uw['right']
    shoulder = float(np.hypot(wl + wr, ul - ur))
    out = {
        'chest_girth_cm': float(sum(chest_parts)),
        'shoulder_width_cm': shoulder,
        'sleeve_length_cm': float(np.mean(sleeve_parts)),
        'body_length_cm': float(np.mean(length_parts)),
    }
    detail['landmarks'] = lm
    detail['left_right_diff'] = {
        'body_length': round(abs(length_parts[0] - length_parts[1]), 4),
        'sleeve_length': round(abs(sleeve_parts[0] - sleeve_parts[1]), 4),
    }
    return out, detail


def canonical_pattern(spec):
    """순서와 무관한 정규형: 패널은 이름순(dict), 봉제선은 양쪽을 정렬한 뒤 목록 정렬, panel_order는 그대로 두지 않고 정렬.
    GarmentCode는 같은 입력에서도 패널·봉제선 저장 순서가 실행마다 바뀔 수 있어(C3 확인) 파일 sha256과 함께 쓴다."""
    import json as _json
    p = spec['pattern'] if 'pattern' in spec else spec
    st = sorted((_json.dumps(sorted(s, key=lambda x: (x['panel'], x['edge'])), sort_keys=True) for s in p['stitches']))
    return {'panels': p['panels'], 'stitches': st, 'panel_order': sorted(p.get('panel_order', []))}


def canonical_sha256(spec):
    import hashlib
    import json as _json
    return hashlib.sha256(_json.dumps(canonical_pattern(spec), sort_keys=True).encode('utf-8')).hexdigest()
