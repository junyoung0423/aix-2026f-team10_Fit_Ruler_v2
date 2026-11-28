import { describe, expect, it } from 'vitest';
import { outerStarts, photoFrame, photoGrid, scatterOffsets, seeded, seeds } from './particleShapes';

describe('particleShapes', () => {
  it('같은 씨앗이면 같은 난수가 0~1 사이로 나온다', () => {
    const a = seeded(7);
    const b = seeded(7);
    const xs = Array.from({ length: 50 }, () => a());
    expect(xs).toEqual(Array.from({ length: 50 }, () => b()));
    expect(xs.every((x) => x >= 0 && x < 1)).toBe(true);
    expect(seeds(10, seeded(1))).toHaveLength(30);
  });

  it('사방 출발 점은 정한 거리만큼 떨어져 있고, 대체로 도착 점 쪽 방향에서 출발한다', () => {
    const rand = seeded(11);
    const center: [number, number, number] = [0, 0.9, 0];
    const n = 2000;
    const targets = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      targets[i * 3] = (rand() - 0.5) * 0.6;
      targets[i * 3 + 1] = rand() * 1.75;
      targets[i * 3 + 2] = (rand() - 0.5) * 0.3;
    }
    const starts = outerStarts(targets, center, 2, 3, 0.55, seeded(12));
    let aligned = 0;
    const dirs = new Set<string>();
    for (let i = 0; i < n; i++) {
      const sx = starts[i * 3] - center[0];
      const sy = starts[i * 3 + 1] - center[1];
      const sz = starts[i * 3 + 2] - center[2];
      const r = Math.hypot(sx, sy, sz);
      expect(r).toBeGreaterThanOrEqual(2 - 1e-6);
      expect(r).toBeLessThanOrEqual(3 + 1e-6);
      const tx = targets[i * 3] - center[0];
      const ty = targets[i * 3 + 1] - center[1];
      const tz = targets[i * 3 + 2] - center[2];
      if (sx * tx + sy * ty + sz * tz > 0) aligned++;
      dirs.add(`${Math.sign(Math.round(sx))},${Math.sign(Math.round(sy))},${Math.sign(Math.round(sz))}`);
    }
    // 대부분 제 쪽에서 날아오고(몸을 가로지르지 않게), 위·아래·옆·앞뒤 여러 방향에서 출발한다
    expect(aligned / n).toBeGreaterThan(0.85);
    expect(dirs.size).toBeGreaterThan(10);
  });

  it('사진 점 격자는 카드를 빈틈없이 덮고 uv가 위치와 맞는다', () => {
    const grid = photoGrid(30, 40, 0.6, 0.8);
    expect(grid.positions).toHaveLength(30 * 40 * 3);
    expect(grid.uvs).toHaveLength(30 * 40 * 2);
    for (let k = 0; k < 30 * 40; k++) {
      const x = grid.positions[k * 3];
      const y = grid.positions[k * 3 + 1];
      expect(Math.abs(x)).toBeLessThan(0.3);
      expect(Math.abs(y)).toBeLessThan(0.4);
      expect(grid.uvs[k * 2]).toBeCloseTo(x / 0.6 + 0.5, 5);
      expect(grid.uvs[k * 2 + 1]).toBeCloseTo(y / 0.8 + 0.5, 5);
    }
  });

  it('사진 틀 점은 카드 가장자리에 있고, 꺾쇠(밝은 점)는 네 귀퉁이 가까이, 테두리(어두운 점)는 둘레 전체에 있다', () => {
    const w = 0.6;
    const h = 0.8;
    const n = 3000;
    const { positions, seeds: sd } = photoFrame(w, h, n, seeded(21));
    expect(positions).toHaveLength(n * 3);
    expect(sd).toHaveLength(n * 3);
    const arm = Math.min(w, h) * 0.22;
    let bracket = 0;
    let midEdge = 0;
    for (let i = 0; i < n; i++) {
      const ax = Math.abs(positions[i * 3]);
      const ay = Math.abs(positions[i * 3 + 1]);
      // 가장자리에서 떨어진 거리(흔들림 2.5 mm 안)
      expect(Math.min(Math.abs(ax - w / 2), Math.abs(ay - h / 2))).toBeLessThan(0.002);
      expect(ax).toBeLessThan(w / 2 + 0.002);
      expect(ay).toBeLessThan(h / 2 + 0.002);
      if (sd[i * 3 + 2] >= 0.55) {
        bracket++;
        expect(w / 2 - ax).toBeLessThan(arm + 0.002);
        expect(h / 2 - ay).toBeLessThan(arm + 0.002);
      } else {
        expect(sd[i * 3 + 2]).toBeLessThanOrEqual(0.3);
        if (ax < w * 0.1 || ay < h * 0.1) midEdge++;
      }
    }
    expect(bracket).toBe(Math.round(n * 0.7));
    expect(midEdge).toBeGreaterThan(50);
  });

  it('흩어질 자리는 카드 가운데에서 바깥쪽으로 멀리, 앞뒤 이동은 줄어든다', () => {
    const grid = photoGrid(20, 30, 0.6, 0.8);
    const off = scatterOffsets(grid.positions, 1, 2, 0.5, 0.3, seeded(8));
    let outward = 0;
    let maxZ = 0;
    const n = grid.positions.length / 3;
    for (let i = 0; i < n; i++) {
      const px = grid.positions[i * 3];
      const py = grid.positions[i * 3 + 1];
      const sx = px + off[i * 3];
      const sy = py + off[i * 3 + 1];
      const sz = off[i * 3 + 2];
      if (sx * px + sy * py > 0) outward++;
      // 가로·세로 거리는 rMin 근처 이상(z를 줄여도 대부분 화면 밖)
      expect(Math.hypot(sx, sy, sz / 0.3)).toBeGreaterThanOrEqual(1 - 1e-6);
      maxZ = Math.max(maxZ, Math.abs(sz));
    }
    expect(outward / n).toBeGreaterThan(0.85);
    expect(maxZ).toBeLessThanOrEqual(2 * 0.3 + 1e-6);
  });
});

