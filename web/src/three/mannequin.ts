import type { MeshData, Sdf, Vec3 } from './surfaceNets';

/**
 * Tripo AI로 만든 마네킹(assets/mannequin.glb)을 입력 치수에 맞춰 늘리고 줄인다.
 * - GLB: 꼭짓점 2만 5천 개·삼각형 5만 개, 키 1(발바닥 y=0, 위 +y, 얼굴 +z)로 맞춘 위치와 꼭짓점별 팔 가중치(_ARM).
 *   변환 방법과 기준 치수를 재는 법은 README "3D 마네킹" 참고. 아래 MANNEQUIN 값은 변환할 때 함께 잰 값이고,
 *   GLB extras에도 같은 값이 들어 있다(테스트가 둘이 같은지 확인한다).
 * - 맞추기: 키는 그대로 비례해 늘리고, 몸통은 높이마다 다른 배율로 가슴·허리·엉덩이 둘레를 맞춘다(다리는 다리 축 기준).
 *   팔은 몸통 배율을 받지 않고 어깨너비만큼 옆으로 옮기고 팔길이만큼 팔 축을 따라 늘인다.
 *   몸통이 굵어져 팔 안쪽과 닿으면 팔을 어깨 관절 기준으로 조금 벌린다.
 */

/** 키 1로 맞춘 마네킹의 기준 치수(사람 오른쪽 = -x) */
export const MANNEQUIN = {
  landmarks: { crotch: 0.465, hip: 0.4975, waist: 0.6275, armpit: 0.7175, chest: 0.7425, shoulder: 0.81, neck: 0.885, wrist: 0.5175, fingertip: 0.4025 },
  girth: { chest: 0.5905, waist: 0.4356, hip: 0.5587 },
  /** 겨드랑이 높이에서 몸통 반폭(팔 안쪽 끝) */
  torsoCut: 0.0912,
  /** 견봉(어깨 끝) 반폭 */
  shoulderHalf: 0.1198,
  /** 견봉 → 손목 */
  armLength: 0.2936,
  shoulderJoint: [-0.1142, 0.788, -0.0325] as Vec3,
  wristJoint: [-0.1438, 0.5175, -0.0079] as Vec3,
  /** 겨드랑이 아래 높이별 [y, 몸통 반폭, 팔 안쪽 |x|] */
  clearance: [
    [0.47, 0.0981, 0.1241], [0.48, 0.097, 0.1256], [0.49, 0.0959, 0.1281], [0.5, 0.0947, 0.1293], [0.51, 0.0934, 0.1285],
    [0.52, 0.0921, 0.1271], [0.53, 0.0907, 0.1251], [0.54, 0.0894, 0.1233], [0.55, 0.0881, 0.1212], [0.56, 0.0866, 0.1189],
    [0.57, 0.085, 0.1163], [0.58, 0.0839, 0.1137], [0.59, 0.0829, 0.1111], [0.6, 0.0807, 0.1091], [0.61, 0.0786, 0.1075],
    [0.62, 0.0769, 0.1065], [0.63, 0.0756, 0.1061], [0.64, 0.076, 0.1051], [0.65, 0.0785, 0.104], [0.66, 0.0813, 0.1027],
    [0.67, 0.084, 0.1006], [0.68, 0.0863, 0.0986], [0.69, 0.0883, 0.097], [0.7, 0.0899, 0.0954], [0.71, 0.0912, 0.0936],
  ] as readonly (readonly [number, number, number])[],
} as const;

/** 다리 단면 가운데의 |x|(가랑이 아래는 이 축을 기준으로 굵기를 바꾼다) */
const LEG_CX = 0.056;

export interface MannequinBase {
  /** 키 1 기준 위치 */
  positions: Float32Array;
  /** 꼭짓점별 팔 가중치(0 몸통 ~ 1 팔) */
  arm: Float32Array;
  indices: Uint16Array | Uint32Array;
  /** GLB extras.fitRuler(변환할 때 잰 값) */
  meta: Record<string, unknown> | null;
}

interface GltfAccessor {
  bufferView: number;
  byteOffset?: number;
  componentType: number;
  normalized?: boolean;
  count: number;
  type: 'SCALAR' | 'VEC2' | 'VEC3' | 'VEC4';
}

/** 우리 변환기가 만든 GLB(메시 하나, POSITION·_ARM·indices, KHR_mesh_quantization)를 읽는다 */
export function parseMannequinGlb(buffer: ArrayBuffer): MannequinBase {
  const dv = new DataView(buffer);
  if (dv.getUint32(0, true) !== 0x46546c67) throw new Error('GLB 파일이 아닙니다.');
  const jsonLength = dv.getUint32(12, true);
  const json = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 20, jsonLength)));
  const binStart = 20 + jsonLength + 8;
  const node = json.nodes[0];
  const primitive = json.meshes[node.mesh].primitives[0];
  const width = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 } as const;
  const bytes: Record<number, number> = { 5121: 1, 5123: 2, 5125: 4, 5126: 4 };
  const read = (index: number): Float64Array => {
    const acc = json.accessors[index] as GltfAccessor;
    const view = json.bufferViews[acc.bufferView];
    const comps = width[acc.type];
    const size = bytes[acc.componentType];
    const stride = view.byteStride ?? comps * size;
    const start = binStart + (view.byteOffset ?? 0) + (acc.byteOffset ?? 0);
    const out = new Float64Array(acc.count * comps);
    const scaleBy = acc.normalized ? (acc.componentType === 5123 ? 65535 : acc.componentType === 5121 ? 255 : 1) : 1;
    for (let i = 0; i < acc.count; i++) {
      for (let c = 0; c < comps; c++) {
        const at = start + i * stride + c * size;
        const v =
          acc.componentType === 5126
            ? dv.getFloat32(at, true)
            : acc.componentType === 5125
              ? dv.getUint32(at, true)
              : acc.componentType === 5123
                ? dv.getUint16(at, true)
                : dv.getUint8(at);
        out[i * comps + c] = v / scaleBy;
      }
    }
    return out;
  };
  const q = read(primitive.attributes.POSITION);
  const t: number[] = node.translation ?? [0, 0, 0];
  const s: number[] = node.scale ?? [1, 1, 1];
  const positions = new Float32Array(q.length);
  for (let i = 0; i < q.length; i += 3) for (let a = 0; a < 3; a++) positions[i + a] = t[a] + q[i + a] * s[a];
  const arm = Float32Array.from(read(primitive.attributes._ARM));
  const rawIndex = read(primitive.indices);
  const indices = positions.length / 3 > 65535 ? Uint32Array.from(rawIndex) : Uint16Array.from(rawIndex);
  return { positions, arm, indices, meta: json.extras?.fitRuler ?? null };
}

export interface FitTargets {
  /** m */
  height: number;
  /** 둘레(m) */
  chest: number;
  waist: number;
  hip: number;
  /** 견봉 반폭(m) */
  shoulderHalf: number;
  /** 견봉 → 손목(m) */
  armLength: number;
}

export interface MannequinFit {
  height: number;
  /** 키 1 기준 높이 → 몸통 둘레 배율 */
  radial: (y: number) => number;
  /** 키 1 기준 높이 → 팔을 바깥으로 옮기는 거리(키 1 기준) */
  armShift: (y: number) => number;
  /** 팔 축을 따라 늘이는 배율 */
  armScale: number;
}

const clampN = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const smoothT = (t: number) => (t <= 0 ? 0 : t >= 1 ? 1 : t * t * (3 - 2 * t));

/** 입력 치수 → 마네킹을 늘리고 줄이는 방법 */
export function mannequinFit(target: FitTargets): MannequinFit {
  const H = target.height;
  const L = MANNEQUIN.landmarks;
  const fc = clampN(target.chest / H / MANNEQUIN.girth.chest, 0.7, 1.9);
  const fw = clampN(target.waist / H / MANNEQUIN.girth.waist, 0.7, 2.2);
  const fh = clampN(target.hip / H / MANNEQUIN.girth.hip, 0.7, 1.9);
  // 무릎 아래와 목 위는 그대로, 그 사이는 기준 높이마다 둘레 배율을 부드럽게 잇는다
  const points: [number, number][] = [
    [0.29, 1],
    [L.crotch - 0.03, 1 + (fh - 1) * 0.8],
    [L.hip, fh],
    [L.waist, fw],
    [L.chest, fc],
    [L.armpit + 0.045, fc],
    [L.shoulder, 1 + (fc - 1) * 0.5],
    [0.85, 1],
  ];
  const radial = (y: number) => {
    if (y <= points[0][0]) return points[0][1];
    for (let i = 1; i < points.length; i++) {
      const [y1, f1] = points[i];
      if (y <= y1) {
        const [y0, f0] = points[i - 1];
        return f0 + (f1 - f0) * smoothT((y - y0) / (y1 - y0));
      }
    }
    return points[points.length - 1][1];
  };
  // 팔: 어깨너비를 맞추되, 겨드랑이에서 몸통이 넓어진 만큼은 꼭 밖으로
  const shift0 = Math.max(target.shoulderHalf / H - MANNEQUIN.shoulderHalf, (fc - 1) * MANNEQUIN.torsoCut);
  const jy = MANNEQUIN.shoulderJoint[1];
  let tilt = 0;
  for (const [y, half, inner] of MANNEQUIN.clearance) {
    const room = inner + shift0 - half * radial(y) - 0.004;
    if (room < 0 && jy - y > 0.02) tilt = Math.max(tilt, -room / (jy - y));
  }
  const armShift = (y: number) => shift0 + (y < jy ? (jy - y) * tilt : 0);
  const armScale = clampN(target.armLength / H / MANNEQUIN.armLength, 0.75, 1.4);
  return { height: H, radial, armShift, armScale };
}

const ARM_AXIS: Vec3 = (() => {
  const [jx, jy, jz] = MANNEQUIN.shoulderJoint;
  const [wx, wy, wz] = MANNEQUIN.wristJoint;
  const l = Math.hypot(wx - jx, wy - jy, wz - jz);
  return [(wx - jx) / l, (wy - jy) / l, (wz - jz) / l];
})();

/** 키 1 기준 오른쪽(-x) 팔 위의 점을 맞춘 자리로(늘이기 → 옆으로 옮기기). 결과도 키 1 기준 */
function armPointUnit(fit: MannequinFit, x: number, y: number, z: number): Vec3 {
  const [jx, jy, jz] = MANNEQUIN.shoulderJoint;
  const t = (x - jx) * ARM_AXIS[0] + (y - jy) * ARM_AXIS[1] + (z - jz) * ARM_AXIS[2];
  const k = t > 0 ? t * (fit.armScale - 1) : 0;
  return [x + ARM_AXIS[0] * k - fit.armShift(y), y + ARM_AXIS[1] * k, z + ARM_AXIS[2] * k];
}

/** 맞춘 마네킹의 오른쪽 어깨 관절·팔꿈치·손목(m). 팔꿈치는 위팔:아래팔 = 0.55:0.45 */
export function fittedArmJoints(fit: MannequinFit): { shoulder: Vec3; elbow: Vec3; wrist: Vec3 } {
  const H = fit.height;
  const s = armPointUnit(fit, ...MANNEQUIN.shoulderJoint);
  const w = armPointUnit(fit, ...MANNEQUIN.wristJoint);
  const toM = (p: Vec3): Vec3 => [p[0] * H, p[1] * H, p[2] * H];
  const e: Vec3 = [s[0] + (w[0] - s[0]) * 0.55, s[1] + (w[1] - s[1]) * 0.55, s[2] + (w[2] - s[2]) * 0.55];
  return { shoulder: toM(s), elbow: toM(e), wrist: toM(w) };
}

/** 마네킹을 치수에 맞춘 메시(m)와 꼭짓점별 팔 가중치 */
export function fitMannequin(base: MannequinBase, fit: MannequinFit): { mesh: MeshData; arm: Float32Array } {
  const H = fit.height;
  const src = base.positions;
  const n = src.length / 3;
  const out = new Float32Array(src.length);
  const crotch = MANNEQUIN.landmarks.crotch;
  for (let i = 0; i < n; i++) {
    const x = src[i * 3];
    const y = src[i * 3 + 1];
    const z = src[i * 3 + 2];
    const w = base.arm[i];
    const side = x < 0 ? -1 : 1;
    const ax = Math.abs(x);
    // 몸통·다리: 둘레 배율. 가랑이 아래는 다리 축(|x| = LEG_CX)을 기준으로
    const f = fit.radial(y);
    const c = LEG_CX * (1 - smoothT((y - (crotch - 0.03)) / 0.06));
    const tx = side * Math.max(ax > 0 ? 0.002 : 0, c + (ax - c) * f);
    const tz = z * f;
    let px = tx;
    let py = y;
    let pz = tz;
    if (w > 0) {
      const a = armPointUnit(fit, -ax, y, z);
      px = tx + (-side * a[0] - tx) * w;
      py = y + (a[1] - y) * w;
      pz = tz + (a[2] - tz) * w;
    }
    out[i * 3] = px * H;
    out[i * 3 + 1] = py * H;
    out[i * 3 + 2] = pz * H;
  }
  return { mesh: { positions: out, normals: vertexNormals(out, base.indices), indices: Uint32Array.from(base.indices) }, arm: base.arm };
}

/** 면 넓이로 가중한 꼭짓점 법선 */
function vertexNormals(p: Float32Array, idx: ArrayLike<number>): Float32Array {
  const nrm = new Float32Array(p.length);
  for (let t = 0; t < idx.length; t += 3) {
    const a = idx[t] * 3;
    const b = idx[t + 1] * 3;
    const c = idx[t + 2] * 3;
    const ux = p[b] - p[a];
    const uy = p[b + 1] - p[a + 1];
    const uz = p[b + 2] - p[a + 2];
    const vx = p[c] - p[a];
    const vy = p[c + 1] - p[a + 1];
    const vz = p[c + 2] - p[a + 2];
    const cx = uy * vz - uz * vy;
    const cy = uz * vx - ux * vz;
    const cz = ux * vy - uy * vx;
    for (const k of [a, b, c]) {
      nrm[k] += cx;
      nrm[k + 1] += cy;
      nrm[k + 2] += cz;
    }
  }
  for (let i = 0; i < nrm.length; i += 3) {
    const l = Math.hypot(nrm[i], nrm[i + 1], nrm[i + 2]) || 1;
    nrm[i] /= l;
    nrm[i + 1] /= l;
    nrm[i + 2] /= l;
  }
  return nrm;
}

/**
 * 소매가 덮어야 할 팔 겉(맞춘 마네킹에서 잰다). 오른팔 축(joint에서 dir 방향, t0~t1 구간)을 따라
 * 위치·각도마다 축에서 가장 먼 팔 점까지 거리를 표로 만들고 (축까지 거리 − 그 거리)를 돌려준다. 왼팔은 거울로 같이 잰다.
 */
export function armCover(mesh: MeshData, arm: Float32Array, joint: Vec3, dir: Vec3, t0: number, t1: number): Sdf {
  const NS = Math.max(6, Math.round((t1 - t0) / 0.01));
  const NT = 48;
  // 축에 수직인 두 방향(e1은 대략 바깥 x, e2는 앞뒤)
  const up: Vec3 = Math.abs(dir[2]) < 0.9 ? [0, 0, 1] : [1, 0, 0];
  const c1: Vec3 = [dir[1] * up[2] - dir[2] * up[1], dir[2] * up[0] - dir[0] * up[2], dir[0] * up[1] - dir[1] * up[0]];
  const l1 = Math.hypot(c1[0], c1[1], c1[2]) || 1;
  const e1: Vec3 = [c1[0] / l1, c1[1] / l1, c1[2] / l1];
  const e2: Vec3 = [dir[1] * e1[2] - dir[2] * e1[1], dir[2] * e1[0] - dir[0] * e1[2], dir[0] * e1[1] - dir[1] * e1[0]];
  const local = (x: number, y: number, z: number) => {
    const rx = -Math.abs(x) - joint[0];
    const ry = y - joint[1];
    const rz = z - joint[2];
    const t = rx * dir[0] + ry * dir[1] + rz * dir[2];
    const px = rx - dir[0] * t;
    const py = ry - dir[1] * t;
    const pz = rz - dir[2] * t;
    return { t, r: Math.hypot(px, py, pz), a: Math.atan2(px * e2[0] + py * e2[1] + pz * e2[2], px * e1[0] + py * e1[1] + pz * e1[2]) };
  };
  const R = new Float32Array((NS + 1) * NT);
  const p = mesh.positions;
  for (let i = 0; i < p.length / 3; i++) {
    if (arm[i] < 0.5) continue;
    const q = local(p[i * 3], p[i * 3 + 1], p[i * 3 + 2]);
    if (q.t < t0 || q.t > t1) continue;
    const k = Math.round(((q.t - t0) / (t1 - t0)) * NS);
    const j = ((Math.round(((q.a + Math.PI) / (Math.PI * 2)) * NT) % NT) + NT) % NT;
    if (q.r > R[k * NT + j]) R[k * NT + j] = q.r;
  }
  const D = fillAndDilate(R, NS, NT);
  return (x, y, z) => {
    const q = local(x, y, z);
    if (q.t < t0 || q.t > t1) return 1;
    return q.r - lookup(D, NS, NT, (q.t - t0) / (t1 - t0), q.a);
  };
}

/** 빈 칸은 같은 줄의 가까운 각도에서, 그래도 비면 이웃 줄에서 채운 뒤 각도·줄로 한 칸씩 넓힌다(보수적으로 덮게) */
function fillAndDilate(R: Float32Array, N: number, NT: number): Float32Array {
  for (let k = 0; k <= N; k++) {
    for (let j = 0; j < NT; j++) {
      if (R[k * NT + j] > 0) continue;
      for (let d = 1; d < NT / 2; d++) {
        const v = Math.max(R[k * NT + ((j + d) % NT)], R[k * NT + ((j - d + NT) % NT)]);
        if (v > 0) {
          R[k * NT + j] = v;
          break;
        }
      }
    }
  }
  for (let k = 0; k <= N; k++) {
    if (R[k * NT] > 0) continue;
    for (let d = 1; d <= N; d++) {
      const src = k - d >= 0 && R[(k - d) * NT] > 0 ? k - d : k + d <= N && R[(k + d) * NT] > 0 ? k + d : -1;
      if (src >= 0) {
        R.copyWithin(k * NT, src * NT, src * NT + NT);
        break;
      }
    }
  }
  const D = new Float32Array(R.length);
  for (let k = 0; k <= N; k++) {
    for (let j = 0; j < NT; j++) {
      const at = (dk: number, dj: number) => R[Math.min(N, Math.max(0, k + dk)) * NT + ((j + dj + NT) % NT)];
      D[k * NT + j] = Math.max(at(0, 0), at(0, -1), at(0, 1), at(-1, 0), at(1, 0));
    }
  }
  return D;
}

/** 표(줄 0~N, 각도 NT칸)에서 줄 위치 u(0~1)·각도 a(라디안)의 값을 이웃 네 칸으로 섞어 읽는다 */
function lookup(D: Float32Array, N: number, NT: number, u: number, a: number): number {
  const fk = Math.min(N, Math.max(0, u * N));
  const k0 = Math.min(N - 1, Math.floor(fk));
  const tk = fk - k0;
  const fj = ((a + Math.PI) / (Math.PI * 2)) * NT;
  const j0 = ((Math.floor(fj) % NT) + NT) % NT;
  const j1 = (j0 + 1) % NT;
  const tj = fj - Math.floor(fj);
  const r0 = D[k0 * NT + j0] + (D[k0 * NT + j1] - D[k0 * NT + j0]) * tj;
  const r1 = D[(k0 + 1) * NT + j0] + (D[(k0 + 1) * NT + j1] - D[(k0 + 1) * NT + j0]) * tj;
  return r0 + (r1 - r0) * tk;
}

/**
 * 티셔츠가 덮어야 할 몸통 겉(맞춘 마네킹에서 잰다). 높이·각도마다 몸통 가운데 축에서 가장 먼 거리를 표로 만들고,
 * (축까지 거리 − 그 거리)를 돌려준다. 팔 쪽 점(가중치 0.5 이상)은 뺀다(어깨 끝과 위팔은 소매가 덮는다).
 */
export function torsoCover(mesh: MeshData, arm: Float32Array, y0: number, y1: number): Sdf {
  const NY = Math.max(8, Math.round((y1 - y0) / 0.01));
  const NT = 72;
  const R = new Float32Array((NY + 1) * NT);
  const p = mesh.positions;
  for (let i = 0; i < p.length / 3; i++) {
    const x = p[i * 3];
    const y = p[i * 3 + 1];
    const z = p[i * 3 + 2];
    if (y < y0 || y > y1) continue;
    if (arm[i] >= 0.5) continue;
    const k = Math.round(((y - y0) / (y1 - y0)) * NY);
    const j = ((Math.round(((Math.atan2(z, x) + Math.PI) / (Math.PI * 2)) * NT) % NT) + NT) % NT;
    const r = Math.hypot(x, z);
    if (r > R[k * NT + j]) R[k * NT + j] = r;
  }
  const D = fillAndDilate(R, NY, NT);
  return (x, y, z) => {
    if (y < y0 || y > y1) return 1;
    return Math.hypot(x, z) - lookup(D, NY, NT, (y - y0) / (y1 - y0), Math.atan2(z, x));
  };
}
