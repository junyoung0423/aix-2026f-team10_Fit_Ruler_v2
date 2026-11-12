import { describe, expect, it } from 'vitest';
import type { ErrorCode, FittingStatus, Stage, StageStatus } from '../api/types';
import { ERROR_LABEL } from '../labels';
import { example } from '../mocks/examples';
import { attemptText, errorText, formatElapsed, jobHeadline, stageMessage, visualStages } from './jobSummary';

const job = (name: string) => example<FittingStatus>(`fitting_status_${name}.json`);

/** 영상 다섯 단계의 상태를 바꾼 작업 상태 */
function withStages(base: FittingStatus, statuses: StageStatus[], jobStatus: FittingStatus['status'] = 'RUNNING') {
  const order: Stage[] = ['BODY_FIT', 'DRAPE', 'RENDER', 'REALIZE', 'VALIDATE'];
  return {
    ...base,
    status: jobStatus,
    stages: [
      ...base.stages.filter((s) => !order.includes(s.stage)),
      ...order.map((stage, i) => ({ stage, status: statuses[i], attempt_no: 1 })),
    ],
  };
}

describe('jobHeadline (작업 전체 상태 한 줄)', () => {
  it('진행 중이면 지금 단계와 몇 번째인지', () => {
    expect(jobHeadline(job('running'))).toEqual({ text: '실사화 중 (4/5)', tone: 'running' });
    const draping = withStages(job('running'), ['SUCCEEDED', 'RUNNING', 'WAITING', 'WAITING', 'WAITING']);
    expect(jobHeadline(draping).text).toBe('3D 착장 중 (2/5)');
    const first = withStages(job('running'), ['RUNNING', 'WAITING', 'WAITING', 'SKIPPED', 'SKIPPED']);
    expect(jobHeadline(first).text).toBe('몸 생성 중 (1/5)');
  });

  it('대기열이면 차례를 기다린다, 단계 사이면 다음 단계 준비 중', () => {
    const queued = withStages(job('running'), Array(5).fill('WAITING'), 'ACCEPTED');
    expect(jobHeadline({ ...queued, visual: { ...queued.visual, status: 'QUEUED' } }).text).toBe('차례를 기다리는 중');
    const between = withStages(job('running'), ['SUCCEEDED', 'SUCCEEDED', 'WAITING', 'WAITING', 'WAITING']);
    expect(jobHeadline(between).text).toBe('정면 렌더 준비 중 (3/5)');
  });

  it('완료, 실사화 미동의면 렌더까지, 리포트만이면 리포트만', () => {
    expect(jobHeadline(job('succeeded'))).toEqual({ text: '완료', tone: 'done' });
    expect(jobHeadline(job('no_consent'))).toEqual({ text: '완료 (렌더까지)', tone: 'done' });
    expect(jobHeadline(job('report_only')).text).toBe('완료 (핏 리포트만)');
  });

  it('일부 완료는 마지막으로 끝난 단계까지 준비됐다고 알린다', () => {
    expect(jobHeadline(job('realize_failed'))).toEqual({
      text: '일부 완료 · 핏 리포트와 정면 렌더까지 준비됨',
      tone: 'partial',
    });
    expect(jobHeadline(job('drape_failed')).text).toBe('일부 완료 · 핏 리포트와 몸 생성까지 준비됨');
    expect(jobHeadline(job('validation_failed')).text).toBe('일부 완료 · 핏 리포트와 실사화까지 준비됨');
    const bodyFailed = withStages(job('drape_failed'), ['FAILED', 'SKIPPED', 'SKIPPED', 'SKIPPED', 'SKIPPED'], 'PARTIAL_SUCCEEDED');
    expect(jobHeadline(bodyFailed).text).toBe('일부 완료 · 핏 리포트만 준비됨');
  });

  it('실패는 실패한 단계 이름', () => {
    const notReady: FittingStatus = {
      ...job('report_only'),
      status: 'FAILED',
      stages: [
        { stage: 'FIT_CALCULATE', status: 'FAILED', attempt_no: 1 },
        { stage: 'FIT_REPORT', status: 'SKIPPED', attempt_no: 1 },
      ],
    };
    expect(jobHeadline(notReady)).toEqual({ text: '실패 · 핏 계산', tone: 'failed' });
  });
});

describe('stageMessage (단계별 실패·건너뜀)', () => {
  it('실패한 단계에는 오류 안내, 그 뒤 단계는 앞 단계 실패로 건너뜀', () => {
    const status = job('drape_failed');
    const messages = visualStages(status.stages).map((s) => [s.stage, stageMessage(s, status.visual)]);
    expect(messages).toEqual([
      ['BODY_FIT', null],
      ['DRAPE', ERROR_LABEL.DRAPE_INVALID],
      ['RENDER', '앞 단계가 실패해 건너뛰었습니다.'],
      ['REALIZE', '앞 단계가 실패해 건너뛰었습니다.'],
      ['VALIDATE', '앞 단계가 실패해 건너뛰었습니다.'],
    ]);
  });

  it('실사화 미동의·리포트만은 건너뛴 이유를 알린다', () => {
    const noConsent = job('no_consent');
    const realize = noConsent.stages.find((s) => s.stage === 'REALIZE')!;
    expect(stageMessage(realize, noConsent.visual)).toBe('실사화(외부 전송)에 동의하지 않아 건너뛰었습니다.');
    const reportOnly = job('report_only');
    expect(stageMessage(reportOnly.stages.find((s) => s.stage === 'BODY_FIT')!, reportOnly.visual)).toBe(
      '핏 리포트만 요청해 건너뛰었습니다.',
    );
  });

  it('화면 표에 없는 오류 코드는 "알 수 없는 오류(코드)"', () => {
    const status = job('realize_failed');
    const realize = status.stages.find((s) => s.stage === 'REALIZE')!;
    const visual = { ...status.visual, error_code: 'NEW_CODE_FROM_SERVER' as ErrorCode };
    expect(stageMessage(realize, visual)).toBe('알 수 없는 오류(NEW_CODE_FROM_SERVER)');
    expect(errorText('REALIZE_TIMEOUT')).toBe(ERROR_LABEL.REALIZE_TIMEOUT);
    expect(errorText('JOB_NOT_FOUND')).toBe('알 수 없는 오류(JOB_NOT_FOUND)');
  });

  it('진행 중·완료·대기 단계에는 설명이 없다', () => {
    const status = job('running');
    expect(visualStages(status.stages).map((s) => stageMessage(s, status.visual))).toEqual([null, null, null, null, null]);
  });
});

describe('attemptText', () => {
  it('attempt_no가 2 이상일 때만 재시도 횟수', () => {
    expect(attemptText({ stage: 'REALIZE', status: 'RUNNING', attempt_no: 1 })).toBeNull();
    expect(attemptText({ stage: 'REALIZE', status: 'RUNNING', attempt_no: 2 })).toBe('재시도 2회째');
    expect(attemptText({ stage: 'REALIZE', status: 'FAILED', attempt_no: 3 })).toBe('재시도 3회째');
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
