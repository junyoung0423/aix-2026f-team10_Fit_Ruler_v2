import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/api';
import type { ApiErrorBody, FittingStatus } from '../api/types';
import { example } from '../mocks/examples';
import { MAX_CONSECUTIVE_FAILURES, POLL_INTERVAL_MS, isJobLost, retryDelayMs, startPolling, type PollStop } from './poller';

const RUNNING = example<FittingStatus>('fitting_status_running.json');
const SUCCEEDED = example<FittingStatus>('fitting_status_succeeded.json');
const NETWORK = new ApiError(0, 'NETWORK_ERROR', '서버에 연결할 수 없습니다.');
const LOST = ApiError.fromBody(404, example<ApiErrorBody>('api_error_job_not_found.json'));

type Step = FittingStatus | Error;

/** 차례대로 응답하는 가짜 조회. 목록이 끝나면 마지막 값을 계속 쓴다. */
function setup(steps: Step[]) {
  const fetchStatus = vi.fn(async () => {
    const step = steps.length > 1 ? steps.shift()! : steps[0];
    if (step instanceof Error) throw step;
    return step;
  });
  const statuses: FittingStatus[] = [];
  const retries: { failures: number; delayMs: number }[] = [];
  const stops: PollStop[] = [];
  const stop = startPolling(fetchStatus, {
    onStatus: (job) => statuses.push(job),
    onRetry: (_error, failures, delayMs) => retries.push({ failures, delayMs }),
    onStop: (value) => stops.push(value),
  });
  return { fetchStatus, statuses, retries, stops, stop };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe('retryDelayMs', () => {
  it('1초 → 2초 → 4초 → 8초, 최대 10초', () => {
    expect([1, 2, 3, 4, 5, 6].map(retryDelayMs)).toEqual([1000, 2000, 4000, 8000, 10_000, 10_000]);
  });
});

describe('isJobLost', () => {
  it('404 JOB_NOT_FOUND만 작업 기록 없음으로 본다', () => {
    expect(isJobLost(LOST)).toBe(true);
    expect(isJobLost(new ApiError(404, 'UNEXPECTED_RESPONSE', '없음'))).toBe(false);
    expect(isJobLost(new ApiError(404, 'GARMENT_NOT_FOUND', '없음'))).toBe(false);
    expect(isJobLost(NETWORK)).toBe(false);
  });
});

describe('startPolling', () => {
  it('진행 중이면 1초마다 다시 부르고, 끝난 상태를 받으면 멈춘다', async () => {
    const t = setup([RUNNING, RUNNING, SUCCEEDED]);
    await vi.advanceTimersByTimeAsync(0);
    expect(t.fetchStatus).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS - 1);
    expect(t.fetchStatus).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(t.fetchStatus).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    expect(t.fetchStatus).toHaveBeenCalledTimes(3);
    expect(t.stops).toEqual([{ reason: 'done', job: SUCCEEDED }]);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(t.fetchStatus).toHaveBeenCalledTimes(3);
  });

  it.each(['SUCCEEDED', 'PARTIAL_SUCCEEDED', 'FAILED', 'CANCELLED'] as const)('%s면 더 부르지 않는다', async (status) => {
    const t = setup([{ ...RUNNING, status }]);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(t.fetchStatus).toHaveBeenCalledTimes(1);
    expect(t.stops.map((s) => s.reason)).toEqual(['done']);
  });

  it('ACCEPTED·RUNNING이면 계속 부른다', async () => {
    const t = setup([{ ...RUNNING, status: 'ACCEPTED' }]);
    await vi.advanceTimersByTimeAsync(3 * POLL_INTERVAL_MS);
    expect(t.fetchStatus).toHaveBeenCalledTimes(4);
    expect(t.stops).toEqual([]);
  });

  it('실패하면 간격을 1 → 2 → 4초로 늘리고, 성공하면 다시 1초', async () => {
    const t = setup([NETWORK, NETWORK, NETWORK, RUNNING, RUNNING, NETWORK, RUNNING]);
    await vi.advanceTimersByTimeAsync(0); // 1번째 실패
    expect(t.retries).toEqual([{ failures: 1, delayMs: 1000 }]);
    await vi.advanceTimersByTimeAsync(1000); // 2번째 실패
    await vi.advanceTimersByTimeAsync(1999);
    expect(t.fetchStatus).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1); // 3번째 실패
    expect(t.fetchStatus).toHaveBeenCalledTimes(3);
    expect(t.retries.map((r) => r.delayMs)).toEqual([1000, 2000, 4000]);
    await vi.advanceTimersByTimeAsync(4000); // 성공
    expect(t.statuses).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1000); // 성공 뒤 1초
    expect(t.statuses).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(1000); // 다시 실패하면 연속 실패 수도 1부터
    expect(t.retries[t.retries.length - 1]).toEqual({ failures: 1, delayMs: 1000 });
    expect(t.stops).toEqual([]);
  });

  it('연속 실패가 5번이면 멈춘다(4번은 계속)', async () => {
    const t = setup([NETWORK]);
    await vi.advanceTimersByTimeAsync(0);
    for (const delay of [1000, 2000, 4000]) await vi.advanceTimersByTimeAsync(delay);
    expect(t.fetchStatus).toHaveBeenCalledTimes(MAX_CONSECUTIVE_FAILURES - 1);
    expect(t.stops).toEqual([]);
    await vi.advanceTimersByTimeAsync(8000);
    expect(t.fetchStatus).toHaveBeenCalledTimes(MAX_CONSECUTIVE_FAILURES);
    expect(t.stops).toEqual([{ reason: 'gave_up', error: NETWORK }]);
    expect(t.retries).toHaveLength(MAX_CONSECUTIVE_FAILURES - 1);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(t.fetchStatus).toHaveBeenCalledTimes(MAX_CONSECUTIVE_FAILURES);
  });

  it('404 JOB_NOT_FOUND는 바로 멈춘다', async () => {
    const t = setup([RUNNING, LOST]);
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    expect(t.stops).toEqual([{ reason: 'lost', error: LOST }]);
    expect(t.retries).toEqual([]);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(t.fetchStatus).toHaveBeenCalledTimes(2);
  });

  it('멈추면(화면을 떠나면) 예약한 호출을 지우고 늦게 온 응답도 버린다', async () => {
    const t = setup([RUNNING]);
    await vi.advanceTimersByTimeAsync(0);
    t.stop();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(t.fetchStatus).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);

    let resolve: (job: FittingStatus) => void = () => {};
    const onStatus = vi.fn();
    const stop = startPolling(() => new Promise<FittingStatus>((r) => (resolve = r)), { onStatus, onStop: vi.fn() });
    stop();
    resolve(RUNNING);
    await vi.advanceTimersByTimeAsync(0);
    expect(onStatus).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });
});
