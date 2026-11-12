import { describe, expect, it } from 'vitest';
import { bodyGarmentText } from './format';

describe('리포트 행의 몸·옷 치수', () => {
  it('판정한 행: 몸·옷 둘 다', () => {
    expect(bodyGarmentText({ body_cm: 100, garment_cm: 110 })).toBe('몸 100.0 · 옷 110.0 cm');
  });

  it('소수 첫째 자리 반올림(서버 display_cm과 같음)', () => {
    expect(bodyGarmentText({ body_cm: 99.95, garment_cm: 103.44 })).toBe('몸 100.0 · 옷 103.4 cm');
  });

  it('허리 비교 안 함(옷 null): 몸만', () => {
    expect(bodyGarmentText({ body_cm: 80, garment_cm: null })).toBe('몸 80.0 cm');
  });

  it('총장(몸 null): 옷만', () => {
    expect(bodyGarmentText({ body_cm: null, garment_cm: 70 })).toBe('옷 70.0 cm');
  });

  it('둘 다 없으면 null', () => {
    expect(bodyGarmentText({ body_cm: null, garment_cm: null })).toBeNull();
  });
});
