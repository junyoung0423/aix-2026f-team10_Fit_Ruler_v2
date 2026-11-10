import { ApiError, type FittingApi } from '../api/api';
import type {
  BodyMeasurements,
  FieldErrors,
  FittingJob,
  FittingRequest,
  Garment,
  StageName,
  StageState,
  Verification,
} from '../api/types';
import { computeFitReport } from './fit';
import { SAMPLE_GARMENTS } from './garments';
import { placeholderImage } from './placeholder';

/** 단계별로 걸리는 시간(ms). 화면에서 진행 상태가 보이도록 일부러 몇 초씩 둔다. */
export const STAGE_PLAN: readonly { name: StageName; durationMs: number }[] = [
  { name: 'body', durationMs: 2000 },
  { name: 'drape', durationMs: 3000 },
  { name: 'render', durationMs: 2000 },
  { name: 'realize', durationMs: 4000 },
  { name: 'verify', durationMs: 1000 },
];

const STAGE_NAMES: readonly StageName[] = STAGE_PLAN.map((step) => step.name);

const FAIL_MESSAGE: Record<StageName, string> = {
  body: '몸 생성 실패: 입력 치수와의 오차가 허용 범위를 넘었습니다. (가짜 API)',
  drape: '착장 실패: 천 시뮬레이션이 불안정합니다. (가짜 API)',
  render: '렌더 실패: Blender 실행 시간이 초과됐습니다. (가짜 API)',
  realize: '실사화 실패: 응답 시간이 초과됐습니다. (가짜 API)',
  verify: '검증 실패: 색·실루엣 차이가 기준을 넘었습니다. (가짜 API)',
};

const BODY_FIELDS: readonly (keyof BodyMeasurements)[] = [
  'heightCm',
  'chestCircumferenceCm',
  'waistCircumferenceCm',
  'shoulderWidthCm',
  'armLengthCm',
];

interface JobRecord {
  jobId: string;
  request: FittingRequest;
  createdAt: number;
  failStage: StageName | null;
}

export interface MockApiOptions {
  /** 현재 시각(ms). 테스트에서 시간을 직접 넘기려고 둔다. */
  now?: () => number;
  /** 응답마다 기다리는 시간(ms) */
  latencyMs?: number;
  /** 새로고침해도 작업이 남도록 저장할 곳. null이면 저장하지 않는다. */
  storage?: Storage | null;
  /** 실패를 흉내 낼 단계. 기본은 주소의 ?mockFail=단계 값(body·drape·render·realize·verify). */
  failStage?: () => StageName | null;
}

const STORAGE_KEY = 'fit-ruler.mock-jobs';

export function createMockApi(options: MockApiOptions = {}): FittingApi {
  const now = options.now ?? (() => Date.now());
  const latencyMs = options.latencyMs ?? 300;
  const storage = options.storage === undefined ? sessionStorageOrNull() : options.storage;
  const failStage = options.failStage ?? failStageFromUrl;
  const jobs = new Map<string, JobRecord>(loadJobs(storage).map((record) => [record.jobId, record]));

  return {
    async listGarments() {
      await wait(latencyMs);
      return structuredClone(SAMPLE_GARMENTS);
    },

    async fit(body, garmentId) {
      await wait(latencyMs);
      validateBody(body);
      return computeFitReport(body, findGarment(garmentId));
    },

    async createFitting(request) {
      await wait(latencyMs);
      validateBody(request.body);
      findGarment(request.garmentId);
      const createdAt = now();
      const record: JobRecord = {
        jobId: makeJobId(createdAt),
        request: structuredClone(request),
        createdAt,
        failStage: failStage(),
      };
      jobs.set(record.jobId, record);
      saveJobs(storage, [...jobs.values()]);
      return buildJob(record, now());
    },

    async getFitting(jobId) {
      await wait(latencyMs);
      const record = jobs.get(jobId);
      if (!record) throw new ApiError(404, '작업을 찾을 수 없습니다. 처음부터 다시 진행해 주세요.');
      return buildJob(record, now());
    },
  };
}

function buildJob(record: JobRecord, at: number): FittingJob {
  const garment = findGarment(record.request.garmentId);
  const elapsed = at - record.createdAt;
  const stages: StageState[] = [];
  let start = 0;
  let skipReason: string | null = null;

  for (const step of STAGE_PLAN) {
    if (skipReason) {
      stages.push({ name: step.name, status: 'skipped', message: skipReason });
      continue;
    }
    if (step.name === 'realize' && !record.request.realizeConsent) {
      stages.push({ name: 'realize', status: 'skipped', message: '외부 전송에 동의하지 않아 건너뛰었습니다.' });
      skipReason = '실사화를 하지 않아 건너뛰었습니다.';
      continue;
    }
    const end = start + step.durationMs;
    if (elapsed < start) {
      stages.push({ name: step.name, status: 'pending' });
    } else if (elapsed < end) {
      stages.push({ name: step.name, status: 'running' });
    } else if (record.failStage === step.name) {
      stages.push({ name: step.name, status: 'failed', message: FAIL_MESSAGE[step.name] });
      skipReason = '앞 단계가 실패해 건너뛰었습니다.';
    } else {
      stages.push({ name: step.name, status: 'succeeded' });
    }
    start = end;
  }

  const statusOf = (name: StageName) => stages.find((stage) => stage.name === name)?.status;
  const previewImageUrl =
    statusOf('drape') === 'succeeded' ? placeholderImage('3D 착장 미리보기 (가짜)', garment.colorHex) : null;
  const realization =
    statusOf('realize') === 'succeeded'
      ? {
          videoUrl: null,
          frameUrl: placeholderImage('대표 프레임 (가짜)', garment.colorHex),
          renderFrameUrl: placeholderImage('렌더 프레임 (가짜)', garment.colorHex),
        }
      : null;

  let verification: Verification | null = null;
  if (statusOf('verify') === 'succeeded') verification = { passed: true, colorDeltaE: 3.2, silhouetteIoU: 0.91 };
  if (statusOf('verify') === 'failed') verification = { passed: false, colorDeltaE: 12.4, silhouetteIoU: 0.62 };

  return {
    jobId: record.jobId,
    garmentId: garment.id,
    fitReport: computeFitReport(record.request.body, garment),
    stages,
    previewImageUrl,
    realization,
    verification,
    fallbackPreview: previewImageUrl !== null && (statusOf('render') === 'failed' || statusOf('realize') === 'failed'),
  };
}

function validateBody(body: BodyMeasurements): void {
  const errors: FieldErrors = {};
  for (const field of BODY_FIELDS) {
    const value = body[field];
    if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) {
      errors[field] = '0보다 큰 숫자를 넣어 주세요.';
    }
  }
  if (Object.keys(errors).length > 0) throw new ApiError(422, '입력한 치수를 확인해 주세요.', errors);
}

function findGarment(garmentId: string): Garment {
  const garment = SAMPLE_GARMENTS.find((item) => item.id === garmentId);
  if (!garment) throw new ApiError(404, '티셔츠를 찾을 수 없습니다.');
  return garment;
}

function failStageFromUrl(): StageName | null {
  if (typeof window === 'undefined') return null;
  const value = new URLSearchParams(window.location.search).get('mockFail');
  return STAGE_NAMES.find((name) => name === value) ?? null;
}

function makeJobId(createdAt: number): string {
  return `job-${createdAt.toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function wait(ms: number): Promise<void> {
  return ms > 0 ? new Promise((resolve) => setTimeout(resolve, ms)) : Promise.resolve();
}

function sessionStorageOrNull(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

function loadJobs(storage: Storage | null): JobRecord[] {
  try {
    const raw = storage?.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as JobRecord[]) : [];
  } catch {
    return [];
  }
}

function saveJobs(storage: Storage | null, records: JobRecord[]): void {
  try {
    storage?.setItem(STORAGE_KEY, JSON.stringify(records.slice(-20)));
  } catch {
    // 저장소를 쓸 수 없으면 새로고침 때 작업이 사라질 뿐이다.
  }
}
