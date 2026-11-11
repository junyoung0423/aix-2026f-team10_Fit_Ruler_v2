import { describe, expect, it } from 'vitest';
import { ApiError } from '../api/api';
import type { MeasurementField } from '../api/contract';
import type { FittingRequest, FittingStatus, GarmentList, Stage } from '../api/types';
import { example } from './examples';
import { createMockApi } from './mockApi';


const REQUEST = example<FittingRequest>('fitting_request.json');

function setup(failStage: Stage | null = null, rejectField: MeasurementField | null = null) {
  let time = 0;
  const api = createMockApi({
    now: () => time,
    latencyMs: 0,
    storage: null,
    failStage: () => failStage,
    rejectField: () => rejectField,
  });
  return { api, advance: (ms: number) => (time += ms) };
}

const keysOf = (value: object) => Object.keys(value).sort();
const stageStatus = (job: FittingStatus, stage: Stage) => job.stages.find((s) => s.stage === stage)?.status;

describe('계약 모양', () => {
  it('의류 목록이 garment_list.json과 같은 모양이다', async () => {
    const list = await setup().api.listGarments();
    const expected = example<GarmentList>('garment_list.json');
    expect(keysOf(list)).toEqual(keysOf(expected));
    expect(list.garments[0]).toEqual(expected.garments[0]);
    expect(list.garments.every((item) => item.garment.status === 'READY')).toBe(true);
  });

  it('작업 상태가 fitting_status_*.json과 같은 키를 가진다', async () => {
    const { api, advance } = setup();
    const job = await api.createFitting(REQUEST);
    advance(20_000);
    const done = await api.getFitting(job.job_id);
    const expected = example<FittingStatus>('fitting_status_succeeded.json');
    expect(keysOf(done)).toEqual(keysOf(expected));
    expect(keysOf(done.report)).toEqual(keysOf(expected.report));
    expect(keysOf(done.visual)).toEqual(keysOf(expected.visual));
    expect(done.stages.map((s) => s.stage)).toEqual(expected.stages.map((s) => s.stage));
  });
});

describe('POST /api/fit', () => {
  it('예시 요청이면 예시 리포트와 같은 판정', async () => {
    const report = await setup().api.fit(REQUEST);
    expect(report.summary).toBe(example<{ summary: string }>('fit_report.json').summary);
  });

  it('범위 밖 값은 422 INPUT_INVALID와 칸별 오류(서버와 같은 문구)', async () => {
    const error = await setup()
      .api.fit({ ...REQUEST, measurements: { ...REQUEST.measurements, chest_cm: 170 } })
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 422, code: 'INPUT_INVALID', message: '입력한 값을 확인해 주세요.' });
    expect((error as ApiError).fields).toEqual([
      {
        field: 'measurements.chest_cm',
        code: 'OUT_OF_RANGE',
        message: '가슴둘레 값은 60~160 cm 사이로 입력해 주세요.',
        min: 60,
        max: 160,
      },
    ]);
  });

  it('rejectField를 주면 범위 안의 값도 422로 거절한다(?mockReject)', async () => {
    const error = (await setup(null, 'shoulder_cm').api.fit(REQUEST).catch((e: unknown) => e)) as ApiError;
    expect(error.status).toBe(422);
    expect(error.fields.map((f) => f.field)).toEqual(['measurements.shoulder_cm']);
  });

  it('없는 의류는 404 GARMENT_NOT_FOUND, 프로토콜이 다르면 422', async () => {
    const { api } = setup();
    await expect(api.fit({ ...REQUEST, garment_variant_id: 'nope' })).rejects.toMatchObject({
      status: 404,
      code: 'GARMENT_NOT_FOUND',
    });
    await expect(api.fit({ ...REQUEST, measurement_protocol_version: 'v0' })).rejects.toMatchObject({
      status: 422,
      code: 'INPUT_INVALID',
    });
  });
});

describe('POST·GET /api/fittings (B3 전 가짜)', () => {
  it('단계가 시간에 따라 진행되고 끝나면 검증 통과', async () => {
    const { api, advance } = setup();
    const created = await api.createFitting(REQUEST);
    expect(created.status).toBe('RUNNING');
    expect(created.report.status).toBe('SUCCEEDED');
    expect(stageStatus(created, 'BODY_FIT')).toBe('RUNNING');
    expect(created.visual.quality_status).toBe('PENDING');

    advance(5_500);
    const middle = await api.getFitting(created.job_id);
    expect(stageStatus(middle, 'RENDER')).toBe('RUNNING');
    expect(middle.visual.drape_preview_artifact_id).toBe(`${created.job_id}_drape_preview`);
    expect(api.artifactUrl(middle.visual.drape_preview_artifact_id ?? '')).toMatch(/^data:image\/svg\+xml/);

    advance(10_000);
    const done = await api.getFitting(created.job_id);
    expect(done.status).toBe('SUCCEEDED');
    expect(done.visual).toMatchObject({ status: 'SUCCEEDED', stage: 'VALIDATE', quality_status: 'PASS' });
    expect(done.visual.quality_metrics).toEqual({ color_delta_e: 3.2, silhouette_iou: 0.91 });
    expect(api.artifactUrl(done.visual.video_artifact_id ?? '')).toBeNull(); // 가짜 영상 파일은 없다
  });

  it('실사화에 동의하지 않으면 렌더까지만 하고 성공(no_consent 예시와 같은 모양)', async () => {
    const { api, advance } = setup();
    const created = await api.createFitting({ ...REQUEST, realize_consent: false });
    advance(20_000);
    const done = await api.getFitting(created.job_id);
    const expected = example<FittingStatus>('fitting_status_no_consent.json');
    expect(done.status).toBe(expected.status);
    expect(done.visual).toMatchObject({
      status: expected.visual.status,
      stage: expected.visual.stage,
      skip_reason: 'CONSENT_NOT_GIVEN',
      quality_status: null,
      video_artifact_id: null,
    });
    expect(done.stages.map((s) => s.status)).toEqual(expected.stages.map((s) => s.status));
  });

  it('리포트만 요청하면 영상 경로를 건너뛴다', async () => {
    const { api } = setup();
    const job = await api.createFitting({ ...REQUEST, mode: 'report_only' });
    expect(job.status).toBe('SUCCEEDED');
    expect(job.visual).toMatchObject({ status: 'SKIPPED', skip_reason: 'REPORT_ONLY', quality_status: null });
  });

  it('실사화가 실패하면 리포트·3D 미리보기는 남고 검증은 건너뜀(realize_failed 예시와 같은 단계 상태)', async () => {
    const { api, advance } = setup('REALIZE');
    const created = await api.createFitting(REQUEST);
    advance(20_000);
    const done = await api.getFitting(created.job_id);
    const expected = example<FittingStatus>('fitting_status_realize_failed.json');
    expect(done.status).toBe('PARTIAL_SUCCEEDED');
    expect(done.visual).toMatchObject({ status: 'FAILED', stage: 'REALIZE', error_code: 'REALIZE_TIMEOUT', quality_status: null });
    expect(done.visual.drape_preview_artifact_id).not.toBeNull();
    expect(done.stages.map((s) => s.status)).toEqual(expected.stages.map((s) => s.status));
  });

  it('검증 실패는 영상은 있지만 quality_status FAIL', async () => {
    const { api, advance } = setup('VALIDATE');
    const created = await api.createFitting(REQUEST);
    advance(20_000);
    const done = await api.getFitting(created.job_id);
    expect(done.status).toBe('PARTIAL_SUCCEEDED');
    expect(done.visual).toMatchObject({ status: 'FAILED', error_code: 'VALIDATION_FAILED', quality_status: 'FAIL' });
    expect(done.visual.video_artifact_id).not.toBeNull();
  });

  it('없는 작업은 404', async () => {
    await expect(setup().api.getFitting('nope')).rejects.toMatchObject({ status: 404 });
  });
});
