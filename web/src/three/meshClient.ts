import type { BodyInput, GarmentShape } from './bodyModel';
import { buildAll, loadMannequin, type BuiltMeshes } from './meshBuild';
import type { MeshRequest, MeshResponse } from './meshWorker';

export type { BuiltMeshes } from './meshBuild';

let worker: Worker | null = null;
let workerBroken = false;
let nextId = 1;
const pending = new Map<number, { resolve: (value: BuiltMeshes) => void; reject: (error: unknown) => void }>();
const cache = new Map<string, Promise<BuiltMeshes>>();

function getWorker(): Worker | null {
  if (worker || workerBroken) return worker;
  if (typeof Worker === 'undefined') return null;
  try {
    worker = new Worker(new URL('./meshWorker.ts', import.meta.url), { type: 'module' });
    worker.onmessage = (event: MessageEvent<MeshResponse>) => {
      const job = pending.get(event.data.id);
      if (!job) return;
      pending.delete(event.data.id);
      if (event.data.error) job.reject(new Error(event.data.error));
      else job.resolve({ body: event.data.body, tee: event.data.tee });
    };
    worker.onerror = (event) => {
      workerBroken = true;
      worker = null;
      for (const job of pending.values()) job.reject(event);
      pending.clear();
    };
  } catch {
    workerBroken = true;
    worker = null;
  }
  return worker;
}

function buildHere(body: Partial<BodyInput>, garment: GarmentShape | null, withTee: boolean): Promise<BuiltMeshes> {
  return loadMannequin().then(
    (base) => new Promise((resolve) => setTimeout(() => resolve(buildAll(base, body, garment, withTee)), 0)),
  );
}

/**
 * 몸(과 티셔츠) 메시를 만든다. 웹 워커가 있으면 워커에서, 없으면 화면에서 만든다.
 * 같은 치수·옷이면 앞서 만든 결과를 다시 쓴다(최근 6개). 돌려준 배열은 여러 곳에서 같이 쓰므로 고치지 않는다.
 */
export function buildMeshes(body: Partial<BodyInput>, garment: GarmentShape | null, withTee: boolean): Promise<BuiltMeshes> {
  const key = JSON.stringify([body, garment, withTee]);
  const hit = cache.get(key);
  if (hit) return hit;
  const w = getWorker();
  const promise = w
    ? new Promise<BuiltMeshes>((resolve, reject) => {
        const id = nextId++;
        pending.set(id, { resolve, reject });
        const request: MeshRequest = { id, body, garment, withTee };
        w.postMessage(request);
      }).catch(() => buildHere(body, garment, withTee))
    : buildHere(body, garment, withTee);
  cache.set(key, promise);
  if (cache.size > 6) cache.delete(cache.keys().next().value as string);
  promise.catch(() => cache.delete(key));
  return promise;
}
