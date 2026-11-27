import { describe, expect, it } from 'vitest';
import { ERROR_LABEL } from '../labels';
import { errorText, formatElapsed } from './jobSummary';

describe('errorText', () => {
  it('화면 표에 있는 코드는 안내 문구, 없는 코드는 "알 수 없는 오류(코드)"', () => {
    expect(errorText('REALIZE_TIMEOUT')).toBe(ERROR_LABEL.REALIZE_TIMEOUT);
    expect(errorText('NEW_CODE_FROM_SERVER')).toBe('알 수 없는 오류(NEW_CODE_FROM_SERVER)');
    expect(errorText('JOB_NOT_FOUND')).toBe('알 수 없는 오류(JOB_NOT_FOUND)');
  });
});

describe('formatElapsed', () => {
  it.each([
    [0, '0:00'],
    [999, '0:00'],
    [1000, '0:01'],
    [42_000, '0:42'],
    [59_999, '0:59'],
    [60_000, '1:00'],
    [725_000, '12:05'],
    [3_599_000, '59:59'],
    [3_600_000, '1:00:00'],
    [3_723_000, '1:02:03'],
    [-500, '0:00'],
  ])('%d ms → %s', (ms, text) => {
    expect(formatElapsed(ms)).toBe(text);
  });
});
