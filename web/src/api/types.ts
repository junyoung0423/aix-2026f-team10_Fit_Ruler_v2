/**
 * 임시 API 형식 (A1에서 정함)
 *
 * B의 공통 계약(B1)과 핏 API(B2)가 나오기 전까지 화면 개발에 쓰는 형식이다.
 * 계약이 나오면 이 파일과 http.ts만 맞추면 화면 코드는 그대로 쓸 수 있게 했다.
 *
 * - 단위는 모두 cm.
 * - "단면"은 옷을 펼쳐 놓고 잰 한쪽 폭, "둘레"는 몸을 한 바퀴 잰 길이.
 */

/** 사용자가 입력하는 신체 치수 */
export interface BodyMeasurements {
  heightCm: number;
  chestCircumferenceCm: number;
  waistCircumferenceCm: number;
  shoulderWidthCm: number;
  armLengthCm: number;
}

/** 등록된 티셔츠의 치수 */
export interface GarmentMeasurements {
  /** 가슴단면 */
  chestHalfCm: number;
  /** 총장 */
  lengthCm: number;
  /** 어깨너비 */
  shoulderWidthCm: number;
  /** 소매길이 */
  sleeveLengthCm: number;
  /** 밑단단면. 데이터에 없을 수 있다. */
  hemHalfCm?: number;
}

export interface Garment {
  id: string;
  name: string;
  colorName: string;
  colorHex: string;
  fabric: string;
  /** 상품의 형태 구분(정핏·오버핏 등). 사용자별 핏 판정과는 다르다. */
  fitLabel?: string;
  measurements: GarmentMeasurements;
}

export type FitPart = 'chest' | 'shoulder' | 'waist' | 'length';
export type FitVerdict = 'tight' | 'regular' | 'loose' | 'oversized';

export interface FitPartResult {
  part: FitPart;
  verdict: FitVerdict;
  /** 옷 치수 − 몸 치수(cm). 양수면 여유가 있다. */
  easeCm: number;
  sentence: string;
  /** 입력에 없는 신체 위치를 추정해 판정했는지 */
  estimated: boolean;
}

export interface FitReport {
  garmentId: string;
  parts: FitPartResult[];
  /** 어떤 기준표로 판정했는지 (예: temp-2026-11-10) */
  criteriaVersion: string;
}

export type StageName = 'body' | 'drape' | 'render' | 'realize' | 'verify';
export type StageStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped';

export interface StageState {
  name: StageName;
  status: StageStatus;
  /** 실패·건너뜀 이유 같은 짧은 설명 */
  message?: string;
}

export interface FittingRequest {
  body: BodyMeasurements;
  garmentId: string;
  /** 렌더 클립을 외부 서버(fal)로 보내는 데 동의했는지. false면 실사화를 건너뛴다. */
  realizeConsent: boolean;
}

export interface Realization {
  videoUrl: string | null;
  /** 실사 영상에서 뽑은 대표 프레임 */
  frameUrl: string | null;
  /** 대표 프레임과 같은 시점의 렌더 프레임 */
  renderFrameUrl: string | null;
}

export interface Verification {
  passed: boolean;
  colorDeltaE: number;
  silhouetteIoU: number;
}

export interface FittingJob {
  jobId: string;
  garmentId: string;
  /** 요청하자마자 채워진다. 3D·실사화를 기다리지 않는다. */
  fitReport: FitReport;
  stages: StageState[];
  /** 3D 착장 미리보기 */
  previewImageUrl: string | null;
  realization: Realization | null;
  verification: Verification | null;
  /** 실사화가 안 돼 3D 미리보기를 대체 결과로 보여 주는 경우 true */
  fallbackPreview: boolean;
}

/** 입력 칸별 오류 문구. 키는 BodyMeasurements의 필드 이름. */
export type FieldErrors = Partial<Record<string, string>>;
