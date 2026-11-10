import { useLocation } from 'react-router-dom';

const STEPS = [
  { label: '입력', matches: (path: string) => path === '/' },
  { label: '선택', matches: (path: string) => path.startsWith('/garments') },
  { label: '결과', matches: (path: string) => path.startsWith('/fittings') },
];

export function StepIndicator() {
  const { pathname } = useLocation();
  const current = STEPS.findIndex((step) => step.matches(pathname));

  return (
    <ol className="steps" aria-label="진행 단계">
      {STEPS.map((step, index) => {
        const state = index === current ? 'current' : index < current ? 'done' : 'todo';
        return (
          <li key={step.label} className={`step step-${state}`} aria-current={index === current ? 'step' : undefined}>
            <span className="step-num">{index + 1}</span>
            {step.label}
          </li>
        );
      })}
    </ol>
  );
}
