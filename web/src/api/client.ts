import type { FittingApi } from './api';
import { createHttpApi } from './http';
import { createMockApi } from '../mocks/mockApi';

/** VITE_USE_MOCK=false일 때만 실제 서버를 부른다. 기본은 가짜 API다. */
export const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false';

export const api: FittingApi = USE_MOCK
  ? createMockApi()
  : createHttpApi(import.meta.env.VITE_API_BASE_URL ?? '');
