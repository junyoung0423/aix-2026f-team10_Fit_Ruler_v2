"""봉제 쌍 용접(weld) 계산 (순수 numpy, Blender 불필요).

2단계 착장(C2 후보 4)에서 쓴다:
  1) 중력 없이 봉제 스프링으로 패널을 모은다.
  2) 용접 전 봉제 쌍 최대 거리가 기준 이하이면 쌍을 한 정점으로 합친 메시로 바꿔 이어서 착장한다.
     기준을 넘으면 용접하지 않고 실패로 둔다(나쁜 결과를 감추는 후처리 금지).

휴지 길이: 용접 시점 형상을 휴지 형상으로 쓰면 그때의 늘어남이 옷 치수에 들어간다(R06 위반).
그래서 원래 패턴 변 길이에 맞춘 3D 이완 형상(relax_to_lengths)을 휴지 형상으로 쓰고,
패턴 대비 휴지 길이 편차를 기록한다.
"""
import numpy as np


def weld_map(n_verts, stitch_pairs):
    """union-find로 봉제 쌍을 묶는다. 반환: 원래 정점 -> 용접 정점 번호 (n_verts,), 용접 정점 수."""
    parent = np.arange(n_verts)

    def find(i):
        root = i
        while parent[root] != root:
            root = parent[root]
        while parent[i] != root:
            parent[i], i = root, parent[i]
        return root

    for a, b, *_ in stitch_pairs:
        ra, rb = find(int(a)), find(int(b))
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    roots = np.array([find(i) for i in range(n_verts)])
    uniq, new_idx = np.unique(roots, return_inverse=True)
    return new_idx, len(uniq)


def weld_mesh(co, tris, vmap, n_new):
    """용접 정점 좌표(그룹 평균)와 다시 번호 매긴 삼각형. 정점이 겹쳐 퇴화한 삼각형은 뺀다."""
    co = np.asarray(co, float)
    acc = np.zeros((n_new, 3))
    cnt = np.zeros(n_new)
    np.add.at(acc, vmap, co)
    np.add.at(cnt, vmap, 1)
    new_co = acc / cnt[:, None]
    t = vmap[np.asarray(tris, int)]
    ok = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
    return new_co, t[ok], int((~ok).sum())


def target_lengths(rest, tris, vmap):
    """용접 메시의 변마다 원래 패턴 길이(같은 변으로 합쳐진 원래 변들의 평균)."""
    rest = np.asarray(rest, float)
    tris = np.asarray(tris, int)
    e = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    e = np.unique(np.sort(e, axis=1), axis=0)
    L = np.linalg.norm(rest[e[:, 0]] - rest[e[:, 1]], axis=1)
    we = np.sort(vmap[e], axis=1)
    keep = we[:, 0] != we[:, 1]
    we, L = we[keep], L[keep]
    edges, inv = np.unique(we, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    s = np.zeros(len(edges))
    c = np.zeros(len(edges))
    np.add.at(s, inv, L)
    np.add.at(c, inv, 1)
    return edges, s / c


def relax_to_lengths(x0, edges, L0, iterations, step=0.5):
    """변 길이를 L0에 맞추는 Jacobi 위치 보정(PBD). 3D 형상은 x0에서 출발한다."""
    x = np.asarray(x0, float).copy()
    i, j = edges[:, 0], edges[:, 1]
    deg = np.zeros(len(x))
    np.add.at(deg, i, 1)
    np.add.at(deg, j, 1)
    deg = np.maximum(deg, 1)[:, None]
    for _ in range(iterations):
        d = x[j] - x[i]
        L = np.linalg.norm(d, axis=1)
        corr = (step * (L - L0) / np.maximum(L, 1e-12))[:, None] * d
        dx = np.zeros_like(x)
        np.add.at(dx, i, corr)
        np.add.at(dx, j, -corr)
        x += dx / deg
    return x


def length_ratio_stats(x, edges, L0):
    r = np.linalg.norm(x[edges[:, 0]] - x[edges[:, 1]], axis=1) / np.maximum(L0, 1e-12)
    return {'min': round(float(r.min()), 4), 'max': round(float(r.max()), 4),
            'p01': round(float(np.percentile(r, 1)), 4), 'p99': round(float(np.percentile(r, 99)), 4),
            'mean_abs_dev': round(float(np.abs(r - 1).mean()), 5)}
