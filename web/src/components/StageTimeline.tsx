import type { StageProgress, VisualPart } from '../api/types';
import { ERROR_LABEL, STAGE_LABEL, STATUS_LABEL } from '../labels';

const VISUAL_STAGES = new Set(['BODY_FIT', 'DRAPE', 'RENDER', 'REALIZE', 'VALIDATE']);

function message(stage: StageProgress, visual: VisualPart): string | null {
  if (stage.status === 'FAILED') return visual.error_code ? (ERROR_LABEL[visual.error_code] ?? visual.error_code) : null;
  if (stage.status !== 'SKIPPED') return null;
  if (visual.skip_reason === 'REPORT_ONLY') return '핏 리포트만 요청해 건너뛰었습니다.';
  if (visual.skip_reason === 'CONSENT_NOT_GIVEN') return '실사화(외부 전송)에 동의하지 않아 건너뛰었습니다.';
  return '앞 단계가 실패해 건너뛰었습니다.';
}

/** 영상 경로 단계(몸 → 착장 → 렌더 → 실사화 → 검증)의 진행 상태 */
export function StageTimeline({ stages, visual }: { stages: StageProgress[]; visual: VisualPart }) {
  return (
    <ol className="timeline" aria-live="polite">
      {stages
        .filter((stage) => VISUAL_STAGES.has(stage.stage))
        .map((stage) => {
          const text = message(stage, visual);
          return (
            <li key={stage.stage} className={`stage stage-${stage.status.toLowerCase()}`}>
              <span className="stage-name">{STAGE_LABEL[stage.stage]}</span>
              <span className="stage-status">{STATUS_LABEL[stage.status]}</span>
              {text && <span className="stage-message">{text}</span>}
            </li>
          );
        })}
    </ol>
  );
}
