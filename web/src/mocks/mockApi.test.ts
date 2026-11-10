import { describe, expect, it } from 'vitest';
import { ApiError } from '../api/api';
import type { BodyMeasurements, StageName } from '../api/types';
import { createMockApi } from './mockApi';

const body: BodyMeasurements = {
  heightCm: 175,
  chestCircumferenceCm: 96,
  waistCircumferenceCm: 82,
  shoulderWidthCm: 45,
  armLengthCm: 60,
};

function setup(failStage: StageName | null = null) {
  let time = 0;
  const api = createMockApi({ now: () => time, latencyMs: 0, storage: null, failStage: () => failStage });
  return {
    api,
    advance(ms: number) {
      time += ms;
    },
  };
}

const statusMap = (stages: { name: string; status: string }[]) =>
  Object.fromEntries(stages.map((stage) => [stage.name, stage.status]));

describe('가짜 API', () => {
  it('핏 리포트는 작업을 만들자마자 들어 있다', async () => {
    const { api } = setup();
    const job = await api.createFitting({ body, garmentId: 'sample-basic-white', realizeConsent: true });
    expect(job.fitReport.parts.length).toBeGreaterThan(0);
    expect(statusMap(job.stages)).toEqual({
      body: 'running',
      drape: 'pending',
      render: 'pending',
      realize: 'pending',
      verify: 'pending',
    });
  });

  it('모두 성공하면 실사 결과와 검증 결과가 있다', async () => {
    const { api, advance } = setup();
    const { jobId } = await api.createFitting({ body, garmentId: 'sample-basic-white', realizeConsent: true });
    advance(20_000);
    const job = await api.getFitting(jobId);
    expect(job.stages.every((stage) => stage.status === 'succeeded')).toBe(true);
    expect(job.previewImageUrl).not.toBeNull();
    expect(job.realization?.frameUrl).not.toBeNull();
    expect(job.verification?.passed).toBe(true);
    expect(job.fallbackPreview).toBe(false);
  });

  it('외부 전송에 동의하지 않으면 실사화와 검증을 건너뛴다', async () => {
    const { api, advance } = setup();
    const { jobId } = await api.createFitting({ body, garmentId: 'sample-basic-white', realizeConsent: false });
    advance(20_000);
    const job = await api.getFitting(jobId);
    expect(statusMap(job.stages)).toMatchObject({ render: 'succeeded', realize: 'skipped', verify: 'skipped' });
    expect(job.previewImageUrl).not.toBeNull();
    expect(job.realization).toBeNull();
    expect(job.fallbackPreview).toBe(false);
  });

  it('실사화가 실패하면 뒤 단계는 건너뛰고 3D 미리보기를 대체 결과로 표시한다', async () => {
    const { api, advance } = setup('realize');
    const { jobId } = await api.createFitting({ body, garmentId: 'sample-basic-white', realizeConsent: true });
    advance(20_000);
    const job = await api.getFitting(jobId);
    expect(statusMap(job.stages)).toMatchObject({ realize: 'failed', verify: 'skipped' });
    expect(job.realization).toBeNull();
    expect(job.fallbackPreview).toBe(true);
    expect(job.fitReport.parts.length).toBeGreaterThan(0);
  });

  it('검증만 실패하면 실사 결과는 남기고 검증 실패를 알린다', async () => {
    const { api, advance } = setup('verify');
    const { jobId } = await api.createFitting({ body, garmentId: 'sample-basic-white', realizeConsent: true });
    advance(20_000);
    const job = await api.getFitting(jobId);
    expect(job.realization).not.toBeNull();
    expect(job.verification?.passed).toBe(false);
  });

  it('없는 작업은 404를 돌려준다', async () => {
    const { api } = setup();
    await expect(api.getFitting('nope')).rejects.toMatchObject({ status: 404 });
  });

  it('치수가 비어 있으면 422와 칸별 오류를 돌려준다', async () => {
    const { api } = setup();
    const error = await api
      .createFitting({ body: { ...body, shoulderWidthCm: 0 }, garmentId: 'sample-basic-white', realizeConsent: false })
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(422);
    expect((error as ApiError).fieldErrors.shoulderWidthCm).toBeDefined();
  });
});
