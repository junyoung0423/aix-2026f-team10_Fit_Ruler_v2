import { describe, expect, it } from 'vitest';
import type { FitReport, FitRequest, GarmentVariant, Measurements } from '../api/types';
import { RULES, classify, computeFitReport, notComputableReason } from './fit';
import { example } from './examples';
import { MOCK_GARMENTS } from './garments';


const OPTIONS = { jobId: 'job_test', createdAt: '2026-11-11T00:00:00Z' };
const navy = MOCK_GARMENTS[0];

describe('B2 계약 예시와 같은 결과', () => {
  it('예시 의류는 팀 저장소 garment.json과 같다', () => {
    expect(navy).toEqual(example<GarmentVariant>('garment.json'));
  });

  it('예시 요청의 리포트가 fit_report.json과 같다(job_id·created_at 제외)', () => {
    const request = example<FitRequest>('fit_request.json');
    const expected = example<FitReport>('fit_report.json');
    const report = computeFitReport(request.measurements, navy, OPTIONS);
    expect(report.regions).toEqual(expected.regions);
    expect(report.summary).toBe(expected.summary);
    expect(report.warnings).toEqual(expected.warnings);
    expect(report.rules_version).toBe(expected.rules_version);
  });
});

describe('기준표 fit_rules_v0 경계', () => {
  it.each([
    [-0.1, 'INSUFFICIENT'],
    [0, 'SNUG'],
    [5.9, 'SNUG'],
    [6, 'REGULAR'],
    [14, 'REGULAR'],
    [14.1, 'LOOSE'],
  ])('가슴 여유 %s → %s', (ease, label) => {
    expect(classify(RULES.chest, ease).label).toBe(label);
  });

  it.each([
    [-1.1, 'NARROW'],
    [-1, 'REGULAR'],
    [2, 'REGULAR'],
    [2.1, 'DROPPED'],
  ])('어깨 여유 %s → %s', (ease, label) => {
    expect(classify(RULES.shoulder, ease).label).toBe(label);
  });
});

describe('판정하지 않는 경우', () => {
  const body: Measurements = { height_cm: 175, chest_cm: 100, waist_cm: 80, shoulder_cm: 44, arm_length_cm: 60 };

  it('허리 위치가 확인되지 않은 의류는 허리 UNKNOWN과 경고', () => {
    const gray = MOCK_GARMENTS.find((g) => g.id === 'mock_tee_slim_s_gray') as GarmentVariant;
    const report = computeFitReport(body, gray, OPTIONS);
    const waist = report.regions.find((r) => r.region === 'waist');
    expect(waist).toMatchObject({ label: 'UNKNOWN', reason_code: 'MEASUREMENT_NOT_COMPARABLE', garment_cm: null });
    expect(report.warnings).toContain('WAIST_NOT_EVALUATED');
    expect(report.summary).not.toContain('허리');
  });

  it('허리 실측이 없으면 MEASUREMENT_MISSING', () => {
    const white = MOCK_GARMENTS.find((g) => g.id === 'mock_tee_regular_l_white') as GarmentVariant;
    const waist = computeFitReport(body, white, OPTIONS).regions.find((r) => r.region === 'waist');
    expect(waist?.reason_code).toBe('MEASUREMENT_MISSING');
  });

  it('여유가 음수면 신축성 문구를 덧붙인다', () => {
    const tight: Measurements = { ...body, chest_cm: 110 };
    const chest = computeFitReport(tight, navy, OPTIONS).regions[0];
    expect(chest.label).toBe('INSUFFICIENT');
    expect(chest.explanation).toContain('몸보다 2.0 cm 작아서');
    expect(chest.explanation).toContain('신축성에 따라');
  });

  it('계산할 수 없는 의류를 알아본다', () => {
    expect(notComputableReason(navy, 'measure_v1')).toBeNull();
    expect(notComputableReason(navy, 'measure_v2')).not.toBeNull();
    expect(notComputableReason({ ...navy, status: 'DRAFT' }, 'measure_v1')).not.toBeNull();
  });
});
