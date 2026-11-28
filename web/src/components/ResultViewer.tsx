import { useEffect, useRef, useState, type ReactNode } from 'react';
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
  validation_failed: '실사 영상',
  no_consent: '3D 미리보기',
  fallback: '3D 미리보기 · 대체 결과',
};

/** 실사 영상과 함께 알릴 것. 영상을 가리지 않게 제목 옆 한 줄 표시로 두고, 누르면 영상 위에 전체 문장을 펼친다. */
interface Note {
  chip: string;
  title: string | null;
  body: string;
  tone: 'warn' | 'info';
}

function noteOf(outcome: FinishedOutcome, qualityNote: string | null): Note | null {
  if (outcome.kind === 'validation_failed') {
    return {
      chip: '검증 실패 · 참고용',
      title: '검증을 통과하지 못했어요',
      body: `${outcome.message} 정상 결과와 다를 수 있으니 참고로만 봐 주세요.`,
      tone: 'warn',
    };
  }
  if (outcome.kind === 'success' && qualityNote) {
    return { chip: '자동 검증 못 함 · 참고용', title: null, body: qualityNote, tone: 'info' };
  }
  return null;
}

/** 실사 영상이 없을 때의 안내(미동의·대체 결과). 3D 미리보기 위에 상자로 두고, 다시 시도 단추도 여기에 둔다. */
function Notice({ outcome, children }: { outcome: FinishedOutcome; children?: ReactNode }) {
  switch (outcome.kind) {
    case 'no_consent':
      return (
        <div className="notice notice-info">
          <strong>실사 영상은 만들지 않았어요</strong>
          외부 전송에 동의하지 않아 3D 미리보기까지만 만들었어요. 다시 입어 볼 때 &lsquo;실사 사진·회전 영상도 만들기&rsquo;를 켜면 만들 수 있어요.
          {children}
        </div>
      );
    case 'fallback':
      return (
        <div className="notice notice-warn">
          <strong>{STEP_LABEL[outcome.failedStep]}에서 멈췄어요</strong>
          {outcome.message} 실사 결과가 아니라 3D 미리보기를 대신 보여 드려요.
          {children}
        </div>
      );
    default:
      return null;
  }
}

function PauseIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <rect x="3.5" y="2.5" width="3" height="11" rx="0.8" />
      <rect x="9.5" y="2.5" width="3" height="11" rx="0.8" />
    </svg>
  );
}

function PlayIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M4.6 2.9v10.2a.6.6 0 0 0 .92.5l8-5.1a.6.6 0 0 0 0-1l-8-5.1a.6.6 0 0 0-.92.5z" />
    </svg>
  );
}

function ExpandIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path
        d="M2.6 6V2.6H6M10 2.6h3.4V6M13.4 10v3.4H10M6 13.4H2.6V10"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

type FullscreenVideo = HTMLVideoElement & { webkitEnterFullscreen?: () => void };

/**
 * 실사 회전 영상. 칸을 위쪽(목·어깨) 기준으로 채우고(아래 다리 쪽이 잘린다), 소리 없이 반복 재생한다.
 * 조작은 일시정지·전체 화면 단추 두 개만 두고, 전체 화면에서는 브라우저 기본 조작 막대를 쓴다.
 * 움직임 줄이기 설정이면 멈춘 채(대표 장면) 시작한다.
 */
function RealVideo({ src, poster }: { src: string; poster: string | null }) {
  const ref = useRef<FullscreenVideo>(null);
  const [reduceMotion] = useState(
    () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true,
  );
  const [paused, setPaused] = useState(reduceMotion);

  useEffect(() => {
    const video = ref.current;
    if (!video) return;
    const sync = () => setPaused(video.paused);
    const onFullscreen = () => {
      video.controls = document.fullscreenElement === video;
    };
    video.addEventListener('play', sync);
    video.addEventListener('pause', sync);
    video.addEventListener('canplay', sync);
    document.addEventListener('fullscreenchange', onFullscreen);
    return () => {
      video.removeEventListener('play', sync);
      video.removeEventListener('pause', sync);
      video.removeEventListener('canplay', sync);
      document.removeEventListener('fullscreenchange', onFullscreen);
    };
  }, []);

  const toggle = () => {
    const video = ref.current;
    if (!video) return;
    if (video.paused) void video.play().catch(() => undefined);
    else video.pause();
  };

  const fullscreen = () => {
    const video = ref.current;
    if (!video) return;
    if (typeof video.requestFullscreen === 'function') void video.requestFullscreen().catch(() => undefined);
    else video.webkitEnterFullscreen?.();
  };

  return (
    <>
      <video
        ref={ref}
        src={src}
        poster={poster ?? undefined}
        autoPlay={!reduceMotion}
        muted
        loop
        playsInline
        aria-label="8초 동안 한 바퀴 도는 실사 영상"
      />
      <div className="media-controls">
        <button type="button" className="media-btn" onClick={toggle} aria-label={paused ? '영상 재생' : '영상 일시정지'}>
          {paused ? <PlayIcon /> : <PauseIcon />}
        </button>
        <button type="button" className="media-btn" onClick={fullscreen} aria-label="전체 화면으로 보기">
          <ExpandIcon />
        </button>
      </div>
    </>
  );
}

/** 실사 영상(없으면 대표 프레임). 렌더 프레임이 있으면 처음에 빛 커튼이 지나가며 렌더에서 실사로 바뀐다. */
function RealMedia({ media }: { media: ResultMedia }) {
  const reveal = media.renderFrame !== null && (media.video !== null || media.frame !== null);
  return (
    <>
      {media.video ? (
        <RealVideo src={media.video} poster={media.frame} />
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
 * 작업이 끝난 뒤 결과 카드. 실사 영상이 카드의 주인공이고, 3D 미리보기로 바꿔 볼 수 있다.
 * - 검증 실패·자동 검증 못 함은 제목 옆 한 줄 표시로 알리고, 누르면 영상 위에 전체 문장(과 다시 시도 단추)을 펼친다.
 * - 동의하지 않았거나 실사 단계에서 멈췄으면 3D 미리보기만 보여 주고, 그 이유는 미리보기 위 안내 상자로 알린다.
 * - 넓은 화면에서는 진행 표시(와 3D 설명)가 영상 아래쪽에 겹친다(styles.css).
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
  const note = noteOf(outcome, qualityNote);
  const [noteOpen, setNoteOpen] = useState(false);
  const chipRef = useRef<HTMLButtonElement>(null);
  // 안내 상자(미동의·대체 결과)가 있는지(낮은 화면에서 사진 칸 크기를 정할 때 그만큼 뺀다)
  const hasNotice = outcome.kind === 'no_consent' || outcome.kind === 'fallback';
  const shown: Tab = tab === 'real' && !hasReal ? '3d' : tab === '3d' && !has3d && hasReal ? 'real' : tab;
  const caption = shown === '3d' ? '고른 티셔츠를 내 치수의 3D 몸에 입혀 정면에서 본 모습이에요.' : null;

  const closeNote = () => {
    setNoteOpen(false);
    chipRef.current?.focus();
  };

  return (
    <section className={`result-card is-${outcome.kind}${hasNotice ? ' has-notice' : ''}`} aria-labelledby="result-title">
      <div className="result-card-head">
        <div className="result-title">
          <h2 id="result-title">{TITLE[outcome.kind]}</h2>
          {note && (
            <button
              ref={chipRef}
              type="button"
              className={`result-chip is-${note.tone}`}
              aria-expanded={noteOpen}
              aria-controls="result-note"
              onClick={() => setNoteOpen((open) => !open)}
            >
              {note.chip}
              <span className="result-chip-caret" aria-hidden="true" />
            </button>
          )}
        </div>
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

      <Notice outcome={outcome}>{children}</Notice>

      <div className={`result-media${shown === 'real' ? ' is-real' : ''}`} role={hasReal && has3d ? 'tabpanel' : undefined}>
        {shown === 'real' ? (
          <RealMedia media={media} />
        ) : media.preview ? (
          <ArtifactImage key={media.preview} src={media.preview} alt="3D 착장 미리보기" failText="미리보기 파일을 불러오지 못했습니다." />
        ) : (
          <p className="media-empty">3D 미리보기가 없어요.</p>
        )}
        {note && (
          <div
            id="result-note"
            className={`result-note is-${note.tone}`}
            role="region"
            aria-label={note.title ?? note.chip}
            hidden={!noteOpen}
          >
            <div className="result-note-head">
              {note.title ? <strong>{note.title}</strong> : <span />}
              <button type="button" className="result-note-close" onClick={closeNote} aria-label="안내 닫기">
                ×
              </button>
            </div>
            <p>{note.body}</p>
            {children}
          </div>
        )}
      </div>

      <div className="result-foot">
        {caption && <p className="media-caption">{caption}</p>}
        <StepTrack steps={steps} tone="dark" />
        {!note && !hasNotice && children}
      </div>
    </section>
  );
}
