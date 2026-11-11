import {
  MEASUREMENT_FIELDS,
  MEASUREMENT_LABEL,
  MEASUREMENT_RANGES,
  type MeasurementField,
} from '../api/contract';
import type { Measurements } from '../api/types';

/**
 * 신체 치수 입력 검사. 범위와 문구는 서버(B2 `server/errors.py`)와 같게 맞췄다.
 * 서버로 보내기 전에 같은 검사를 해서 바로 알려 주고, 서버가 거절하면(422) 서버 문구를 그대로 쓴다.
 * 화면만 하는 일: 단위 실수 안내(m·인치·mm), 소수 둘째 자리 이상 막기, 끝의 'cm'·쉼표 받아 주기.
 */

export type BodyDraft = Record<MeasurementField, string>;
export type BodyErrors = Partial<Record<MeasurementField, string>>;

/** 입력칸 안내와 '예시 치수 넣기'에 쓰는 값. 팀 예시 docs/api/examples/fit_request.json */
export const EXAMPLE_MEASUREMENTS: Measurements = {
  height_cm: 175,
  chest_cm: 100,
  waist_cm: 80,
  shoulder_cm: 44,
  arm_length_cm: 60,
};

const INCH_CM = 2.54;
const round1 = (n: number) => Math.round(n * 10) / 10;
const inRange = (value: number, field: MeasurementField) =>
  value >= MEASUREMENT_RANGES[field].min && value <= MEASUREMENT_RANGES[field].max;

/** 입력칸 글자를 숫자로 바꾼다. 앞뒤 공백, 끝의 'cm', 소수점 대신 쓴 쉼표는 받아 준다. 숫자가 아니면 NaN. */
export function parseCm(raw: string): number {
  const text = raw.trim().replace(/\s*cm$/i, '').replace(',', '.');
  if (!/^\d+(\.\d+)?$/.test(text)) return Number.NaN;
  return Number(text);
}

/** 범위를 벗어난 값이 다른 단위로 잰 값처럼 보이면 cm로 바꾼 값을 알려 준다. */
function unitHint(value: number, field: MeasurementField): string {
  if (inRange(value * 100, field)) return ` m로 잰 값 같아요. cm로는 ${round1(value * 100)} cm입니다.`;
  if (inRange(value * INCH_CM, field)) return ` 인치로 잰 값 같아요. cm로는 약 ${round1(value * INCH_CM)} cm입니다.`;
  if (inRange(value / 10, field)) return ` mm로 잰 값 같아요. cm로는 ${round1(value / 10)} cm입니다.`;
  return '';
}

/** 입력칸 글자 하나를 검사한다. 맞으면 null, 틀리면 오류 문구. */
export function validateField(field: MeasurementField, raw: string): string | null {
  const label = MEASUREMENT_LABEL[field];
  if (raw.trim() === '') return `${label} 값을 입력해 주세요.`;
  const value = parseCm(raw);
  if (!Number.isFinite(value)) return `${label} 값은 숫자로 입력해 주세요.`;
  if (!inRange(value, field)) {
    const { min, max } = MEASUREMENT_RANGES[field];
    return `${label} 값은 ${min}~${max} cm 사이로 입력해 주세요.${unitHint(value, field)}`;
  }
  if (round1(value) !== value) return '소수점 첫째 자리까지만 입력해 주세요.';
  return null;
}

export type BodyValidation =
  | { ok: true; measurements: Measurements }
  | { ok: false; errors: BodyErrors; firstInvalid: MeasurementField };

/** 입력칸 전체를 검사한다. 모두 맞으면 숫자로 바꾼 치수를 돌려준다. */
export function validateDraft(draft: BodyDraft): BodyValidation {
  const errors: BodyErrors = {};
  let firstInvalid: MeasurementField | null = null;
  for (const field of MEASUREMENT_FIELDS) {
    const message = validateField(field, draft[field]);
    if (message) {
      errors[field] = message;
      firstInvalid ??= field;
    }
  }
  if (firstInvalid) return { ok: false, errors, firstInvalid };
  return {
    ok: true,
    measurements: {
      height_cm: parseCm(draft.height_cm),
      chest_cm: parseCm(draft.chest_cm),
      waist_cm: parseCm(draft.waist_cm),
      shoulder_cm: parseCm(draft.shoulder_cm),
      arm_length_cm: parseCm(draft.arm_length_cm),
    },
  };
}
