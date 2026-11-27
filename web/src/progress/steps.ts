import type { FittingStatus, Stage, StageStatus } from '../api/types';
import { errorText } from './jobSummary';

/**
 * 결과 화면의 사용자용 4단계. 서버 단계(몸 생성 → 3D 착장 → 정면 렌더 → 실사화 → 검증)를
 * 사용자가 알아듣는 이름으로 묶는다. 검증은 따로 보이지 않고 실사 사진 단계 안에서 "확인 중"으로 보인다.
 * 서버 단계 이름은 화면에 보이지 않는다.
 */

export type StepKey = 'body' | 'drape' | 'render' | 'realize';
export type StepState = 'waiting' | 'running' | 'done' | 'warning' | 'failed' | 'skipped';

export const STEP_ORDER: readonly StepKey[] = ['body', 'drape', 'render', 'realize'];

export const STEP_LABEL: Record<StepKey, string> = {
  body: '몸 만들기',
  drape: '옷 입히기',
  render: '정면 사진',
  realize: '실사 사진',
};

/** 진행 중일 때 단계 아래에 쓰는 문장 */
export const STEP_DOING: Record<StepKey, string> = {
  body: '입력한 치수로 몸을 만들고 있어요.',
  drape: '고른 티셔츠를 몸에 입히고 있어요.',
  render: '옷 입은 모습을 정면에서 3D 사진으로 찍고 있어요.',
  realize: '3D 사진을 실제 사진처럼 바꾸고 8초 회전 영상을 만들고 있어요.',
};

/** 실사화가 끝나고 검증하는 동안 */
export const CHECKING_DOING = '실사 사진이 3D 사진과 같은 핏인지 확인하고 있어요.';

export interface StepEta {
  minSeconds: number;
  maxSeconds: number;
  text: string;
}

/**
 * 보통 걸리는 시간. 실제 서버를 이 PC에서 돌린 실측으로 잡았다(가짜 API는 몇 초 만에 끝난다).
 * - 몸 만들기: docs/body-fit.md "통과하는 몸은 12~20초"
 * - 옷 입히기: docs/adapters.md 착장 "실측 40~430초"
 * - 정면 사진: docs/adapters.md 렌더 "실측 40~62초"
 * - 실사 사진: config/pipeline_v0.yaml "피팅룸 실사화(사진·회전 약 2분)" + 맨 위 자르기 약 25초
 *   (docs/realize-fitroom.md) + 검증 약 15초(config/pipeline_v0.yaml)
 */
export const STEP_ETA: Record<StepKey, StepEta> = {
  body: { minSeconds: 12, maxSeconds: 20, text: '보통 20초 안팎 걸려요' },
  drape: { minSeconds: 40, maxSeconds: 430, text: '보통 1~7분 걸려요' },
  render: { minSeconds: 40, maxSeconds: 62, text: '보통 1분 안팎 걸려요' },
  realize: { minSeconds: 120, maxSeconds: 180, text: '보통 2~3분 걸려요' },
};

/** 실측 최대의 이만큼 배를 넘기면 "평소보다 오래" 안내를 띄운다 */
export const SLOW_FACTOR = 1.5;

const STAGE_OF: Record<Exclude<StepKey, 'realize'>, Stage> = { body: 'BODY_FIT', drape: 'DRAPE', render: 'RENDER' };

const backend = (job: FittingStatus, stage: Stage): StageStatus | undefined =>
  job.stages.find((item) => item.stage === stage)?.status;

function simpleState(status: StageStatus | undefined): StepState {
  switch (status) {
    case 'RUNNING':
      return 'running';
    case 'SUCCEEDED':
      return 'done';
    case 'FAILED':
      return 'failed';
    case 'SKIPPED':
    case 'CANCELLED':
      return 'skipped';
    default:
      return 'waiting'; // WAITING·QUEUED·아직 없음
  }
}

/** 실사 사진 = 실사화 + 검증. 검증만 실패하면 영상은 있으므로 실패가 아니라 'warning'이다(wireframes.md ⑤). */
function realizeState(job: FittingStatus): StepState {
  const realize = simpleState(backend(job, 'REALIZE'));
  if (realize !== 'done') return realize;
  const validate = backend(job, 'VALIDATE');
  if (validate === 'RUNNING' || validate === 'WAITING' || validate === 'QUEUED') return 'running';
  if (validate === 'FAILED') return 'warning';
  return 'done';
}

export interface StepView {
  key: StepKey;
  label: string;
  state: StepState;
  /** 단계 이름 아래 짧은 설명. 없으면 null */
  note: string | null;
}

function stepNote(job: FittingStatus, key: StepKey, state: StepState): string | null {
  if (state === 'skipped') {
    if (job.visual.skip_reason === 'REPORT_ONLY') return '핏 리포트만 요청';
    if (key === 'realize' && job.visual.skip_reason === 'CONSENT_NOT_GIVEN') return '동의하지 않아 건너뜀';
    return '앞 단계에서 멈춤';
  }
  if (state === 'failed') return '여기서 멈춤';
  if (state === 'warning') return '검증 실패';
  return null;
}

export function stepViews(job: FittingStatus): StepView[] {
  return STEP_ORDER.map((key) => {
    const state = key === 'realize' ? realizeState(job) : simpleState(backend(job, STAGE_OF[key]));
    return { key, label: STEP_LABEL[key], state, note: stepNote(job, key, state) };
  });
}

export interface CurrentStep {
  key: StepKey;
  /** 실사화가 끝나고 검증하는 중 */
  checking: boolean;
  /** 앞 단계가 끝나고 이 단계를 기다리는 중(단계 사이) */
  preparing: boolean;
}

/** 지금 진행 중인 단계. 단계 사이면 다음 단계를 "준비 중"으로 본다. 작업이 끝났으면 null */
export function currentStep(job: FittingStatus): CurrentStep | null {
  if (job.status !== 'ACCEPTED' && job.status !== 'RUNNING') return null;
  const views = stepViews(job);
  const running = views.find((view) => view.state === 'running');
  if (running) {
    const checking = running.key === 'realize' && backend(job, 'REALIZE') === 'SUCCEEDED';
    return { key: running.key, checking, preparing: false };
  }
  const next = views.find((view) => view.state === 'waiting');
  return next ? { key: next.key, checking: false, preparing: true } : null;
}

/** 작업이 끝난 뒤 결과 칸에 무엇을 보여 줄지 */
export type Outcome =
  | { kind: 'running' }
  | { kind: 'success' }
  /** 실사 영상은 있지만 렌더와 색·실루엣이 달라 검증을 통과하지 못함 */
  | { kind: 'validation_failed'; message: string }
  /** 실사화(외부 전송)에 동의하지 않아 정면 사진까지만 */
  | { kind: 'no_consent' }
  | { kind: 'report_only' }
  /** 정면 사진·실사 사진 단계에서 멈춰 3D 미리보기를 대체 결과로 */
  | { kind: 'fallback'; failedStep: StepKey; message: string }
  /** 몸 만들기·옷 입히기에서 멈췄거나 의류를 쓸 수 없어 핏 리포트만 */
  | { kind: 'failed'; failedStep: StepKey | null; message: string }
  | { kind: 'cancelled' };

export function outcomeOf(job: FittingStatus): Outcome {
  if (job.status === 'ACCEPTED' || job.status === 'RUNNING') return { kind: 'running' };
  if (job.status === 'CANCELLED') return { kind: 'cancelled' };
  const { visual } = job;
  if (visual.skip_reason === 'REPORT_ONLY') return { kind: 'report_only' };

  const views = stepViews(job);
  const visualMessage = visual.error_code ? errorText(visual.error_code) : null;
  if (views[views.length - 1].state === 'warning') {
    return { kind: 'validation_failed', message: visualMessage ?? errorText('VALIDATION_FAILED') };
  }

  const failed = views.find((view) => view.state === 'failed');
  if (failed || visual.status === 'FAILED' || job.status === 'FAILED') {
    const message =
      visualMessage ??
      (job.report.error_code ? errorText(job.report.error_code) : '3D·실사 단계를 마치지 못했어요.');
    if (failed && (failed.key === 'render' || failed.key === 'realize') && visual.drape_preview_artifact_id) {
      return { kind: 'fallback', failedStep: failed.key, message };
    }
    return { kind: 'failed', failedStep: failed?.key ?? null, message };
  }

  if (visual.skip_reason === 'CONSENT_NOT_GIVEN') return { kind: 'no_consent' };
  return { kind: 'success' };
}

/** 예상 시간 안내. 화면이 그 단계를 처음 본 뒤 흐른 시간이 실측 최대의 SLOW_FACTOR배를 넘으면 "평소보다 오래" */
export function etaNote(key: StepKey, elapsedSeconds: number | null): { text: string; slow: boolean } {
  const eta = STEP_ETA[key];
  const slow = elapsedSeconds !== null && elapsedSeconds > eta.maxSeconds * SLOW_FACTOR;
  return { text: slow ? '평소보다 오래 걸리고 있어요. 조금만 더 기다려 주세요.' : eta.text, slow };
}
