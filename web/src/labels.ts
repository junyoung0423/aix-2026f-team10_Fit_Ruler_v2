import type { BodyMeasurements, FitPart, FitVerdict, StageName, StageStatus } from './api/types';

export const BODY_LABEL: Record<keyof BodyMeasurements, string> = {
  heightCm: '키',
  chestCircumferenceCm: '가슴둘레',
  waistCircumferenceCm: '허리둘레',
  shoulderWidthCm: '어깨너비',
  armLengthCm: '팔길이',
};

export const PART_LABEL: Record<FitPart, string> = {
  chest: '가슴',
  shoulder: '어깨',
  waist: '허리',
  length: '총장',
};

export const VERDICT_LABEL: Record<FitVerdict, string> = {
  tight: '타이트',
  regular: '정핏',
  loose: '여유',
  oversized: '오버핏',
};

export const STAGE_LABEL: Record<StageName, string> = {
  body: '몸 생성',
  drape: '3D 착장',
  render: '정면 렌더',
  realize: '실사화',
  verify: '검증',
};

export const STATUS_LABEL: Record<StageStatus, string> = {
  pending: '대기',
  running: '진행 중',
  succeeded: '완료',
  failed: '실패',
  skipped: '건너뜀',
};
