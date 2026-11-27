import { useState, type CSSProperties, type ReactNode } from 'react';
import type { BodyInput, GarmentShape } from '../three/bodyModel';
import { webglAvailable } from '../three/support';
import {
  CHECKING_DOING,
  STEP_DOING,
  STEP_LABEL,
  STEP_ORDER,
  type CurrentStep,
  type StepKey,
  type StepView,
} from '../progress/steps';
import { PHOTO_FRAME, PHOTO_VIEWBOX } from './figurePaths';
import { Mannequin, TeeOverlay } from './Mannequin';
import { Studio3DStage } from './Studio3DStage';
import { StepTrack } from './StepTrack';

/**
 * 결과 화면의 어두운 피팅룸. WebGL이 되면 반짝이는 입자로 몸·옷·사진이 만들어지는 3D 장면(three/StudioEngine.ts)만 보여 준다
 * (3D를 불러오는 동안은 빈 무대). WebGL이 안 되거나 3D를 만들지 못하면 아래 2D 그림을 보여 준다. 2D 그림도 단계마다 효과가 하나씩 있다.
 * - 몸 만들기: 빛 고리가 아래에서 위로 올라가며 공통 마네킹이 드러난다(실제 몸은 옷 입히기 뒤 미리보기로 확인).
 * - 옷 입히기: 빛 리본이 몸을 돌며 내려가고, 지나간 자리부터 티셔츠가 생긴다.
 * - 정면 사진: 네 귀퉁이에서 빛 사진틀이 그려지고 안쪽이 부드럽게 한 번씩 밝아진다.
 * - 실사 사진: 사진 카드 위로 빛이 옆으로 지나간다(검증 중에는 더 천천히).
 * 실제 산출물이 오면 그림 대신 산출물을 보여 준다(착장 미리보기 → 렌더 클립).
 * `prefers-reduced-motion`이면 움직임 없이 각 단계의 마지막 모습만 보인다(styles.css).
 */

export interface StudioMedia {
  /** 3D 착장 미리보기(그림) */
  preview: string | null;
  /** 정면 렌더 클립(영상). 가짜 API는 파일이 없어 null */
  renderClip: string | null;
}

export interface StudioFailure {
  step: StepKey | null;
  message: string;
}

export interface StudioEta {
  text: string;
  slow: boolean;
  /** 이 단계를 화면이 처음 본 뒤 흐른 시간. 예: "0:42" */
  elapsed: string | null;
}

interface StudioProps {
  steps: StepView[];
  current: CurrentStep | null;
  failure?: StudioFailure | null;
  media: StudioMedia;
  garmentColor: string;
  /** 3D 마네킹을 만들 몸 치수(입력 화면 값) */
  body: Partial<BodyInput>;
  /** 3D 티셔츠를 만들 옷 치수(핏 리포트의 옷 값) */
  garment: GarmentShape | null;
  eta?: StudioEta | null;
  /** 진행 상태 갱신이 멈춘 채 남은 화면 */
  stale?: boolean;
  /** 멈췄을 때 버튼(다시 시도·다른 티셔츠) */
  children?: ReactNode;
}

const ORBIT = 'M76 30 A74 12 0 1 0 224 30 A74 12 0 1 0 76 30';

/** 빛 사진틀 위치(무대 3:4 안의 %) */
const FIGURE_FRAME: CSSProperties = {
  left: `${(PHOTO_FRAME.x / 300) * 100}%`,
  top: `${(PHOTO_FRAME.y / 400) * 100}%`,
  width: `${(PHOTO_FRAME.width / 300) * 100}%`,
  height: `${(PHOTO_FRAME.height / 400) * 100}%`,
};
const CARD_FRAME: CSSProperties = { left: '11%', top: '13%', width: '78%', height: '74%' };

/** 사진 카드 안: 렌더 클립 → 착장 미리보기 → (아무것도 없으면) 마네킹과 티셔츠 그림 */
function PhotoContent({ media, color }: { media: StudioMedia; color: string }) {
  if (media.renderClip) return <video className="card-media" src={media.renderClip} autoPlay muted loop playsInline />;
  if (media.preview) return <img className="card-media" src={media.preview} alt="" />;
  return (
    <>
      <Mannequin className="figure" viewBox={PHOTO_VIEWBOX} fade={false} preserveAspectRatio="xMidYMid slice" />
      <TeeOverlay className="figure" color={color} viewBox={PHOTO_VIEWBOX} preserveAspectRatio="xMidYMid slice" />
    </>
  );
}

function Corners() {
  return (
    <>
      <span className="corner corner-tl" />
      <span className="corner corner-tr" />
      <span className="corner corner-bl" />
      <span className="corner corner-br" />
    </>
  );
}

function BodyScene() {
  return (
    <div className="scene scene-body">
      <Mannequin className="figure figure-ghost" />
      <div className="figure-wrap reveal-up">
        <Mannequin className="figure" />
      </div>
      <span className="halo" />
    </div>
  );
}

function DrapeScene({ color }: { color: string }) {
  return (
    <div className="scene scene-drape">
      <Mannequin className="figure" />
      <div className="figure-wrap reveal-down">
        <TeeOverlay className="figure" color={color} />
      </div>
      <div className="orbit">
        <svg viewBox="0 0 300 60" focusable="false">
          <path className="orbit-track" d={ORBIT} pathLength={1} />
          <path className="orbit-comet" d={ORBIT} pathLength={1} />
        </svg>
      </div>
    </div>
  );
}

function RenderScene({ media, color }: { media: StudioMedia; color: string }) {
  const card = Boolean(media.preview || media.renderClip);
  return (
    <div className="scene scene-render">
      {card ? (
        <div className="photo-card">
          <PhotoContent media={media} color={color} />
        </div>
      ) : (
        <>
          <Mannequin className="figure" />
          <TeeOverlay className="figure" color={color} />
        </>
      )}
      <div className="light-frame" style={card ? CARD_FRAME : FIGURE_FRAME}>
        {/* 위 → 오른쪽 → 아래 → 왼쪽 순서로 선이 그려진다 */}
        <span className="edge edge-top" />
        <span className="edge edge-right" />
        <span className="edge edge-bottom" />
        <span className="edge edge-left" />
        <Corners />
        <span className="frame-flash" />
      </div>
    </div>
  );
}

function RealizeScene({ media, color, checking }: { media: StudioMedia; color: string; checking: boolean }) {
  return (
    <div className="scene scene-realize">
      <div className="photo-card">
        <PhotoContent media={media} color={color} />
        <span className={`sheen${checking ? ' is-slow' : ''}`} />
      </div>
    </div>
  );
}

function FailedScene({ media }: { media: StudioMedia }) {
  return (
    <div className="scene scene-failed">
      {media.preview ? (
        <div className="photo-card">
          <img className="card-media" src={media.preview} alt="" />
        </div>
      ) : (
        <Mannequin className="figure" />
      )}
    </div>
  );
}

function Scene({ current, failure, media, color }: { current: CurrentStep | null; failure: StudioFailure | null; media: StudioMedia; color: string }) {
  if (failure) return <FailedScene media={media} />;
  switch (current?.key ?? 'body') {
    case 'body':
      return <BodyScene />;
    case 'drape':
      return <DrapeScene color={color} />;
    case 'render':
      return <RenderScene media={media} color={color} />;
    case 'realize':
      return <RealizeScene media={media} color={color} checking={current?.checking ?? false} />;
  }
}

export function Studio({ steps, current, failure = null, media, garmentColor, body, garment, eta = null, stale = false, children }: StudioProps) {
  // WebGL이 되면 3D 장면만(불러오는 동안은 빈 무대), 안 되면 2D 그림만
  const [use3d, setUse3d] = useState(() => webglAvailable());
  const index = current ? STEP_ORDER.indexOf(current.key) : -1;
  const label = current ? STEP_LABEL[current.key] : '';
  const doing = current
    ? current.checking
      ? CHECKING_DOING
      : current.preparing
        ? `${label} 단계를 준비하고 있어요.`
        : STEP_DOING[current.key]
    : '';
  const failedTitle = failure
    ? failure.step
      ? `${STEP_LABEL[failure.step]}에서 멈췄어요`
      : '3D·실사 단계를 하지 못했어요'
    : '';
  // 단계가 바뀔 때만 화면 낭독기가 읽도록 이름과 문장만 한 줄로 둔다(경과 시간은 읽지 않는다).
  const announce = failure ? `${failedTitle}. ${failure.message}` : current ? `${label}: ${doing}` : '';

  return (
    <section className={`studio${stale ? ' is-stale' : ''}${failure ? ' is-failed' : ''}`} aria-labelledby="studio-title">
      <div className={`studio-stage${use3d ? ' is-3d' : ''}`}>
        {/* 3D는 배경이 투명해 카드 색이 그대로 보인다. 위쪽 빛 무리는 2D 그림에만 */}
        {!use3d && <span className="studio-spot" aria-hidden="true" />}
        {!use3d && (
          <div className="stage-2d" aria-hidden="true">
            {/* 단계가 바뀌면 key가 바뀌어 효과가 처음부터 다시 시작한다. */}
            <Scene key={failure ? 'failed' : (current?.key ?? 'body')} current={current} failure={failure} media={media} color={garmentColor} />
          </div>
        )}
        {use3d && (
          <Studio3DStage
            state={{
              step: failure ? failure.step : (current?.key ?? null),
              preparing: current?.preparing ?? false,
              checking: current?.checking ?? false,
              failed: Boolean(failure),
            }}
            body={body}
            garment={garment}
            onFail={() => setUse3d(false)}
          />
        )}
      </div>

      <div className="studio-caption">
        <p className="studio-kicker">{failure ? '멈춤' : index >= 0 ? `${index + 1} / ${STEP_ORDER.length}` : ''}</p>
        <h2 id="studio-title">{failure ? failedTitle : label}</h2>
        <p className="studio-doing">{failure ? failure.message : doing}</p>
        {!failure && eta && (
          <p className={`studio-eta${eta.slow ? ' is-slow' : ''}`}>
            {eta.text}
            {eta.elapsed && <span className="num"> · 지금 {eta.elapsed}</span>}
          </p>
        )}
        <p className="sr-only" role={failure ? 'alert' : 'status'}>
          {announce}
        </p>
      </div>

      <StepTrack steps={steps} tone="dark" />
      {children && <div className="studio-actions">{children}</div>}
    </section>
  );
}

/** 첫 진행 상태를 받기 전 자리 */
export function StudioPlaceholder({ text }: { text: string }) {
  return (
    <section className="studio is-placeholder" aria-busy="true">
      <div className="studio-stage" />
      <div className="studio-caption">
        <p className="studio-doing">{text}</p>
      </div>
    </section>
  );
}
