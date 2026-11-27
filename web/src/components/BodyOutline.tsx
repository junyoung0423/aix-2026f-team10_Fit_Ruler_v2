import type { ReactNode } from 'react';
import type { MeasurementField } from '../api/contract';
import { MEASUREMENT_LABEL } from '../api/contract';

/**
 * 입력 화면의 전신 선 그림(머리부터 발끝까지). WebGL이 없거나 3D를 불러오는 동안 보인다.
 * 칸을 고르면 그 치수를 재는 곳이 진하게 표시된다. 칸 옆 글이 같은 내용을 말하므로 보조 그림이다.
 */
const HEAD = { cx: 150, cy: 52, rx: 24, ry: 30 };
const NECK = 'M139 76 C140 86 139 92 136 100 L164 100 C161 92 160 86 161 76 Z';
const TORSO =
  'M136 98 C120 102 102 105 93 112 C86 118 84 130 86 142 C90 160 95 176 98 192 C101 220 103 244 104 262 ' +
  'C105 284 104 300 106 318 L194 318 C196 300 195 284 196 262 C197 244 199 220 202 192 C205 176 210 160 214 142 ' +
  'C216 130 214 118 207 112 C198 105 180 102 164 98 Z';
const ARM_LEFT =
  'M88 118 C78 124 73 140 71 158 C68 186 64 212 60 236 C56 266 52 296 49 324 C48 338 47 350 48 362 ' +
  'C50 374 62 374 64 362 C65 346 67 332 70 320 C74 292 79 266 83 242 C87 218 92 196 96 176 C98 160 97 140 94 126 Z';
const ARM_RIGHT =
  'M212 118 C222 124 227 140 229 158 C232 186 236 212 240 236 C244 266 248 296 251 324 C252 338 253 350 252 362 ' +
  'C250 374 238 374 236 362 C235 346 233 332 230 320 C226 292 221 266 217 242 C213 218 208 196 204 176 C202 160 203 140 206 126 Z';
const LEG_LEFT =
  'M106 300 C103 360 105 420 109 470 C111 510 113 550 114 590 C114 604 109 610 101 616 C96 620 98 628 107 628 ' +
  'L125 628 C131 628 133 620 131 610 C129 590 131 550 134 510 C137 470 141 420 145 360 L148 316 Z';
const LEG_RIGHT =
  'M194 300 C197 360 195 420 191 470 C189 510 187 550 186 590 C186 604 191 610 199 616 C204 620 202 628 193 628 ' +
  'L175 628 C169 628 167 620 169 610 C171 590 169 550 166 510 C163 470 159 420 155 360 L152 316 Z';

const MARKS: Record<MeasurementField, { shape: ReactNode; label: { x: number; y: number; anchor?: 'start' | 'end' } }> = {
  height_cm: {
    shape: (
      <>
        <line x1="20" y1="22" x2="20" y2="628" />
        <line x1="14" y1="22" x2="26" y2="22" />
        <line x1="14" y1="628" x2="26" y2="628" />
      </>
    ),
    label: { x: 9, y: 26, anchor: 'end' },
  },
  shoulder_cm: {
    shape: (
      <>
        <line x1="86" y1="116" x2="214" y2="116" />
        <circle cx="86" cy="116" r="3.2" className="mark-dot" />
        <circle cx="214" cy="116" r="3.2" className="mark-dot" />
      </>
    ),
    label: { x: 222, y: 110, anchor: 'start' },
  },
  chest_cm: { shape: <ellipse cx="150" cy="166" rx="58" ry="9" />, label: { x: 226, y: 170, anchor: 'start' } },
  waist_cm: { shape: <ellipse cx="150" cy="258" rx="50" ry="8" />, label: { x: 210, y: 262, anchor: 'start' } },
  // 정면 그림이라 사람의 오른팔은 그림의 왼쪽이다 (measure_v1: 오른쪽 어깨끝 → 팔꿈치 → 손목)
  arm_length_cm: {
    shape: (
      <>
        <polyline points="86,120 64,238 49,344" fill="none" />
        <circle cx="86" cy="120" r="3.2" className="mark-dot" />
        <circle cx="49" cy="344" r="3.2" className="mark-dot" />
      </>
    ),
    label: { x: 42, y: 292, anchor: 'end' },
  },
};

const ORDER: MeasurementField[] = ['height_cm', 'shoulder_cm', 'chest_cm', 'waist_cm', 'arm_length_cm'];

export function BodyOutline({ active }: { active: MeasurementField | null }) {
  return (
    // 왼쪽에 키 자와 팔길이 이름이 들어가도록 몸 그림(0~300)보다 왼쪽으로 30만큼 넓게 본다.
    <svg className="body-outline" viewBox="-30 0 330 640" aria-hidden="true" focusable="false">
      <g className="outline-body">
        <path d={ARM_LEFT} />
        <path d={ARM_RIGHT} />
        <path d={LEG_LEFT} />
        <path d={LEG_RIGHT} />
        <path d={TORSO} />
        <path d={NECK} />
        <ellipse cx={HEAD.cx} cy={HEAD.cy} rx={HEAD.rx} ry={HEAD.ry} />
      </g>
      {ORDER.map((field) => {
        const mark = MARKS[field];
        const on = active === field;
        return (
          <g key={field} className={`outline-mark${on ? ' is-active' : ''}${active && !on ? ' is-dim' : ''}`}>
            {mark.shape}
            <text x={mark.label.x} y={mark.label.y} textAnchor={mark.label.anchor ?? 'middle'}>
              {MEASUREMENT_LABEL[field]}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
