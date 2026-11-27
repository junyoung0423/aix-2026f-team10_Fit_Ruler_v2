import { describe, expect, it } from 'vitest';
import type { FittingStatus, Stage, StageStatus } from '../api/types';
import { example } from '../mocks/examples';
import { STEP_ETA, currentStep, etaNote, outcomeOf, stepViews } from './steps';

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

const states = (status: FittingStatus) => stepViews(status).map((view) => view.state);

describe('stepViews (사용자용 4단계)', () => {
  it('서버 다섯 단계를 몸 만들기·옷 입히기·정면 사진·실사 사진으로 묶는다', () => {
    expect(stepViews(job('running')).map((view) => view.label)).toEqual(['몸 만들기', '옷 입히기', '정면 사진', '실사 사진']);
    expect(states(job('running'))).toEqual(['done', 'done', 'done', 'running']);
    expect(states(job('succeeded'))).toEqual(['done', 'done', 'done', 'done']);
  });

  it('실사화가 끝나고 검증 중이면 실사 사진은 아직 진행 중이다', () => {
    const checking = withStages(job('running'), ['SUCCEEDED', 'SUCCEEDED', 'SUCCEEDED', 'SUCCEEDED', 'RUNNING']);
    expect(states(checking)[3]).toBe('running');
    expect(currentStep(checking)).toEqual({ key: 'realize', checking: true, preparing: false });
  });

  it('검증만 실패하면 실사 사진은 실패가 아니라 검증 실패 표시다', () => {
    const view = stepViews(job('validation_failed'))[3];
    expect(view).toMatchObject({ state: 'warning', note: '검증 실패' });
  });

  it('건너뛴 단계에는 이유를 붙인다', () => {
    expect(stepViews(job('no_consent'))[3]).toMatchObject({ state: 'skipped', note: '동의하지 않아 건너뜀' });
    expect(stepViews(job('drape_failed')).map((view) => view.note)).toEqual([null, '여기서 멈춤', '앞 단계에서 멈춤', '앞 단계에서 멈춤']);
    expect(stepViews(job('report_only'))[0]).toMatchObject({ state: 'skipped', note: '핏 리포트만 요청' });
  });
});

describe('currentStep (지금 단계)', () => {
  it('진행 중인 단계, 단계 사이면 다음 단계를 준비 중으로', () => {
    expect(currentStep(job('running'))).toEqual({ key: 'realize', checking: false, preparing: false });
    const between = withStages(job('running'), ['SUCCEEDED', 'WAITING', 'WAITING', 'WAITING', 'WAITING']);
    expect(currentStep(between)).toEqual({ key: 'drape', checking: false, preparing: true });
    const first = withStages(job('running'), Array(5).fill('WAITING'), 'ACCEPTED');
    expect(currentStep(first)?.key).toBe('body');
  });

  it('작업이 끝났으면 없다', () => {
    expect(currentStep(job('succeeded'))).toBeNull();
    expect(currentStep(job('realize_failed'))).toBeNull();
  });
});

describe('outcomeOf (끝난 뒤 결과 칸)', () => {
  it('정상 완료, 진행 중', () => {
    expect(outcomeOf(job('succeeded'))).toEqual({ kind: 'success' });
    expect(outcomeOf(job('running'))).toEqual({ kind: 'running' });
  });

  it('동의하지 않았으면 정면 사진까지만, 리포트만 요청했으면 리포트만', () => {
    expect(outcomeOf(job('no_consent'))).toEqual({ kind: 'no_consent' });
    expect(outcomeOf(job('report_only'))).toEqual({ kind: 'report_only' });
  });

  it('정면 사진·실사 사진에서 멈추면 3D 미리보기를 대체 결과로 보여 준다', () => {
    expect(outcomeOf(job('realize_failed'))).toEqual({
      kind: 'fallback',
      failedStep: 'realize',
      message: '실사화 응답이 제시간에 오지 않았습니다.',
    });
  });

  it('몸 만들기·옷 입히기에서 멈추면 대체 결과 없이 실패다', () => {
    expect(outcomeOf(job('drape_failed'))).toEqual({
      kind: 'failed',
      failedStep: 'drape',
      message: '3D 착장 결과가 올바르지 않아 멈췄습니다.',
    });
    const bodyFailed = withStages(job('drape_failed'), ['FAILED', 'SKIPPED', 'SKIPPED', 'SKIPPED', 'SKIPPED'], 'PARTIAL_SUCCEEDED');
    const body = { ...bodyFailed, visual: { ...bodyFailed.visual, stage: 'BODY_FIT' as const, error_code: 'BODY_FIT_NOT_CONVERGED' as const } };
    expect(outcomeOf(body)).toMatchObject({ kind: 'failed', failedStep: 'body', message: '입력한 치수에 맞는 몸을 만들지 못했습니다.' });
  });

  it('검증만 실패하면 영상은 보여 주되 검증 실패로 구분한다', () => {
    expect(outcomeOf(job('validation_failed'))).toEqual({
      kind: 'validation_failed',
      message: '실사 영상이 렌더와 색·실루엣이 달라 검증을 통과하지 못했습니다.',
    });
  });

  it('의류를 쓸 수 없어 작업 전체가 실패하면 리포트 오류 문구를 쓴다', () => {
    const base = withStages(job('drape_failed'), Array(5).fill('SKIPPED'), 'FAILED');
    const notReady: FittingStatus = {
      ...base,
      report: { ...base.report, status: 'FAILED', error_code: 'GARMENT_NOT_READY' },
      visual: { ...base.visual, status: 'SKIPPED', stage: null, error_code: null },
    };
    expect(outcomeOf(notReady)).toEqual({ kind: 'failed', failedStep: null, message: '선택한 의류를 아직 사용할 수 없습니다.' });
  });
});

describe('etaNote (예상 시간 안내)', () => {
  it('보통은 실측 범위를, 실측 최대의 1.5배를 넘기면 오래 걸린다고 알린다', () => {
    expect(etaNote('render', 30)).toEqual({ text: STEP_ETA.render.text, slow: false });
    expect(etaNote('render', null).slow).toBe(false);
    expect(etaNote('render', 62 * 1.5 + 1)).toEqual({ text: '평소보다 오래 걸리고 있어요. 조금만 더 기다려 주세요.', slow: true });
  });
});
