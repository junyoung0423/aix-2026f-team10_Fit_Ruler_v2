import { describe, expect, it } from 'vitest';
import type { GarmentListItem, GarmentMeasurements } from './api/types';
import { sizeItems, sizeText, sortGarments, sourceBadge } from './garmentCard';
import { MOCK_GARMENTS, MOCK_MEASURED_GARMENT } from './mocks/garments';

const FULL: GarmentMeasurements = {
  chest_girth_cm: 108,
  waist_girth_cm: 96,
  shoulder_width_cm: 46,
  sleeve_length_cm: 22,
  body_length_cm: 70,
};

const item = (id: string, chest: number | null, isDemo: boolean): GarmentListItem => ({
  garment: {
    ...MOCK_MEASURED_GARMENT,
    id,
    measurements: { ...MOCK_MEASURED_GARMENT.measurements, chest_girth_cm: chest },
    measurement_meta: { ...MOCK_MEASURED_GARMENT.measurement_meta, is_measured: !isDemo },
  },
  is_demo: isDemo,
});

describe('치수 줄', () => {
  it('가슴둘레 · 어깨 · 소매 · 총장 순서', () => {
    expect(sizeText(FULL)).toBe('가슴둘레 108 · 어깨 46 · 소매 22 · 총장 70');
    expect(sizeItems(FULL).map((size) => size.key)).toEqual([
      'chest_girth_cm',
      'shoulder_width_cm',
      'sleeve_length_cm',
      'body_length_cm',
    ]);
  });

  it('값이 없는 치수는 빼지 않고 "—"', () => {
    const noLength = { ...FULL, body_length_cm: null, sleeve_length_cm: null };
    expect(sizeText(noLength)).toBe('가슴둘레 108 · 어깨 46 · 소매 — · 총장 —');
    expect(sizeItems(noLength).filter((size) => size.missing).map((size) => size.label)).toEqual(['소매', '총장']);
  });

  it('소수 값은 그대로', () => {
    expect(sizeText(MOCK_MEASURED_GARMENT.measurements)).toBe('가슴둘레 103.5 · 어깨 45.5 · 소매 20.5 · 총장 —');
  });

  it('허리는 카드에 보이지 않는다', () => {
    expect(sizeText(FULL)).not.toContain('허리');
  });
});

describe('출처 배지', () => {
  it('시연용 의류', () => {
    const demo = { garment: MOCK_GARMENTS[0], is_demo: true };
    expect(sourceBadge(demo)).toEqual({ kind: 'demo', text: '시연용 의류' });
  });

  it('실측 의류는 "실측"과 출처 문자열', () => {
    expect(sourceBadge({ garment: MOCK_MEASURED_GARMENT, is_demo: false })).toEqual({
      kind: 'measured',
      text: '실측',
      source: MOCK_MEASURED_GARMENT.measurement_meta.source,
    });
  });

  it('시연용도 실측도 아니면 배지 없음', () => {
    expect(sourceBadge(item('x', 100, false))).not.toBeNull();
    const neither = item('x', 100, false);
    neither.garment.measurement_meta.is_measured = false;
    expect(sourceBadge(neither)).toBeNull();
  });
});

describe('정렬', () => {
  it('시연용 먼저, 그다음 가슴둘레 오름차순', () => {
    const sorted = sortGarments([
      item('b', 110, false),
      item('demo2', 118, true),
      item('a', 98.5, false),
      item('demo1', 96, true),
      item('c', 104, false),
    ]);
    expect(sorted.map((x) => x.garment.id)).toEqual(['demo1', 'demo2', 'a', 'c', 'b']);
  });

  it('가슴둘레가 같으면 ID 순, 없으면 맨 뒤', () => {
    const sorted = sortGarments([item('z', null, false), item('y', 100, false), item('x', 100, false)]);
    expect(sorted.map((x) => x.garment.id)).toEqual(['x', 'y', 'z']);
  });

  it('원래 배열을 바꾸지 않는다', () => {
    const list = [item('b', 110, false), item('a', 100, false)];
    sortGarments(list);
    expect(list.map((x) => x.garment.id)).toEqual(['b', 'a']);
  });
});
