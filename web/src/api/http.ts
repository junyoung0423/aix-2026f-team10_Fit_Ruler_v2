import { ApiError, type FittingApi } from './api';
import type { ApiErrorBody, FitReport, FitRequest, FittingRequest, FittingStatus, GarmentList } from './types';

const isApiErrorBody = (value: unknown): value is ApiErrorBody =>
  typeof value === 'object' &&
  value !== null &&
  typeof (value as ApiErrorBody).code === 'string' &&
  typeof (value as ApiErrorBody).safe_message === 'string';

/**
 * 실제 서버를 부르는 API. `baseUrl`이 빈 문자열이면 같은 출처의 `/api`로 보낸다.
 * 개발 중에는 Vite 프록시가 `/api`를 핏 API 서버로 넘긴다(vite.config.ts).
 */
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
      throw new ApiError(0, 'NETWORK_ERROR', '서버에 연결할 수 없습니다. 잠시 뒤 다시 시도해 주세요.');
    }

    const data: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      if (isApiErrorBody(data)) throw ApiError.fromBody(response.status, data);
      throw new ApiError(response.status, 'UNEXPECTED_RESPONSE', `요청이 실패했습니다 (${response.status}).`);
    }
    return data as T;
  }

  return {
    listGarments: () => request<GarmentList>('/api/garments'),
    fit: (body: FitRequest) => request<FitReport>('/api/fit', { method: 'POST', body }),
    createFitting: (body: FittingRequest) => request<FittingStatus>('/api/fittings', { method: 'POST', body }),
    getFitting: (jobId: string) => request<FittingStatus>(`/api/fittings/${encodeURIComponent(jobId)}`),
    artifactUrl: (artifactId: string) => `${root}/api/artifacts/${encodeURIComponent(artifactId)}`,
  };
}
