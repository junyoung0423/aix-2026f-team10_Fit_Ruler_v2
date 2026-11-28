import { useState, type ReactNode } from 'react';
import { STEP_LABEL, type Outcome, type StepView } from '../progress/steps';
import { ArtifactImage } from './ArtifactImage';
import { StepTrack } from './StepTrack';

/** 끝난 뒤 보여 줄 수 있는 결과 종류 */
export type FinishedOutcome = Extract<Outcome, { kind: 'success' | 'validation_failed' | 'no_consent' | 'fallback' }>;

export interface ResultMedia {
  /** 실사 회전 영상(8초 한 바퀴). 가짜 API는 파일이 없어 null */
  video: string | null;
  /** 실사 영상의 대표 프레임 */
  frame: string | null;
  /** 같은 시점의 렌더 프레임(빛 커튼이 지나가며 실사로 바뀌는 첫 장면에 쓴다) */
  renderFrame: string | null;
  /** 3D 착장 미리보기 */
  preview: string | null;
}

type Tab = 'real' | '3d';

const TITLE: Record<FinishedOutcome['kind'], string> = {
  success: '실사 영상이 완성됐어요',
  validation_failed: '실사 영상 · 검증 실패',
  no_consent: '3D 미리보기',
  fallback: '3D 미리보기 · 대체 결과',
};

function Notice({ outcome, qualityNote }: { outcome: FinishedOutcome; qualityNote: string | null }) {
  switch (outcome.kind) {
    case 'validation_failed':
      return (
        <div className="notice notice-warn">
          <strong>검증을 통과하지 못했어요</strong>
          {outcome.message} 정상 결과와 다를 수 있으니 참고로만 봐 주세요.
        </div>
      );
    case 'no_consent':
      return (
        <div className="notice notice-info">
          <strong>실사 영상은 만들지 않았어요</strong>
          외부 전송에 동의하지 않아 3D 미리보기까지만 만들었어요. 다시 입어 볼 때 &lsquo;실사 사진·회전 영상도 만들기&rsquo;를 켜면 만들 수 있어요.
        </div>
      );
    case 'fallback':
      return (
        <div className="notice notice-warn">
          <strong>{STEP_LABEL[outcome.failedStep]}에서 멈췄어요</strong>
          {outcome.message} 실사 결과가 아니라 3D 미리보기를 대신 보여 드려요.
        </div>
      );
    case 'success':
      return qualityNote ? <div className="notice notice-info">{qualityNote}</div> : null;
  }
}

/** 실사 영상(없으면 대표 프레임). 렌더 프레임이 있으면 처음에 빛 커튼이 지나가며 렌더에서 실사로 바뀐다. */
function RealMedia({ media }: { media: ResultMedia }) {
  const reveal = media.renderFrame !== null && (media.video !== null || media.frame !== null);
  return (
    <>
      {media.video ? (
        <video
          src={media.video}
          poster={media.frame ?? undefined}
          controls
          autoPlay
          muted
          loop
          playsInline
          aria-label="8초 동안 한 바퀴 도는 실사 영상"
        />
      ) : (
        media.frame && (
          <ArtifactImage key={media.frame} src={media.frame} alt="실사 영상의 대표 장면" failText="실사 영상의 대표 장면을 불러오지 못했습니다." />
        )
      )}
      {reveal && media.renderFrame && (
        <div className="reveal" aria-hidden="true">
          <img className="reveal-wipe" src={media.renderFrame} alt="" />
          <span className="reveal-line" />
        </div>
      )}
    </>
  );
}

/**
 * 작업이 끝난 뒤 결과 카드. 실사 영상과 3D 미리보기를 바꿔 볼 수 있다.
 * 동의하지 않았거나 실사 단계에서 멈췄으면 3D 미리보기만, 검증만 실패했으면 실사를 보여 주되 검증 실패를 함께 알린다.
 */
export function ResultViewer({
  outcome,
  media,
  steps,
  qualityNote = null,
  children,
}: {
  outcome: FinishedOutcome;
  media: ResultMedia;
  steps: StepView[];
  qualityNote?: string | null;
  children?: ReactNode;
}) {
  const hasReal = media.video !== null || media.frame !== null;
  const has3d = media.preview !== null;
  const [tab, setTab] = useState<Tab>(hasReal ? 'real' : '3d');
  // 안내 상자가 있는지(낮은 화면에서 사진 칸 크기를 정할 때 그만큼 뺀다)
  const hasNotice = outcome.kind !== 'success' || qualityNote !== null;
  const shown: Tab = tab === 'real' && !hasReal ? '3d' : tab === '3d' && !has3d && hasReal ? 'real' : tab;

  const caption =
    shown === 'real'
      ? media.video
        ? '8초 동안 한 바퀴 도는 실사 영상이에요.'
        : '실사 영상의 대표 장면이에요.'
      : '고른 티셔츠를 내 치수의 3D 몸에 입혀 정면에서 본 모습이에요.';

  return (
    <section className={`result-card is-${outcome.kind}${hasNotice ? ' has-notice' : ''}`} aria-labelledby="result-title">
      <div className="result-card-head">
        <h2 id="result-title">{TITLE[outcome.kind]}</h2>
        {hasReal && has3d && (
          <div className="seg" role="tablist" aria-label="결과 보기">
            <button type="button" role="tab" aria-selected={shown === 'real'} onClick={() => setTab('real')}>
              실사 영상
            </button>
            <button type="button" role="tab" aria-selected={shown === '3d'} onClick={() => setTab('3d')}>
              3D 미리보기
            </button>
          </div>
        )}
      </div>

      <div className="result-media" role={hasReal && has3d ? 'tabpanel' : undefined}>
        {shown === 'real' ? (
          <RealMedia media={media} />
        ) : media.preview ? (
          <ArtifactImage key={media.preview} src={media.preview} alt="3D 착장 미리보기" failText="미리보기 파일을 불러오지 못했습니다." />
        ) : (
          <p className="media-empty">3D 미리보기가 없어요.</p>
        )}
      </div>
      <p className="media-caption">{caption}</p>

      <Notice outcome={outcome} qualityNote={qualityNote} />
      <StepTrack steps={steps} tone="dark" />
      {children}
    </section>
  );
}
