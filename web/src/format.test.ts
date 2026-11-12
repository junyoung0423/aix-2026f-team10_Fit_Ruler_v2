import { describe, expect, it } from 'vitest';
import { bodyGarmentText } from './format';

describe('리포트 행의 몸·옷 치수: 판정한 행', () => {
  it('몸·옷 둘 다', () => {
    expect(bodyGarmentText({ label: 'REGULAR', body_cm: 100, garment_cm: 110 })).toBe('몸 100.0 · 옷 110.0 cm');
  });

  it('소수 첫째 자리 반올림(서버 display_cm과 같음)', () => {
    expect(bodyGarmentText({ label: 'SNUG', body_cm: 99.95, garment_cm: 103.44 })).toBe('몸 100.0 · 옷 103.4 cm');
  });
});

describe('리포트 행의 몸·옷 치수: 판정하지 않은 행은 몸 값을 빼기', () => {
  it('소매(NOT_EVALUATED): 팔 전체 길이는 빼고 옷만', () => {
    expect(bodyGarmentText({ label: 'NOT_EVALUATED', body_cm: 60, garment_cm: 20.5 })).toBe('옷 20.5 cm');
  });

  it('총장(NOT_EVALUATED, 몸 null): 옷만', () => {
    expect(bodyGarmentText({ label: 'NOT_EVALUATED', body_cm: null, garment_cm: 70 })).toBe('옷 70.0 cm');
  });

  it('허리 비교 안 함(UNKNOWN, 옷 null): 몸 값만 있어도 보여 주지 않는다', () => {
    expect(bodyGarmentText({ label: 'UNKNOWN', body_cm: 80, garment_cm: null })).toBeNull();
  });

  it('둘 다 없으면 null', () => {
    expect(bodyGarmentText({ label: 'NOT_EVALUATED', body_cm: null, garment_cm: null })).toBeNull();
  });
});
