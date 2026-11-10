import { describe, expect, it } from 'vitest';
import { EXAMPLE_MEASUREMENTS, parseCm, validateDraft, validateField, type BodyDraft } from './body';

const draftOf = (overrides: Partial<BodyDraft> = {}): BodyDraft => ({
  height_cm: String(EXAMPLE_MEASUREMENTS.height_cm),
  chest_cm: String(EXAMPLE_MEASUREMENTS.chest_cm),
  waist_cm: String(EXAMPLE_MEASUREMENTS.waist_cm),
  shoulder_cm: String(EXAMPLE_MEASUREMENTS.shoulder_cm),
  arm_length_cm: String(EXAMPLE_MEASUREMENTS.arm_length_cm),
  ...overrides,
});

describe('parseCm', () => {
  it('공백, 끝의 cm, 쉼표 소수점을 받아 준다', () => {
    expect(parseCm(' 175 ')).toBe(175);
    expect(parseCm('96.5cm')).toBe(96.5);
    expect(parseCm('96,5 CM')).toBe(96.5);
  });

  it('숫자가 아니면 NaN', () => {
    expect(parseCm('abc')).toBeNaN();
    expect(parseCm('-5')).toBeNaN();
    expect(parseCm('1e3')).toBeNaN();
    expect(parseCm('')).toBeNaN();
  });
});

describe('validateField (문구는 서버 server/errors.py와 같다)', () => {
  it('빈칸', () => {
    expect(validateField('height_cm', '  ')).toBe('키 값을 입력해 주세요.');
  });

  it('숫자가 아님', () => {
    expect(validateField('height_cm', '백칠십')).toBe('키 값은 숫자로 입력해 주세요.');
  });

  it('B1 범위의 끝값은 통과하고, 바로 바깥은 막는다', () => {
    expect(validateField('shoulder_cm', '28')).toBeNull();
    expect(validateField('shoulder_cm', '60')).toBeNull();
    expect(validateField('shoulder_cm', '27.9')).toBe('어깨너비 값은 28~60 cm 사이로 입력해 주세요.');
    expect(validateField('chest_cm', '160.1')).toBe('가슴둘레 값은 60~160 cm 사이로 입력해 주세요.');
    expect(validateField('waist_cm', '50')).toBeNull();
  });

  it('소수점은 첫째 자리까지만', () => {
    expect(validateField('chest_cm', '96.5')).toBeNull();
    expect(validateField('chest_cm', '96.55')).toBe('소수점 첫째 자리까지만 입력해 주세요.');
  });

  it('다른 단위로 잰 것 같으면 cm 값을 알려 준다', () => {
    expect(validateField('height_cm', '1.75')).toContain('m로 잰 값 같아요. cm로는 175 cm입니다.');
    expect(validateField('chest_cm', '38')).toContain('인치로 잰 값 같아요. cm로는 약 96.5 cm입니다.');
    expect(validateField('shoulder_cm', '450')).toContain('mm로 잰 값 같아요. cm로는 45 cm입니다.');
  });
});

describe('validateDraft', () => {
  it('모두 맞으면 계약 모양(Measurements)으로 바꿔 준다', () => {
    const result = validateDraft(draftOf({ chest_cm: '96.5cm' }));
    expect(result).toEqual({ ok: true, measurements: { ...EXAMPLE_MEASUREMENTS, chest_cm: 96.5 } });
  });

  it('틀린 칸마다 오류를 모으고, 화면 순서상 첫 칸을 알려 준다', () => {
    const result = validateDraft(draftOf({ waist_cm: '', arm_length_cm: '200' }));
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(Object.keys(result.errors)).toEqual(['waist_cm', 'arm_length_cm']);
    expect(result.firstInvalid).toBe('waist_cm');
  });
});
