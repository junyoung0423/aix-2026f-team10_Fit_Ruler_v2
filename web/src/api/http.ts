import { ApiError, type FittingApi } from './api';
import type { BodyMeasurements, FitReport, FittingJob, FittingRequest, Garment } from './types';

/** 실제 서버를 부르는 API. 서버 주소는 VITE_API_BASE_URL로 정한다. */
export function createHttpApi(baseUrl: string): FittingApi {
  const root = baseUrl.replace(/\/$/, '');

  async function request<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
    const hasBody = init.body !== undefined;
    let response: Response;
    try {
      response = await fetch(`${root}${path}`, {
        method: init.method ?? 'GET',
        headers: hasBody ? { 'Content-Type': 'application/json' } : undefined,
        body: hasBody ? JSON.stringify(init.body) : undefined,
      });
    } catch {
      throw new ApiError(0, '서버에 연결할 수 없습니다. 잠시 뒤 다시 시도해 주세요.');
    }

    const data: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      const payload = (data ?? {}) as { message?: string; errors?: Record<string, string> };
      throw new ApiError(
        response.status,
        payload.message ?? `요청이 실패했습니다 (${response.status}).`,
        payload.errors ?? {},
      );
    }
    return data as T;
  }

  return {
    listGarments: () => request<Garment[]>('/api/garments'),
    fit: (body: BodyMeasurements, garmentId: string) =>
      request<FitReport>('/api/fit', { method: 'POST', body: { body, garmentId } }),
    createFitting: (fittingRequest: FittingRequest) =>
      request<FittingJob>('/api/fittings', { method: 'POST', body: fittingRequest }),
    getFitting: (jobId: string) => request<FittingJob>(`/api/fittings/${encodeURIComponent(jobId)}`),
  };
}
