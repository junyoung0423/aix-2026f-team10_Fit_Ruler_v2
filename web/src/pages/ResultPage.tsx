import { useEffect, useState, type ReactNode } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { errorMessage } from '../api/api';
import { api } from '../api/client';
import type { FittingStatus } from '../api/types';
import { FitReportView, type ReportLike } from '../components/FitReportView';
import { ResultViewer, type FinishedOutcome, type ResultMedia } from '../components/ResultViewer';
import { Studio, StudioPlaceholder, type StudioMedia } from '../components/Studio';
import { formatElapsed } from '../progress/jobSummary';
import { MAX_CONSECUTIVE_FAILURES, isTerminal, startPolling } from '../progress/poller';
import { canRestart, restartFitting } from '../progress/retry';
import { currentStep, etaNote, outcomeOf, stepViews, type Outcome } from '../progress/steps';
import { useFitting } from '../state/fittingStore';
import { bodyForScene, garmentForScene } from '../three/shape';

/** 다시 부르기 상태(progress/poller.ts) */
type PollView =
  | { kind: 'polling'; failures: number; lastError: string | null }
  | { kind: 'done' }
  | { kind: 'lost' }
  | { kind: 'gave_up'; message: string };

const POLLING: PollView = { kind: 'polling', failures: 0, lastError: null };

/** 색을 모를 때(예전에 저장한 결과·새 탭) 스튜디오 티셔츠 그림 색 */
const DEFAULT_TEE_COLOR = '#24324f';

const isFinished = (outcome: Outcome | null): outcome is FinishedOutcome =>
  outcome !== null &&
  (outcome.kind === 'success' || outcome.kind === 'validation_failed' || outcome.kind === 'no_consent' || outcome.kind === 'fallback');

/** 다시 부르기가 끊겼을 때의 안내 */
function PollNotice({ poll, onReload }: { poll: PollView; onReload: () => void }) {
  if (poll.kind === 'polling' && poll.failures > 0) {
    return (
      <p className="notice notice-info">
        진행 상태를 받지 못해 다시 시도하는 중이에요 ({poll.failures}/{MAX_CONSECUTIVE_FAILURES}). {poll.lastError}
      </p>
    );
  }
  if (poll.kind === 'gave_up') {
    return (
      <div className="notice notice-error" role="alert">
        <strong>진행 상태를 받지 못했어요</strong>
        <p>{poll.message}</p>
        <button type="button" className="btn btn-ghost btn-small" onClick={onReload}>
          다시 불러오기
        </button>
      </div>
    );
  }
  if (poll.kind === 'lost') {
    return (
      <div className="notice notice-error" role="alert">
        <strong>작업 기록을 찾을 수 없어요</strong>
        서버가 다시 시작되었을 수 있어요.
      </div>
    );
  }
  return null;
}

/** 작업 주소가 바뀌면(다시 시도) 상태를 새로 시작하도록 작업 ID를 key로 준다. */
export function ResultPage() {
  const { jobId: id = '' } = useParams();
  return <ResultView key={id} id={id} />;
}

function ResultView({ id }: { id: string }) {
  const { results, saveResult, measurements } = useFitting();
  const navigate = useNavigate();
  const stored = results[id] ?? null;
  const pollId = stored ? stored.jobId : id; // 저장된 결과가 없으면(새 탭 등) 주소의 ID를 작업 ID로 본다
  const [job, setJob] = useState<FittingStatus | null>(null);
  const [poll, setPoll] = useState<PollView>(POLLING);
  const [round, setRound] = useState(0); // "다시 불러오기"를 누를 때마다 1씩 는다
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [stoppedAt, setStoppedAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [restarting, setRestarting] = useState(false);
  const [restartError, setRestartError] = useState<string | null>(null);
  // 지금 단계를 화면이 처음 본 시각(예상 시간 안내의 "지금 0:42"). 서버가 단계별 시각을 주지 않아 브라우저 시계로 잰다.
  const [phase, setPhase] = useState<{ id: string; since: number } | null>(null);

  // 3D·실사화 단계는 끝날 때까지 다시 묻는다(간격·멈춤 규칙은 progress/poller.ts). 핏 리포트는 이미 받아 두었다.
  useEffect(() => {
    if (!pollId) return;
    return startPolling(() => api.getFitting(pollId), {
      onStatus: (next) => {
        setJob(next);
        setPoll(POLLING);
        if (!isTerminal(next)) setStartedAt((prev) => prev ?? Date.now());
      },
      onRetry: (error, failures) => setPoll({ kind: 'polling', failures, lastError: errorMessage(error) }),
      onStop: (stop) => {
        setStoppedAt((prev) => prev ?? Date.now());
        if (stop.reason === 'done') setPoll({ kind: 'done' });
        else if (stop.reason === 'lost') setPoll({ kind: 'lost' });
        else setPoll({ kind: 'gave_up', message: errorMessage(stop.error) });
      },
    });
  }, [pollId, round]);

  useEffect(() => {
    if (startedAt === null || stoppedAt !== null) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [startedAt, stoppedAt]);

  const current = job ? currentStep(job) : null;
  const phaseId = current ? `${current.key}${current.checking ? '-check' : ''}${current.preparing ? '-prep' : ''}` : null;
  useEffect(() => {
    if (!phaseId) return;
    setPhase((prev) => (prev?.id === phaseId ? prev : { id: phaseId, since: Date.now() }));
  }, [phaseId]);

  function reload() {
    setPoll(POLLING);
    setStoppedAt(null);
    setRound((value) => value + 1);
  }

  async function restart() {
    if (!canRestart(stored)) return;
    setRestarting(true);
    setRestartError(null);
    try {
      const next = await restartFitting(api, stored);
      saveResult(next.id, next.result);
      navigate(`/fittings/${encodeURIComponent(next.id)}`);
    } catch (error) {
      setRestartError(errorMessage(error));
      setRestarting(false);
    }
  }

  const report: ReportLike | null =
    stored?.report ??
    (job?.report.regions
      ? { ...job.report, regions: job.report.regions }
      : null);

  const visual = job?.visual ?? null;
  const outcome = job ? outcomeOf(job) : null;
  const showActions = poll.kind === 'lost' || visual?.status === 'FAILED' || outcome?.kind === 'cancelled';
  const actions = showActions ? (
    <div className="result-actions">
      {canRestart(stored) && (
        <button type="button" className="btn btn-primary" onClick={() => void restart()} disabled={restarting}>
          {restarting ? '다시 요청 중…' : '같은 조건으로 다시 시도'}
        </button>
      )}
      <Link to="/garments" className="btn btn-ghost">
        다른 티셔츠 고르기
      </Link>
      {restartError && (
        <p className="notice notice-error" role="alert">
          {restartError}
        </p>
      )}
    </div>
  ) : null;

  const garmentTitle = stored?.garmentName ?? '피팅 결과';
  const color = stored?.garmentColor ?? DEFAULT_TEE_COLOR;

  if (!report && !job) {
    return (
      <section className="page">
        <header className="page-head">
          <p className="kicker">3 / 3</p>
          <h1>{garmentTitle}</h1>
        </header>
        {poll.kind === 'polling' ? (
          <StudioPlaceholder text="결과를 불러오는 중…" />
        ) : (
          <>
            <PollNotice poll={poll} onReload={reload} />
            {actions}
            <Link to="/" className="text-link">
              처음으로
            </Link>
          </>
        )}
        {poll.kind === 'polling' && <PollNotice poll={poll} onReload={reload} />}
      </section>
    );
  }

  const url = (artifactId: string | null | undefined) => (artifactId ? api.artifactUrl(artifactId) : null);
  const studioMedia: StudioMedia = {
    preview: url(visual?.drape_preview_artifact_id),
    renderClip: url(visual?.render_artifact_id),
  };
  const resultMedia: ResultMedia = {
    video: url(visual?.video_artifact_id),
    frame: url(visual?.frame_artifact_id),
    renderFrame: url(visual?.render_frame_artifact_id),
    preview: studioMedia.preview,
  };
  const steps = job ? stepViews(job) : [];
  const stale = job !== null && !isTerminal(job) && (poll.kind === 'lost' || poll.kind === 'gave_up');
  const stepSeconds = phase && current && stoppedAt === null ? Math.max(0, (now - phase.since) / 1000) : null;
  const eta = current
    ? { ...etaNote(current.key, stepSeconds), elapsed: stepSeconds !== null ? formatElapsed(stepSeconds * 1000) : null }
    : null;
  const qualityNote =
    visual?.quality_status === 'UNVERIFIABLE' ? '실사 영상을 자동으로 검증하지 못했어요. 참고로 봐 주세요.' : null;
  // 3D 피팅룸: 몸은 입력 치수로, 티셔츠는 핏 리포트의 옷 치수로 만든다.
  const sceneBody = bodyForScene(measurements, report?.regions);
  const sceneGarment = garmentForScene(sceneBody, report?.regions);

  let visualArea: ReactNode = null;
  let actionsShown = false;
  if (!job || !outcome) {
    if (stored && !stored.jobId) {
      visualArea = (
        <div className="panel-note">
          <strong>3D·실사 진행 상태를 받지 못했어요</strong>
          핏 리포트만 보여 드려요.
        </div>
      );
    } else if (poll.kind === 'polling') {
      visualArea = <StudioPlaceholder text="진행 상태를 불러오는 중…" />;
    }
  } else if (outcome.kind === 'running' || outcome.kind === 'failed') {
    actionsShown = outcome.kind === 'failed';
    visualArea = (
      <Studio
        steps={steps}
        current={current}
        failure={outcome.kind === 'failed' ? { step: outcome.failedStep, message: outcome.message } : null}
        media={studioMedia}
        garmentColor={color}
        body={sceneBody}
        garment={sceneGarment}
        eta={eta}
        stale={stale}
      >
        {outcome.kind === 'failed' ? actions : null}
      </Studio>
    );
  } else if (isFinished(outcome)) {
    actionsShown = true;
    visualArea = (
      <ResultViewer outcome={outcome} media={resultMedia} steps={steps} qualityNote={qualityNote}>
        {actions}
      </ResultViewer>
    );
  } else if (outcome.kind === 'report_only') {
    visualArea = (
      <div className="panel-note">
        <strong>핏 리포트만 요청했어요</strong>
        3D·실사 단계는 하지 않았어요.
      </div>
    );
  } else {
    actionsShown = true;
    visualArea = (
      <div className="panel-note">
        <strong>작업이 취소됐어요</strong>
        {actions}
      </div>
    );
  }

  return (
    <section className="page">
      <header className="page-head page-head-row">
        <div>
          <p className="kicker">3 / 3</p>
          <h1 className="garment-title">
            <span className="swatch" style={{ background: color }} aria-hidden="true" />
            {garmentTitle}
          </h1>
        </div>
        <Link to="/garments" className="btn btn-ghost btn-small">
          다른 티셔츠 고르기
        </Link>
      </header>

      <div className="result-layout">
        <div className="result-main">
          {visualArea}
          <PollNotice poll={poll} onReload={reload} />
          {!actionsShown && actions}
        </div>

        <aside className="result-side">
          {report ? (
            <FitReportView report={report} />
          ) : (
            <p className="notice notice-error">핏 리포트를 받지 못했어요.</p>
          )}
        </aside>
      </div>
    </section>
  );
}
