// Tripo 마네킹(FBX) → 웹용 GLB(src/three/assets/mannequin.glb)
// 사용(web 폴더에서): node scripts/convert-mannequin.mjs <tripo.fbx> src/three/assets/mannequin.glb
//
// 하는 일
// 1. FBX의 메시 하나를 읽어 같은 자리 꼭짓점을 합친다(용접). 텍스처·재질은 쓰지 않는다(화면이 흰 무광 재질을 쓴다).
// 2. 키 1로 맞춘다(발바닥 y=0, 위 +y, 얼굴 +z). Tripo 내보내기는 이미 Y가 위이고 가운데 정렬되어 있다.
// 3. 높이별 단면(평면과 삼각형의 교선 고리)으로 기준 치수를 잰다:
//    가랑이·엉덩이·허리·겨드랑이·가슴·어깨 높이, 가슴·허리·엉덩이 둘레, 견봉 반폭, 팔 축·팔길이, 높이별 몸통 반폭과 팔 안쪽.
// 4. 꼭짓점별 팔 가중치(_ARM)를 정한다: 겨드랑이 아래로 자르면 따로 떨어지는 덩어리 = 팔(1), 어깨는 서서히.
// 5. GLB로 쓴다(위치는 KHR_mesh_quantization 16비트, 기준 치수는 extras.fitRuler).
//    출력에 찍힌 기준 치수는 src/three/mannequin.ts의 MANNEQUIN에 옮긴다(mannequin.test.ts가 둘이 같은지 확인한다).
import fs from 'node:fs';
import path from 'node:path';
import * as THREE from 'three';
import { FBXLoader } from 'three/examples/jsm/loaders/FBXLoader.js';

const [fbxPath, outPath] = process.argv.slice(2);
if (!fbxPath || !outPath) {
  console.error('사용: node scripts/convert-mannequin.mjs <tripo.fbx> <out.glb>');
  process.exit(1);
}
// Node에는 이미지 로더가 없다(FBX 안 텍스처는 읽지 않는다)
globalThis.window = { URL: { createObjectURL: () => '' } };
THREE.TextureLoader.prototype.load = () => new THREE.Texture();
const file = fs.readFileSync(fbxPath);
const group = new FBXLoader().parse(file.buffer.slice(file.byteOffset, file.byteOffset + file.byteLength), '');
let geometry = null;
group.traverse((o) => {
  if (o.isMesh && !geometry) geometry = o.geometry.index ? o.geometry.toNonIndexed() : o.geometry;
});
if (!geometry) throw new Error('FBX에 메시가 없습니다.');
const srcPos = geometry.attributes.position.array;
let minY = Infinity;
let maxY = -Infinity;
for (let i = 1; i < srcPos.length; i += 3) { minY = Math.min(minY, srcPos[i]); maxY = Math.max(maxY, srcPos[i]); }
const height = maxY - minY;
// 같은 자리 꼭짓점 합치기(키의 0.0005% 격자)
const step = height * 5e-6;
const weldMap = new Map();
const welded = [];
const T = [];
for (let t = 0; t < srcPos.length / 9; t++) {
  const tri = [];
  for (let k = 0; k < 3; k++) {
    const i = t * 3 + k;
    const key = `${Math.round(srcPos[i * 3] / step)},${Math.round(srcPos[i * 3 + 1] / step)},${Math.round(srcPos[i * 3 + 2] / step)}`;
    let id = weldMap.get(key);
    if (id === undefined) {
      id = welded.length / 3;
      weldMap.set(key, id);
      welded.push(srcPos[i * 3], srcPos[i * 3 + 1], srcPos[i * 3 + 2]);
    }
    tri.push(id);
  }
  if (tri[0] !== tri[1] && tri[1] !== tri[2] && tri[0] !== tri[2]) T.push(...tri);
}
const n = welded.length / 3;
if (n > 65535) throw new Error('꼭짓점이 65535개를 넘습니다(16비트 인덱스).');
// 키 1로(발바닥 y=0)
const P = new Float64Array(welded.length);
for (let i = 0; i < n; i++) {
  P[i * 3] = welded[i * 3] / height;
  P[i * 3 + 1] = (welded[i * 3 + 1] - minY) / height;
  P[i * 3 + 2] = welded[i * 3 + 2] / height;
}

// ---------- 단면: 평면 y = y0와 삼각형의 교선을 닫힌 고리로 묶는다(고리 하나 = 몸통·팔·다리 단면 하나)
function sliceLoops(y0) {
  const point = new Map();
  const adj = new Map();
  const keyOf = (a, b) => (a < b ? a * 65536 + b : b * 65536 + a);
  const cut = (a, b) => {
    const ya = P[a * 3 + 1], yb = P[b * 3 + 1];
    const f = (y0 - ya) / (yb - ya);
    return [P[a * 3] + (P[b * 3] - P[a * 3]) * f, P[a * 3 + 2] + (P[b * 3 + 2] - P[a * 3 + 2]) * f];
  };
  for (let t = 0; t < T.length; t += 3) {
    const ids = [T[t], T[t + 1], T[t + 2]];
    const keys = [];
    for (let k = 0; k < 3; k++) {
      const a = ids[k], b = ids[(k + 1) % 3];
      if ((P[a * 3 + 1] >= y0) === (P[b * 3 + 1] >= y0)) continue;
      const key = keyOf(a, b);
      if (!point.has(key)) point.set(key, cut(a, b));
      keys.push(key);
    }
    if (keys.length !== 2) continue;
    for (const [u, v] of [[keys[0], keys[1]], [keys[1], keys[0]]]) {
      if (!adj.has(u)) adj.set(u, []);
      adj.get(u).push(v);
    }
  }
  const seen = new Set();
  const loops = [];
  for (const start of point.keys()) {
    if (seen.has(start)) continue;
    const stack = [start];
    seen.add(start);
    const pts = [];
    while (stack.length) {
      const k = stack.pop();
      pts.push(point.get(k));
      for (const m of adj.get(k) ?? []) if (!seen.has(m)) { seen.add(m); stack.push(m); }
    }
    if (pts.length < 6) continue;
    let x0 = Infinity, x1 = -Infinity, z0 = Infinity, z1 = -Infinity;
    for (const [x, z] of pts) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); z0 = Math.min(z0, z); z1 = Math.max(z1, z); }
    loops.push({ pts, x0, x1, cx: (x0 + x1) / 2, z0, z1 });
  }
  return loops;
}
const slice = (y0) => sliceLoops(y0).flatMap((l) => l.pts);
function hullPerimeter(pts) {
  const s = [...pts].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lo = [], up = [];
  for (const p of s) { while (lo.length >= 2 && cross(lo[lo.length - 2], lo[lo.length - 1], p) <= 0) lo.pop(); lo.push(p); }
  for (let i = s.length - 1; i >= 0; i--) { const p = s[i]; while (up.length >= 2 && cross(up[up.length - 2], up[up.length - 1], p) <= 0) up.pop(); up.push(p); }
  const h = lo.slice(0, -1).concat(up.slice(0, -1));
  let per = 0;
  for (let i = 0; i < h.length; i++) { const a = h[i], b = h[(i + 1) % h.length]; per += Math.hypot(a[0] - b[0], a[1] - b[1]); }
  return per;
}
const centerOf = (isl) => isl.filter((i) => i.x0 <= 0 && i.x1 >= 0).sort((a, b) => b.pts.length - a.pts.length)[0];
const rightArmOf = (isl, center) => isl.filter((i) => center && i.x1 < center.x0 && i.cx < -0.08);

// ---------- 높이 기준
const rows = [];
for (let y = 0.3; y <= 0.95 + 1e-9; y += 0.0025) {
  const isl = sliceLoops(y);
  const center = centerOf(isl);
  rows.push({ y, isl, center, arms: rightArmOf(isl, center) });
}
// 가랑이: 가운데(x=0)를 지나는 단면이 처음 생기는 높이
const crotch = rows.find((r) => r.center && r.y > 0.4).y;
// 겨드랑이: 가랑이 위에서 팔 단면이 몸통과 따로 있는 가장 높은 곳
const armRows = rows.filter((r) => r.y > crotch + 0.05 && r.y < 0.8 && r.arms.length > 0);
let armpit = crotch;
for (const r of rows) { if (r.y <= crotch + 0.05 || r.y > 0.8) continue; if (r.arms.length > 0) armpit = r.y; else break; }
const girthAt = (r) => hullPerimeter(r.center.pts);
const between = (lo, hi) => rows.filter((r) => r.center && r.y >= lo && r.y <= hi);
const waistRow = between(crotch + 0.1, armpit - 0.03).reduce((a, b) => (girthAt(b) < girthAt(a) ? b : a));
const hipRow = between(crotch + 0.005, waistRow.y - 0.06).reduce((a, b) => (girthAt(b) > girthAt(a) ? b : a));
// 가슴: 겨드랑이 바로 위. 팔이 붙어 있으므로 겨드랑이 안쪽 x보다 안쪽 점만 몸통으로 본다
const armpitRow = rows.reduce((a, b) => (Math.abs(b.y - (armpit - 0.005)) < Math.abs(a.y - (armpit - 0.005)) ? b : a));
const armInnerX = Math.max(...armpitRow.arms.map((a) => a.x1)); // 음수(오른쪽 팔 안쪽 끝)
const torsoCut = Math.abs(armInnerX) - 0.002;
const chestY = armpit + 0.025;
const chestPts = slice(chestY).filter((p) => Math.abs(p[0]) < torsoCut);
const chestGirth = hullPerimeter(chestPts);
let chestZ0 = Infinity, chestZ1 = -Infinity;
for (const p of chestPts) if (Math.abs(p[0]) < 0.02) { chestZ0 = Math.min(chestZ0, p[1]); chestZ1 = Math.max(chestZ1, p[1]); }
// 어깨(견봉): 0.81 높이의 가장 바깥에서 조금 안쪽
const shoulderY = 0.81;
const shoulderRow = sliceLoops(shoulderY);
const shoulderHalf = Math.max(...shoulderRow.map((i) => Math.max(Math.abs(i.x0), Math.abs(i.x1)))) - 0.008;
// 목: 어깨 위에서 가장 가는 곳 / 턱: 그 위에서 머리가 넓어지기 시작하는 곳
const neckRows = rows.filter((r) => r.center && r.y > 0.84 && r.y < 0.92);
const neckRow = neckRows.reduce((a, b) => (b.center.x1 - b.center.x0 < a.center.x1 - a.center.x0 ? b : a));

// 팔 축: 겨드랑이 아래 ~ 손목(팔 단면이 가장 가는 곳)
const armSamples = armRows.filter((r) => r.y < armpit - 0.005).map((r) => {
  const a = r.arms.reduce((p, q) => (q.pts.length > p.pts.length ? q : p));
  return { y: r.y, cx: a.cx, width: a.x1 - a.x0, cz: (a.z0 + a.z1) / 2 };
});
const wrist = armSamples.filter((s) => s.y > crotch && s.y < crotch + 0.12).reduce((a, b) => (b.width < a.width ? b : a));
const fit = (pts) => {
  // x = a + b*y 최소제곱
  let sy = 0, sx = 0, syy = 0, sxy = 0;
  for (const [y, x] of pts) { sy += y; sx += x; syy += y * y; sxy += x * y; }
  const m = pts.length;
  const b = (m * sxy - sy * sx) / (m * syy - sy * sy);
  return { a: (sx - b * sy) / m, b };
};
const upper = armSamples.filter((s) => s.y >= wrist.y && s.y < armpit - 0.005);
const axX = fit(upper.map((s) => [s.y, s.cx]));
const axZ = fit(upper.map((s) => [s.y, s.cz]));
const shoulderJointY = shoulderY - 0.022;
const joint = (y) => [axX.a + axX.b * y, y, axZ.a + axZ.b * y];
const shoulderJoint = joint(shoulderJointY);

const wristJoint = joint(wrist.y);
const armLength = Math.hypot(-shoulderHalf - wristJoint[0], shoulderY - wristJoint[1], wristJoint[2]);
let fingertip = wrist.y;
for (const r of rows) if (r.y < wrist.y && r.isl.some((i) => i.cx < -0.11) && r.y < fingertip) fingertip = r.y;

// ---------- 꼭짓점별 팔 가중치(0 몸통 ~ 1 팔)
// 겨드랑이 아래로 자르면 팔(손 포함)은 몸통·다리와 이어지지 않은 덩어리가 된다 → 그 덩어리는 팔(1)
// 겨드랑이 위(어깨)는 겨드랑이 안쪽 끝 → 견봉 안쪽을 잇는 선보다 바깥을 팔 쪽으로 서서히
const smooth = (e0, e1, x) => { const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0))); return t * t * (3 - 2 * t); };
const nbr = Array.from({ length: n }, () => []);
for (let t = 0; t < T.length; t += 3) {
  const a = T[t], b = T[t + 1], c = T[t + 2];
  nbr[a].push(b, c); nbr[b].push(a, c); nbr[c].push(a, b);
}
const cutY = armpit - 0.004;
const comp = new Int32Array(n).fill(-1);
const comps = [];
for (let s0 = 0; s0 < n; s0++) {
  if (comp[s0] !== -1 || P[s0 * 3 + 1] >= cutY) continue;
  const id = comps.length;
  const stack = [s0];
  comp[s0] = id;
  let count = 0, sx = 0, ymin = Infinity;
  while (stack.length) {
    const v = stack.pop();
    count++; sx += P[v * 3]; ymin = Math.min(ymin, P[v * 3 + 1]);
    for (const m of nbr[v]) if (comp[m] === -1 && P[m * 3 + 1] < cutY) { comp[m] = id; stack.push(m); }
  }
  comps.push({ id, count, cx: sx / count, ymin });
}
// 다리까지 이어진 가장 큰 덩어리는 몸통, 바닥까지 내려가지 않고 옆에 있는 덩어리는 팔
const armComps = new Set(comps.filter((c) => Math.abs(c.cx) > 0.08 && c.ymin > 0.2).map((c) => c.id));
console.log('components below armpit:', comps.map((c) => `${c.count}@x${c.cx.toFixed(3)} ymin${c.ymin.toFixed(2)}`).join(' | '));
const arm = new Uint8Array(n);
const boundTop = shoulderHalf - 0.035;
for (let i = 0; i < n; i++) {
  const x = Math.abs(P[i * 3]), y = P[i * 3 + 1];
  let w = 0;
  if (y < cutY) {
    w = armComps.has(comp[i]) ? 1 : 0;
  } else if (y < shoulderY + 0.03) {
    const k = Math.min(1, (y - cutY) / (shoulderY + 0.01 - cutY));
    const b = torsoCut + (boundTop - torsoCut) * k;
    w = smooth(b - 0.006, b + 0.014, x);
    if (y > shoulderY) w *= 1 - smooth(shoulderY, shoulderY + 0.03, y);
  }
  arm[i] = Math.round(w * 255);
}
// 어깨 관절 z는 겨드랑이 바로 아래 팔 단면 가운데(위로 길게 늘이면 뒤로 기울어 보인다)
const armLoop = rows.reduce((a, b) => (Math.abs(b.y - (armpit - 0.01)) < Math.abs(a.y - (armpit - 0.01)) ? b : a)).arms[0];
if (armLoop) shoulderJoint[2] = (armLoop.z0 + armLoop.z1) / 2;
let armCount = 0;
for (let i = 0; i < n; i++) if (arm[i] > 127) armCount++;

// 높이별 몸통 반폭과 팔 안쪽 x(겨드랑이 아래): 몸이 굵어지면 팔을 그만큼 밖으로 민다
const clearance = [];
for (let y = Math.ceil(crotch * 100) / 100; y <= armpit - 0.005 + 1e-9; y += 0.01) {
  const r = rows.reduce((a, b) => (Math.abs(b.y - y) < Math.abs(a.y - y) ? b : a));
  if (!r.center || !r.arms.length) continue;
  const inner = Math.max(...r.arms.map((a) => a.x1));
  clearance.push([+r.y.toFixed(3), +Math.max(Math.abs(r.center.x0), r.center.x1).toFixed(4), +Math.abs(inner).toFixed(4)]);
}
const meta = {
  source: `${path.basename(fbxPath)} (Tripo AI), welded; height normalized to 1, +y up, +z front, feet at y=0`,
  vertices: n,
  triangles: T.length / 3,
  landmarks: {
    crotch: +crotch.toFixed(4),
    hip: +hipRow.y.toFixed(4),
    waist: +waistRow.y.toFixed(4),
    armpit: +armpit.toFixed(4),
    chest: +chestY.toFixed(4),
    shoulder: shoulderY,
    neck: +neckRow.y.toFixed(4),
    wrist: +wrist.y.toFixed(4),
    fingertip: +fingertip.toFixed(4),
  },
  girth: { chest: +chestGirth.toFixed(4), waist: +girthAt(waistRow).toFixed(4), hip: +girthAt(hipRow).toFixed(4) },
  chestDepth: +(chestZ1 - chestZ0).toFixed(4),
  torsoCut: +torsoCut.toFixed(4),
  shoulderHalf: +shoulderHalf.toFixed(4),
  armLength: +armLength.toFixed(4),
  shoulderJoint: shoulderJoint.map((v) => +v.toFixed(4)),
  wristJoint: wristJoint.map((v) => +v.toFixed(4)),
  armAxis: { x: [+axX.a.toFixed(5), +axX.b.toFixed(5)], z: [+axZ.a.toFixed(5), +axZ.b.toFixed(5)] },
  clearance,
};
console.log(JSON.stringify(meta, null, 1));
console.log('arm vertices (w>0.5):', armCount, 'of', n);
console.log('at 1.75 m: chest', (chestGirth * 1.75).toFixed(3), 'waist', (girthAt(waistRow) * 1.75).toFixed(3), 'hip', (girthAt(hipRow) * 1.75).toFixed(3), 'shoulder width', (shoulderHalf * 2 * 1.75).toFixed(3), 'arm', (armLength * 1.75).toFixed(3));

// ---------- GLB(KHR_mesh_quantization: 위치는 0~1 정규화 uint16, 노드 변환으로 되돌린다)
const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
for (let i = 0; i < n; i++) for (let a = 0; a < 3; a++) { min[a] = Math.min(min[a], P[i * 3 + a]); max[a] = Math.max(max[a], P[i * 3 + a]); }
const span = [0, 1, 2].map((a) => max[a] - min[a]);
const qpos = new Uint16Array(n * 4); // 4바이트 정렬을 위해 한 칸 비운다
for (let i = 0; i < n; i++) for (let a = 0; a < 3; a++) qpos[i * 4 + a] = Math.round(((P[i * 3 + a] - min[a]) / span[a]) * 65535);
const idx16 = new Uint16Array(T);
const pad4 = (b) => (b + 3) & ~3;
const posBytes = qpos.byteLength;
const armBytes = pad4(n);
const idxBytes = pad4(idx16.byteLength);
const bin = new Uint8Array(posBytes + armBytes + idxBytes);
bin.set(new Uint8Array(qpos.buffer), 0);
bin.set(arm, posBytes);
bin.set(new Uint8Array(idx16.buffer), posBytes + armBytes);
const gltf = {
  asset: { version: '2.0', generator: 'fit_ruler convert.mjs' },
  extensionsUsed: ['KHR_mesh_quantization'],
  extensionsRequired: ['KHR_mesh_quantization'],
  scene: 0,
  scenes: [{ nodes: [0] }],
  nodes: [{ name: 'mannequin', mesh: 0, translation: min, scale: span }],
  meshes: [{ name: 'mannequin', primitives: [{ attributes: { POSITION: 0, _ARM: 1 }, indices: 2, mode: 4 }] }],
  buffers: [{ byteLength: bin.byteLength }],
  bufferViews: [
    { buffer: 0, byteOffset: 0, byteLength: posBytes, byteStride: 8, target: 34962 },
    { buffer: 0, byteOffset: posBytes, byteLength: n, target: 34962 },
    { buffer: 0, byteOffset: posBytes + armBytes, byteLength: idx16.byteLength, target: 34963 },
  ],
  accessors: [
    { bufferView: 0, componentType: 5123, normalized: true, count: n, type: 'VEC3', min: [0, 0, 0], max: [1, 1, 1] },
    { bufferView: 1, componentType: 5121, normalized: true, count: n, type: 'SCALAR' },
    { bufferView: 2, componentType: 5123, count: idx16.length, type: 'SCALAR' },
  ],
  extras: { fitRuler: meta },
};
let json = new TextEncoder().encode(JSON.stringify(gltf));
const jsonPad = pad4(json.byteLength);
const jsonChunk = new Uint8Array(jsonPad).fill(0x20);
jsonChunk.set(json);
const total = 12 + 8 + jsonPad + 8 + bin.byteLength;
const out = new Uint8Array(total);
const dv = new DataView(out.buffer);
dv.setUint32(0, 0x46546c67, true);
dv.setUint32(4, 2, true);
dv.setUint32(8, total, true);
dv.setUint32(12, jsonPad, true);
dv.setUint32(16, 0x4e4f534a, true);
out.set(jsonChunk, 20);
dv.setUint32(20 + jsonPad, bin.byteLength, true);
dv.setUint32(24 + jsonPad, 0x004e4942, true);
out.set(bin, 28 + jsonPad);
fs.writeFileSync(outPath, out);
console.log('wrote', outPath, total, 'bytes');
