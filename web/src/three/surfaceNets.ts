/**
 * 부호 거리 함수(SDF, 안쪽이 음수)를 격자로 재고 표면을 삼각형으로 뽑는다(Naive Surface Nets).
 * 칸마다 표면이 지나가면 꼭짓점 하나를 두고, 표면을 가로지르는 격자 모서리마다 둘레 네 칸의 꼭짓점을 사각형으로 잇는다.
 * 법선은 SDF 기울기로 구해 매끈하게 보인다. 화면(three.js)과 워커가 같이 쓰도록 DOM에 기대지 않는다.
 */

export type Sdf = (x: number, y: number, z: number) => number;
export type Vec3 = readonly [number, number, number];

export interface MeshData {
  positions: Float32Array;
  normals: Float32Array;
  indices: Uint32Array;
}

const CORNERS: readonly Vec3[] = [
  [0, 0, 0],
  [1, 0, 0],
  [0, 1, 0],
  [1, 1, 0],
  [0, 0, 1],
  [1, 0, 1],
  [0, 1, 1],
  [1, 1, 1],
];

const EDGES: readonly (readonly [number, number])[] = [
  [0, 1],
  [2, 3],
  [4, 5],
  [6, 7],
  [0, 2],
  [1, 3],
  [4, 6],
  [5, 7],
  [0, 4],
  [1, 5],
  [2, 6],
  [3, 7],
];

/** SDF 기울기(바깥쪽을 향하는 단위 벡터). 정사면체 네 꼭짓점에서 재어 여섯 번 대신 네 번만 부른다 */
export function sdfNormal(sdf: Sdf, x: number, y: number, z: number, eps: number): Vec3 {
  const a = sdf(x + eps, y - eps, z - eps);
  const b = sdf(x - eps, y - eps, z + eps);
  const c = sdf(x - eps, y + eps, z - eps);
  const d = sdf(x + eps, y + eps, z + eps);
  const dx = a - b - c + d;
  const dy = -a - b + c + d;
  const dz = -a + b - c + d;
  const len = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;
  return [dx / len, dy / len, dz / len];
}

/** 성긴 격자 한 칸에 들어가는 촘촘한 격자 칸 수. 표면에서 먼 성긴 칸은 촘촘하게 재지 않는다(narrow band). */
const COARSE = 4;

/**
 * 격자 값을 잰다. 먼저 성긴 격자로 재고, 여덟 꼭짓점이 모두 같은 부호이면서 표면까지 칸 대각선보다 멀면
 * 그 칸 안은 표면이 지날 수 없으므로 부호만 채운다. 나머지(표면 근처)만 촘촘하게 잰다.
 * SDF가 정확한 거리가 아니어서(부드러운 합치기·근사) 여유를 1.5배 둔다.
 */
function sampleField(sdf: Sdf, min: Vec3, cell: number, nx: number, ny: number, nz: number): Float32Array {
  const field = new Float32Array(nx * ny * nz).fill(Number.NaN);
  const idx = (i: number, j: number, k: number) => i + nx * (j + ny * k);
  const cnx = Math.ceil((nx - 1) / COARSE) + 1;
  const cny = Math.ceil((ny - 1) / COARSE) + 1;
  const cnz = Math.ceil((nz - 1) / COARSE) + 1;
  const gi = (c: number, n: number) => Math.min(c * COARSE, n - 1);
  const coarse = new Float32Array(cnx * cny * cnz);
  for (let ck = 0; ck < cnz; ck++) {
    for (let cj = 0; cj < cny; cj++) {
      for (let ci = 0; ci < cnx; ci++) {
        const i = gi(ci, nx);
        const j = gi(cj, ny);
        const k = gi(ck, nz);
        const v = sdf(min[0] + i * cell, min[1] + j * cell, min[2] + k * cell);
        coarse[ci + cnx * (cj + cny * ck)] = v;
        field[idx(i, j, k)] = v;
      }
    }
  }
  const reach = COARSE * cell * Math.sqrt(3) * 1.5;
  // 칸 가운데 값으로도 거른다: 표면이 칸 안을 지나면 가운데에서 표면까지는 반 대각선보다 가깝다(여유 1.35배)
  const centerReach = COARSE * cell * Math.sqrt(3) * 0.5 * 1.35;
  const far: number[] = [];
  for (let ck = 0; ck < cnz - 1; ck++) {
    for (let cj = 0; cj < cny - 1; cj++) {
      for (let ci = 0; ci < cnx - 1; ci++) {
        let minAbs = Infinity;
        let pos = 0;
        for (let c = 0; c < 8; c++) {
          const v = coarse[ci + (c & 1) + cnx * (cj + ((c >> 1) & 1) + cny * (ck + ((c >> 2) & 1)))];
          minAbs = Math.min(minAbs, Math.abs(v));
          if (v > 0) pos++;
        }
        const i0 = gi(ci, nx);
        const j0 = gi(cj, ny);
        const k0 = gi(ck, nz);
        const i1 = gi(ci + 1, nx);
        const j1 = gi(cj + 1, ny);
        const k1 = gi(ck + 1, nz);
        const same = pos === 0 || pos === 8;
        let isFar = same && minAbs > reach;
        if (same && !isFar) {
          const center = sdf(min[0] + ((i0 + i1) / 2) * cell, min[1] + ((j0 + j1) / 2) * cell, min[2] + ((k0 + k1) / 2) * cell);
          isFar = (center > 0) === (pos === 8) && Math.abs(center) > centerReach;
        }
        if (isFar) {
          far.push(i0, j0, k0, i1, j1, k1, pos === 8 ? minAbs : -minAbs);
          continue;
        }
        for (let k = k0; k <= k1; k++) {
          const z = min[2] + k * cell;
          for (let j = j0; j <= j1; j++) {
            const y = min[1] + j * cell;
            for (let i = i0; i <= i1; i++) {
              const n = idx(i, j, k);
              if (Number.isNaN(field[n])) field[n] = sdf(min[0] + i * cell, y, z);
            }
          }
        }
      }
    }
  }
  for (let f = 0; f < far.length; f += 7) {
    const value = far[f + 6];
    for (let k = far[f + 2]; k <= far[f + 5]; k++) {
      for (let j = far[f + 1]; j <= far[f + 4]; j++) {
        for (let i = far[f]; i <= far[f + 3]; i++) {
          const n = idx(i, j, k);
          if (Number.isNaN(field[n])) field[n] = value;
        }
      }
    }
  }
  return field;
}

export function surfaceNets(sdf: Sdf, min: Vec3, max: Vec3, cell: number): MeshData {
  const nx = Math.ceil((max[0] - min[0]) / cell) + 1;
  const ny = Math.ceil((max[1] - min[1]) / cell) + 1;
  const nz = Math.ceil((max[2] - min[2]) / cell) + 1;
  const field = sampleField(sdf, min, cell, nx, ny, nz);
  const at = (i: number, j: number, k: number) => field[i + nx * (j + ny * k)];

  const cx = nx - 1;
  const cy = ny - 1;
  const cz = nz - 1;
  const cubeVertex = new Int32Array(cx * cy * cz).fill(-1);
  const cubeIndex = (i: number, j: number, k: number) => i + cx * (j + cy * k);
  const positions: number[] = [];
  const values = new Float64Array(8);

  for (let k = 0; k < cz; k++) {
    for (let j = 0; j < cy; j++) {
      for (let i = 0; i < cx; i++) {
        let mask = 0;
        for (let c = 0; c < 8; c++) {
          const o = CORNERS[c];
          const v = at(i + o[0], j + o[1], k + o[2]);
          values[c] = v;
          if (v < 0) mask |= 1 << c;
        }
        if (mask === 0 || mask === 255) continue;
        let sx = 0;
        let sy = 0;
        let sz = 0;
        let count = 0;
        for (const [a, b] of EDGES) {
          if (((mask >> a) & 1) === ((mask >> b) & 1)) continue;
          const t = values[a] / (values[a] - values[b]);
          const ca = CORNERS[a];
          const cb = CORNERS[b];
          sx += ca[0] + t * (cb[0] - ca[0]);
          sy += ca[1] + t * (cb[1] - ca[1]);
          sz += ca[2] + t * (cb[2] - ca[2]);
          count++;
        }
        cubeVertex[cubeIndex(i, j, k)] = positions.length / 3;
        positions.push(min[0] + (i + sx / count) * cell, min[1] + (j + sy / count) * cell, min[2] + (k + sz / count) * cell);
      }
    }
  }

  const vertexCount = positions.length / 3;
  const normals = new Float32Array(vertexCount * 3);
  for (let v = 0; v < vertexCount; v++) {
    const [x, y, z] = [positions[v * 3], positions[v * 3 + 1], positions[v * 3 + 2]];
    const nrm = sdfNormal(sdf, x, y, z, cell * 0.5);
    normals[v * 3] = nrm[0];
    normals[v * 3 + 1] = nrm[1];
    normals[v * 3 + 2] = nrm[2];
  }

  const indices: number[] = [];
  // 면의 방향은 격자 모서리 양 끝의 안·밖으로 정한다. 아래 꼭짓점 순서는 모서리 축의 +방향을 바라보므로,
  // 모서리 아래쪽 끝이 안(음수)이면 그대로, 바깥이면 뒤집는다(꼭짓점이 접힌 곳에서도 면이 뒤집히지 않는다).
  const quad = (q: readonly number[], lowInside: boolean) => {
    if (q.some((value) => value < 0)) return;
    const [a, b, c, d] = q;
    if (lowInside) indices.push(a, b, c, a, c, d);
    else indices.push(a, c, b, a, d, c);
  };

  for (let k = 0; k < nz; k++) {
    for (let j = 0; j < ny; j++) {
      for (let i = 0; i < nx; i++) {
        const v0 = at(i, j, k) < 0;
        // x 방향 모서리
        if (i < cx && j >= 1 && k >= 1 && j < cy && k < cz && v0 !== at(i + 1, j, k) < 0) {
          quad(
            [cubeVertex[cubeIndex(i, j - 1, k - 1)], cubeVertex[cubeIndex(i, j, k - 1)], cubeVertex[cubeIndex(i, j, k)], cubeVertex[cubeIndex(i, j - 1, k)]],
            v0,
          );
        }
        // y 방향 모서리
        if (j < cy && i >= 1 && k >= 1 && i < cx && k < cz && v0 !== at(i, j + 1, k) < 0) {
          quad(
            [cubeVertex[cubeIndex(i - 1, j, k - 1)], cubeVertex[cubeIndex(i - 1, j, k)], cubeVertex[cubeIndex(i, j, k)], cubeVertex[cubeIndex(i, j, k - 1)]],
            v0,
          );
        }
        // z 방향 모서리
        if (k < cz && i >= 1 && j >= 1 && i < cx && j < cy && v0 !== at(i, j, k + 1) < 0) {
          quad(
            [cubeVertex[cubeIndex(i - 1, j - 1, k)], cubeVertex[cubeIndex(i, j - 1, k)], cubeVertex[cubeIndex(i, j, k)], cubeVertex[cubeIndex(i - 1, j, k)]],
            v0,
          );
        }
      }
    }
  }

  return { positions: new Float32Array(positions), normals, indices: new Uint32Array(indices) };
}
