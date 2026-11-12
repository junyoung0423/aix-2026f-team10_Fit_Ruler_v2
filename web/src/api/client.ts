import type { FittingApi } from './api';
import { createHttpApi } from './http';
import { createMockApi } from '../mocks/mockApi';

/**
 * VITE_USE_MOCK=false일 때만 실제 서버를 부른다. 기본은 가짜 API다.
 *
 * 실제 서버는 `/api/garments`·`/api/fit`(B2)과 `/api/fittings`·`/api/artifacts`(B3)를 모두 답한다.
 * 작업 서버에 닿지 못하거나 경로가 없으면(`isFittingApiMissing`) 결과 화면이 핏 리포트만 보여 주고
 * 진행 상태 자리에 안내를 띄운다.
 */
export const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false';

export const api: FittingApi = USE_MOCK ? createMockApi() : createHttpApi(import.meta.env.VITE_API_BASE_URL ?? '');
