import { ApiError } from '../api/api';
import type { FittingStatus } from '../api/types';

/**
 * 작업 상태(`GET /api/fittings/{job_id}`)를 다시 부르는 규칙. 화면 컴포넌트와 떼어 두어 가짜 타이머로 테스트한다.
 *
 * - 성공하면 1초 뒤 다시 부른다. 끝난 상태(SUCCEEDED·PARTIAL_SUCCEEDED·FAILED·CANCELLED)면 멈춘다.
 * - 실패하면 1초 → 2초 → 4초 …(최대 10초) 뒤 다시 부른다. 성공하면 간격은 다시 1초다.
 * - 연속 실패가 5번이면 멈춘다(`gave_up`). 화면은 "다시 불러오기"로 새로 시작한다.
 * - 404 `JOB_NOT_FOUND`는 바로 멈춘다(`lost`). 서버를 다시 켜면 작업 기록이 사라진다(docs/api/README.md).
 */

export const POLL_INTERVAL_MS = 1000;
export const MAX_RETRY_DELAY_MS = 10_000;
export const MAX_CONSECUTIVE_FAILURES = 5;

/** n번째 연속 실패(1부터) 뒤에 기다릴 시간 */
export function retryDelayMs(failures: number): number {
  return Math.min(POLL_INTERVAL_MS * 2 ** Math.max(failures - 1, 0), MAX_RETRY_DELAY_MS);
}

/** 작업이 끝나 더 부를 필요가 없는지 */
export const isTerminal = (job: FittingStatus) => job.status !== 'ACCEPTED' && job.status !== 'RUNNING';

/** 서버에 작업 기록이 없는지(서버 재시작 등) */
export const isJobLost = (error: unknown) =>
  error instanceof ApiError && error.status === 404 && error.code === 'JOB_NOT_FOUND';

export type PollStop =
  | { reason: 'done'; job: FittingStatus }
  | { reason: 'lost'; error: unknown }
  | { reason: 'gave_up'; error: unknown };

export interface PollHandlers {
  onStatus: (job: FittingStatus) => void;
  /** 실패했지만 다시 부를 때. failures는 연속 실패 수(1부터) */
  onRetry?: (error: unknown, failures: number, delayMs: number) => void;
  onStop: (stop: PollStop) => void;
}

export interface PollTimers {
  setTimeout: (fn: () => void, ms: number) => unknown;
  clearTimeout: (id: unknown) => void;
}

const defaultTimers: PollTimers = {
  setTimeout: (fn, ms) => globalThis.setTimeout(fn, ms),
  clearTimeout: (id) => globalThis.clearTimeout(id as ReturnType<typeof setTimeout>),
};

/** 바로 한 번 부르고 위 규칙대로 이어 부른다. 돌려준 함수를 부르면 멈춘다(화면을 떠날 때). */
export function startPolling(
  fetchStatus: () => Promise<FittingStatus>,
  handlers: PollHandlers,
  timers: PollTimers = defaultTimers,
): () => void {
  let alive = true;
  let failures = 0;
  let timer: unknown = null;

  const schedule = (ms: number) => {
    timer = timers.setTimeout(() => {
      timer = null;
      void tick();
    }, ms);
  };

  async function tick() {
    let job: FittingStatus;
    try {
      job = await fetchStatus();
    } catch (error) {
      if (!alive) return;
      if (isJobLost(error)) {
        alive = false;
        handlers.onStop({ reason: 'lost', error });
        return;
      }
      failures += 1;
      if (failures >= MAX_CONSECUTIVE_FAILURES) {
        alive = false;
        handlers.onStop({ reason: 'gave_up', error });
        return;
      }
      const delay = retryDelayMs(failures);
      handlers.onRetry?.(error, failures, delay);
      schedule(delay);
      return;
    }
    if (!alive) return;
    failures = 0;
    handlers.onStatus(job);
    if (isTerminal(job)) {
      alive = false;
      handlers.onStop({ reason: 'done', job });
      return;
    }
    schedule(POLL_INTERVAL_MS);
  }

  void tick();
  return () => {
    alive = false;
    if (timer !== null) timers.clearTimeout(timer);
    timer = null;
  };
}
