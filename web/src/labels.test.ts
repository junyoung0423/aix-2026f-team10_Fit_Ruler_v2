import { describe, expect, it } from 'vitest';
import type { GarmentVariant } from './api/types';
import {
  COLOR_KEYS,
  FIBER_KEYS,
  STRETCH_VALUES,
  colorHex,
  colorLabel,
  fabricText,
  fiberLabel,
  garmentName,
  stretchLabel,
  swatchColor,
} from './labels';

/** `fitting/adapters/aihub/tables.py`의 표 키. 서버 표가 바뀌면 여기와 labels.ts를 같이 고친다. */
const SERVER_COLOR_HEX: Record<string, string> = {
  black: '#222326',
  white: '#f4f4f2',
  gray: '#8d9096',
  red: '#c0392b',
  orange: '#e67e22',
  pink: '#f2a7bb',
  yellow: '#f1d24a',
  brown: '#7b5539',
  green: '#3f7d4e',
  blue: '#2f5fa7',
  purple: '#6c4a9e',
  beige: '#d9c8a9',
  navy: '#24324f',
};
const SERVER_FIBERS = [
  'cotton',
  'hemp',
  'cellulose_other',
  'silk',
  'wool',
  'protein_other',
  'viscose_rayon',
  'regenerated_other',
  'polyester',
  'nylon',
  'polyurethane',
  'synthetic_other',
];
const SERVER_STRETCH = ['none at all', 'none', 'contain little', 'contain', 'contain a lot'];

const named = (style: string, size: string, color: string) => garmentName({ style, size, color });

describe('색', () => {
  it('서버 색 키 13개 모두 한국어 이름과 서버와 같은 대표 hex가 있다', () => {
    expect([...COLOR_KEYS].sort()).toEqual(Object.keys(SERVER_COLOR_HEX).sort());
    for (const [key, hex] of Object.entries(SERVER_COLOR_HEX)) {
      expect(colorLabel(key)).not.toBe(key);
      expect(colorHex(key)).toBe(hex);
    }
  });

  it('예시 이름: 빨강·파랑·베이지·네이비', () => {
    expect(['red', 'blue', 'beige', 'navy'].map(colorLabel)).toEqual(['빨강', '파랑', '베이지', '네이비']);
  });

  it('모르는 키는 키 그대로, 견본은 기본 회색', () => {
    expect(colorLabel('teal')).toBe('teal');
    expect(colorHex('teal')).toBe('#c9ced6');
  });

  it('견본은 color_hex를 먼저 쓴다', () => {
    expect(swatchColor({ color: 'red', color_hex: '#123456' })).toBe('#123456');
  });

  it('color_hex가 없거나(null·생략) 형식이 틀리면 색 키 표를 쓴다', () => {
    expect(swatchColor({ color: 'red', color_hex: null })).toBe('#c0392b');
    expect(swatchColor({ color: 'red' })).toBe('#c0392b');
    expect(swatchColor({ color: 'red', color_hex: 'red; background: url(x)' })).toBe('#c0392b');
    expect(swatchColor({ color: 'teal', color_hex: null })).toBe('#c9ced6');
  });
});

describe('이름', () => {
  it('핏형·사이즈가 있으면 모두 보인다', () => {
    expect(named('regular', 'M', 'navy')).toBe('기본핏 반팔 티 · M · 네이비');
  });

  it('핏형·사이즈가 unknown이면 뺀다', () => {
    expect(named('unknown', 'unknown', 'red')).toBe('반팔 티 · 빨강');
  });

  it('하나만 unknown이면 그것만 뺀다', () => {
    expect(named('unknown', 'L', 'blue')).toBe('반팔 티 · L · 파랑');
    expect(named('slim', 'unknown', 'beige')).toBe('슬림핏 반팔 티 · 베이지');
  });

  it('모르는 핏형·색 키는 그대로', () => {
    expect(named('boxy', 'XL', 'teal')).toBe('boxy 반팔 티 · XL · teal');
  });
});

describe('원단', () => {
  const fabric = (fibers: string[], stretch: string | null): GarmentVariant['fabric'] => ({
    fibers,
    composition: null,
    stretch,
    description_en: null,
  });

  it('서버 섬유 키 12개와 신축성 값 5개 모두 한국어 이름이 있다', () => {
    expect([...FIBER_KEYS].sort()).toEqual([...SERVER_FIBERS].sort());
    expect([...STRETCH_VALUES].sort()).toEqual([...SERVER_STRETCH].sort());
    for (const key of SERVER_FIBERS) expect(fiberLabel(key)).not.toBe(key);
  });

  it('섬유(출처 순서)와 신축성을 한 줄로', () => {
    expect(fabricText(fabric(['cotton', 'polyurethane'], 'contain little'))).toBe('면 · 폴리우레탄 · 신축성 조금');
    expect(fabricText(fabric(['polyester'], 'none at all'))).toBe('폴리에스터 · 신축성 전혀 없음');
  });

  it('신축성이 없으면(null) 섬유만', () => {
    expect(fabricText(fabric(['cotton'], null))).toBe('면');
  });

  it('모르는 섬유·신축성 값은 그대로 보여 준다', () => {
    expect(fiberLabel('bamboo')).toBe('bamboo');
    expect(stretchLabel('very high')).toBe('신축성 very high');
    expect(fabricText(fabric(['bamboo'], 'very high'))).toBe('bamboo · 신축성 very high');
  });

  it('원단 정보가 없으면(null·생략·빈 값) null', () => {
    expect(fabricText(null)).toBeNull();
    expect(fabricText(undefined)).toBeNull();
    expect(fabricText(fabric([], null))).toBeNull();
  });
});
