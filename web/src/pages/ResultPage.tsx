import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { errorMessage } from '../api/api';
import { api } from '../api/client';
import type { FittingStatus, VisualPart } from '../api/types';
import { ArtifactImage } from '../components/ArtifactImage';
import { FitReportView, type ReportLike } from '../components/FitReportView';
import { StageTimeline } from '../components/StageTimeline';
import { formatElapsed, jobHeadline, realizeMessage } from '../progress/jobSummary';
import { MAX_CONSECUTIVE_FAILURES, isTerminal, startPolling } from '../progress/poller';
import { canRestart, restartFitting } from '../progress/retry';
import { useFitting } from '../state/fittingStore';

/** 다시 부르기 상태(progress/poller.ts) */
type PollView =
  | { kind: 'polling'; failures: number; lastError: string | null }
  | { kind: 'done' }
  | { kind: 'lost' }
  | { kind: 'gave_up'; message: string };

const POLLING: PollView = { kind: 'polling', failures: 0, lastError: null };

function previewMessage(visual: VisualPart): string {
  if (visual.status === 'FAILED') return '3D 착장 전에 멈춰 미리보기가 없습니다.';
  if (visual.status === 'SKIPPED') return '3D 착장을 하지 않았습니다.';
  return '3D 착장이 끝나면 미리보기가 나옵니다.';
}

function qualityLine(visual: VisualPart): { text: string; ok: boolean } | null {
  if (!visual.quality_status || visual.quality_status === 'PENDING') return null;
  const metrics = visual.quality_metrics ?? {};
  const numbers = [
    metrics.color_delta_e !== undefined && `색 차이 ${metrics.color_delta_e}`,
    metrics.silhouette_iou !== undefined && `실루엣 겹침 ${metrics.silhouette_iou}`,
  ]
    .filter(Boolean)
    .join(' · ');
  const title = { PASS: '검증 통과', FAIL: '검증 실패', UNVERIFIABLE: '검증할 수 없음' }[visual.quality_status];
  return { text: numbers ? `${title} · ${numbers}` : title, ok: visual.quality_status === 'PASS' };
}

/** 다시 부르기가 끊겼을 때의 안내 */
function PollNotice({ poll, onReload }: { poll: PollView; onReload: () => void }) {
  if (poll.kind === 'polling' && poll.failures > 0) {
    return (
      <p className="note poll-retry">
        진행 상태를 받지 못해 다시 시도하는 중입니다 ({poll.failures}/{MAX_CONSECUTIVE_FAILURES}). {poll.lastError}
      </p>
    );
  }
  if (poll.kind === 'gave_up') {
    return (
      <div className="error poll-stopped" role="alert">
        <p>진행 상태를 받지 못했습니다.</p>
        <p className="note">{poll.message}</p>
        <button type="button" onClick={onReload}>
          다시 불러오기
        </button>
      </div>
    );
  }
  if (poll.kind === 'lost') {
    return (
      <div className="error poll-stopped" role="alert">
        <p>작업 기록을 찾을 수 없습니다. 서버가 다시 시작되었을 수 있습니다.</p>
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
  const { results, saveResult } = useFitting();
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

  // 3D·실사화 단계는 끝날 때까지 다시 묻는다(간격·멈춤 규칙은 progress/poller.ts). 핏 리포트는 이미 받아 두었다.
  useEffect(() => {
    if (!pollId) return;
    return startPolling(() => api.getFitting(pollId), {
      onStatus: (next) => {
        setJob(next);
        setPoll(POLLING);
        // 경과 시간은 서버가 시각을 주지 않아 화면이 진행 중인 상태를 처음 받은 때부터 브라우저 시계로 잰다.
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
  const showActions = poll.kind === 'lost' || visual?.status === 'FAILED';
  const actions = showActions && (
    <div className="actions recover-actions">
      {canRestart(stored) && (
        <button type="button" className="primary" onClick={() => void restart()} disabled={restarting}>
          {restarting ? '다시 요청 중…' : '같은 조건으로 다시 시도'}
        </button>
      )}
      <Link to="/garments" className="button">
        다른 티셔츠 고르기
      </Link>
      {restartError && (
        <p className="error" role="alert">
          {restartError}
        </p>
      )}
    </div>
  );

  if (!report && !job) {
    if (poll.kind === 'polling') {
      return (
        <section>
          <p aria-busy="true">결과를 불러오는 중…</p>
          <PollNotice poll={poll} onReload={reload} />
        </section>
      );
    }
    return (
      <section>
        <h1>결과</h1>
        <PollNotice poll={poll} onReload={reload} />
        {actions}
        <Link to="/">처음으로</Link>
      </section>
    );
  }

  const preview = visual?.drape_preview_artifact_id ? api.artifactUrl(visual.drape_preview_artifact_id) : null;
  const frame = visual?.frame_artifact_id ? api.artifactUrl(visual.frame_artifact_id) : null;
  const renderFrame = visual?.render_frame_artifact_id ? api.artifactUrl(visual.render_frame_artifact_id) : null;
  const video = visual?.video_artifact_id ? api.artifactUrl(visual.video_artifact_id) : null;
  const fallback = visual?.status === 'FAILED' && preview !== null && visual.stage !== 'VALIDATE';
  // 실사화·검증이 실패해도 핏 리포트와 3D 미리보기는 남는다(#12). "대체 결과" 표시는 위 fallback(A6)과 같은 조건을 쓴다.
  const keptNote = visual?.status === 'FAILED' && preview !== null && (visual.stage === 'REALIZE' || visual.stage === 'VALIDATE');
  const quality = visual ? qualityLine(visual) : null;
  const headline = job ? jobHeadline(job) : null;
  const stale = job !== null && !isTerminal(job) && (poll.kind === 'lost' || poll.kind === 'gave_up');
  const elapsed = startedAt !== null ? formatElapsed((stoppedAt ?? now) - startedAt) : null;

  return (
    <section>
      <div className="result-head">
        <h1>{stored?.garmentName ?? '결과'}</h1>
        <Link to="/garments" className="button">
          다른 티셔츠 고르기
        </Link>
      </div>

      {report ? <FitReportView report={report} /> : <p className="error">핏 리포트를 받지 못했습니다.</p>}

      <section className="card" aria-labelledby="progress-title">
        <h2 id="progress-title">3D·실사 착장 진행</h2>
        {headline && (
          <div className={`job-headline tone-${stale ? 'stale' : headline.tone}`}>
            {/* 화면 낭독기는 이 줄이 바뀔 때(단계가 넘어갈 때)만 알린다. 경과 시간은 알리지 않는다. */}
            <p role="status">{stale ? `${headline.text} · 갱신 멈춤` : headline.text}</p>
            {elapsed !== null && <span className="elapsed num">경과 {elapsed}</span>}
          </div>
        )}
        {job ? (
          <div className={stale ? 'timeline-stale' : undefined}>
            <StageTimeline stages={job.stages} visual={job.visual} />
          </div>
        ) : (
          stored && !stored.jobId ? (
            <p className="note">3D·실사 진행 상태를 받지 못해 핏 리포트만 보여 드립니다.</p>
          ) : (
            poll.kind === 'polling' && <p className="note">진행 상태를 불러오는 중…</p>
          )
        )}
        <PollNotice poll={poll} onReload={reload} />
        {actions}
      </section>

      {job && visual && (
        <div className="media-grid">
          <figure className="card media">
            <figcaption>{fallback ? '3D 착장 미리보기 · 대체 결과' : '3D 착장 미리보기'}</figcaption>
            {preview ? (
              <ArtifactImage key={preview} src={preview} alt="3D 착장 미리보기" failText="미리보기 파일을 불러오지 못했습니다." />
            ) : (
              <p className="placeholder">{previewMessage(visual)}</p>
            )}
            {fallback && <p className="note">실사 영상이 없어 3D 미리보기를 대신 보여 드립니다. 실사 결과가 아닙니다.</p>}
          </figure>

          <figure className="card media">
            <figcaption>{visual.quality_status === 'FAIL' ? '실사 착장 결과 · 검증 실패' : '실사 착장 결과'}</figcaption>
            {frame || video ? (
              <>
                {video && <video src={video} controls />}
                <div className="frames">
                  {frame && <img src={frame} alt="실사 영상의 대표 프레임" />}
                  {renderFrame && <img src={renderFrame} alt="같은 시점의 렌더 프레임" />}
                </div>
              </>
            ) : (
              <p className="placeholder">{realizeMessage(job)}</p>
            )}
            {quality && <p className={quality.ok ? 'verify verify-pass' : 'verify verify-fail'}>{quality.text}</p>}
            {keptNote && <p className="note">핏 리포트와 3D 미리보기는 그대로 볼 수 있습니다.</p>}
          </figure>
        </div>
      )}
    </section>
  );
}
