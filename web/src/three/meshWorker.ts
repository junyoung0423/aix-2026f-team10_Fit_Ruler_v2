/**
 * 몸·티셔츠 메시를 화면 밖(웹 워커)에서 만든다. 치수가 바뀌어 다시 만들 때 입력이 멈추지 않게 한다.
 */
import type { BodyInput, GarmentShape } from './bodyModel';
import { buildAll, loadMannequin } from './meshBuild';
import type { MeshData } from './surfaceNets';

export interface MeshRequest {
  id: number;
  body: Partial<BodyInput>;
  garment: GarmentShape | null;
  withTee: boolean;
}

export interface MeshResponse {
  id: number;
  body: MeshData;
  tee: MeshData | null;
  error?: string;
}

const scope = self as unknown as {
  onmessage: ((event: MessageEvent<MeshRequest>) => void) | null;
  postMessage: (message: MeshResponse, transfer: Transferable[]) => void;
};

scope.onmessage = (event) => {
  const { id, body, garment, withTee } = event.data;
  void loadMannequin().then((base) => {
    try {
      const built = buildAll(base, body, garment, withTee);
      const transfer: Transferable[] = [built.body.positions.buffer, built.body.normals.buffer, built.body.indices.buffer];
      if (built.tee) transfer.push(built.tee.positions.buffer, built.tee.normals.buffer, built.tee.indices.buffer);
      scope.postMessage({ id, body: built.body, tee: built.tee }, transfer);
    } catch (error) {
      const empty: MeshData = { positions: new Float32Array(), normals: new Float32Array(), indices: new Uint32Array() };
      scope.postMessage({ id, body: empty, tee: null, error: String(error) }, []);
    }
  });
};
