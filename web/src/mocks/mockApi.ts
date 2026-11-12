/**
 * 가짜 API. 서버 없이 화면을 개발할 때 쓴다(`VITE_USE_MOCK`가 false가 아니면 이것을 쓴다).
 *
 * 주소 옵션 (티셔츠 선택 화면 주소 뒤에 붙이고 "핏 보기"를 누른다. 작업을 만들 때 읽어 그 작업에 남긴다)
 * - `?mockFail=단계`   그 단계가 실패한다. body·drape·render·realize·validate
 * - `?mockReject=칸`   그 칸을 서버가 422로 거절한다. height_cm·chest_cm·waist_cm·shoulder_cm·arm_length_cm
 * - `?mockFlaky=N`     진행 상태 조회(GET /api/fittings/{id})가 처음 N번 연결 오류로 실패한다(다시 부르기 확인)
 * - `?mockLost=1`      진행 상태 조회가 404 JOB_NOT_FOUND다(서버를 다시 켜 작업 기록이 사라진 경우)
 * 여러 개를 함께 쓸 수 있다. 예: `/garments?mockFail=realize&mockFlaky=2`
 */
import { ApiError, type FittingApi } from '../api/api';
import { MEASUREMENT_FIELDS, MEASUREMENT_LABEL, MEASUREMENT_RANGES, type MeasurementField } from '../api/contract';
import type {
  ErrorCode,
  FieldError,
  FitReport,
  FitRequest,
  FittingRequest,
  FittingStatus,
  GarmentVariant,
  QualityStatus,
  Stage,
  StageProgress,
  StageStatus,
  VisualPart,
} from '../api/types';
import { MEASUREMENT_PROTOCOL_VERSION } from '../api/types';
import { colorHex, swatchColor } from '../labels';
import { computeFitReport, notComputableReason } from './fit';
import { MOCK_GARMENTS, mockGarmentList } from './garments';
import { placeholderImage } from './placeholder';

/** 영상 경로 단계와 걸리는 시간(ms). 화면에서 진행 상태가 보이도록 일부러 몇 초씩 둔다. */
export const VISUAL_PLAN: readonly { stage: Stage; durationMs: number }[] = [
  { stage: 'BODY_FIT', durationMs: 2000 },
  { stage: 'DRAPE', durationMs: 3000 },
  { stage: 'RENDER', durationMs: 2000 },
  { stage: 'REALIZE', durationMs: 4000 },
  { stage: 'VALIDATE', durationMs: 1000 },
];

const FAIL_CODE: Record<Stage, ErrorCode> = {
  FIT_CALCULATE: 'GARMENT_NOT_READY',
  FIT_REPORT: 'GARMENT_NOT_READY',
  BODY_FIT: 'BODY_FIT_NOT_CONVERGED',
  DRAPE: 'DRAPE_INVALID',
  RENDER: 'RENDER_FAILED',
  REALIZE: 'REALIZE_TIMEOUT',
  VALIDATE: 'VALIDATION_FAILED',
};

/** 주소의 ?mockFail= 값 → 실패시킬 단계 */
const FAIL_ALIASES: Record<string, Stage> = {
  body: 'BODY_FIT',
  drape: 'DRAPE',
  render: 'RENDER',
  realize: 'REALIZE',
  validate: 'VALIDATE',
  verify: 'VALIDATE',
};

const PASS_METRICS = { color_delta_e: 3.2, silhouette_iou: 0.91 };
const FAIL_METRICS = { color_delta_e: 12.4, silhouette_iou: 0.62 };

interface JobRecord {
  jobId: string;
  request: FittingRequest;
  report: FitReport | null;
  notReady: boolean;
  createdAt: number;
  failStage: Stage | null;
  /** 남은 조회 실패 횟수(?mockFlaky) */
  flaky?: number;
  /** 작업 기록이 없는 것처럼 404를 돌려줄지(?mockLost) */
  lost?: boolean;
}

export interface MockApiOptions {
  /** 현재 시각(ms). 테스트에서 시간을 직접 넘기려고 둔다. */
  now?: () => number;
  /** 응답마다 기다리는 시간(ms) */
  latencyMs?: number;
  /** 새로고침해도 작업이 남도록 저장할 곳. null이면 저장하지 않는다. */
  storage?: Storage | null;
  /** 실패를 흉내 낼 단계. 기본은 주소의 ?mockFail= 값(body·drape·render·realize·validate). */
  failStage?: () => Stage | null;
  /** 서버가 거절한 것처럼 422를 돌려줄 치수 칸. 기본은 주소의 ?mockReject= 값(height_cm 등). */
  rejectField?: () => MeasurementField | null;
  /** 진행 상태 조회가 처음 몇 번 연결 오류로 실패할지. 기본은 주소의 ?mockFlaky= 값 */
  flakyCount?: () => number;
  /** 진행 상태 조회가 404 JOB_NOT_FOUND인지. 기본은 주소의 ?mockLost=1 */
  lostJob?: () => boolean;
}

const STORAGE_KEY = 'fit-ruler.mock-jobs.v2';

export function createMockApi(options: MockApiOptions = {}): FittingApi {
  const now = options.now ?? (() => Date.now());
  const latencyMs = options.latencyMs ?? 300;
  const storage = options.storage === undefined ? sessionStorageOrNull() : options.storage;
  const failStage = options.failStage ?? failStageFromUrl;
  const rejectField = options.rejectField ?? rejectFieldFromUrl;
  const flakyCount = options.flakyCount ?? flakyCountFromUrl;
  const lostJob = options.lostJob ?? lostJobFromUrl;
  const jobs = new Map<string, JobRecord>(loadJobs(storage).map((record) => [record.jobId, record]));
  const isoNow = () => new Date(now()).toISOString();

  return {
    async listGarments() {
      await wait(latencyMs);
      return { schema_version: 'v1', garments: mockGarmentList() };
    },

    async fit(request: FitRequest) {
      await wait(latencyMs);
      validateRequest(request, rejectField());
      const garment = findGarment(request.garment_variant_id);
      if (notComputableReason(garment, request.measurement_protocol_version)) throw garmentNotReady();
      return computeFitReport(request.measurements, garment, { jobId: makeId('fit', now()), createdAt: isoNow() });
    },

    async createFitting(request: FittingRequest) {
      await wait(latencyMs);
      validateRequest(request, rejectField());
      const garment = findGarment(request.garment_variant_id);
      const createdAt = now();
      const jobId = makeId('job', createdAt);
      const notReady = notComputableReason(garment, request.measurement_protocol_version) !== null;
      const record: JobRecord = {
        jobId,
        request: structuredClone(request),
        report: notReady ? null : computeFitReport(request.measurements, garment, { jobId, createdAt: isoNow() }),
        notReady,
        createdAt,
        failStage: failStage(),
        flaky: flakyCount(),
        lost: lostJob(),
      };
      jobs.set(jobId, record);
      saveJobs(storage, [...jobs.values()]);
      return buildStatus(record, now());
    },

    async getFitting(jobId: string) {
      await wait(latencyMs);
      const record = jobs.get(jobId);
      if (!record || record.lost) throw jobNotFound();
      if (record.flaky && record.flaky > 0) {
        record.flaky -= 1;
        saveJobs(storage, [...jobs.values()]);
        throw new ApiError(0, 'NETWORK_ERROR', '서버에 연결할 수 없습니다. 잠시 뒤 다시 시도해 주세요. (가짜 API)');
      }
      return buildStatus(record, now());
    },

    artifactUrl(artifactId: string) {
      const record = [...jobs.values()].find((item) => artifactId.startsWith(`${item.jobId}_`));
      const role = record ? artifactId.slice(record.jobId.length + 1) : '';
      const label = ARTIFACT_LABEL[role];
      if (!record || !label) return null; // 실사 영상은 가짜 파일이 없어 대표 프레임만 보여 준다
      const garment = MOCK_GARMENTS.find((item) => item.id === record.request.garment_variant_id);
      return placeholderImage(label, garment ? swatchColor(garment) : colorHex(''));
    },
  };
}

const ARTIFACT_LABEL: Record<string, string> = {
  drape_preview: '3D 착장 미리보기 (가짜)',
  frame: '실사 대표 프레임 (가짜)',
  render_frame: '같은 시점의 렌더 프레임 (가짜)',
};

/** 작업 생성 뒤 흐른 시간으로 단계별 상태를 만든다. 계약의 FittingStatus 모양을 그대로 따른다. */
function buildStatus(record: JobRecord, at: number): FittingStatus {
  const { jobId, request, report } = record;
  const fitStages: StageProgress[] = [
    { stage: 'FIT_CALCULATE', status: record.notReady ? 'FAILED' : 'SUCCEEDED', attempt_no: 1 },
    { stage: 'FIT_REPORT', status: record.notReady ? 'SKIPPED' : 'SUCCEEDED', attempt_no: 1 },
  ];
  const reportPart: FittingStatus['report'] = report
    ? {
        status: 'SUCCEEDED',
        rules_version: report.rules_version,
        regions: report.regions,
        error_code: null,
        summary: report.summary,
        warnings: report.warnings,
      }
    : { status: 'FAILED', rules_version: null, regions: null, error_code: 'GARMENT_NOT_READY', summary: null, warnings: [] };

  const skippedVisual = (skipReason: VisualPart['skip_reason']): VisualPart => ({
    status: 'SKIPPED',
    stage: null,
    quality_status: null,
    skip_reason: skipReason,
    drape_preview_artifact_id: null,
    render_artifact_id: null,
    video_artifact_id: null,
    frame_artifact_id: null,
    render_frame_artifact_id: null,
    quality_metrics: null,
    error_code: null,
  });
  const allSkipped = VISUAL_PLAN.map(({ stage }) => ({ stage, status: 'SKIPPED' as StageStatus, attempt_no: 1 }));

  if (record.notReady) {
    return status(jobId, 'FAILED', reportPart, skippedVisual(null), [...fitStages, ...allSkipped]);
  }
  if (request.mode === 'report_only') {
    return status(jobId, 'SUCCEEDED', reportPart, skippedVisual('REPORT_ONLY'), [...fitStages, ...allSkipped]);
  }

  const elapsed = at - record.createdAt;
  // 실사화에 동의하지 않았으면 첫 응답부터 skip_reason이 CONSENT_NOT_GIVEN, quality_status가 null이다(docs/api/README.md).
  const visual: VisualPart = request.realize_consent
    ? { ...skippedVisual(null), status: 'RUNNING', quality_status: 'PENDING' }
    : { ...skippedVisual('CONSENT_NOT_GIVEN'), status: 'RUNNING' };
  const stages: StageProgress[] = [];
  let start = 0;
  let stopped: 'failed' | 'consent' | null = null;

  for (const { stage, durationMs } of VISUAL_PLAN) {
    if (stopped) {
      stages.push({ stage, status: 'SKIPPED', attempt_no: 1 });
      continue;
    }
    if (stage === 'REALIZE' && !request.realize_consent) {
      stages.push({ stage, status: 'SKIPPED', attempt_no: 1 });
      stopped = 'consent';
      continue;
    }
    const end = start + durationMs;
    if (elapsed < start) {
      stages.push({ stage, status: 'WAITING', attempt_no: 1 });
    } else if (elapsed < end) {
      stages.push({ stage, status: 'RUNNING', attempt_no: 1 });
      visual.stage = stage;
    } else if (record.failStage === stage) {
      stages.push({ stage, status: 'FAILED', attempt_no: 1 });
      visual.stage = stage;
      visual.error_code = FAIL_CODE[stage];
      if (stage === 'VALIDATE') addValidateOutputs(visual, jobId, 'FAIL');
      stopped = 'failed';
    } else {
      stages.push({ stage, status: 'SUCCEEDED', attempt_no: 1 });
      visual.stage = stage;
      addOutputs(visual, jobId, stage);
    }
    start = end;
  }

  const active = stages.some((item) => item.status === 'WAITING' || item.status === 'RUNNING');
  let jobStatus: FittingStatus['status'] = 'RUNNING';
  if (!active) {
    if (stopped === 'failed') {
      visual.status = 'FAILED';
      if (visual.quality_status === 'PENDING') visual.quality_status = null;
      jobStatus = 'PARTIAL_SUCCEEDED';
    } else {
      visual.status = 'SUCCEEDED';
      if (stopped === 'consent') {
        visual.skip_reason = 'CONSENT_NOT_GIVEN';
        visual.quality_status = null;
        visual.stage = 'RENDER';
      }
      jobStatus = 'SUCCEEDED';
    }
  }
  return status(jobId, jobStatus, reportPart, visual, [...fitStages, ...stages]);
}

function addOutputs(visual: VisualPart, jobId: string, stage: Stage): void {
  if (stage === 'DRAPE') visual.drape_preview_artifact_id = `${jobId}_drape_preview`;
  if (stage === 'RENDER') visual.render_artifact_id = `${jobId}_render_clip`;
  if (stage === 'REALIZE') visual.video_artifact_id = `${jobId}_video`;
  if (stage === 'VALIDATE') addValidateOutputs(visual, jobId, 'PASS');
}

function addValidateOutputs(visual: VisualPart, jobId: string, quality: QualityStatus): void {
  visual.frame_artifact_id = `${jobId}_frame`;
  visual.render_frame_artifact_id = `${jobId}_render_frame`;
  visual.quality_status = quality;
  visual.quality_metrics = quality === 'PASS' ? { ...PASS_METRICS } : { ...FAIL_METRICS };
}

function status(
  jobId: string,
  jobStatus: FittingStatus['status'],
  report: FittingStatus['report'],
  visual: VisualPart,
  stages: StageProgress[],
): FittingStatus {
  return { schema_version: 'v1', job_id: jobId, status: jobStatus, report, visual, stages };
}

/**
 * 서버(B2)의 입력 검사를 흉내 낸다. 오류 형식과 문구는 `server/errors.py`와 같다.
 * rejectField가 있으면 범위 안의 값이어도 그 칸을 서버가 거절한 것처럼 만든다(화면 확인용).
 */
function validateRequest(request: FitRequest, rejectField: MeasurementField | null = null): void {
  const fields: FieldError[] = [];
  if (!request.garment_variant_id) {
    fields.push({ field: 'garment_variant_id', code: 'INVALID', message: '의류 값의 형식이 올바르지 않습니다.' });
  }
  if (request.measurement_protocol_version !== MEASUREMENT_PROTOCOL_VERSION) {
    fields.push({
      field: 'measurement_protocol_version',
      code: 'UNSUPPORTED_VALUE',
      message: '지원하지 않는 측정 방법 버전입니다.',
    });
  }
  for (const name of MEASUREMENT_FIELDS) {
    const value: unknown = request.measurements?.[name];
    const label = MEASUREMENT_LABEL[name];
    const field = `measurements.${name}`;
    if (value === undefined) {
      fields.push({ field, code: 'MISSING', message: `${label} 값을 입력해 주세요.` });
    } else if (typeof value !== 'number' || !Number.isFinite(value)) {
      fields.push({ field, code: 'NOT_A_NUMBER', message: `${label} 값은 숫자로 입력해 주세요.`, min: null, max: null });
    } else {
      const { min, max } = MEASUREMENT_RANGES[name];
      if (value < min || value > max) {
        fields.push({ field, code: 'OUT_OF_RANGE', message: `${label} 값은 ${min}~${max} cm 사이로 입력해 주세요.`, min, max });
      }
    }
  }
  if (rejectField && !fields.some((item) => item.field === `measurements.${rejectField}`)) {
    fields.push({
      field: `measurements.${rejectField}`,
      code: 'INVALID',
      message: `${MEASUREMENT_LABEL[rejectField]} 값을 서버가 받지 않았습니다. 다시 재서 입력해 주세요. (가짜 API)`,
    });
  }
  if (fields.length > 0) throw new ApiError(422, 'INPUT_INVALID', '입력한 값을 확인해 주세요.', fields);
}

function findGarment(garmentId: string): GarmentVariant {
  const garment = MOCK_GARMENTS.find((item) => item.id === garmentId);
  if (!garment) {
    throw new ApiError(404, 'GARMENT_NOT_FOUND', '선택한 의류를 찾을 수 없어요. 목록에서 다시 선택해 주세요.');
  }
  return garment;
}

/** 서버와 같은 문구(docs/api/examples/api_error_job_not_found.json) */
const jobNotFound = () =>
  new ApiError(404, 'JOB_NOT_FOUND', '작업을 찾을 수 없어요. 서버가 다시 시작됐거나 오래된 작업일 수 있어요.');

const garmentNotReady = () =>
  new ApiError(409, 'GARMENT_NOT_READY', '선택한 의류를 아직 사용할 수 없어요. 다른 의류를 선택해 주세요.');

function failStageFromUrl(): Stage | null {
  if (typeof window === 'undefined') return null;
  const value = new URLSearchParams(window.location.search).get('mockFail')?.toLowerCase() ?? '';
  return FAIL_ALIASES[value] ?? null;
}

function rejectFieldFromUrl(): MeasurementField | null {
  if (typeof window === 'undefined') return null;
  const value = new URLSearchParams(window.location.search).get('mockReject') ?? '';
  return MEASUREMENT_FIELDS.find((field) => field === value) ?? null;
}

function flakyCountFromUrl(): number {
  if (typeof window === 'undefined') return 0;
  const value = Number(new URLSearchParams(window.location.search).get('mockFlaky') ?? '0');
  return Number.isInteger(value) && value > 0 ? Math.min(value, 20) : 0;
}

function lostJobFromUrl(): boolean {
  if (typeof window === 'undefined') return false;
  return new URLSearchParams(window.location.search).get('mockLost') === '1';
}

function makeId(prefix: string, at: number): string {
  return `${prefix}_${at.toString(36)}${Math.random().toString(36).slice(2, 6)}`;
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
