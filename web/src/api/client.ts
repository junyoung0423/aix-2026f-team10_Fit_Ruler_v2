import type { FittingApi } from './api';
import { createHttpApi } from './http';
import { createMockApi } from '../mocks/mockApi';

/**
 * VITE_USE_MOCK=false일 때만 실제 서버를 부른다. 기본은 가짜 API다.
 *
 * 실제 서버를 쓸 때 `/api/garments`·`/api/fit`은 B2 서버가 답한다. `/api/fittings`(3D·실사화
 * 진행)는 B3에서 생기므로, 그 전에는 결과 화면이 핏 리포트만 보여 주고 진행 상태 자리에 안내를 띄운다.
 */
export const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false';

export const api: FittingApi = USE_MOCK ? createMockApi() : createHttpApi(import.meta.env.VITE_API_BASE_URL ?? '');
