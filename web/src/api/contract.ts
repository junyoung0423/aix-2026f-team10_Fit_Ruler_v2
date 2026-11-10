import type { Measurements } from './types';

/**
 * 계약에 적힌 값 중 화면이 직접 쓰는 것. 출처는 팀 저장소의 `fitting/domain/measurements.py`(범위)와
 * `server/errors.py`(필드 이름)다. 서버와 다르면 서버가 맞다.
 */

export type MeasurementField = keyof Measurements;

export const MEASUREMENT_FIELDS: readonly MeasurementField[] = [
  'height_cm',
  'chest_cm',
  'waist_cm',
  'shoulder_cm',
  'arm_length_cm',
];

/** 신체 입력 지원 범위(cm, 경계 포함). docs/api/README.md "POST /api/fit" */
export const MEASUREMENT_RANGES: Record<MeasurementField, { min: number; max: number }> = {
  height_cm: { min: 140, max: 210 },
  chest_cm: { min: 60, max: 160 },
  waist_cm: { min: 50, max: 160 },
  shoulder_cm: { min: 28, max: 60 },
  arm_length_cm: { min: 40, max: 85 },
};

/** 화면 문구에 쓰는 필드 이름. 서버 오류 문구(`server/errors.py`)와 같다. */
export const MEASUREMENT_LABEL: Record<MeasurementField, string> = {
  height_cm: '키',
  chest_cm: '가슴둘레',
  waist_cm: '허리둘레',
  shoulder_cm: '어깨너비',
  arm_length_cm: '팔길이',
};

/** 서버 오류의 `fields[].field`("measurements.chest_cm") → 신체 치수 칸 이름 */
export function measurementFieldOf(path: string): MeasurementField | null {
  const name = path.split('.').pop() ?? '';
  return (MEASUREMENT_FIELDS as readonly string[]).includes(name) ? (name as MeasurementField) : null;
}
