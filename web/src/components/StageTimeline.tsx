import type { StageProgress, VisualPart } from '../api/types';
import { STAGE_LABEL, STATUS_LABEL } from '../labels';
import { attemptText, stageMessage, visualStages } from '../progress/jobSummary';

/**
 * 영상 경로 단계(몸 → 착장 → 렌더 → 실사화 → 검증)의 진행 상태.
 * 화면 낭독기 알림은 결과 화면의 한 줄 상태(role="status")가 단계가 바뀔 때만 한 번 한다. 이 목록은 알리지 않는다.
 */
export function StageTimeline({ stages, visual }: { stages: StageProgress[]; visual: VisualPart }) {
  return (
    <ol className="timeline">
      {visualStages(stages).map((stage) => {
        const text = stageMessage(stage, visual);
        const attempt = attemptText(stage);
        const running = stage.status === 'RUNNING';
        return (
          <li
            key={stage.stage}
            className={`stage stage-${stage.status.toLowerCase()}`}
            aria-current={running ? 'step' : undefined}
          >
            <span className="stage-name">
              {running && <span className="stage-pulse" aria-hidden="true" />}
              {STAGE_LABEL[stage.stage]}
            </span>
            <span className="stage-status">
              {STATUS_LABEL[stage.status]}
              {attempt && <span className="stage-attempt"> · {attempt}</span>}
            </span>
            {text && <span className="stage-message">{text}</span>}
          </li>
        );
      })}
    </ol>
  );
}
