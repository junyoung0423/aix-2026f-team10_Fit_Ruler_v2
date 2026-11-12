import type { FittingStatus, StageProgress, VisualPart } from '../api/types';
import { ERROR_LABEL, STAGE_LABEL } from '../labels';

/** 진행 표시에 쓰는 문구. 서버는 상태 코드만 주므로 화면이 문장을 고른다. */

const VISUAL_STAGE_SET = new Set(['BODY_FIT', 'DRAPE', 'RENDER', 'REALIZE', 'VALIDATE']);

/** 영상 경로 단계(몸 → 착장 → 렌더 → 실사화 → 검증)만 서버가 준 순서대로 */
export const visualStages = (stages: StageProgress[]) => stages.filter((stage) => VISUAL_STAGE_SET.has(stage.stage));

/** 오류 코드 → 안내 문구. 화면 표에 없는 코드는 코드를 그대로 쓰지 않고 "알 수 없는 오류(코드)"로 보여 준다. */
export function errorText(code: string): string {
  return (ERROR_LABEL as Record<string, string | undefined>)[code] ?? `알 수 없는 오류(${code})`;
}

/** 단계 아래에 붙이는 설명. 없으면 null */
export function stageMessage(stage: StageProgress, visual: VisualPart): string | null {
  if (stage.status === 'FAILED') return visual.error_code ? errorText(visual.error_code) : null;
  if (stage.status !== 'SKIPPED') return null;
  if (visual.skip_reason === 'REPORT_ONLY') return '핏 리포트만 요청해 건너뛰었습니다.';
  if (visual.skip_reason === 'CONSENT_NOT_GIVEN') return '실사화(외부 전송)에 동의하지 않아 건너뛰었습니다.';
  return '앞 단계가 실패해 건너뛰었습니다.';
}

/** attempt_no가 2 이상이면 "재시도 2회째" */
export const attemptText = (stage: StageProgress) => (stage.attempt_no >= 2 ? `재시도 ${stage.attempt_no}회째` : null);

export type HeadlineTone = 'running' | 'done' | 'partial' | 'failed';

/** 작업 전체 상태 한 줄. 예: "3D 착장 중 (2/5)", "완료 (렌더까지)", "일부 완료 · 핏 리포트와 3D 착장까지 준비됨" */
export function jobHeadline(job: FittingStatus): { text: string; tone: HeadlineTone } {
  const stages = visualStages(job.stages);
  const position = (index: number) => `(${index + 1}/${stages.length})`;

  switch (job.status) {
    case 'ACCEPTED':
    case 'RUNNING': {
      const running = stages.findIndex((stage) => stage.status === 'RUNNING');
      if (running >= 0) return { text: `${STAGE_LABEL[stages[running].stage]} 중 ${position(running)}`, tone: 'running' };
      if (job.status === 'ACCEPTED' || job.visual.status === 'QUEUED') return { text: '차례를 기다리는 중', tone: 'running' };
      const next = stages.findIndex((stage) => stage.status === 'WAITING' || stage.status === 'QUEUED');
      if (next >= 0) return { text: `${STAGE_LABEL[stages[next].stage]} 준비 중 ${position(next)}`, tone: 'running' };
      return { text: '진행 중', tone: 'running' };
    }
    case 'SUCCEEDED':
      if (job.visual.skip_reason === 'CONSENT_NOT_GIVEN') return { text: '완료 (렌더까지)', tone: 'done' };
      if (job.visual.skip_reason === 'REPORT_ONLY') return { text: '완료 (핏 리포트만)', tone: 'done' };
      return { text: '완료', tone: 'done' };
    case 'PARTIAL_SUCCEEDED': {
      const done = stages.filter((stage) => stage.status === 'SUCCEEDED').pop();
      return {
        text: done ? `일부 완료 · 핏 리포트와 ${STAGE_LABEL[done.stage]}까지 준비됨` : '일부 완료 · 핏 리포트만 준비됨',
        tone: 'partial',
      };
    }
    case 'FAILED': {
      const failed = job.stages.find((stage) => stage.status === 'FAILED');
      const where = failed?.stage ?? job.visual.stage;
      return { text: where ? `실패 · ${STAGE_LABEL[where]}` : '실패', tone: 'failed' };
    }
    case 'CANCELLED':
      return { text: '취소됨', tone: 'failed' };
  }
}

/** 경과 시간. 예: 0:42, 12:05, 1:02:03 */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = String(total % 60).padStart(2, '0');
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, '0')}:${seconds}` : `${minutes}:${seconds}`;
}
