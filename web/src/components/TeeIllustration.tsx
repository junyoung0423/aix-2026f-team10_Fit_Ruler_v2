import { useId } from 'react';

/** 티셔츠 선택 카드의 상품 그림(평평하게 놓은 반팔 티). 실제 상품 사진이 없어 색 견본 색으로 그린다. */
const TEE_FLAT =
  'M70 34 Q100 52 130 34 L166 48 L192 88 L166 104 L156 90 L158 176 Q100 182 42 176 L44 90 L34 104 L8 88 L34 48 Z';
const COLLAR = 'M70 34 Q100 58 130 34';
const SEAMS = ['M44 90 L50 96', 'M156 90 L150 96', 'M42 168 Q100 174 158 168'];

export function TeeIllustration({ color }: { color: string }) {
  const shade = `tee-flat-${useId().replace(/:/g, '')}`;
  return (
    <svg className="tee-illustration" viewBox="0 0 200 200" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id={shade} x1="0" x2="1" y1="0" y2="1">
          <stop offset="0" stopColor="#fff" stopOpacity="0.14" />
          <stop offset="0.5" stopColor="#fff" stopOpacity="0" />
          <stop offset="1" stopColor="#000" stopOpacity="0.18" />
        </linearGradient>
      </defs>
      <ellipse cx="100" cy="186" rx="70" ry="6" fill="#000" fillOpacity="0.07" />
      <path d={TEE_FLAT} fill={color} stroke="#000" strokeOpacity="0.14" strokeWidth="1.2" strokeLinejoin="round" />
      <path d={TEE_FLAT} fill={`url(#${shade})`} />
      <path d={COLLAR} fill="none" stroke="#000" strokeOpacity="0.18" strokeWidth="2.2" />
      {SEAMS.map((d) => (
        <path key={d} d={d} fill="none" stroke="#000" strokeOpacity="0.1" strokeWidth="1" />
      ))}
    </svg>
  );
}
