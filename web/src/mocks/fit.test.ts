import { describe, expect, it } from 'vitest';
import type { BodyMeasurements, Garment } from '../api/types';
import { computeFitReport, TEMP_THRESHOLDS, verdictFor } from './fit';

const body: BodyMeasurements = {
  heightCm: 175,
  chestCircumferenceCm: 96,
  waistCircumferenceCm: 82,
  shoulderWidthCm: 45,
  armLengthCm: 60,
};

const garment: Garment = {
  id: 'g1',
  name: '테스트 티',
  colorName: '흰색',
  colorHex: '#ffffff',
  fabric: '면 100%',
  measurements: { chestHalfCm: 52, lengthCm: 70, shoulderWidthCm: 46, sleeveLengthCm: 20, hemHalfCm: 50 },
};

const partOf = (name: string, g: Garment = garment) => computeFitReport(body, g).parts.find((p) => p.part === name);

describe('임시 핏 계산', () => {
  it('가슴 여유 = 가슴단면 × 2 − 가슴둘레', () => {
    const chest = partOf('chest');
    expect(chest?.easeCm).toBe(8);
    expect(chest?.verdict).toBe('regular');
    expect(chest?.sentence).toBe('가슴둘레보다 8cm 넉넉해 알맞게 맞습니다.');
  });

  it('어깨 차이 = 옷 어깨너비 − 몸 어깨너비', () => {
    const shoulder = partOf('shoulder');
    expect(shoulder?.easeCm).toBe(1);
    expect(shoulder?.verdict).toBe('regular');
  });

  it('경계값과 같으면 위 구간으로 판정한다', () => {
    expect(verdictFor(3.9, TEMP_THRESHOLDS.chest)).toBe('tight');
    expect(verdictFor(4, TEMP_THRESHOLDS.chest)).toBe('regular');
    expect(verdictFor(12, TEMP_THRESHOLDS.chest)).toBe('loose');
    expect(verdictFor(20, TEMP_THRESHOLDS.chest)).toBe('oversized');
  });

  it('옷이 몸보다 작으면 작다고 설명한다', () => {
    const tight = partOf('chest', { ...garment, measurements: { ...garment.measurements, chestHalfCm: 46 } });
    expect(tight?.easeCm).toBe(-4);
    expect(tight?.verdict).toBe('tight');
    expect(tight?.sentence).toBe('가슴둘레보다 4cm 작아 몸에 붙습니다.');
  });

  it('총장은 키로 추정한 값이라 추정 표시를 한다', () => {
    const length = partOf('length');
    expect(length?.estimated).toBe(true);
    expect(length?.sentence).toContain('추정');
  });

  it('밑단 치수가 없으면 허리는 판정하지 않는다', () => {
    const withoutHem = { chestHalfCm: 52, lengthCm: 70, shoulderWidthCm: 46, sleeveLengthCm: 20 };
    const report = computeFitReport(body, { ...garment, measurements: withoutHem });
    expect(report.parts.map((p) => p.part)).toEqual(['chest', 'shoulder', 'length']);
  });
});
