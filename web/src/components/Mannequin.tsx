import { useId } from 'react';
import { ARM_LEFT, ARM_RIGHT, FIGURE_VIEWBOX, HEAD, NECK, TEE, TEE_COLLAR, TEE_FOLDS, TORSO } from './figurePaths';

/** SVG id에는 useId의 ':'를 쓸 수 없어 뺀다. 같은 화면에 그림이 여러 개여도 id가 겹치지 않는다. */
const useSvgId = (name: string) => `${name}-${useId().replace(/:/g, '')}`;

interface FigureProps {
  className?: string;
  /** 기본은 머리부터 밑단 아래까지. 사진 카드 안에서는 PHOTO_VIEWBOX(턱 아래부터) */
  viewBox?: string;
  /** 아래쪽을 어둠으로 흐리게 할지(스튜디오) */
  fade?: boolean;
  /** 사진 카드를 꽉 채울 때 'xMidYMid slice' */
  preserveAspectRatio?: string;
}

/** 공통 마네킹(무광 회색, 이목구비 없음). 장식 그림이라 화면 낭독기에서는 숨긴다. */
export function Mannequin({ className, viewBox = FIGURE_VIEWBOX, fade = true, preserveAspectRatio }: FigureProps) {
  const skin = useSvgId('mq-skin');
  const light = useSvgId('mq-light');
  const fadeGrad = useSvgId('mq-fade');
  const mask = useSvgId('mq-mask');
  return (
    <svg
      className={className}
      viewBox={viewBox}
      preserveAspectRatio={preserveAspectRatio}
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient id={skin} x1="0" x2="1" y1="0" y2="0">
          <stop offset="0" stopColor="#57524c" />
          <stop offset="0.42" stopColor="#99928a" />
          <stop offset="0.58" stopColor="#a29b92" />
          <stop offset="1" stopColor="#534e48" />
        </linearGradient>
        <radialGradient id={light} cx="0.5" cy="0.12" r="0.75">
          <stop offset="0" stopColor="#fff4e2" stopOpacity="0.32" />
          <stop offset="1" stopColor="#fff4e2" stopOpacity="0" />
        </radialGradient>
        <linearGradient id={fadeGrad} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0.74" stopColor="#fff" />
          <stop offset="0.98" stopColor="#000" />
        </linearGradient>
        <mask id={mask} maskUnits="userSpaceOnUse" x="0" y="0" width="300" height="400">
          <rect width="300" height="400" fill={fade ? `url(#${fadeGrad})` : '#fff'} />
        </mask>
      </defs>
      <g mask={`url(#${mask})`}>
        <path d={ARM_LEFT} fill={`url(#${skin})`} />
        <path d={ARM_RIGHT} fill={`url(#${skin})`} />
        <path d={TORSO} fill={`url(#${skin})`} />
        <path d={NECK} fill={`url(#${skin})`} />
        <ellipse cx={HEAD.cx} cy={HEAD.cy} rx={HEAD.rx} ry={HEAD.ry} fill={`url(#${skin})`} />
        <path d={TORSO} fill={`url(#${light})`} />
      </g>
    </svg>
  );
}

/** 마네킹 위에 겹치는 반팔 티(고른 티셔츠 색). 실제 착장 미리보기가 오기 전까지 보여 준다. */
export function TeeOverlay({ color, className, viewBox = FIGURE_VIEWBOX, preserveAspectRatio }: FigureProps & { color: string }) {
  const shade = useSvgId('tee-shade');
  return (
    <svg
      className={className}
      viewBox={viewBox}
      preserveAspectRatio={preserveAspectRatio}
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient id={shade} x1="0" x2="1" y1="0" y2="0">
          <stop offset="0" stopColor="#000" stopOpacity="0.34" />
          <stop offset="0.32" stopColor="#000" stopOpacity="0" />
          <stop offset="0.68" stopColor="#000" stopOpacity="0" />
          <stop offset="1" stopColor="#000" stopOpacity="0.36" />
        </linearGradient>
      </defs>
      <path d={TEE} fill={color} />
      <path d={TEE} fill={`url(#${shade})`} />
      {TEE_FOLDS.map((d) => (
        <path key={d} d={d} fill="none" stroke="#000" strokeOpacity="0.12" strokeWidth="2" strokeLinecap="round" />
      ))}
      <path d={TEE_COLLAR} fill="none" stroke="#fff" strokeOpacity="0.16" strokeWidth="3" />
    </svg>
  );
}
