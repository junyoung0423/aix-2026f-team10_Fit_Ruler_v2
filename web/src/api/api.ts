import type {
  ApiErrorBody,
  ErrorCode,
  FieldError,
  FitReport,
  FitRequest,
  FittingRequest,
  FittingStatus,
  GarmentList,
} from './types';

/**
 * 화면이 쓰는 API. 가짜 API(mocks/mockApi.ts)와 실제 서버 API(http.ts)가 같은 모양을 따른다.
 * 경로와 형식은 팀 계약 `docs/api/README.md`를 따른다.
 *
 * | 경로 | 응답 | 서버 구현 |
 * |---|---|---|
 * | GET  /api/garments              | GarmentList   | B2 |
 * | POST /api/fit                   | FitReport     | B2 |
 * | POST /api/fittings              | FittingStatus | B3 (첫 상태를 돌려준다고 가정) |
 * | GET  /api/fittings/{job_id}     | FittingStatus | B3 |
 * | GET  /api/artifacts/{artifact_id} | 파일        | B3 (docs/api/README.md의 임시 약속) |
 *
 * 오류는 모두 `ApiErrorBody`다: 422 INPUT_INVALID, 404 GARMENT_NOT_FOUND, 409 GARMENT_NOT_READY.
 */
export interface FittingApi {
  listGarments(): Promise<GarmentList>;
  fit(request: FitRequest): Promise<FitReport>;
  createFitting(request: FittingRequest): Promise<FittingStatus>;
  getFitting(jobId: string): Promise<FittingStatus>;
  /** 산출물 ID → 화면에서 쓸 파일 주소. 없으면 null */
  artifactUrl(artifactId: string): string | null;
}

/** 서버에 닿지 못했거나 응답 본문이 계약 형식이 아닐 때 쓰는 코드 */
export type ClientErrorCode = 'NETWORK_ERROR' | 'UNEXPECTED_RESPONSE';

export class ApiError extends Error {
  readonly status: number;
  readonly code: ErrorCode | ClientErrorCode;
  readonly fields: FieldError[];

  constructor(status: number, code: ErrorCode | ClientErrorCode, message: string, fields: FieldError[] = []) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.fields = fields;
  }

  static fromBody(status: number, body: ApiErrorBody): ApiError {
    return new ApiError(status, body.code, body.safe_message, body.fields ?? []);
  }
}

/**
 * 작업 API(`POST /api/fittings`, B3)가 아직 없어서 실패했는지.
 * - 경로가 없으면 계약 형식이 아닌 404(FastAPI 기본 응답 `{"detail": "Not Found"}`)가 온다.
 * - 작업 서버에 닿지 못하면 NETWORK_ERROR다.
 * 이 두 경우만 핏 리포트만 보여 주고, 계약 오류(422·404 GARMENT_NOT_FOUND·409)나 500 등은 화면에 오류로 보여 준다.
 */
export function isFittingApiMissing(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  return error.code === 'NETWORK_ERROR' || (error.status === 404 && error.code === 'UNEXPECTED_RESPONSE');
}

/** 화면에 보여 줄 오류 문구. 서버의 safe_message를 그대로 쓴다. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return '알 수 없는 오류가 났습니다. 잠시 뒤 다시 시도해 주세요.';
}
