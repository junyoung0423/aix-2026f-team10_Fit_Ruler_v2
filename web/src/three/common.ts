import * as THREE from 'three';
import type { MeshData } from './surfaceNets';

/** MeshData → three.js 도형. withUv면 몸 둘레를 따라 감는 원통 좌표(니트 결 텍스처용)를 붙인다. */
export function toGeometry(mesh: MeshData, withUv = false): THREE.BufferGeometry {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(mesh.positions.slice(), 3));
  geometry.setAttribute('normal', new THREE.BufferAttribute(mesh.normals.slice(), 3));
  geometry.setIndex(new THREE.BufferAttribute(mesh.indices.slice(), 1));
  if (withUv) {
    const count = mesh.positions.length / 3;
    const uv = new Float32Array(count * 2);
    for (let i = 0; i < count; i++) {
      const x = mesh.positions[i * 3];
      const y = mesh.positions[i * 3 + 1];
      const z = mesh.positions[i * 3 + 2];
      uv[i * 2] = ((Math.atan2(x, z) / (Math.PI * 2) + 0.5) * 24) % 24;
      uv[i * 2 + 1] = y * 22;
    }
    geometry.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
  }
  geometry.computeBoundingSphere();
  return geometry;
}

/** 표면 위 점을 넓이에 비례해 고르게 뽑는다(빛 먼지·반짝임 위치) */
export function samplePoints(mesh: MeshData, count: number, seed = 1): { positions: Float32Array; normals: Float32Array } {
  const { positions: p, normals: n, indices } = mesh;
  const triCount = indices.length / 3;
  const cumulative = new Float64Array(triCount);
  let total = 0;
  for (let t = 0; t < triCount; t++) {
    const a = indices[t * 3] * 3;
    const b = indices[t * 3 + 1] * 3;
    const c = indices[t * 3 + 2] * 3;
    const ux = p[b] - p[a];
    const uy = p[b + 1] - p[a + 1];
    const uz = p[b + 2] - p[a + 2];
    const vx = p[c] - p[a];
    const vy = p[c + 1] - p[a + 1];
    const vz = p[c + 2] - p[a + 2];
    const cx = uy * vz - uz * vy;
    const cy = uz * vx - ux * vz;
    const cz = ux * vy - uy * vx;
    total += Math.sqrt(cx * cx + cy * cy + cz * cz) / 2;
    cumulative[t] = total;
  }
  let s = seed;
  const rand = () => {
    s = (s * 16807) % 2147483647;
    return s / 2147483647;
  };
  const outP = new Float32Array(count * 3);
  const outN = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    const r = rand() * total;
    let lo = 0;
    let hi = triCount - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (cumulative[mid] < r) lo = mid + 1;
      else hi = mid;
    }
    let u = rand();
    let v = rand();
    if (u + v > 1) {
      u = 1 - u;
      v = 1 - v;
    }
    const w = 1 - u - v;
    const a = indices[lo * 3] * 3;
    const b = indices[lo * 3 + 1] * 3;
    const c = indices[lo * 3 + 2] * 3;
    for (let k = 0; k < 3; k++) {
      outP[i * 3 + k] = p[a + k] * w + p[b + k] * u + p[c + k] * v;
      outN[i * 3 + k] = n[a + k] * w + n[b + k] * u + n[c + k] * v;
    }
  }
  return { positions: outP, normals: outN };
}

export const easeInOut = (t: number) => (t <= 0 ? 0 : t >= 1 ? 1 : t * t * (3 - 2 * t));
export const easeOut = (t: number) => (t <= 0 ? 0 : t >= 1 ? 1 : 1 - (1 - t) ** 3);
export const clamp01 = (t: number) => Math.min(1, Math.max(0, t));

/** 값이 목표로 부드럽게 다가간다(프레임 속도와 무관) */
export const approach = (value: number, target: number, rate: number, dt: number) =>
  value + (target - value) * (1 - Math.exp(-rate * dt));

/** 장면의 도형·재질·텍스처를 모두 정리한다 */
export function disposeObject(root: THREE.Object3D) {
  root.traverse((object) => {
    const mesh = object as THREE.Mesh;
    mesh.geometry?.dispose();
    const material = mesh.material as THREE.Material | THREE.Material[] | undefined;
    const list = Array.isArray(material) ? material : material ? [material] : [];
    for (const m of list) {
      for (const value of Object.values(m)) if (value instanceof THREE.Texture) value.dispose();
      m.dispose();
    }
  });
}

/**
 * 이름표를 화면 좌표에 둔다. 왼쪽 이름표(is-left)는 오른쪽 끝이 그 점에 오도록.
 * 캔버스(width) 밖으로 나가면 안쪽으로 당겨 잘리지 않게 한다.
 */
export function placeLabel(el: HTMLElement, x: number, y: number, width: number) {
  const left = el.classList.contains('is-left');
  const w = el.offsetWidth;
  const pad = 4;
  const px = left ? Math.max(x, w + pad) : Math.min(x, width - w - pad);
  el.style.transform = left ? `translate(${(px - w).toFixed(1)}px, ${y.toFixed(1)}px)` : `translate(${px.toFixed(1)}px, ${y.toFixed(1)}px)`;
}

/** 3D 점을 캔버스 위 픽셀 좌표로. 카메라 뒤면 null */
export function toScreen(point: THREE.Vector3, camera: THREE.Camera, width: number, height: number): { x: number; y: number } | null {
  const v = point.clone().project(camera);
  if (v.z > 1) return null;
  return { x: (v.x * 0.5 + 0.5) * width, y: (-v.y * 0.5 + 0.5) * height };
}
