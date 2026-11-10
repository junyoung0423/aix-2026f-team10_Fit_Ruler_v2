import type { BodyMeasurements, FitPartResult, FitReport, FitVerdict, Garment } from '../api/types';

/**
 * 가짜 API에서 쓰는 임시 핏 계산.
 * 식은 활동지 1-3을 따르고, 경계값은 팀 기준표(#2)가 정해지기 전까지 쓰는 임시값이다.
 * 실제 판정은 B의 핏 API가 한다.
 */
export const TEMP_CRITERIA_VERSION = 'temp-2026-11-10';

/** [타이트|정핏, 정핏|여유, 여유|오버핏] 경계(cm). 값이 경계와 같으면 위 구간으로 본다. */
export type Thresholds = readonly [number, number, number];

export const TEMP_THRESHOLDS = {
  chest: [4, 12, 20],
  shoulder: [-1, 2, 5],
  waist: [4, 14, 22],
  length: [-4, 3, 8],
} as const satisfies Record<string, Thresholds>;

/** 키로 기준 총장을 어림하는 임시 비율. 결과에는 추정이라고 표시한다. */
export const TEMP_LENGTH_RATIO = 0.4;

export function verdictFor(value: number, [toRegular, toLoose, toOversized]: Thresholds): FitVerdict {
  if (value < toRegular) return 'tight';
  if (value < toLoose) return 'regular';
  if (value < toOversized) return 'loose';
  return 'oversized';
}

const round1 = (n: number) => Math.round(n * 10) / 10;
const cm = (n: number) => `${round1(Math.abs(n))}cm`;

const AROUND: Record<FitVerdict, string> = {
  tight: '몸에 붙습니다',
  regular: '알맞게 맞습니다',
  loose: '여유 있게 맞습니다',
  oversized: '크게 떨어집니다',
};

const SHOULDER: Record<FitVerdict, string> = {
  tight: '어깨가 끼일 수 있습니다',
  regular: '잘 맞습니다',
  loose: '조금 내려옵니다',
  oversized: '많이 내려옵니다',
};

const LENGTH: Record<FitVerdict, string> = {
  tight: '짧은 편입니다',
  regular: '알맞은 길이입니다',
  loose: '조금 긴 편입니다',
  oversized: '길게 내려옵니다',
};

function aroundSentence(bodyLabel: string, ease: number, verdict: FitVerdict): string {
  const gap = ease >= 0 ? '넉넉해' : '작아';
  return `${bodyLabel}보다 ${cm(ease)} ${gap} ${AROUND[verdict]}.`;
}

function shoulderSentence(diff: number, verdict: FitVerdict): string {
  if (diff === 0) return `어깨선이 어깨와 같아 ${SHOULDER[verdict]}.`;
  const gap = diff > 0 ? '넓어' : '좁아';
  return `어깨선이 어깨보다 ${cm(diff)} ${gap} ${SHOULDER[verdict]}.`;
}

function lengthSentence(diff: number, ideal: number, verdict: FitVerdict): string {
  if (diff === 0) return `키로 추정한 기준 총장(${ideal}cm)과 같아 ${LENGTH[verdict]}. 추정값입니다.`;
  const gap = diff > 0 ? '길어' : '짧아';
  return `키로 추정한 기준 총장(${ideal}cm)보다 ${cm(diff)} ${gap} ${LENGTH[verdict]}. 추정값입니다.`;
}

export function computeFitReport(body: BodyMeasurements, garment: Garment): FitReport {
  const g = garment.measurements;
  const parts: FitPartResult[] = [];

  // 가슴 여유 = 옷 가슴단면 × 2 − 몸 가슴둘레
  const chestEase = round1(g.chestHalfCm * 2 - body.chestCircumferenceCm);
  const chestVerdict = verdictFor(chestEase, TEMP_THRESHOLDS.chest);
  parts.push({
    part: 'chest',
    verdict: chestVerdict,
    easeCm: chestEase,
    sentence: aroundSentence('가슴둘레', chestEase, chestVerdict),
    estimated: false,
  });

  // 어깨 차이 = 옷 어깨너비 − 몸 어깨너비
  const shoulderDiff = round1(g.shoulderWidthCm - body.shoulderWidthCm);
  const shoulderVerdict = verdictFor(shoulderDiff, TEMP_THRESHOLDS.shoulder);
  parts.push({
    part: 'shoulder',
    verdict: shoulderVerdict,
    easeCm: shoulderDiff,
    sentence: shoulderSentence(shoulderDiff, shoulderVerdict),
    estimated: false,
  });

  // 허리 여유 = 옷 밑단단면 × 2 − 몸 허리둘레 (밑단 치수가 없으면 판정하지 않는다)
  if (g.hemHalfCm !== undefined) {
    const waistEase = round1(g.hemHalfCm * 2 - body.waistCircumferenceCm);
    const waistVerdict = verdictFor(waistEase, TEMP_THRESHOLDS.waist);
    parts.push({
      part: 'waist',
      verdict: waistVerdict,
      easeCm: waistEase,
      sentence: aroundSentence('허리둘레', waistEase, waistVerdict),
      estimated: false,
    });
  }

  // 총장: 입력에 엉덩이 위치가 없어 키로 기준 총장을 추정한다
  const idealLength = round1(body.heightCm * TEMP_LENGTH_RATIO);
  const lengthDiff = round1(g.lengthCm - idealLength);
  const lengthVerdict = verdictFor(lengthDiff, TEMP_THRESHOLDS.length);
  parts.push({
    part: 'length',
    verdict: lengthVerdict,
    easeCm: lengthDiff,
    sentence: lengthSentence(lengthDiff, idealLength, lengthVerdict),
    estimated: true,
  });

  return { garmentId: garment.id, parts, criteriaVersion: TEMP_CRITERIA_VERSION };
}
