import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, isFittingApiMissing } from './api';
import { createHttpApi } from './http';
import type { FittingRequest } from './types';

const request: FittingRequest = {
  garment_variant_id: 'tee_regular_m_navy_v1',
  measurements: { height_cm: 175, chest_cm: 100, waist_cm: 80, shoulder_cm: 44, arm_length_cm: 60 },
  measurement_protocol_version: 'measure_v1',
  mode: 'full',
  realize_consent: false,
};

const respond = (status: number, body: unknown) =>
  vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));

/** 실제 서버 API로 POST /api/fittings를 부르고, 던진 오류를 돌려준다. */
async function createFittingError(): Promise<unknown> {
  try {
    await createHttpApi('').createFitting(request);
  } catch (error) {
    return error;
  }
  throw new Error('createFitting이 실패하지 않았다');
}

describe('isFittingApiMissing: 작업 API(B3)가 없을 때만 리포트만 보여 준다', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('경로가 없는 404(FastAPI 기본 응답)는 작업 API 없음으로 본다', async () => {
    vi.stubGlobal('fetch', respond(404, { detail: 'Not Found' }));
    const error = await createFittingError();
    expect(error).toMatchObject({ status: 404, code: 'UNEXPECTED_RESPONSE' });
    expect(isFittingApiMissing(error)).toBe(true);
  });

  it('서버에 닿지 못하면(NETWORK_ERROR) 작업 API 없음으로 본다', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))));
    const error = await createFittingError();
    expect(error).toMatchObject({ status: 0, code: 'NETWORK_ERROR' });
    expect(isFittingApiMissing(error)).toBe(true);
  });

  it.each([
    [422, 'INPUT_INVALID'],
    [404, 'GARMENT_NOT_FOUND'],
    [409, 'GARMENT_NOT_READY'],
  ])('계약 오류 %i %s는 화면에 오류로 보여 준다', async (status, code) => {
    vi.stubGlobal('fetch', respond(status, { schema_version: 'v1', code, safe_message: '서버 문구', fields: [] }));
    const error = await createFittingError();
    expect(error).toMatchObject({ status, code, message: '서버 문구' });
    expect(isFittingApiMissing(error)).toBe(false);
  });

  it('계약 형식이 아닌 500도 화면에 오류로 보여 준다', async () => {
    vi.stubGlobal('fetch', respond(500, { detail: 'Internal Server Error' }));
    const error = await createFittingError();
    expect(error).toMatchObject({ status: 500, code: 'UNEXPECTED_RESPONSE' });
    expect(isFittingApiMissing(error)).toBe(false);
  });

  it('ApiError가 아닌 오류는 작업 API 없음으로 보지 않는다', () => {
    expect(isFittingApiMissing(new Error('x'))).toBe(false);
    expect(isFittingApiMissing(new ApiError(404, 'GARMENT_NOT_FOUND', '없음'))).toBe(false);
  });
});
