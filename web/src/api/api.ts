import type { BodyMeasurements, FieldErrors, FitReport, FittingJob, FittingRequest, Garment } from './types';

/**
 * 화면이 쓰는 API. 가짜 API(mocks/mockApi.ts)와 실제 서버 API(http.ts)가 같은 모양을 따른다.
 *
 * 임시 엔드포인트
 * - GET  /api/garments            → Garment[]
 * - POST /api/fit                 → FitReport            (body, garmentId)
 * - POST /api/fittings            → FittingJob           (FittingRequest)
 * - GET  /api/fittings/{jobId}    → FittingJob
 * - 오류: 422 { message, errors: { 필드: 문구 } }, 404 { message }
 */
export interface FittingApi {
  listGarments(): Promise<Garment[]>;
  fit(body: BodyMeasurements, garmentId: string): Promise<FitReport>;
  createFitting(request: FittingRequest): Promise<FittingJob>;
  getFitting(jobId: string): Promise<FittingJob>;
}

export class ApiError extends Error {
  readonly status: number;
  readonly fieldErrors: FieldErrors;

  constructor(status: number, message: string, fieldErrors: FieldErrors = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.fieldErrors = fieldErrors;
  }
}

/** 화면에 보여 줄 오류 문구 */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return '알 수 없는 오류가 났습니다. 잠시 뒤 다시 시도해 주세요.';
}
