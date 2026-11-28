import type { StepState, StepView } from '../progress/steps';

const STATE_TEXT: Record<StepState, string> = {
  waiting: '대기',
  running: '진행 중',
  done: '완료',
  warning: '완료, 검증 실패',
  failed: '멈춤',
  skipped: '건너뜀',
};

function Mark({ state }: { state: StepState }) {
  if (state === 'done') {
    return (
      <svg viewBox="0 0 16 16" focusable="false">
        <path d="M4 8.5 L7 11.2 L12.2 5.4" fill="none" />
      </svg>
    );
  }
  if (state === 'failed' || state === 'warning') return <span>!</span>;
  if (state === 'skipped') return <span>–</span>;
  return null;
}

/**
 * 사용자용 4단계 진행 표시(점과 가는 선). 피팅룸과 결과 카드(회색 바탕)에서는 dark, 밝은 바탕에서는 light를 쓴다.
 * 화면 낭독기 알림은 스튜디오의 한 줄 상태가 맡고, 이 목록은 알리지 않는다.
 */
export function StepTrack({ steps, tone }: { steps: StepView[]; tone: 'dark' | 'light' }) {
  return (
    <ol className={`step-track is-${tone}`} aria-label="3D·실사 진행 단계">
      {steps.map((step, index) => (
        <li
          key={step.key}
          className={`track-step is-${step.state}`}
          aria-current={step.state === 'running' ? 'step' : undefined}
        >
          <span className="track-dot" aria-hidden="true">
            <Mark state={step.state} />
          </span>
          <span className="track-label">{step.label}</span>
          <span className="sr-only">
            ({index + 1}/{steps.length}, {STATE_TEXT[step.state]})
          </span>
          {step.note && (
            <span className="track-note" aria-hidden="true">
              {step.note}
            </span>
          )}
        </li>
      ))}
    </ol>
  );
}
