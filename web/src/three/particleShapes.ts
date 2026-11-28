/**
 * 반짝이는 점구름이 처음에 이루는 모양(순수 계산, three.js 없이).
 * - 사방 출발 점: 화면 바깥 먼 곳(체형 생성·가상 착용에서 입자가 날아오는 곳)
 * - 사진 점 격자: 정면 사진을 이루는 점(실사 변환 단계)
 */

export type Rand = () => number;
export type Vec3 = [number, number, number];

/** 같은 씨앗이면 같은 순서로 나오는 0~1 난수 */
export function seeded(seed: number): Rand {
  let s = Math.floor(Math.abs(seed)) % 2147483647 || 1;
  return () => {
    s = (s * 16807) % 2147483647;
    return (s - 1) / 2147483646;
  };
}

/**
 * 사방 먼 곳의 출발 점. 도착 점마다 가운데(center)에서 그 점 쪽으로 뻗은 방향을 조금씩(spread) 흩뜨린 뒤
 * rMin~rMax만큼 떨어진 곳에 둔다. 입자가 바깥 사방(위·아래·옆·앞뒤)에서 제자리로 곧장 날아든다(몸을 가로질러 엉키지 않게).
 */
export function outerStarts(targets: Float32Array, center: Vec3, rMin: number, rMax: number, spread: number, rand: Rand): Float32Array {
  const out = new Float32Array(targets.length);
  for (let i = 0; i < targets.length / 3; i++) {
    let dx = targets[i * 3] - center[0];
    let dy = targets[i * 3 + 1] - center[1];
    let dz = targets[i * 3 + 2] - center[2];
    let l = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;
    dx /= l;
    dy /= l;
    dz /= l;
    let rx = 0;
    let ry = 0;
    let rz = 0;
    let r2 = 2;
    while (r2 > 1 || r2 < 1e-6) {
      rx = rand() * 2 - 1;
      ry = rand() * 2 - 1;
      rz = rand() * 2 - 1;
      r2 = rx * rx + ry * ry + rz * rz;
    }
    const rl = Math.sqrt(r2);
    dx += (rx / rl) * spread;
    dy += (ry / rl) * spread;
    dz += (rz / rl) * spread;
    l = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;
    const r = rMin + (rMax - rMin) * rand();
    out[i * 3] = center[0] + (dx / l) * r;
    out[i * 3 + 1] = center[1] + (dy / l) * r;
    out[i * 3 + 2] = center[2] + (dz / l) * r;
  }
  return out;
}

/** 점마다 0~1 난수 세 개(시작 시각·크기·반짝임 박자) */
export function seeds(count: number, rand: Rand): Float32Array {
  const out = new Float32Array(count * 3);
  for (let i = 0; i < out.length; i++) out[i] = rand();
  return out;
}

/**
 * 사진 한 장을 이루는 점 격자(cols × rows). 위치는 카드 가운데 기준(가로 w, 세로 h), uv는 사진 좌표.
 * noise는 흩어질 때의 이동량: 대부분 가까이, 몇몇은 멀리.
 */
export function photoGrid(
  cols: number,
  rows: number,
  w: number,
  h: number,
  spread: Vec3,
  rand: Rand,
): { positions: Float32Array; uvs: Float32Array; noise: Float32Array } {
  const n = cols * rows;
  const positions = new Float32Array(n * 3);
  const uvs = new Float32Array(n * 2);
  const noise = new Float32Array(n * 3);
  const gauss = () => (rand() + rand() + rand() - 1.5) / 1.5;
  for (let j = 0; j < rows; j++) {
    for (let i = 0; i < cols; i++) {
      const k = j * cols + i;
      const u = (i + 0.5) / cols;
      const v = (j + 0.5) / rows;
      positions[k * 3] = (u - 0.5) * w;
      positions[k * 3 + 1] = (v - 0.5) * h;
      positions[k * 3 + 2] = 0;
      uvs[k * 2] = u;
      uvs[k * 2 + 1] = v;
      const far = rand() < 0.06 ? 2.2 : 1;
      noise[k * 3] = gauss() * spread[0] * far;
      noise[k * 3 + 1] = gauss() * spread[1] * far;
      noise[k * 3 + 2] = gauss() * spread[2] * far;
    }
  }
  return { positions, uvs, noise };
}
