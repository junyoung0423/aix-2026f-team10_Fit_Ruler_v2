/**
 * 화면이 쓰는 API 계약. 팀 계약 `docs/api/schemas/*.schema.json`(B1·B2)을 그대로 옮겼다.
 * 필드 이름과 값을 바꾸지 않는다. 계약이 바뀌면 이 파일과 `docs/api/examples`로 확인하는
 * 테스트(`src/mocks/*.test.ts`)를 같이 고친다.
 */

// ---------- 열거형 (fitting/domain/enums.py, fitting/domain/api.py)

export type Mode = 'report_only' | 'full';
export type GarmentStatus = 'DRAFT' | 'PREPARING' | 'READY' | 'FAILED';
export type Region = 'chest' | 'shoulder' | 'waist' | 'sleeve' | 'body_length';
export type FitLabel =
  | 'INSUFFICIENT'
  | 'SNUG'
  | 'REGULAR'
  | 'LOOSE'
  | 'NARROW'
  | 'DROPPED'
  | 'UNKNOWN'
  | 'NOT_EVALUATED';
export type ReasonCode =
  | 'MEASUREMENT_NOT_COMPARABLE'
  | 'MEASUREMENT_MISSING'
  | 'NO_DIRECT_BODY_COUNTERPART'
  | 'BODY_INPUT_NOT_PROVIDED';
export type ErrorCode =
  | 'INPUT_INVALID'
  | 'GARMENT_NOT_READY'
  | 'GARMENT_NOT_FOUND'
  | 'BODY_FIT_NOT_CONVERGED'
  | 'DRAPE_INVALID'
  | 'RENDER_FAILED'
  | 'REALIZE_TIMEOUT'
  | 'EXTERNAL_SUBMIT_UNKNOWN'
  | 'VALIDATION_FAILED'
  | 'VALIDATION_UNVERIFIABLE'
  | 'BUDGET_LIMIT';
export type Stage = 'FIT_CALCULATE' | 'FIT_REPORT' | 'BODY_FIT' | 'DRAPE' | 'RENDER' | 'REALIZE' | 'VALIDATE';
export type StageStatus = 'WAITING' | 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'SKIPPED' | 'CANCELLED';
export type JobStatus = 'ACCEPTED' | 'RUNNING' | 'SUCCEEDED' | 'PARTIAL_SUCCEEDED' | 'FAILED' | 'CANCELLED';
export type QualityStatus = 'PENDING' | 'PASS' | 'FAIL' | 'UNVERIFIABLE';
export type VisualSkipReason = 'CONSENT_NOT_GIVEN' | 'REPORT_ONLY';
export type FieldErrorCode = 'MISSING' | 'UNKNOWN_FIELD' | 'NOT_A_NUMBER' | 'OUT_OF_RANGE' | 'UNSUPPORTED_VALUE' | 'INVALID';

// ---------- 신체 입력·요청 (fitting/domain/measurements.py)

/** 측정 위치는 docs/decisions.md "신체 측정 위치 (measure_v1)" 표를 따른다. 단위 cm. */
export interface Measurements {
  height_cm: number;
  chest_cm: number;
  waist_cm: number;
  shoulder_cm: number;
  arm_length_cm: number;
}

export const MEASUREMENT_PROTOCOL_VERSION = 'measure_v1';

/** POST /api/fit 요청 본문 (B2) */
export interface FitRequest {
  garment_variant_id: string;
  measurements: Measurements;
  measurement_protocol_version: string;
}

/** POST /api/fittings 요청 본문 (B3에서 구현) */
export interface FittingRequest extends FitRequest {
  mode: Mode;
  /** 실사화(렌더 클립을 외부 서비스로 전송)에 동의했는지. false면 렌더까지만 한다. */
  realize_consent: boolean;
}

// ---------- 의류 (fitting/domain/garment.py, fitting/domain/api.py)

export interface GarmentMeasurements {
  chest_girth_cm: number | null;
  waist_girth_cm: number | null;
  shoulder_width_cm: number | null;
  sleeve_length_cm: number | null;
  body_length_cm: number | null;
}

export interface MeasurementMeta {
  measurement_protocol_version: string;
  source: string;
  is_measured: boolean;
  chest_from_flat_width: boolean;
  waist_from_flat_width: boolean;
  waist_position_matched: boolean;
  measured_by: string | null;
  measured_on: string | null;
  notes: string | null;
}

/** 원단 정보 (C1 #3). 서버 계약에서는 기본값이 있는 선택 필드다. */
export interface FabricInfo {
  /** 섬유 키(영어 소문자·밑줄), 출처에 적힌 순서 */
  fibers: string[];
  /** 섬유 키 -> 비율(%). 출처에 비율이 없으면 null */
  composition: Record<string, number> | null;
  /** 신축성. 출처 값 그대로 */
  stretch: string | null;
  /** 실사화(D5) 프롬프트용 영어 한 줄 */
  description_en: string | null;
}

export interface GarmentVariant {
  schema_version: string;
  id: string;
  style: string;
  size: string;
  color: string;
  /** 화면 색 견본용 `#rrggbb` (C1 #3). 없으면 null */
  color_hex?: string | null;
  fabric_preset: string;
  /** 원단 정보 (C1 #3). 없으면 null */
  fabric?: FabricInfo | null;
  garment_family: string;
  status: GarmentStatus;
  measurements: GarmentMeasurements;
  measurement_meta: MeasurementMeta;
  asset_version: string;
  source_ref: string;
}

export interface GarmentListItem {
  garment: GarmentVariant;
  /** true면 화면에 "시연용 의류"로 표시한다. */
  is_demo: boolean;
}

/** GET /api/garments 응답 (B2). READY 의류만 들어 있다. */
export interface GarmentList {
  schema_version: string;
  garments: GarmentListItem[];
}

// ---------- 핏 리포트 (fitting/domain/report.py)

export interface RegionResult {
  region: Region;
  body_cm: number | null;
  garment_cm: number | null;
  /** 반올림하지 않은 여유량(의류 − 신체). 화면에 보일 때만 소수 첫째 자리로 반올림한다. */
  ease_cm: number | null;
  label: FitLabel;
  reason_code: ReasonCode | null;
  explanation: string;
  /** true면 body_cm이 입력이 아니라 추정값이다(지금은 총장에만 쓰고 기본은 꺼짐). */
  is_estimated: boolean;
}

/** POST /api/fit 응답 (B2) */
export interface FitReport {
  schema_version: string;
  job_id: string;
  rules_version: string;
  regions: RegionResult[];
  summary: string;
  warnings: string[];
  created_at: string;
}

// ---------- 작업 상태 (fitting/domain/status.py, B3에서 API 구현)

export interface StageProgress {
  stage: Stage;
  status: StageStatus;
  attempt_no: number;
}

export interface ReportPart {
  status: StageStatus;
  rules_version: string | null;
  regions: RegionResult[] | null;
  error_code: ErrorCode | null;
  summary: string | null;
  warnings: string[];
}

export interface VisualPart {
  status: StageStatus;
  stage: Stage | null;
  quality_status: QualityStatus | null;
  skip_reason: VisualSkipReason | null;
  drape_preview_artifact_id: string | null;
  render_artifact_id: string | null;
  video_artifact_id: string | null;
  frame_artifact_id: string | null;
  render_frame_artifact_id: string | null;
  quality_metrics: Record<string, number> | null;
  error_code: ErrorCode | null;
}

/** GET /api/fittings/{job_id} 응답 */
export interface FittingStatus {
  schema_version: string;
  job_id: string;
  status: JobStatus;
  report: ReportPart;
  visual: VisualPart;
  stages: StageProgress[];
}

// ---------- 오류 (fitting/domain/api.py)

export interface FieldError {
  /** 요청 본문 안의 위치. 예: "measurements.chest_cm". 본문 전체 문제면 "" */
  field: string;
  code: FieldErrorCode;
  message: string;
  min?: number | null;
  max?: number | null;
}

/** 모든 API 오류의 본문. 422 INPUT_INVALID, 404 GARMENT_NOT_FOUND, 409 GARMENT_NOT_READY */
export interface ApiErrorBody {
  schema_version: string;
  code: ErrorCode;
  safe_message: string;
  fields: FieldError[] | null;
}
