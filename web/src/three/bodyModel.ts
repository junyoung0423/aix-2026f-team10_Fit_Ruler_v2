import { MANNEQUIN, fittedArmJoints, mannequinFit, type MannequinFit } from './mannequin';
import { surfaceNets, type MeshData, type Sdf, type Vec3 } from './surfaceNets';

/**
 * 입력한 치수로 만드는 3D 마네킹과 고른 티셔츠의 모양(단위 m, y가 위, 얼굴은 +z).
 * - 몸: Tripo 마네킹(mannequin.ts)을 치수에 맞춰 늘리고 줄인다. 높이 기준·팔 관절도 그 마네킹에서 잰 값이다.
 *   마네킹 파일을 불러오지 못하면 아래 SDF 몸(몸통 + 목·머리·어깨·팔·다리를 부드럽게 이은 모양)을 대신 쓴다.
 * - 티셔츠: 옷 치수(가슴둘레·어깨너비·소매·총장)로 몸 밖에 씌운다. 박스형으로 떨어지고 몸보다 작으면 최소 간격만 둔다.
 * 실제 몸 메시(SMPL 등)는 쓰지 않는다. 화면용 근사 모양이며 핏 판정에는 쓰지 않는다(판정은 서버 핏 리포트).
 */

export interface BodyInput {
  height_cm: number;
  chest_cm: number;
  waist_cm: number;
  shoulder_cm: number;
  arm_length_cm: number;
}

export interface GarmentShape {
  /** 옷 가슴둘레(m) */
  chestGirth: number;
  /** 옷 어깨너비(m) */
  shoulderWidth: number;
  /** 소매 길이(m) */
  sleeveLength: number;
  /** 총장(m) */
  bodyLength: number;
}

export interface Ellipse {
  a: number;
  b: number;
}

export interface BodyParams {
  input: BodyInput;
  height: number;
  chest: Ellipse;
  waist: Ellipse;
  hip: Ellipse;
  shoulderHalf: number;
  upperArm: number;
  foreArm: number;
  neckR: number;
  y: {
    headTop: number;
    headCenter: number;
    chin: number;
    neckBase: number;
    shoulder: number;
    armpit: number;
    chest: number;
    waist: number;
    hip: number;
    crotch: number;
    knee: number;
    ankle: number;
  };
  /** 사람의 오른쪽(R)은 화면 왼쪽(-x)이다. */
  joints: {
    shoulderR: Vec3;
    elbowR: Vec3;
    wristR: Vec3;
    shoulderL: Vec3;
    elbowL: Vec3;
    wristL: Vec3;
    hipR: Vec3;
    kneeR: Vec3;
    ankleR: Vec3;
    hipL: Vec3;
    kneeL: Vec3;
    ankleL: Vec3;
  };
}

export const DEFAULT_BODY: BodyInput = { height_cm: 175, chest_cm: 100, waist_cm: 80, shoulder_cm: 44, arm_length_cm: 60 };

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const smooth = (t: number) => t * t * (3 - 2 * t);

/** 타원 둘레(라마누잔 근사) */
export function ellipsePerimeter(a: number, b: number): number {
  const h = ((a - b) / (a + b)) ** 2;
  return Math.PI * (a + b) * (1 + (3 * h) / (10 + Math.sqrt(4 - 3 * h)));
}

/** 둘레와 너비:두께 비율로 단면 타원(반지름)을 구한다 */
export function ellipseFromGirth(girth: number, ratio: number): Ellipse {
  const b = girth / ellipsePerimeter(ratio, 1);
  return { a: ratio * b, b };
}

const add = (p: Vec3, q: Vec3): Vec3 => [p[0] + q[0], p[1] + q[1], p[2] + q[2]];
const scale = (p: Vec3, s: number): Vec3 => [p[0] * s, p[1] * s, p[2] * s];
const norm = (p: Vec3): Vec3 => {
  const l = Math.hypot(p[0], p[1], p[2]) || 1;
  return [p[0] / l, p[1] / l, p[2] / l];
};
const mirror = (p: Vec3): Vec3 => [-p[0], p[1], p[2]];
const sub = (p: Vec3, q: Vec3): Vec3 => [p[0] - q[0], p[1] - q[1], p[2] - q[2]];
const dot3 = (p: Vec3, q: Vec3) => p[0] * q[0] + p[1] * q[1] + p[2] * q[2];
const cross = (p: Vec3, q: Vec3): Vec3 => [p[1] * q[2] - p[2] * q[1], p[2] * q[0] - p[0] * q[2], p[0] * q[1] - p[1] * q[0]];
const along = (p: Vec3, q: Vec3, t: number): Vec3 => [lerp(p[0], q[0], t), lerp(p[1], q[1], t), lerp(p[2], q[2], t)];

/** 범위 밖 치수는 그림이 깨지지 않게 화면용으로만 지원 범위 안으로 당긴다 */
function sanitize(input: Partial<BodyInput>): BodyInput {
  const pick = (value: number | undefined, fallback: number, lo: number, hi: number) =>
    typeof value === 'number' && Number.isFinite(value) ? clamp(value, lo, hi) : fallback;
  return {
    height_cm: pick(input.height_cm, DEFAULT_BODY.height_cm, 140, 210),
    chest_cm: pick(input.chest_cm, DEFAULT_BODY.chest_cm, 60, 160),
    waist_cm: pick(input.waist_cm, DEFAULT_BODY.waist_cm, 50, 160),
    shoulder_cm: pick(input.shoulder_cm, DEFAULT_BODY.shoulder_cm, 28, 60),
    arm_length_cm: pick(input.arm_length_cm, DEFAULT_BODY.arm_length_cm, 40, 85),
  };
}

export function bodyParams(raw: Partial<BodyInput>): BodyParams {
  const input = sanitize(raw);
  const H = input.height_cm / 100;
  const chest = ellipseFromGirth(input.chest_cm / 100, 1.38);
  const waist = ellipseFromGirth(input.waist_cm / 100, 1.32);
  // 엉덩이둘레는 입력이 없어 가슴·허리로 짐작한다(화면용). 디스플레이 마네킹처럼 어깨보다 좁게 둔다.
  const hip = ellipseFromGirth(Math.max(input.waist_cm + 3, input.chest_cm * 0.9) / 100, 1.32);
  const shoulderHalf = Math.max(input.shoulder_cm / 200, chest.a + 0.02);
  const L = input.arm_length_cm / 100;
  const upperArm = L * 0.55;
  const foreArm = L * 0.45;
  const s = H / 1.75;
  const neckR = 0.063 * Math.sqrt(s) * clamp(input.chest_cm / 100, 0.85, 1.25) ** 0.3;

  // 높이 기준은 Tripo 마네킹에서 잰 비율(목 밑은 목과 승모근이 만나는 곳, 턱은 목이 가장 가는 곳 바로 위)
  const M = MANNEQUIN.landmarks;
  const y = {
    headTop: H,
    headCenter: 0.94 * H,
    chin: M.neck * H,
    neckBase: 0.858 * H,
    shoulder: M.shoulder * H,
    armpit: M.armpit * H,
    chest: M.chest * H,
    waist: M.waist * H,
    hip: M.hip * H,
    crotch: M.crotch * H,
    knee: 0.285 * H,
    ankle: 0.042 * H,
  };

  // 팔: 마네킹 팔을 치수에 맞춘 자리(어깨너비만큼 옆으로, 팔길이만큼 늘인 팔 축)
  const arms = fittedArmJoints(
    mannequinFit({ height: H, chest: input.chest_cm / 100, waist: input.waist_cm / 100, hip: ellipsePerimeter(hip.a, hip.b), shoulderHalf, armLength: L }),
  );
  const shoulderR = arms.shoulder;
  const elbowR = arms.elbow;
  const wristR = arms.wrist;
  const hipX = hip.a * 0.46;
  const hipR: Vec3 = [-hipX, y.crotch + 0.07 * s, 0];
  const kneeR: Vec3 = [-(hipX + 0.012 * s), y.knee, 0.012];
  const ankleR: Vec3 = [-(hipX + 0.022 * s), y.ankle, -0.012];

  return {
    input,
    height: H,
    chest,
    waist,
    hip,
    shoulderHalf,
    upperArm,
    foreArm,
    neckR,
    y,
    joints: {
      shoulderR,
      elbowR,
      wristR,
      shoulderL: mirror(shoulderR),
      elbowL: mirror(elbowR),
      wristL: mirror(wristR),
      hipR,
      kneeR,
      ankleR,
      hipL: mirror(hipR),
      kneeL: mirror(kneeR),
      ankleL: mirror(ankleR),
    },
  };
}

// ---------- SDF 조각 ----------

const smin = (a: number, b: number, k: number) => {
  const h = Math.max(k - Math.abs(a - b), 0) / k;
  return Math.min(a, b) - h * h * k * 0.25;
};
const smax = (a: number, b: number, k: number) => -smin(-a, -b, k);

function ellipsoid(c: Vec3, r: Vec3): Sdf {
  const [rx, ry, rz] = r;
  return (x, y, z) => {
    const px = (x - c[0]) / rx;
    const py = (y - c[1]) / ry;
    const pz = (z - c[2]) / rz;
    const k0 = Math.sqrt(px * px + py * py + pz * pz);
    const qx = px / rx;
    const qy = py / ry;
    const qz = pz / rz;
    const k1 = Math.sqrt(qx * qx + qy * qy + qz * qz);
    return k1 === 0 ? -Math.min(rx, ry, rz) : (k0 * (k0 - 1)) / k1;
  };
}

/**
 * 길이 방향(지역 y)이 dir인 타원체. 팔·다리를 따라 기운 근육 덩어리, 아래 가장자리만 앞으로 나온 가슴처럼
 * 축이 기운 모양에 쓴다. 지역 z는 face(기본: 앞쪽 +z)를 dir에 수직이 되게 맞춘 방향이다.
 */
function ellipsoidAlong(c: Vec3, dir: Vec3, r: Vec3, face: Vec3 = [0, 0, 1]): Sdf {
  const yv = norm(dir);
  const zv = norm(sub(face, scale(yv, dot3(face, yv))));
  const xv = cross(yv, zv);
  const [rx, ry, rz] = r;
  return (x, y, z) => {
    const px = x - c[0];
    const py = y - c[1];
    const pz = z - c[2];
    const lx = (px * xv[0] + py * xv[1] + pz * xv[2]) / rx;
    const ly = (px * yv[0] + py * yv[1] + pz * yv[2]) / ry;
    const lz = (px * zv[0] + py * zv[1] + pz * zv[2]) / rz;
    const k0 = Math.sqrt(lx * lx + ly * ly + lz * lz);
    const qx = lx / rx;
    const qy = ly / ry;
    const qz = lz / rz;
    const k1 = Math.sqrt(qx * qx + qy * qy + qz * qz);
    return k1 === 0 ? -Math.min(rx, ry, rz) : (k0 * (k0 - 1)) / k1;
  };
}

interface Sphere {
  c: Vec3;
  r: number;
}

/** 여러 점을 감싸는 구(조각이 멀면 계산을 건너뛰는 데 쓴다) */
function enclose(points: readonly Vec3[], pad: number): Sphere {
  const lo: [number, number, number] = [Infinity, Infinity, Infinity];
  const hi: [number, number, number] = [-Infinity, -Infinity, -Infinity];
  for (const p of points) {
    for (let a = 0; a < 3; a++) {
      lo[a] = Math.min(lo[a], p[a]);
      hi[a] = Math.max(hi[a], p[a]);
    }
  }
  const c: Vec3 = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
  let r = 0;
  for (const p of points) r = Math.max(r, Math.sqrt((p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2 + (p[2] - c[2]) ** 2));
  return { c, r: r + pad };
}

/** 점에서 구까지 거리가 limit보다 가까운지(부드러운 합치기 반경을 limit에 더해 넘긴다) */
const near = (s: Sphere, x: number, y: number, z: number, limit: number) => {
  const dx = x - s.c[0];
  const dy = y - s.c[1];
  const dz = z - s.c[2];
  return Math.sqrt(dx * dx + dy * dy + dz * dz) - s.r < limit;
};

/** 두 끝의 반지름이 다른 둥근 원뿔(Inigo Quilez) */
function roundCone(a: Vec3, b: Vec3, r1: number, r2: number): Sdf {
  const bax = b[0] - a[0];
  const bay = b[1] - a[1];
  const baz = b[2] - a[2];
  const l2 = bax * bax + bay * bay + baz * baz;
  const rr = r1 - r2;
  const a2 = l2 - rr * rr;
  const il2 = 1 / l2;
  return (x, y, z) => {
    const pax = x - a[0];
    const pay = y - a[1];
    const paz = z - a[2];
    const yy = pax * bax + pay * bay + paz * baz;
    const zz = yy - l2;
    const qx = pax * l2 - bax * yy;
    const qy = pay * l2 - bay * yy;
    const qz = paz * l2 - baz * yy;
    const x2 = qx * qx + qy * qy + qz * qz;
    const y2 = yy * yy * l2;
    const z2 = zz * zz * l2;
    const k = Math.sign(rr) * rr * rr * x2;
    if (Math.sign(zz) * a2 * z2 > k) return Math.sqrt(x2 + z2) * il2 - r2;
    if (Math.sign(yy) * a2 * y2 < k) return Math.sqrt(x2 + y2) * il2 - r1;
    return (Math.sqrt(x2 * a2 * il2) + yy * rr) * il2 - r1;
  };
}

interface Station {
  y: number;
  a: number;
  b: number;
  cz: number;
}

/** 높이마다 다른 타원 단면을 위아래로 이은 몸통. 단면 사이는 부드럽게(smoothstep) 잇는다. */
function extrusion(input: readonly Station[]): { sdf: Sdf; at: (y: number) => Station } {
  // 단면은 높이순이어야 한다(마네킹마다 가슴둘레 높이가 겨드랑이보다 위일 수도 있다). 거의 같은 높이는 하나만 남긴다
  const stations: Station[] = [];
  for (const s of [...input].sort((a, b) => a.y - b.y)) {
    if (stations.length && s.y - stations[stations.length - 1].y < 1e-4) stations[stations.length - 1] = s;
    else stations.push(s);
  }
  const at = (yy: number): Station => {
    if (yy <= stations[0].y) return stations[0];
    for (let i = 1; i < stations.length; i++) {
      const s1 = stations[i];
      if (yy <= s1.y) {
        const s0 = stations[i - 1];
        const t = smooth((yy - s0.y) / (s1.y - s0.y));
        return { y: yy, a: lerp(s0.a, s1.a, t), b: lerp(s0.b, s1.b, t), cz: lerp(s0.cz, s1.cz, t) };
      }
    }
    return stations[stations.length - 1];
  };
  const bottom = stations[0].y;
  const top = stations[stations.length - 1].y;
  // 격자를 잴 때마다 단면을 새로 계산하지 않도록 높이별 표를 미리 만든다(256칸 사이는 직선 보간).
  const N = 256;
  const span = top - bottom;
  const tableA = new Float64Array(N);
  const tableB = new Float64Array(N);
  const tableC = new Float64Array(N);
  for (let i = 0; i < N; i++) {
    const s = at(bottom + (span * i) / (N - 1));
    tableA[i] = s.a;
    tableB[i] = s.b;
    tableC[i] = s.cz;
  }
  const sdf: Sdf = (x, y, z) => {
    const t = ((Math.min(top, Math.max(bottom, y)) - bottom) / span) * (N - 1);
    const i = Math.min(N - 2, Math.floor(t));
    const f = t - i;
    const sa = tableA[i] + (tableA[i + 1] - tableA[i]) * f;
    const sb = tableB[i] + (tableB[i + 1] - tableB[i]) * f;
    const sc = tableC[i] + (tableC[i + 1] - tableC[i]) * f;
    const qx = x / sa;
    const qz = (z - sc) / sb;
    const r = Math.sqrt(qx * qx + qz * qz);
    const gx = qx / sa;
    const gz = qz / sb;
    const g = Math.sqrt(gx * gx + gz * gz);
    // 단면 타원까지의 거리(1차 근사): (r-1)/|∇r|
    const dxz = g > 1e-9 ? ((r - 1) * r) / g : -Math.min(sa, sb);
    const dy = Math.max(bottom - y, y - top);
    if (dy > 0) {
      const o = Math.max(dxz, 0);
      return Math.sqrt(o * o + dy * dy);
    }
    return Math.max(dxz, dy);
  };
  return { sdf, at };
}

function torsoStations(p: BodyParams): Station[] {
  const { y, chest, waist, hip, shoulderHalf, neckR } = p;
  return [
    { y: y.crotch - 0.018, a: hip.a * 0.5, b: hip.b * 0.6, cz: -0.006 },
    { y: y.crotch + 0.02, a: hip.a * 0.7, b: hip.b * 0.6, cz: -0.03 },
    { y: lerp(y.crotch, y.hip, 0.55), a: hip.a * 0.85, b: hip.b * 0.75, cz: -0.025 },
    { y: y.hip, a: hip.a * 0.9, b: hip.b * 0.9, cz: -0.02 },
    { y: lerp(y.hip, y.waist, 0.55), a: lerp(hip.a, waist.a, 0.6), b: lerp(hip.b, waist.b, 0.55), cz: -0.006 },
    { y: y.waist, a: waist.a, b: waist.b, cz: 0.002 },
    { y: lerp(y.waist, y.chest, 0.5), a: lerp(waist.a, chest.a, 0.55), b: lerp(waist.b, chest.b, 0.6), cz: 0.008 },
    { y: y.chest, a: chest.a, b: chest.b, cz: 0.014 },
    { y: y.armpit, a: chest.a * 1.02, b: chest.b * 0.94, cz: 0.01 },
    { y: y.shoulder - 0.012, a: shoulderHalf * 0.78, b: chest.b * 0.64, cz: 0.0 },
    { y: lerp(y.shoulder, y.neckBase, 0.6), a: neckR * 2.5, b: chest.b * 0.52, cz: -0.006 },
    { y: y.neckBase + 0.004, a: neckR * 1.12, b: neckR * 1.05, cz: -0.004 },
  ];
}

/** 팔·다리 SDF. bound(지금까지 가장 가까운 거리)보다 확실히 먼 부위는 계산을 건너뛴다 */
type Limb = (x: number, y: number, z: number, bound: number) => number;

/** 근육이 얼마나 또렷한지(0~1): 가슴둘레와 허리둘레 차이가 클수록 또렷하다 */
const muscleTone = (p: BodyParams) => clamp((p.input.chest_cm - p.input.waist_cm - 4) / 16, 0, 1);

/** 오른쪽 어깨 세모근(티셔츠가 그 위를 덮을 때도 쓴다) */
function deltoidOf(sh: Vec3, tone: number): Sdf {
  const m = 0.75 + 0.25 * tone;
  return ellipsoid(add(sh, [0.004, -0.014, 0.002]), [0.053 * m, 0.064, 0.056 * m]);
}

/** 오른쪽(화면 왼쪽, -x) 팔 하나: 어깨 세모근·위팔(이두·삼두)·아래팔·손바닥·손가락·엄지 */
function armSdf(sh: Vec3, el: Vec3, wr: Vec3, s: number, tone: number): Limb {
  const upDir = norm(sub(el, sh));
  const foreDir = norm(sub(wr, el));
  const handDir = norm(add(foreDir, [0, 0, 0.06]));
  const m = 0.75 + 0.25 * tone;
  const deltoid = deltoidOf(sh, tone);
  const upper = roundCone(sh, el, 0.046, 0.035);
  const biceps = ellipsoidAlong(add(along(sh, el, 0.55), [0.002, 0, 0.014]), upDir, [0.035 * m, 0.085, 0.034 * m]);
  const triceps = ellipsoidAlong(add(along(sh, el, 0.4), [-0.002, 0, -0.016]), upDir, [0.033 * m, 0.1, 0.03 * m]);
  const fore = roundCone(el, wr, 0.039, 0.024);
  // 아래팔 위쪽 바깥의 볼록한 근육(완요골근)
  const brachio = ellipsoidAlong(add(along(el, wr, 0.27), [-0.009, 0, 0.01]), foreDir, [0.031 * m, 0.085, 0.029 * m]);
  // 손바닥은 허벅지 쪽(+x)을 보고 납작하다. 손가락은 붙여서 살짝 앞으로 굽는다
  const palm = ellipsoidAlong(add(wr, scale(handDir, 0.048 * s)), handDir, [0.016 * s, 0.052 * s, 0.043 * s]);
  const fingerDir = norm(add(handDir, [0, 0, 0.22]));
  const fingers = ellipsoidAlong(add(add(wr, scale(handDir, 0.118 * s)), [0.002, 0, 0.008 * s]), fingerDir, [0.0135 * s, 0.05 * s, 0.036 * s]);
  const thumbBase = add(add(wr, scale(handDir, 0.03 * s)), [0.008 * s, 0, 0.028 * s]);
  const thumbTip = add(add(wr, scale(handDir, 0.095 * s)), [0.014 * s, 0, 0.04 * s]);
  const thumb = roundCone(thumbBase, thumbTip, 0.012 * s, 0.0085 * s);
  const upperBox = enclose([sh, el], 0.07);
  const foreBox = enclose([el, wr], 0.045);
  const handBox = enclose([wr, add(wr, scale(handDir, 0.17 * s)), thumbTip], 0.05 * s);
  return (x, y, z, bound) => {
    let d = Infinity;
    if (near(upperBox, x, y, z, bound + 0.04)) {
      d = smin(upper(x, y, z), deltoid(x, y, z), 0.03);
      d = smin(d, biceps(x, y, z), 0.022);
      d = smin(d, triceps(x, y, z), 0.022);
    }
    if (near(foreBox, x, y, z, Math.min(bound, d) + 0.04)) {
      d = smin(d, fore(x, y, z), 0.02);
      d = smin(d, brachio(x, y, z), 0.02);
    }
    if (near(handBox, x, y, z, Math.min(bound, d) + 0.04)) {
      d = smin(d, smin(smin(palm(x, y, z), fingers(x, y, z), 0.014), thumb(x, y, z), 0.01), 0.016);
    }
    return d;
  };
}

/** 오른쪽 다리 하나: 허벅지(넙다리곧은근·가쪽넓은근·안쪽넓은근·뒤넙다리)·무릎뼈·종아리(두 갈래)·발(뒤꿈치 포함) */
function legSdf(hip: Vec3, knee: Vec3, ankle: Vec3, s: number, tone: number): Limb {
  const thighDir = norm(sub(knee, hip));
  const calfDir = norm(sub(ankle, knee));
  const m = 0.8 + 0.2 * tone;
  const thigh = roundCone(hip, knee, 0.09 * s, 0.051 * s);
  const rectus = ellipsoidAlong(add(along(hip, knee, 0.45), [0, 0, 0.034 * s]), thighDir, [0.047 * s * m, 0.15 * s, 0.043 * s * m]);
  const lateral = ellipsoidAlong(add(along(hip, knee, 0.55), [-0.03 * s, 0, 0.01 * s]), thighDir, [0.048 * s * m, 0.14 * s, 0.052 * s * m]);
  const medial = ellipsoidAlong(add(along(hip, knee, 0.83), [0.025 * s, 0, 0.02 * s]), thighDir, [0.035 * s * m, 0.062 * s, 0.036 * s * m]);
  const hams = ellipsoidAlong(add(along(hip, knee, 0.5), [0, 0, -0.03 * s]), thighDir, [0.058 * s, 0.15 * s, 0.045 * s]);
  const kneecap = ellipsoid(add(knee, [0, 0.006 * s, 0.04 * s]), [0.025 * s, 0.03 * s, 0.016 * s]);
  const calf = roundCone(knee, ankle, 0.045 * s, 0.029 * s);
  // 종아리 근육: 안쪽 갈래가 더 크고 아래로 내려온다
  const gastroIn = ellipsoidAlong(add(along(knee, ankle, 0.3), [0.014 * s, 0, -0.03 * s]), calfDir, [0.033 * s * m, 0.1 * s, 0.037 * s * m]);
  const gastroOut = ellipsoidAlong(add(along(knee, ankle, 0.24), [-0.016 * s, 0, -0.027 * s]), calfDir, [0.029 * s * m, 0.088 * s, 0.033 * s * m]);
  const foot = ellipsoid([ankle[0], 0.028 * s, 0.05 * s], [0.041 * s, 0.03 * s, 0.118 * s]);
  const heel = ellipsoid([ankle[0], 0.034 * s, -0.026 * s], [0.032 * s, 0.036 * s, 0.042 * s]);
  const thighBox = enclose([hip, knee], 0.105 * s);
  const shinBox = enclose([knee, ankle], 0.065 * s);
  const footBox = enclose([ankle, [ankle[0], 0, -0.07 * s], [ankle[0], 0, 0.17 * s]], 0.045 * s);
  return (x, y, z, bound) => {
    let d = Infinity;
    if (near(thighBox, x, y, z, bound + 0.07)) {
      d = smin(thigh(x, y, z), rectus(x, y, z), 0.03);
      d = smin(d, lateral(x, y, z), 0.03);
      d = smin(d, medial(x, y, z), 0.025);
      d = smin(d, hams(x, y, z), 0.03);
    }
    if (near(shinBox, x, y, z, Math.min(bound, d) + 0.05)) {
      d = smin(d, calf(x, y, z), 0.04);
      d = smin(d, kneecap(x, y, z), 0.016);
      d = smin(d, gastroIn(x, y, z), 0.022);
      d = smin(d, gastroOut(x, y, z), 0.022);
    }
    if (near(footBox, x, y, z, Math.min(bound, d) + 0.04)) {
      d = smin(d, smin(foot(x, y, z), heel(x, y, z), 0.02), 0.03);
    }
    // 발바닥은 바닥(y=0)에서 평평하게
    return y < 0.02 ? smax(d, -y, 0.004) : d;
  };
}

/** 몸통과 그 위 근육(가슴·복근·배꼽·골반 V선·엉덩이·등골 홈·승모근). 티셔츠도 이 겉을 덮는다 */
function muscleTorso(p: BodyParams): Sdf {
  const { y, neckR, height: H } = p;
  const s = H / 1.75;
  const trunk = extrusion(torsoStations(p));
  const torso = trunk.sdf;
  const tone = muscleTone(p);
  /** 몸통 겉(정면 또는 등) z: 높이 yy, 옆 위치 xx에서 */
  const surfaceZ = (yy: number, xx: number, back = false) => {
    const st = trunk.at(yy);
    const k = Math.sqrt(Math.max(0, 1 - (xx / st.a) ** 2));
    return back ? st.cz - st.b * k : st.cz + st.b * k;
  };

  // ----- 가슴(대흉근): 위쪽은 몸통 안으로 묻히고 아래 가장자리만 앞으로 나오게 기울인다
  const pecY = y.chest + 0.03 * s;
  const pecX = p.chest.a * 0.45;
  const pecR: Vec3 = [p.chest.a * 0.42, 0.07 * s, 0.042 * s];
  const pecDir = norm([0, Math.cos(0.38), -Math.sin(0.38)]);
  const pecZ = surfaceZ(pecY, pecX) - pecR[2] * (1 - (0.3 + 0.18 * tone));
  const pecs = [ellipsoidAlong([-pecX, pecY, pecZ], pecDir, pecR), ellipsoidAlong([pecX, pecY, pecZ], pecDir, pecR)];

  // ----- 복근: 세 줄 두 칸. 가운데 세로 홈과 가로 홈은 조각 사이에 저절로 생긴다
  const absTop = pecY - pecR[1] * 0.95 - 0.03 * s;
  const absRows = [0, 1, 2].map((i) => lerp(absTop, y.waist + 0.004 * s, i / 2));
  const abR: Vec3 = [0.033 * s, 0.034 * s, 0.014 * s];
  const abOut = abR[2] * (0.12 + 0.24 * tone);
  const abs: Sdf[] = [];
  for (const ry of absRows) {
    for (const side of [-1, 1]) {
      const ax = side * 0.031 * s;
      abs.push(ellipsoid([ax, ry, surfaceZ(ry, ax) - abR[2] + abOut], abR));
    }
  }
  const lowY = y.waist - 0.07 * s;
  const navelY = y.waist - 0.012 * s;
  const navel = ellipsoid([0, navelY, surfaceZ(navelY, 0) + abOut * 0.6 + 0.002], [0.0065 * s, 0.0085 * s, 0.008 * s]);
  // 골반 앞의 V선(엉덩뼈에서 사타구니 쪽으로 내려가는 얕은 홈)
  const vTop = y.hip + 0.045 * s;
  const vBottom = y.crotch + 0.04 * s;
  const vLines = [-1, 1].map((side) => {
    const a: Vec3 = [side * p.hip.a * 0.6, vTop, surfaceZ(vTop, p.hip.a * 0.6) + 0.002];
    const b: Vec3 = [side * 0.04 * s, vBottom, surfaceZ(vBottom, 0.04 * s) + 0.002];
    return roundCone(a, b, 0.005 * s, 0.004 * s);
  });

  // ----- 등과 어깨: 승모근, 엉덩이, 등골 홈
  const trapX = neckR + (p.shoulderHalf - neckR) * 0.36;
  const trapY = lerp(y.shoulder, y.neckBase, 0.55);
  const trapR: Vec3 = [(p.shoulderHalf - neckR) * 0.68, 0.034 * s, 0.05 * s];
  const traps = [-1, 1].map((side) => ellipsoid([side * trapX, trapY, -0.022 * s], trapR));
  // 승모근은 목 위쪽에서 몸통 기본형보다 훨씬 멀리 나오므로 따로 감싸는 구로 거른다
  const trapBox = enclose([[-trapX, trapY, -0.022 * s], [trapX, trapY, -0.022 * s]], Math.max(trapR[0], trapR[1], trapR[2]));
  const gluteY = y.crotch + 0.088 * s;
  const gluteR: Vec3 = [p.hip.a * 0.4, 0.074 * s, p.hip.b * 0.4];
  const glutes = [-1, 1].map((side) => {
    const gx = side * p.hip.a * 0.42;
    return ellipsoid([gx, gluteY, surfaceZ(gluteY, Math.abs(gx), true) + gluteR[2] * 0.68], gluteR);
  });
  const spineLow = y.waist - 0.04 * s;
  const spineHigh = y.armpit;
  const spine = roundCone([0, spineLow, surfaceZ(spineLow, 0, true) - 0.001], [0, spineHigh, surfaceZ(spineHigh, 0, true) - 0.001], 0.006 * s, 0.006 * s);
  const frontTop = pecY + pecR[1];

  return (x, yy, z) => {
    let d = torso(x, yy, z);
    // 몸속 깊은 곳은 근육이 붙어도 안쪽이라 결과가 같다
    if (d < -0.03) return d;
    // 가슴·복근·엉덩이·홈은 몸통 겉에서 2 cm 안쪽으로만 나오므로 표면 가까이에서만 잰다
    if (d < 0.06 && yy > y.crotch - 0.02 && yy < y.neckBase + 0.02) {
      if (z > 0) {
        if (yy > absTop - pecR[1] && yy < frontTop + 0.02) d = smin(d, smin(pecs[0](x, yy, z), pecs[1](x, yy, z), 0.02), 0.026);
        if (Math.abs(x) < 0.09 * s && yy < absTop + 0.05 && yy > lowY - 0.08) {
          let a = abs[0](x, yy, z);
          for (let i = 1; i < abs.length; i++) a = smin(a, abs[i](x, yy, z), 0.014);
          d = smin(d, a, 0.02);
          d = smax(d, -navel(x, yy, z), 0.004);
        }
        if (yy < vTop + 0.02 && yy > vBottom - 0.02) d = smax(d, -Math.min(vLines[0](x, yy, z), vLines[1](x, yy, z)), 0.008);
      } else {
        if (yy < gluteY + 0.12) d = smin(d, Math.min(glutes[0](x, yy, z), glutes[1](x, yy, z)), 0.03);
        if (Math.abs(x) < 0.03 && yy > spineLow - 0.02 && yy < spineHigh + 0.02) d = smax(d, -spine(x, yy, z), 0.01);
      }
    }
    if (near(trapBox, x, yy, z, d + 0.04)) d = smin(d, Math.min(traps[0](x, yy, z), traps[1](x, yy, z)), 0.04);
    return d;
  };
}

/**
 * 몸 SDF. 근육 결이 보이는 디스플레이 마네킹: 몸통 근육(muscleTorso) + 달걀형 머리(옅은 눈썹뼈·코·눈자리) + 팔·다리 근육.
 * 가슴둘레와 허리둘레 차이가 클수록 근육이 더 또렷하다(차이가 작으면 부드럽게).
 */
export function bodySdf(p: BodyParams): Sdf {
  const { y, joints: j, neckR, height: H } = p;
  const s = H / 1.75;
  const tone = muscleTone(p);
  const torso = muscleTorso(p);

  // ----- 목과 머리: 달걀형(위가 넓고 턱 쪽이 좁다)에 옅은 눈썹뼈·코·눈자리
  const neck = roundCone([0, y.neckBase - 0.025, -0.012], [0, y.chin + 0.004, 0.006], neckR * 1.12, neckR * 0.95);
  const cranium = ellipsoid([0, y.headCenter + 0.011 * s, -0.006 * s], [0.0705 * s, 0.103 * s, 0.09 * s]);
  const face = ellipsoid([0, y.chin + 0.058 * s, 0.025 * s], [0.053 * s, 0.062 * s, 0.065 * s]);
  const chin = ellipsoid([0, y.chin + 0.02 * s, 0.055 * s], [0.023 * s, 0.018 * s, 0.019 * s]);
  const brow = ellipsoid([0, y.headCenter + 0.006 * s, 0.075 * s], [0.053 * s, 0.0125 * s, 0.019 * s]);
  const nose = ellipsoidAlong([0, y.headCenter - 0.028 * s, 0.084 * s], norm([0, 1, -0.35]), [0.01 * s, 0.027 * s, 0.0135 * s]);
  const sockets = [-1, 1].map((side) => ellipsoid([side * 0.0275 * s, y.headCenter - 0.005 * s, 0.089 * s], [0.0152 * s, 0.01 * s, 0.0095 * s]));

  // 팔·다리는 오른쪽 하나만 만들고 왼쪽은 거울로 비춘다(x → -x)
  const armR = armSdf(j.shoulderR, j.elbowR, j.wristR, s, tone);
  const legR = legSdf(j.hipR, j.kneeR, j.ankleR, s, tone);

  // 멀리 있는 조각은 계산하지 않는다(부드러운 합치기 반경 안에 들어올 때만)
  const headBox = enclose([[0, y.neckBase - 0.03, 0], [0, H, 0.1 * s]], 0.12 * s);
  const handDir = norm(sub(j.wristR, j.elbowR));
  const handTipR = add(j.wristR, scale(handDir, 0.18 * s));
  const armBoxR = enclose([j.shoulderR, j.elbowR, j.wristR, handTipR], 0.075);
  const legBoxR = enclose([j.hipR, j.kneeR, j.ankleR, [j.ankleR[0], 0, 0.17 * s], [j.ankleR[0], 0, -0.07 * s]], 0.11 * s);

  return (x, yy, z) => {
    let d = torso(x, yy, z);
    // 몸통 속 깊은 곳: 무엇을 더 붙여도 안쪽이다
    if (d < -0.03) return d;
    if (near(headBox, x, yy, z, d + 0.04)) {
      d = smin(d, neck(x, yy, z), 0.03);
      let h = smin(cranium(x, yy, z), face(x, yy, z), 0.04);
      h = smin(h, chin(x, yy, z), 0.02);
      h = smin(h, brow(x, yy, z), 0.014);
      h = smin(h, nose(x, yy, z), 0.012);
      h = smax(h, -Math.min(sockets[0](x, yy, z), sockets[1](x, yy, z)), 0.01);
      d = smin(d, h, 0.022);
    }
    const mx = x < 0 ? x : -x;
    if (near(armBoxR, mx, yy, z, d + 0.04)) d = smin(d, armR(mx, yy, z, d), 0.018);
    if (near(legBoxR, mx, yy, z, d + 0.07)) d = smin(d, legR(mx, yy, z, d), 0.05);
    return d;
  };
}

/** 이 치수로 Tripo 마네킹을 늘리고 줄이는 방법 */
export function mannequinFitFor(p: BodyParams): MannequinFit {
  return mannequinFit({
    height: p.height,
    chest: p.input.chest_cm / 100,
    waist: p.input.waist_cm / 100,
    hip: ellipsePerimeter(p.hip.a, p.hip.b),
    shoulderHalf: p.shoulderHalf,
    armLength: p.input.arm_length_cm / 100,
  });
}

/** 몸통 단면(그림의 치수 표시 링 위치에 쓴다) */
export function torsoSectionAt(p: BodyParams, yy: number): Station {
  return extrusion(torsoStations(p)).at(yy);
}

/** 옷 치수가 없을 때(예전에 저장한 결과 등) 몸보다 조금 크게 둔다 */
export function defaultGarment(p: BodyParams): GarmentShape {
  return {
    chestGirth: p.input.chest_cm / 100 + 0.1,
    shoulderWidth: p.shoulderHalf * 2 + 0.02,
    sleeveLength: 0.21,
    bodyLength: 0.7 * (p.height / 1.75),
  };
}

export interface TeeLayout {
  hemY: number;
  topY: number;
  /** 소매 끝(오른쪽 = 화면 왼쪽) */
  cuffR: Vec3;
  cuffL: Vec3;
  /** 소매 방향(어깨 → 소매 끝) */
  sleeveDirR: Vec3;
  chest: Ellipse;
}

export function teeLayout(p: BodyParams, g: GarmentShape): TeeLayout {
  const topY = p.y.neckBase + 0.012;
  // 티셔츠 밑단은 보통 엉덩이 아래(가랑이보다 조금 아래)까지 온다. 아주 긴 값만 무릎 위에서 멈춘다.
  const hemY = Math.max(topY - g.bodyLength, p.y.knee + 0.15);
  const fit = ellipseFromGirth(g.chestGirth, 1.5);
  const chest = { a: Math.max(fit.a, p.chest.a + 0.009), b: Math.max(fit.b, p.chest.b + 0.009) };
  const seamX = Math.max(g.shoulderWidth / 2 - 0.006, p.shoulderHalf - 0.02);
  // 소매는 어깨 관절 깊이(z)에서 시작한다(마네킹 팔은 몸 가운데보다 조금 뒤에 달려 있다)
  const seam: Vec3 = [-seamX, p.y.shoulder - 0.004, p.joints.shoulderR[2]];
  const sleeveDirR = norm([p.joints.elbowR[0] - p.joints.shoulderR[0], p.joints.elbowR[1] - p.joints.shoulderR[1], p.joints.elbowR[2] - p.joints.shoulderR[2]]);
  const cuffR = add(seam, scale(sleeveDirR, g.sleeveLength + 0.02));
  return { hemY, topY, cuffR, cuffL: mirror(cuffR), sleeveDirR, chest };
}

/** 티셔츠가 덮어야 할 몸 겉(마네킹 메시에서 잰 몸통·팔). 안쪽이 음수 */
export interface TeeCover {
  torso: Sdf;
  arm: Sdf;
}

/**
 * 티셔츠 SDF(속이 찬 덩어리. 겉면만 보이고 안쪽은 몸이 가린다).
 * cover를 주면 마네킹 몸통·팔 겉을, 없으면 SDF 몸통 근육·어깨 세모근을 몇 mm 밖에서 덮는다.
 */
export function teeSdf(p: BodyParams, g: GarmentShape, cover?: TeeCover): Sdf {
  const L = teeLayout(p, g);
  const body = extrusion(torsoStations(p));
  const ease = 0.008;
  const fitAt = (yy: number, a: number, b: number) => {
    const s = body.at(yy);
    return { a: Math.max(a, s.a + ease), b: Math.max(b, s.b + ease) };
  };
  const seamX = Math.max(g.shoulderWidth / 2 - 0.006, p.shoulderHalf - 0.02);
  const st: Station[] = [];
  const push = (yy: number, a: number, b: number, cz: number) => {
    const f = fitAt(yy, a, b);
    st.push({ y: yy, a: f.a, b: f.b, cz });
  };
  push(L.hemY, L.chest.a * 1.02, L.chest.b * 1.0, -0.008);
  push(lerp(L.hemY, p.y.waist, 0.5), L.chest.a * 1.0, L.chest.b * 0.98, -0.004);
  push(p.y.waist, L.chest.a * 0.985, L.chest.b * 0.97, 0.0);
  push(p.y.chest, L.chest.a, L.chest.b, 0.012);
  push(p.y.armpit - 0.01, L.chest.a, L.chest.b * 0.96, 0.008);
  push(p.y.shoulder - 0.008, seamX * 0.88, p.chest.b * 0.7 + 0.012, 0.0);
  push(lerp(p.y.shoulder, L.topY, 0.6), p.neckR * 2.3, p.chest.b * 0.55 + 0.01, -0.004);
  push(L.topY, p.neckR + 0.036, p.neckR + 0.03, -0.002);
  const torso = extrusion(st).sdf;

  // 소매는 어깨 솔기보다 조금 안쪽·아래에서 시작해 어깨선이 부풀지 않게 한다.
  const seamR: Vec3 = [-(seamX - 0.022), p.y.shoulder - 0.02, p.joints.shoulderR[2]];
  const seamL = mirror(seamR);
  // 위팔 굵기(이두·삼두 포함)보다 넉넉하게: 꼭 맞는 옷에서도 팔이 소매 밖으로 나오지 않게
  const armTop = 0.05;
  const t = clamp((g.sleeveLength + 0.02) / p.upperArm, 0, 1);
  const armAtCuff = lerp(0.05, 0.04, t);
  const sleeveR = roundCone(seamR, L.cuffR, armTop + 0.018, armAtCuff + 0.024);
  const sleeveL = roundCone(seamL, L.cuffL, armTop + 0.018, armAtCuff + 0.024);
  const dirR = L.sleeveDirR;
  const dirL = mirror(dirR);
  const neckHole = roundCone([0, p.y.neckBase - 0.035, -0.006], [0, p.y.chin + 0.04, 0.01], p.neckR + 0.013, p.neckR + 0.013);
  const scoop = ellipsoid([0, p.y.neckBase - 0.014, p.neckR + 0.034], [0.07, 0.034, 0.06]);

  const sleeveBoxR = enclose([seamR, L.cuffR], 0.1);
  const sleeveBoxL = enclose([seamL, L.cuffL], 0.1);
  // 근육(가슴·복근·승모근·어깨 세모근)이 옷 밖으로 나오지 않게, 몸 겉에서 6 mm 떨어진 면을 함께 덮는다
  const muscles = cover ? null : muscleTorso(p);
  const deltoid = cover ? null : deltoidOf(p.joints.shoulderR, muscleTone(p));

  return (x, yy, z) => {
    let d = torso(x, yy, z);
    let m: number;
    if (cover) {
      m = cover.torso(x, yy, z);
    } else {
      m = (muscles as Sdf)(x, yy, z);
      if (yy > p.y.armpit - 0.08) m = Math.min(m, (deltoid as Sdf)(x < 0 ? x : -x, yy, z));
    }
    d = smin(d, m - 0.006, 0.012);
    const cuff = x < 0 ? L.cuffR : L.cuffL;
    const dir = x < 0 ? dirR : dirL;
    if (near(x < 0 ? sleeveBoxR : sleeveBoxL, x, yy, z, d + 0.04)) {
      // 소매는 소매 끝에서 팔과 수직으로 자른다(둥근 끝이 팔을 덮지 않게). 마네킹 팔 겉도 7 mm 밖에서 덮는다
      let sl = x < 0 ? sleeveR(x, yy, z) : sleeveL(x, yy, z);
      if (cover) sl = Math.min(sl, cover.arm(x, yy, z) - 0.007);
      const sleeve = smax(
        sl,
        (x - cuff[0]) * dir[0] + (yy - cuff[1]) * dir[1] + (z - cuff[2]) * dir[2],
        0.004,
      );
      d = smin(d, sleeve, 0.024);
    }
    d = smax(d, L.hemY - yy, 0.006);
    if (yy > p.y.neckBase - 0.08) {
      d = smax(d, -neckHole(x, yy, z), 0.008);
      d = smax(d, -scoop(x, yy, z), 0.01);
    }
    // 밑단 쪽 세로 주름(아주 옅게). 몸과의 간격(8 mm)보다 작게 둔다.
    const fold = smooth(clamp((L.hemY + 0.24 - yy) / 0.22, 0, 1));
    if (fold > 0) d -= 0.0026 * fold * Math.sin(Math.atan2(z, x) * 13 + yy * 9);
    return d;
  };
}

/** 몸 메시(워커에서 부른다). cell은 격자 간격(m). */
export function buildBodyMesh(p: BodyParams, cell = 0.008): MeshData {
  const reach = Math.max(Math.abs(p.joints.wristR[0]), p.shoulderHalf) + 0.12;
  return surfaceNets(bodySdf(p), [-reach, -0.01, -0.18], [reach, p.height + 0.012, 0.21], cell);
}

export function buildTeeMesh(p: BodyParams, g: GarmentShape, cell = 0.008, cover?: TeeCover): MeshData {
  const L = teeLayout(p, g);
  const reach = Math.max(Math.abs(L.cuffR[0]) + 0.1, L.chest.a + 0.05);
  return surfaceNets(teeSdf(p, g, cover), [-reach, L.hemY - 0.012, -0.2], [reach, p.y.chin + 0.01, 0.22], cell);
}
