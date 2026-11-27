import { bodyParams, buildBodyMesh, buildTeeMesh, defaultGarment, mannequinFitFor, teeLayout, type BodyInput, type GarmentShape, type TeeCover } from './bodyModel';
import { armCover, fitMannequin, parseMannequinGlb, torsoCover, type MannequinBase } from './mannequin';
import type { MeshData } from './surfaceNets';

/** 몸·티셔츠 메시 만들기(웹 워커와, 워커가 없을 때 화면이 같이 쓴다) */

export interface BuiltMeshes {
  body: MeshData;
  tee: MeshData | null;
}

const MANNEQUIN_URL = new URL('./assets/mannequin.glb', import.meta.url).href;
let loading: Promise<MannequinBase | null> | null = null;

/** Tripo 마네킹 파일을 한 번만 불러온다. 못 불러오면 null(그때는 SDF 몸을 쓴다) */
export function loadMannequin(): Promise<MannequinBase | null> {
  loading ??= fetch(MANNEQUIN_URL)
    .then((response) => (response.ok ? response.arrayBuffer() : Promise.reject(new Error(`마네킹 파일 ${response.status}`))))
    .then(parseMannequinGlb)
    .catch(() => null);
  return loading;
}

/** 치수로 몸(마네킹을 맞춘 것, 없으면 SDF 몸)과 티셔츠를 만든다. 티셔츠는 맞춘 마네킹의 몸통 겉을 덮는다 */
export function buildAll(base: MannequinBase | null, body: Partial<BodyInput>, garment: GarmentShape | null, withTee: boolean): BuiltMeshes {
  const p = bodyParams(body);
  const g = garment ?? defaultGarment(p);
  let bodyMesh: MeshData;
  let cover: TeeCover | undefined;
  if (base) {
    const fitted = fitMannequin(base, mannequinFitFor(p));
    bodyMesh = fitted.mesh;
    if (withTee) {
      const L = teeLayout(p, g);
      const j = p.joints.shoulderR;
      const reach = Math.hypot(L.cuffR[0] - j[0], L.cuffR[1] - j[1], L.cuffR[2] - j[2]) + 0.02;
      cover = {
        // 몸통은 목 밑까지만(그 위는 목둘레가 열려 있다)
        torso: torsoCover(fitted.mesh, fitted.arm, L.hemY - 0.03, p.y.neckBase),
        // 팔은 어깨 관절 조금 위(어깨 세모근 윗면)부터 소매 끝까지
        arm: armCover(fitted.mesh, fitted.arm, j, L.sleeveDirR, -0.07, reach),
      };
    }
  } else {
    bodyMesh = buildBodyMesh(p);
  }
  return { body: bodyMesh, tee: withTee ? buildTeeMesh(p, g, undefined, cover) : null };
}
