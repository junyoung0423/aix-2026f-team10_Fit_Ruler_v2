import { describe, expect, it } from 'vitest';
import { bodyParams, bodySdf, buildBodyMesh, buildTeeMesh, defaultGarment, ellipseFromGirth, ellipsePerimeter, teeLayout, teeSdf } from './bodyModel';
import { surfaceNets } from './surfaceNets';

const bounds = (positions: Float32Array) => {
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < positions.length; i += 3) {
    for (let a = 0; a < 3; a++) {
      min[a] = Math.min(min[a], positions[i + a]);
      max[a] = Math.max(max[a], positions[i + a]);
    }
  }
  return { min, max };
};

describe('surfaceNets', () => {
  it('구의 표면을 반지름 근처에 뽑고 법선은 바깥을 향한다', () => {
    const mesh = surfaceNets((x, y, z) => Math.hypot(x, y, z) - 0.5, [-0.7, -0.7, -0.7], [0.7, 0.7, 0.7], 0.05);
    expect(mesh.indices.length).toBeGreaterThan(0);
    for (let i = 0; i < mesh.positions.length; i += 3) {
      const [x, y, z] = [mesh.positions[i], mesh.positions[i + 1], mesh.positions[i + 2]];
      expect(Math.abs(Math.hypot(x, y, z) - 0.5)).toBeLessThan(0.03);
      expect(x * mesh.normals[i] + y * mesh.normals[i + 1] + z * mesh.normals[i + 2]).toBeGreaterThan(0);
    }
  });
});

describe('bodyParams (치수 → 마네킹 모양)', () => {
  it('둘레와 비율로 구한 타원의 둘레가 입력 둘레와 같다', () => {
    const e = ellipseFromGirth(1.0, 1.38);
    expect(ellipsePerimeter(e.a, e.b)).toBeCloseTo(1.0, 6);
    expect(e.a / e.b).toBeCloseTo(1.38, 6);
  });

  it('키·어깨너비·팔길이가 몸 비율에 반영된다', () => {
    const p = bodyParams({ height_cm: 180, chest_cm: 100, waist_cm: 80, shoulder_cm: 46, arm_length_cm: 62 });
    expect(p.height).toBeCloseTo(1.8);
    expect(p.shoulderHalf).toBeCloseTo(0.23);
    expect(p.upperArm + p.foreArm).toBeCloseTo(0.62);
    expect(p.joints.shoulderR[0]).toBeLessThan(0); // 사람의 오른쪽은 화면 왼쪽(-x)
  });

  it('범위 밖이거나 비어 있는 치수는 그림용으로만 기본값·범위 안 값을 쓴다', () => {
    const p = bodyParams({ height_cm: 999, chest_cm: Number.NaN });
    expect(p.input.height_cm).toBe(210);
    expect(p.input.chest_cm).toBe(100);
  });
});

describe('몸·티셔츠 메시', () => {
  const p = bodyParams({ height_cm: 175, chest_cm: 100, waist_cm: 80, shoulder_cm: 44, arm_length_cm: 60 });

  it('몸 메시 높이가 키와 같다(머리 끝~발바닥)', () => {
    const mesh = buildBodyMesh(p, 0.03);
    const { min, max } = bounds(mesh.positions);
    expect(max[1] - min[1]).toBeGreaterThan(1.7);
    expect(max[1]).toBeLessThan(1.78);
    // 몸통 안쪽은 음수, 멀리 떨어진 곳은 양수
    const sdf = bodySdf(p);
    expect(sdf(0, p.y.chest, 0)).toBeLessThan(0);
    expect(sdf(0.6, p.y.chest, 0)).toBeGreaterThan(0);
  });

  it('몸 메시의 꼭짓점은 모두 SDF 표면 위에 있다(멀리 있는 근육을 건너뛰다 모양이 끊기지 않는다)', () => {
    const mesh = buildBodyMesh(p, 0.012);
    const sdf = bodySdf(p);
    let worst = 0;
    for (let i = 0; i < mesh.positions.length; i += 3) {
      worst = Math.max(worst, Math.abs(sdf(mesh.positions[i], mesh.positions[i + 1], mesh.positions[i + 2])));
    }
    expect(worst).toBeLessThan(0.012);
  });

  it('꼭 맞는 티셔츠도 가슴·복근·승모근을 덮는다(근육이 옷 밖으로 나오지 않는다)', () => {
    const g = { ...defaultGarment(p), chestGirth: 1.02 };
    const layout = teeLayout(p, g);
    const tee = teeSdf(p, g);
    const mesh = buildBodyMesh(p, 0.012);
    let checked = 0;
    for (let i = 0; i < mesh.positions.length; i += 3) {
      const x = mesh.positions[i];
      const y = mesh.positions[i + 1];
      const z = mesh.positions[i + 2];
      // 몸통만(팔은 소매가, 목둘레는 목 구멍이 맡는다)
      if (y < layout.hemY + 0.02 || y > p.y.shoulder - 0.02 || Math.abs(x) > p.chest.a * 0.85) continue;
      checked++;
      expect(tee(x, y, z)).toBeLessThan(0.002);
    }
    expect(checked).toBeGreaterThan(500);
  });

  it('티셔츠 밑단은 목 아래에서 총장만큼 내려오고 몸을 감싼다', () => {
    const g = { ...defaultGarment(p), bodyLength: 0.7 };
    const layout = teeLayout(p, g);
    expect(layout.topY - layout.hemY).toBeCloseTo(0.7, 3);
    const mesh = buildTeeMesh(p, g, 0.02);
    const { min, max } = bounds(mesh.positions);
    expect(min[1]).toBeGreaterThan(layout.hemY - 0.03);
    expect(max[0] - min[0]).toBeGreaterThan(2 * p.chest.a);
  });
});
