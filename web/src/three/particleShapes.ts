/**
 * 반짝이는 점구름이 처음에 이루는 모양(순수 계산, three.js 없이).
 * - 사방 출발 점: 화면 바깥 먼 곳(체형 생성·가상 착용·3D 촬영에서 입자가 날아오는 곳)
 * - 사진 틀: 3D 촬영의 뷰파인더(네 귀퉁이 꺾쇠 + 얇은 테두리)
 * - 사진 점 격자와 흩어질 자리: 정면 사진을 이루는 점과, 실사 변환에서 그 점이 사방으로 흩어졌다 돌아오는 자리
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
 */
export function photoGrid(cols: number, rows: number, w: number, h: number): { positions: Float32Array; uvs: Float32Array } {
  const n = cols * rows;
  const positions = new Float32Array(n * 3);
  const uvs = new Float32Array(n * 2);
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
    }
  }
  return { positions, uvs };
}

/**
 * 카드 위 점이 사방으로 흩어질 자리(도착 점에서 뺀 이동량). 카드 가운데에서 그 점 쪽으로 뻗은 방향으로 rMin~rMax만큼 멀리 간다.
 * 앞뒤(z) 이동은 zScale배로 줄여, 카메라 쪽으로 너무 가까이 날아와 화면을 가리지 않게 한다.
 */
export function scatterOffsets(targets: Float32Array, rMin: number, rMax: number, spread: number, zScale: number, rand: Rand): Float32Array {
  const far = outerStarts(targets, [0, 0, 0], rMin, rMax, spread, rand);
  const out = new Float32Array(targets.length);
  for (let i = 0; i < targets.length; i += 3) {
    out[i] = far[i] - targets[i];
    out[i + 1] = far[i + 1] - targets[i + 1];
    out[i + 2] = (far[i + 2] - targets[i + 2]) * zScale;
  }
  return out;
}

/**
 * 사진 틀(뷰파인더) 점: 카드 가장자리(가로 w, 세로 h, 가운데 기준)에 count개.
 * 70 %는 네 귀퉁이 꺾쇠(팔 길이는 짧은 변의 22 %)에 촘촘하고 밝게, 나머지는 둘레 전체에 성기고 어둡게 놓는다.
 * 밝기는 seeds의 세 번째 값(반짝이 재질의 밝기)으로 정한다: 꺾쇠 0.55~1, 테두리 0~0.3.
 */
export function photoFrame(w: number, h: number, count: number, rand: Rand): { positions: Float32Array; seeds: Float32Array } {
  const positions = new Float32Array(count * 3);
  const sd = new Float32Array(count * 3);
  const arm = Math.min(w, h) * 0.22;
  const nBracket = Math.round(count * 0.7);
  const jitter = 0.0025;
  for (let i = 0; i < count; i++) {
    const bracket = i < nBracket;
    let x: number;
    let y: number;
    if (bracket) {
      const corner = Math.floor(rand() * 4);
      const sx = corner % 2 === 0 ? -1 : 1;
      const sy = corner < 2 ? 1 : -1;
      const along = rand() * arm;
      if (rand() < 0.5) {
        x = sx * (w / 2 - along);
        y = (sy * h) / 2;
      } else {
        x = (sx * w) / 2;
        y = sy * (h / 2 - along);
      }
    } else {
      let d = rand() * 2 * (w + h);
      if (d < w) {
        x = -w / 2 + d;
        y = h / 2;
      } else if ((d -= w) < h) {
        x = w / 2;
        y = h / 2 - d;
      } else if ((d -= h) < w) {
        x = w / 2 - d;
        y = -h / 2;
      } else {
        d -= w;
        x = -w / 2;
        y = -h / 2 + d;
      }
    }
    positions[i * 3] = x + (rand() - 0.5) * jitter;
    positions[i * 3 + 1] = y + (rand() - 0.5) * jitter;
    positions[i * 3 + 2] = (rand() - 0.5) * jitter;
    sd[i * 3] = rand();
    sd[i * 3 + 1] = rand();
    sd[i * 3 + 2] = bracket ? 0.55 + 0.45 * rand() : 0.3 * rand();
  }
  return { positions, seeds: sd };
}
