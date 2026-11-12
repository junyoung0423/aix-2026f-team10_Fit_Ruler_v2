import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/api';
import type { ApiErrorBody, FitReport, FittingRequest } from '../api/types';
import { canRestart, restartFitting } from '../progress/retry';
import type { StoredResult } from '../state/fittingStore';
import { example } from './examples';
import { createMockApi, type MockApiOptions } from './mockApi';

/** 진행 상태 화면(A4)용 가짜 API 옵션: ?mockFlaky=, ?mockLost=, 없는 작업, 실사화 미동의 첫 응답 */

const REQUEST = example<FittingRequest>('fitting_request.json');

function setup(options: MockApiOptions = {}) {
  let time = 0;
  const api = createMockApi({
    now: () => time,
    latencyMs: 0,
    storage: null,
    failStage: () => null,
    rejectField: () => null,
    flakyCount: () => 0,
    lostJob: () => false,
    ...options,
  });
  return { api, advance: (ms: number) => (time += ms) };
}

describe('가짜 API: 진행 상태 조회 실패 흉내', () => {
  it('없는 작업은 서버와 같은 404 JOB_NOT_FOUND', async () => {
    const error = await setup().api.getFitting('nope').catch((e: unknown) => e);
    const expected = example<ApiErrorBody>('api_error_job_not_found.json');
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404, code: expected.code, message: expected.safe_message });
  });

  it('?mockFlaky=2: 처음 2번은 연결 오류, 그다음부터 정상', async () => {
    const { api } = setup({ flakyCount: () => 2 });
    const job = await api.createFitting(REQUEST);
    await expect(api.getFitting(job.job_id)).rejects.toMatchObject({ status: 0, code: 'NETWORK_ERROR' });
    await expect(api.getFitting(job.job_id)).rejects.toMatchObject({ code: 'NETWORK_ERROR' });
    await expect(api.getFitting(job.job_id)).resolves.toMatchObject({ job_id: job.job_id });
  });

  it('?mockLost=1: 만든 작업도 조회하면 404 JOB_NOT_FOUND', async () => {
    const { api } = setup({ lostJob: () => true });
    const job = await api.createFitting(REQUEST);
    expect(job.report.regions).not.toBeNull(); // 첫 응답(리포트)은 정상
    await expect(api.getFitting(job.job_id)).rejects.toMatchObject({ status: 404, code: 'JOB_NOT_FOUND' });
  });
});

describe('가짜 API: 실사화 미동의', () => {
  it('첫 응답부터 skip_reason CONSENT_NOT_GIVEN, quality_status null (서버와 같게)', async () => {
    const { api } = setup();
    const job = await api.createFitting({ ...REQUEST, realize_consent: false });
    expect(job.visual).toMatchObject({ skip_reason: 'CONSENT_NOT_GIVEN', quality_status: null });
    expect(job.stages.filter((s) => s.stage === 'REALIZE' || s.stage === 'VALIDATE').map((s) => s.status)).toEqual([
      'SKIPPED',
      'SKIPPED',
    ]);
  });

  it('동의하면 진행 중 quality_status PENDING, skip_reason null', async () => {
    const job = await setup().api.createFitting(REQUEST);
    expect(job.visual).toMatchObject({ skip_reason: null, quality_status: 'PENDING' });
  });
});

describe('같은 조건으로 다시 시도', () => {
  const stored = (request?: FittingRequest): StoredResult => ({
    report: example<FitReport>('fit_report.json'),
    garmentId: REQUEST.garment_variant_id,
    garmentName: '기본핏 반팔 티 · M · 네이비',
    jobId: 'job_old',
    request,
  });

  it('저장된 요청이 없으면(새 탭 등) 다시 시도할 수 없다', () => {
    expect(canRestart(stored())).toBe(false);
    expect(canRestart(null)).toBe(false);
    expect(canRestart(stored(REQUEST))).toBe(true);
  });

  it.each([true, false])('저장한 요청을 실사화 동의(%s)까지 그대로 다시 보내고 새 작업 ID를 쓴다', async (consent) => {
    const { api } = setup();
    const createFitting = vi.spyOn(api, 'createFitting');
    const request = { ...REQUEST, realize_consent: consent };
    const value = stored(request);
    if (!canRestart(value)) throw new Error('저장된 요청이 있어야 한다');
    const next = await restartFitting(api, value);
    expect(createFitting).toHaveBeenCalledWith(request);
    expect(next.id).not.toBe('job_old');
    expect(next.result).toEqual({ ...value, jobId: next.id });
    const status = await api.getFitting(next.id);
    expect(status.visual.skip_reason).toBe(consent ? null : 'CONSENT_NOT_GIVEN');
  });

  it('다시 보내다 실패하면 오류를 그대로 넘긴다', async () => {
    const { api } = setup();
    const value = stored({ ...REQUEST, garment_variant_id: 'removed' });
    if (!canRestart(value)) throw new Error('저장된 요청이 있어야 한다');
    await expect(restartFitting(api, value)).rejects.toMatchObject({ status: 404, code: 'GARMENT_NOT_FOUND' });
  });
});
