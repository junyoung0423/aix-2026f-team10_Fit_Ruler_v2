import { describe, expect, it } from 'vitest';
import { outerStarts, photoGrid, seeded, seeds } from './particleShapes';

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
    const grid = photoGrid(30, 40, 0.6, 0.8, [0.2, 0.2, 0.2], seeded(4));
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
});
