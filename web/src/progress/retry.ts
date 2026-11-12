import type { FittingApi } from '../api/api';
import type { FittingRequest } from '../api/types';
import type { StoredResult } from '../state/fittingStore';

/**
 * "같은 조건으로 다시 시도": 저장해 둔 요청을 그대로 `POST /api/fittings`로 다시 보낸다.
 * 실사화 동의(`realize_consent`)도 저장한 값 그대로다. 동의하지 않은 요청이 동의한 요청으로 바뀌지 않는다.
 * 핏 리포트는 같은 치수·의류라 처음 받은 것을 그대로 쓴다.
 */
export async function restartFitting(
  api: FittingApi,
  stored: StoredResult & { request: FittingRequest },
): Promise<{ id: string; result: StoredResult }> {
  const job = await api.createFitting(structuredClone(stored.request));
  return { id: job.job_id, result: { ...stored, jobId: job.job_id } };
}

/** 저장된 요청이 있어 다시 시도할 수 있는지(새 탭에서 연 결과에는 없다) */
export const canRestart = (stored: StoredResult | null): stored is StoredResult & { request: FittingRequest } =>
  Boolean(stored?.request);
