import type { ReactNode } from 'react';
import { MEASURE_GUIDES } from '../guides/measureGuides';
import type { MeasurementField } from '../api/contract';

/** 부위마다 몸 그림 위에 재는 선을 표시한다. 단계 글이 같은 내용을 말하므로 그림은 보조로 둔다. */
const MEASURE_MARK: Record<MeasurementField, ReactNode> = {
  height_cm: (
    <>
      <line x1="146" y1="12" x2="146" y2="248" />
      <line x1="140" y1="12" x2="152" y2="12" />
      <line x1="140" y1="248" x2="152" y2="248" />
    </>
  ),
  chest_cm: <ellipse cx="80" cy="80" rx="33" ry="5" />,
  waist_cm: <ellipse cx="80" cy="116" rx="30" ry="4" />,
  shoulder_cm: (
    <>
      <line x1="46" y1="58" x2="114" y2="58" />
      <circle cx="46" cy="58" r="3" className="dot" />
      <circle cx="114" cy="58" r="3" className="dot" />
    </>
  ),
  // 정면 그림이라 사람의 오른팔은 그림의 왼쪽이다 (measure_v1: 오른쪽 어깨끝 → 팔꿈치 → 손목)
  arm_length_cm: (
    <>
      <line x1="41" y1="62" x2="30" y2="150" />
      <circle cx="41" cy="62" r="3" className="dot" />
      <circle cx="30" cy="150" r="3" className="dot" />
    </>
  ),
};

function MeasureFigure({ field }: { field: MeasurementField }) {
  return (
    <svg className="guide-figure" viewBox="0 0 160 260" aria-hidden="true" focusable="false">
      <g className="body">
        <circle cx="80" cy="28" r="16" />
        <rect x="73" y="42" width="14" height="12" />
        <path d="M58 52 Q80 48 102 52 L114 60 L108 128 L104 150 L56 150 L52 128 L46 60 Z" />
        <path d="M46 60 L36 64 L26 150 L36 152 L48 92 Z" />
        <path d="M114 60 L124 64 L134 150 L124 152 L112 92 Z" />
        <path d="M56 150 L58 248 L76 248 L80 172 L84 248 L102 248 L104 150 Z" />
      </g>
      <g className="mark">{MEASURE_MARK[field]}</g>
    </svg>
  );
}

export function MeasureGuide({ id, field, hidden }: { id: string; field: MeasurementField; hidden: boolean }) {
  const guide = MEASURE_GUIDES[field];
  return (
    <div id={id} className="guide" hidden={hidden}>
      <MeasureFigure field={field} />
      <div>
        <ol className="guide-steps">
          {guide.steps.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
        {guide.tip && <p className="guide-tip">{guide.tip}</p>}
      </div>
    </div>
  );
}
