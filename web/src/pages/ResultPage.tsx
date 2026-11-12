import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { errorMessage } from '../api/api';
import { api } from '../api/client';
import type { FittingStatus, VisualPart } from '../api/types';
import { FitReportView, type ReportLike } from '../components/FitReportView';
import { StageTimeline } from '../components/StageTimeline';
import { ERROR_LABEL } from '../labels';
import { useFitting } from '../state/fittingStore';

const POLL_MS = 1000;

const isActive = (job: FittingStatus) => job.status === 'ACCEPTED' || job.status === 'RUNNING';

function previewMessage(visual: VisualPart): string {
  if (visual.status === 'FAILED') return '3D 착장 전에 멈춰 미리보기가 없습니다.';
  if (visual.status === 'SKIPPED') return '3D 착장을 하지 않았습니다.';
  return '3D 착장이 끝나면 미리보기가 나옵니다.';
}

function realizeMessage(job: FittingStatus): string {
  const { visual } = job;
  if (visual.skip_reason === 'CONSENT_NOT_GIVEN') return '실사화(외부 전송)에 동의하지 않아 실사 영상은 만들지 않았습니다.';
  if (visual.skip_reason === 'REPORT_ONLY') return '핏 리포트만 요청했습니다.';
  if (visual.status === 'FAILED') return visual.error_code ? (ERROR_LABEL[visual.error_code] ?? '실사화를 하지 못했습니다.') : '실사화를 하지 못했습니다.';
  const realize = job.stages.find((stage) => stage.stage === 'REALIZE');
  if (realize?.status === 'RUNNING') return '실사화 중입니다.';
  return '렌더가 끝나면 실사화를 시작합니다.';
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

export function ResultPage() {
  const { jobId: id = '' } = useParams();
  const { results } = useFitting();
  const stored = results[id] ?? null;
  const pollId = stored ? stored.jobId : id; // 저장된 결과가 없으면(새 탭 등) 주소의 ID를 작업 ID로 본다
  const [job, setJob] = useState<FittingStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 3D·실사화 단계는 끝날 때까지 1초마다 다시 묻는다. 핏 리포트는 이미 받아 두었다.
  useEffect(() => {
    if (!pollId) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        const next = await api.getFitting(pollId as string);
        if (!alive) return;
        setJob(next);
        setError(null);
        if (isActive(next)) timer = setTimeout(poll, POLL_MS);
      } catch (caught) {
        if (alive) setError(errorMessage(caught));
      }
    }
    void poll();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [pollId]);

  const report: ReportLike | null =
    stored?.report ??
    (job?.report.regions
      ? { ...job.report, regions: job.report.regions }
      : null);

  if (!report && !job) {
    return error ? (
      <section>
        <h1>결과</h1>
        <p className="error" role="alert">
          {error}
        </p>
        <Link to="/">처음으로</Link>
      </section>
    ) : (
      <p aria-busy="true">결과를 불러오는 중…</p>
    );
  }

  const visual = job?.visual ?? null;
  const preview = visual?.drape_preview_artifact_id ? api.artifactUrl(visual.drape_preview_artifact_id) : null;
  const frame = visual?.frame_artifact_id ? api.artifactUrl(visual.frame_artifact_id) : null;
  const renderFrame = visual?.render_frame_artifact_id ? api.artifactUrl(visual.render_frame_artifact_id) : null;
  const video = visual?.video_artifact_id ? api.artifactUrl(visual.video_artifact_id) : null;
  const fallback = visual?.status === 'FAILED' && preview !== null && visual.stage !== 'VALIDATE';
  const quality = visual ? qualityLine(visual) : null;

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
        {job ? (
          <StageTimeline stages={job.stages} visual={job.visual} />
        ) : (
          <p className="note">
            {stored && !stored.jobId
              ? '3D·실사 진행 상태를 받지 못해 핏 리포트만 보여 드립니다.'
              : (error ?? '진행 상태를 불러오는 중…')}
          </p>
        )}
        {job && error && (
          <p className="error" role="alert">
            진행 상태를 새로 받지 못했습니다: {error}
          </p>
        )}
      </section>

      {job && visual && (
        <div className="media-grid">
          <figure className="card media">
            <figcaption>{fallback ? '3D 착장 미리보기 · 대체 결과' : '3D 착장 미리보기'}</figcaption>
            {preview ? <img src={preview} alt="3D 착장 미리보기" /> : <p className="placeholder">{previewMessage(visual)}</p>}
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
          </figure>
        </div>
      )}
    </section>
  );
}
