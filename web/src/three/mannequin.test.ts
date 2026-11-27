import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { bodyParams, defaultGarment, mannequinFitFor, teeLayout } from './bodyModel';
import { MANNEQUIN, fitMannequin, parseMannequinGlb, type MannequinBase } from './mannequin';
import { buildAll } from './meshBuild';

const file = readFileSync(new URL('./assets/mannequin.glb', import.meta.url));
const copy = new ArrayBuffer(file.byteLength);
new Uint8Array(copy).set(file);
const base: MannequinBase = parseMannequinGlb(copy);

/** 높이 y에서 메시를 자른 단면의 볼록 껍질 둘레(팔 가중치가 armMax보다 큰 점은 뺀다) */
function girthAt(pos: Float32Array, idx: ArrayLike<number>, arm: Float32Array, y0: number, armMax = 0.5): number {
  const pts: [number, number][] = [];
  for (let t = 0; t < idx.length; t += 3) {
    const ids = [idx[t], idx[t + 1], idx[t + 2]];
    for (let k = 0; k < 3; k++) {
      const a = ids[k];
      const b = ids[(k + 1) % 3];
      const ya = pos[a * 3 + 1];
      const yb = pos[b * 3 + 1];
      if (ya >= y0 === yb >= y0 || arm[a] > armMax || arm[b] > armMax) continue;
      const f = (y0 - ya) / (yb - ya);
      pts.push([pos[a * 3] + (pos[b * 3] - pos[a * 3]) * f, pos[a * 3 + 2] + (pos[b * 3 + 2] - pos[a * 3 + 2]) * f]);
    }
  }
  const s = pts.sort((p, q) => p[0] - q[0] || p[1] - q[1]);
  const cross = (o: number[], a: number[], b: number[]) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lo: number[][] = [];
  const up: number[][] = [];
  for (const p of s) {
    while (lo.length >= 2 && cross(lo[lo.length - 2], lo[lo.length - 1], p) <= 0) lo.pop();
    lo.push(p);
  }
  for (let i = s.length - 1; i >= 0; i--) {
    while (up.length >= 2 && cross(up[up.length - 2], up[up.length - 1], s[i]) <= 0) up.pop();
    up.push(s[i]);
  }
  const hull = lo.slice(0, -1).concat(up.slice(0, -1));
  let per = 0;
  for (let i = 0; i < hull.length; i++) {
    const a = hull[i];
    const b = hull[(i + 1) % hull.length];
    per += Math.hypot(a[0] - b[0], a[1] - b[1]);
  }
  return per;
}

describe('Tripo 마네킹 파일', () => {
  it('꼭짓점·삼각형 수와 기준 치수가 코드의 값과 같다', () => {
    expect(base.positions.length / 3).toBe(25001);
    expect(base.indices.length / 3).toBe(49998);
    const meta = base.meta as Record<string, unknown>;
    expect(meta.landmarks).toEqual(MANNEQUIN.landmarks);
    expect(meta.girth).toEqual(MANNEQUIN.girth);
    expect(meta.shoulderHalf).toBe(MANNEQUIN.shoulderHalf);
    expect(meta.armLength).toBe(MANNEQUIN.armLength);
    expect(meta.shoulderJoint).toEqual(MANNEQUIN.shoulderJoint);
    expect(meta.wristJoint).toEqual(MANNEQUIN.wristJoint);
    expect(meta.clearance).toEqual(MANNEQUIN.clearance);
  });

  it('키 1로 맞춰 있고(발바닥 0, 머리 끝 1) 구멍 없이 닫혀 있다', () => {
    let lo = Infinity;
    let hi = -Infinity;
    for (let i = 1; i < base.positions.length; i += 3) {
      lo = Math.min(lo, base.positions[i]);
      hi = Math.max(hi, base.positions[i]);
    }
    expect(lo).toBeCloseTo(0, 3);
    expect(hi).toBeCloseTo(1, 3);
    const edges = new Map<string, number>();
    for (let t = 0; t < base.indices.length; t += 3) {
      for (let k = 0; k < 3; k++) {
        const a = base.indices[t + k];
        const b = base.indices[t + ((k + 1) % 3)];
        const key = a < b ? `${a},${b}` : `${b},${a}`;
        edges.set(key, (edges.get(key) ?? 0) + 1);
      }
    }
    expect([...edges.values()].every((count) => count === 2)).toBe(true);
  });
});

describe('치수에 맞춘 마네킹', () => {
  it('키는 그대로, 허리둘레는 입력값이 된다', () => {
    for (const body of [
      { height_cm: 175, chest_cm: 100, waist_cm: 80, shoulder_cm: 44, arm_length_cm: 60 },
      { height_cm: 160, chest_cm: 84, waist_cm: 66, shoulder_cm: 37, arm_length_cm: 52 },
      { height_cm: 190, chest_cm: 120, waist_cm: 115, shoulder_cm: 50, arm_length_cm: 66 },
    ]) {
      const p = bodyParams(body);
      const { mesh, arm } = fitMannequin(base, mannequinFitFor(p));
      let hi = 0;
      for (let i = 1; i < mesh.positions.length; i += 3) hi = Math.max(hi, mesh.positions[i]);
      expect(hi).toBeCloseTo(p.height, 3);
      expect(Math.abs(girthAt(mesh.positions, mesh.indices, arm, p.y.waist) - body.waist_cm / 100)).toBeLessThan(0.01);
    }
  });

  it('배가 나온 몸에서도 팔이 몸통을 파고들지 않는다', () => {
    const p = bodyParams({ height_cm: 170, chest_cm: 96, waist_cm: 110, shoulder_cm: 42, arm_length_cm: 58 });
    const { mesh, arm } = fitMannequin(base, mannequinFitFor(p));
    for (let y = p.y.crotch + 0.02; y < p.y.armpit - 0.03; y += 0.02) {
      let torso = 0;
      let armIn = Infinity;
      for (let i = 0; i < mesh.positions.length / 3; i++) {
        if (Math.abs(mesh.positions[i * 3 + 1] - y) > 0.006) continue;
        const ax = Math.abs(mesh.positions[i * 3]);
        if (arm[i] > 0.5) armIn = Math.min(armIn, ax);
        else torso = Math.max(torso, ax);
      }
      expect(armIn - torso).toBeGreaterThan(0);
    }
  });

  it('꼭 맞는 티셔츠도 몸통을 덮는다(목둘레 파임 아래)', () => {
    const body = { height_cm: 175, chest_cm: 100, waist_cm: 80, shoulder_cm: 44, arm_length_cm: 60 };
    const p = bodyParams(body);
    const g = { ...defaultGarment(p), chestGirth: 1.02 };
    const { mesh, arm } = fitMannequin(base, mannequinFitFor(p));
    const built = buildAll(base, body, g, true);
    expect(built.tee).not.toBeNull();
    // 티셔츠 꼭짓점을 높이·각도 칸에 넣어 칸마다 몸 가운데 축에서 가장 먼 거리를 재고,
    // 같은 칸의 몸통 점이 그보다 바깥에 있지 않은지 본다(옷 밖으로 나온 몸이 없다)
    const L = teeLayout(p, g);
    let checked = 0;
    let outside = 0;
    const tee = built.tee!;
    const bins = new Map<string, number>();
    const key = (y: number, a: number) => `${Math.round(y / 0.01)}:${Math.round(a / 0.1)}`;
    for (let i = 0; i < tee.positions.length / 3; i++) {
      const x = tee.positions[i * 3];
      const y = tee.positions[i * 3 + 1];
      const z = tee.positions[i * 3 + 2];
      const k = key(y, Math.atan2(z, x));
      bins.set(k, Math.max(bins.get(k) ?? 0, Math.hypot(x, z)));
    }
    for (let i = 0; i < mesh.positions.length / 3; i++) {
      const x = mesh.positions[i * 3];
      const y = mesh.positions[i * 3 + 1];
      const z = mesh.positions[i * 3 + 2];
      if (y < L.hemY + 0.03 || y > p.y.shoulder - 0.03 || arm[i] > 0.5) continue;
      const r = bins.get(key(y, Math.atan2(z, x)));
      if (r === undefined) continue;
      checked++;
      if (Math.hypot(x, z) > r + 0.002) outside++;
    }
    expect(checked).toBeGreaterThan(3000);
    expect(outside).toBe(0);
  });
});
