import type { ErrorCode, FitLabel, GarmentVariant, Region, Stage, StageStatus } from './api/types';

/** 화면에 보이는 이름. 서버가 문장(explanation·summary·safe_message)을 주는 곳은 서버 문장을 쓴다. */

export const REGION_LABEL: Record<Region, string> = {
  chest: '가슴',
  shoulder: '어깨',
  waist: '허리',
  sleeve: '소매',
  body_length: '총장',
};

/**
 * 판정 배지에 쓰는 이름. 같은 REGULAR라도 가슴·허리는 "기본 여유", 어깨는 "기본 범위"다.
 * 기준표 `config/fit_rules_v0.yaml`의 label_ko를 옮겼다(응답에 부위별 이름이 없어서).
 */
const LABEL_KO: Partial<Record<Region, Partial<Record<FitLabel, string>>>> = {
  chest: { INSUFFICIENT: '여유 부족 예상', SNUG: '밀착', REGULAR: '기본 여유', LOOSE: '넉넉' },
  waist: { INSUFFICIENT: '여유 부족 예상', SNUG: '밀착', REGULAR: '기본 여유', LOOSE: '넉넉' },
  shoulder: { NARROW: '좁음 예상', REGULAR: '기본 범위', DROPPED: '어깨선 내려옴 예상' },
};

/** 부위별 판정 구간의 순서(작은 쪽 → 큰 쪽). 기준표 fit_rules_v0의 bands 순서와 같다. */
export const REGION_BANDS: Partial<Record<Region, readonly FitLabel[]>> = {
  chest: ['INSUFFICIENT', 'SNUG', 'REGULAR', 'LOOSE'],
  shoulder: ['NARROW', 'REGULAR', 'DROPPED'],
  waist: ['INSUFFICIENT', 'SNUG', 'REGULAR', 'LOOSE'],
};

export function fitLabelText(region: Region, label: FitLabel): string {
  if (label === 'UNKNOWN' || label === 'NOT_EVALUATED') return '판정 안 함';
  return LABEL_KO[region]?.[label] ?? label;
}

export const STAGE_LABEL: Record<Stage, string> = {
  FIT_CALCULATE: '핏 계산',
  FIT_REPORT: '핏 리포트',
  BODY_FIT: '몸 생성',
  DRAPE: '3D 착장',
  RENDER: '정면 렌더',
  REALIZE: '실사화',
  VALIDATE: '검증',
};

export const STATUS_LABEL: Record<StageStatus, string> = {
  WAITING: '대기',
  QUEUED: '대기열',
  RUNNING: '진행 중',
  SUCCEEDED: '완료',
  FAILED: '실패',
  SKIPPED: '건너뜀',
  CANCELLED: '취소됨',
};

/** 영상 경로 실패 안내. 서버는 코드만 주므로 화면이 문구를 고른다. */
export const ERROR_LABEL: Partial<Record<ErrorCode, string>> = {
  BODY_FIT_NOT_CONVERGED: '입력한 치수에 맞는 몸을 만들지 못했습니다.',
  DRAPE_INVALID: '3D 착장 결과가 올바르지 않아 멈췄습니다.',
  RENDER_FAILED: '정면 렌더를 만들지 못했습니다.',
  REALIZE_TIMEOUT: '실사화 응답이 제시간에 오지 않았습니다.',
  EXTERNAL_SUBMIT_UNKNOWN: '실사화 요청 결과를 확인하지 못했습니다.',
  VALIDATION_FAILED: '실사 영상이 렌더와 색·실루엣이 달라 검증을 통과하지 못했습니다.',
  VALIDATION_UNVERIFIABLE: '실사 영상을 검증할 수 없었습니다.',
  BUDGET_LIMIT: '실사화 사용 한도에 도달했습니다.',
  GARMENT_NOT_READY: '선택한 의류를 아직 사용할 수 없습니다.',
};

/** 리포트 warnings 코드 안내 (fitting/fit/engine.py) */
export const WARNING_LABEL: Record<string, string> = {
  DEMO_GARMENT: '시연용 의류입니다. 실제 의류를 실측한 값이 아닙니다.',
  WAIST_NOT_EVALUATED: '이 의류는 허리 위치가 확인되지 않아 허리를 판정하지 않았습니다.',
};

/**
 * 의류 키의 화면 이름과 색 견본. 표기 방법은 C1에서 정하기로 했다(docs/decisions.md "색·원단 표기").
 * 그 전까지 쓰는 임시 표다. 모르는 키는 키를 그대로 보여 준다.
 */
const STYLE_LABEL: Record<string, string> = { regular: '기본핏', relaxed: '여유핏', slim: '슬림핏', oversized: '오버핏' };
const COLOR: Record<string, { label: string; hex: string }> = {
  navy: { label: '네이비', hex: '#24324f' },
  black: { label: '검정', hex: '#222326' },
  white: { label: '흰색', hex: '#f4f4f2' },
  gray: { label: '회색', hex: '#8d9096' },
};

export const colorHex = (color: string) => COLOR[color]?.hex ?? '#c9ced6';
export const colorLabel = (color: string) => COLOR[color]?.label ?? color;
export const styleLabel = (style: string) => STYLE_LABEL[style] ?? style;

/** 예: "기본핏 반팔 티 · M · 네이비" */
export const garmentName = (garment: GarmentVariant) =>
  `${styleLabel(garment.style)} 반팔 티 · ${garment.size} · ${colorLabel(garment.color)}`;
