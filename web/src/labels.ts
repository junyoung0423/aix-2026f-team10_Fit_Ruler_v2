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
 * 의류 키의 화면 이름과 색 견본 (docs/decisions.md "색·원단 표기").
 * 색 키는 `fitting/adapters/aihub/tables.py`의 `COLOR_HEX` 13개와 같고, hex도 그 [제안] 값과 같다.
 * 견본은 서버가 준 `color_hex`를 먼저 쓰고, 이 표는 `color_hex`가 없을 때만 쓴다. 모르는 키는 키를 그대로 보여 준다.
 */
const STYLE_LABEL: Record<string, string> = { regular: '기본핏', relaxed: '여유핏', slim: '슬림핏', oversized: '오버핏' };
const COLOR: Record<string, { label: string; hex: string }> = {
  black: { label: '검정', hex: '#222326' },
  white: { label: '흰색', hex: '#f4f4f2' },
  gray: { label: '회색', hex: '#8d9096' },
  red: { label: '빨강', hex: '#c0392b' },
  orange: { label: '주황', hex: '#e67e22' },
  pink: { label: '분홍', hex: '#f2a7bb' },
  yellow: { label: '노랑', hex: '#f1d24a' },
  brown: { label: '갈색', hex: '#7b5539' },
  green: { label: '초록', hex: '#3f7d4e' },
  blue: { label: '파랑', hex: '#2f5fa7' },
  purple: { label: '보라', hex: '#6c4a9e' },
  beige: { label: '베이지', hex: '#d9c8a9' },
  navy: { label: '네이비', hex: '#24324f' },
};
const DEFAULT_SWATCH = '#c9ced6';
const HEX_PATTERN = /^#[0-9a-fA-F]{6}$/;

/** 데이터에 사이즈·핏형이 없는 의류(AI Hub)의 값 (docs/decisions.md "AI Hub 의류의 사이즈·핏형 표시") */
export const UNKNOWN_KEY = 'unknown';

export const COLOR_KEYS = Object.keys(COLOR);
export const colorHex = (color: string) => COLOR[color]?.hex ?? DEFAULT_SWATCH;
export const colorLabel = (color: string) => COLOR[color]?.label ?? color;
export const styleLabel = (style: string) => STYLE_LABEL[style] ?? style;

/** 색 견본: 서버의 `color_hex`(`#rrggbb`) → 색 키 표 → 기본 회색 순서 */
export const swatchColor = (garment: Pick<GarmentVariant, 'color' | 'color_hex'>) =>
  garment.color_hex && HEX_PATTERN.test(garment.color_hex) ? garment.color_hex : colorHex(garment.color);

/**
 * 예: "기본핏 반팔 티 · M · 네이비". 핏형·사이즈가 `unknown`이면 그 부분을 뺀다. 예: "반팔 티 · 빨강"
 */
export function garmentName(garment: Pick<GarmentVariant, 'style' | 'size' | 'color'>): string {
  const kind = garment.style === UNKNOWN_KEY ? '반팔 티' : `${styleLabel(garment.style)} 반팔 티`;
  const size = garment.size === UNKNOWN_KEY ? null : garment.size;
  return [kind, size, colorLabel(garment.color)].filter(Boolean).join(' · ');
}

/** 섬유 키(`tables.py`의 `AIHUB_FIBER` 12개). 모르는 키는 그대로 보여 준다. */
const FIBER_LABEL: Record<string, string> = {
  cotton: '면',
  hemp: '마',
  cellulose_other: '기타 셀룰로스 섬유',
  silk: '실크',
  wool: '울',
  protein_other: '기타 단백질 섬유',
  viscose_rayon: '비스코스 레이온',
  regenerated_other: '기타 재생 섬유',
  polyester: '폴리에스터',
  nylon: '나일론',
  polyurethane: '폴리우레탄',
  synthetic_other: '기타 합성 섬유',
};

/** 신축성 값(`tables.py`의 `AIHUB_STRETCH`, 출처 값 그대로). 모르는 값은 "신축성 {값}"으로 보여 준다. */
const STRETCH_LABEL: Record<string, string> = {
  'none at all': '신축성 전혀 없음',
  none: '신축성 없음',
  'contain little': '신축성 조금',
  contain: '신축성 있음',
  'contain a lot': '신축성 많음',
};

export const FIBER_KEYS = Object.keys(FIBER_LABEL);
export const STRETCH_VALUES = Object.keys(STRETCH_LABEL);
export const fiberLabel = (fiber: string) => FIBER_LABEL[fiber] ?? fiber;
export const stretchLabel = (stretch: string) => STRETCH_LABEL[stretch] ?? `신축성 ${stretch}`;

/** 원단 한 줄. 예: "면 · 폴리우레탄 · 신축성 조금". 원단 정보가 없거나 비어 있으면 null */
export function fabricText(fabric: GarmentVariant['fabric']): string | null {
  if (!fabric) return null;
  const parts = [...fabric.fibers.map(fiberLabel), ...(fabric.stretch ? [stretchLabel(fabric.stretch)] : [])];
  return parts.length > 0 ? parts.join(' · ') : null;
}
