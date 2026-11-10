import type { FitLabel, FitReport, GarmentVariant, Measurements, Region, RegionResult } from '../api/types';
import { displayCm } from '../format';

/**
 * 가짜 API의 핏 계산. B2 핏 계산기(`fitting/fit/engine.py`)와 기준표 `config/fit_rules_v0.yaml`을
 * 그대로 옮겼다. 실제 판정은 서버(B2)가 하고, 이 코드는 서버 없이 화면을 개발할 때만 쓴다.
 * 결과가 서버와 같은지는 `fit.test.ts`가 팀 예시 `docs/api/examples/fit_report.json`과 비교해 확인한다.
 * 기준표가 바뀌면(버전 `fit_rules_v1` 등) 이 파일도 같이 고친다.
 */
export const MOCK_RULES_VERSION = 'fit_rules_v0';

export interface Band {
  upper: number | null;
  upperInclusive: boolean;
  label: FitLabel;
  labelKo: string;
}

interface RegionRule {
  region: Region;
  shortKo: string;
  nounKo: string;
  bands: readonly Band[];
  negativeNote?: string;
  note?: string;
}

const STRETCH_NOTE = '신축성에 따라 실제 착용감은 달라질 수 있어 착용 가능 여부는 판단하지 않습니다.';

export const RULES: Record<'chest' | 'shoulder' | 'waist', RegionRule> = {
  chest: {
    region: 'chest',
    shortKo: '가슴',
    nounKo: '가슴둘레',
    negativeNote: STRETCH_NOTE,
    bands: [
      { upper: 0, upperInclusive: false, label: 'INSUFFICIENT', labelKo: '여유 부족 예상' },
      { upper: 6, upperInclusive: false, label: 'SNUG', labelKo: '밀착' },
      { upper: 14, upperInclusive: true, label: 'REGULAR', labelKo: '기본 여유' },
      { upper: null, upperInclusive: false, label: 'LOOSE', labelKo: '넉넉' },
    ],
  },
  shoulder: {
    region: 'shoulder',
    shortKo: '어깨',
    nounKo: '어깨너비',
    note: '어깨 여유는 양쪽을 합한 값이며 한쪽 어깨당 값이 아닙니다.',
    bands: [
      { upper: -1, upperInclusive: false, label: 'NARROW', labelKo: '좁음 예상' },
      { upper: 2, upperInclusive: true, label: 'REGULAR', labelKo: '기본 범위' },
      { upper: null, upperInclusive: false, label: 'DROPPED', labelKo: '어깨선 내려옴 예상' },
    ],
  },
  waist: {
    region: 'waist',
    shortKo: '허리',
    nounKo: '허리둘레',
    negativeNote: STRETCH_NOTE,
    bands: [
      { upper: 0, upperInclusive: false, label: 'INSUFFICIENT', labelKo: '여유 부족 예상' },
      { upper: 4, upperInclusive: false, label: 'SNUG', labelKo: '밀착' },
      { upper: 12, upperInclusive: true, label: 'REGULAR', labelKo: '기본 여유' },
      { upper: null, upperInclusive: false, label: 'LOOSE', labelKo: '넉넉' },
    ],
  },
};

/** 서버의 Decimal 계산처럼 이진 소수 오차를 지운다(예: 106.1 - 100.1 → 6). */
const clean = (value: number) => Math.round(value * 1e6) / 1e6;

export function classify(rule: RegionRule, ease: number): Band {
  const band = rule.bands.find((item) =>
    item.upper === null ? true : item.upperInclusive ? ease <= item.upper : ease < item.upper,
  );
  if (!band) throw new Error(`${rule.region}의 마지막 구간은 upper가 null이어야 합니다.`);
  return band;
}

function explain(rule: RegionRule, ease: number, band: Band): string {
  const shown = displayCm(ease);
  let relation: string;
  if (ease === 0) relation = '몸과 같아서';
  else if (shown === '0.0') relation = '몸과 거의 같아서';
  else if (ease > 0) relation = `몸보다 ${shown} cm 커서`;
  else relation = `몸보다 ${shown} cm 작아서`;
  let text = `${rule.nounKo}가 ${relation} 초기 기준상 ${band.labelKo}에 해당합니다.`;
  if (ease < 0 && rule.negativeNote) text += ` ${rule.negativeNote}`;
  if (rule.note) text += ` ${rule.note}`;
  return text;
}

function compare(rule: RegionRule, bodyCm: number, garmentCm: number): RegionResult {
  const ease = clean(garmentCm - bodyCm);
  const band = classify(rule, ease);
  return {
    region: rule.region,
    body_cm: bodyCm,
    garment_cm: garmentCm,
    ease_cm: ease,
    label: band.label,
    reason_code: null,
    explanation: explain(rule, ease, band),
    is_estimated: false,
  };
}

function waistResult(body: Measurements, garment: GarmentVariant): RegionResult {
  const girth = garment.measurements.waist_girth_cm;
  if (girth === null) {
    return {
      region: 'waist',
      body_cm: body.waist_cm,
      garment_cm: null,
      ease_cm: null,
      label: 'UNKNOWN',
      reason_code: 'MEASUREMENT_MISSING',
      explanation: '의류 허리 실측이 없어 허리 판정을 하지 않습니다.',
      is_estimated: false,
    };
  }
  if (!garment.measurement_meta.waist_position_matched) {
    return {
      region: 'waist',
      body_cm: body.waist_cm,
      garment_cm: null,
      ease_cm: null,
      label: 'UNKNOWN',
      reason_code: 'MEASUREMENT_NOT_COMPARABLE',
      explanation: '의류 허리 측정 위치가 신체 자연 허리와 대응하는지 확인되지 않아 허리 판정을 하지 않습니다.',
      is_estimated: false,
    };
  }
  return compare(RULES.waist, body.waist_cm, girth);
}

function sleeveResult(body: Measurements, garment: GarmentVariant): RegionResult {
  const sleeve = garment.measurements.sleeve_length_cm;
  return {
    region: 'sleeve',
    body_cm: body.arm_length_cm,
    garment_cm: sleeve,
    ease_cm: null,
    label: 'NOT_EVALUATED',
    reason_code: sleeve === null ? 'MEASUREMENT_MISSING' : 'NO_DIRECT_BODY_COUNTERPART',
    explanation:
      sleeve === null
        ? '의류 소매 실측이 없어 표시할 수 없습니다.'
        : `소매 실측 ${sleeve.toFixed(1)} cm입니다. 사람의 전체 팔길이와 직접 비교하지 않아 적정 여부는 판단하지 않습니다.`,
    is_estimated: false,
  };
}

function bodyLengthResult(garment: GarmentVariant): RegionResult {
  // 기준표의 총장 추정(body_length_estimate)은 기본 꺼짐이라 옮기지 않았다.
  const length = garment.measurements.body_length_cm;
  return {
    region: 'body_length',
    body_cm: null,
    garment_cm: length,
    ease_cm: null,
    label: 'NOT_EVALUATED',
    reason_code: length === null ? 'MEASUREMENT_MISSING' : 'BODY_INPUT_NOT_PROVIDED',
    explanation:
      length === null
        ? '의류 기장 실측이 없어 표시할 수 없습니다.'
        : `기장 실측 ${length.toFixed(1)} cm입니다. 몸통 길이 입력이 없어 적정 기장은 판단하지 않습니다.`,
    is_estimated: false,
  };
}

/** 서버의 GARMENT_NOT_READY에 해당하는 경우를 막는다. 맞으면 null, 아니면 이유. */
export function notComputableReason(garment: GarmentVariant, protocol: string): string | null {
  if (garment.status !== 'READY') return `의류 상태가 ${garment.status}입니다`;
  if (garment.garment_family !== 'basic_short_sleeve_crew') return '기준표가 적용되지 않는 의류군입니다';
  if (garment.measurement_meta.measurement_protocol_version !== protocol) return '측정 프로토콜이 다릅니다';
  if (garment.measurements.chest_girth_cm === null || garment.measurements.shoulder_width_cm === null) {
    return '필수 의류 실측이 없습니다';
  }
  return null;
}

export function computeFitReport(
  body: Measurements,
  garment: GarmentVariant,
  options: { jobId: string; createdAt: string },
): FitReport {
  const chest = compare(RULES.chest, body.chest_cm, garment.measurements.chest_girth_cm ?? 0);
  const shoulder = compare(RULES.shoulder, body.shoulder_cm, garment.measurements.shoulder_width_cm ?? 0);
  const waist = waistResult(body, garment);
  const judged: [RegionRule, RegionResult][] = [
    [RULES.chest, chest],
    [RULES.shoulder, shoulder],
    [RULES.waist, waist],
  ];
  const summary = judged
    .filter(([, result]) => result.label !== 'UNKNOWN')
    .map(([rule, result]) => `${rule.shortKo} ${rule.bands.find((band) => band.label === result.label)?.labelKo}`)
    .join(' · ');

  const warnings: string[] = [];
  if (waist.label === 'UNKNOWN') warnings.push('WAIST_NOT_EVALUATED');
  if (!garment.measurement_meta.is_measured) warnings.push('DEMO_GARMENT');

  return {
    schema_version: 'v1',
    job_id: options.jobId,
    rules_version: MOCK_RULES_VERSION,
    regions: [chest, shoulder, waist, sleeveResult(body, garment), bodyLengthResult(garment)],
    summary,
    warnings,
    created_at: options.createdAt,
  };
}
