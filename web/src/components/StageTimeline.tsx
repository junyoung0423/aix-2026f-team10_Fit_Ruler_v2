import type { StageState } from '../api/types';
import { STAGE_LABEL, STATUS_LABEL } from '../labels';

export function StageTimeline({ stages }: { stages: StageState[] }) {
  return (
    <ol className="timeline" aria-live="polite">
      {stages.map((stage) => (
        <li key={stage.name} className={`stage stage-${stage.status}`}>
          <span className="stage-name">{STAGE_LABEL[stage.name]}</span>
          <span className="stage-status">{STATUS_LABEL[stage.status]}</span>
          {stage.message && <span className="stage-message">{stage.message}</span>}
        </li>
      ))}
    </ol>
  );
}
