import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { errorMessage } from '../api/api';
import { api } from '../api/client';
import type { FittingJob, Garment, StageState } from '../api/types';
import { FitReportView } from '../components/FitReportView';
import { StageTimeline } from '../components/StageTimeline';

const POLL_MS = 1000;

const isActive = (job: FittingJob) =>
  job.stages.some((stage) => stage.status === 'pending' || stage.status === 'running');

function previewMessage(job: FittingJob): string {
  const drape = job.stages.find((stage) => stage.name === 'drape');
  if (drape?.status === 'failed') return '3D 착장이 실패해 미리보기가 없습니다.';
  if (drape?.status === 'skipped') return '앞 단계가 실패해 미리보기가 없습니다.';
  return '3D 착장이 끝나면 미리보기가 나옵니다.';
}

function realizeMessage(stage: StageState | undefined): string {
  if (!stage) return '실사화 정보가 없습니다.';
  if (stage.status === 'skipped' || stage.status === 'failed') return stage.message ?? '실사화를 하지 않았습니다.';
  if (stage.status === 'running') return '실사화 중입니다.';
  return '렌더가 끝나면 실사화를 시작합니다.';
}

export function ResultPage() {
  const { jobId = '' } = useParams();
  const [job, setJob] = useState<FittingJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [garment, setGarment] = useState<Garment | null>(null);

  // 핏 리포트는 첫 응답에 바로 들어 있고, 나머지 단계는 끝날 때까지 1초마다 다시 묻는다.
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        const next = await api.getFitting(jobId);
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
  }, [jobId]);

  const garmentId = job?.garmentId;
  useEffect(() => {
    if (!garmentId) return;
    let alive = true;
    api
      .listGarments()
      .then((list) => alive && setGarment(list.find((item) => item.id === garmentId) ?? null))
      .catch(() => {
        // 티셔츠 이름을 못 불러와도 결과는 보여 줄 수 있다.
      });
    return () => {
      alive = false;
    };
  }, [garmentId]);

  if (!job) {
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

  const realizeStage = job.stages.find((stage) => stage.name === 'realize');
  const { realization, verification } = job;

  return (
    <section>
      <div className="result-head">
        <h1>{garment ? `${garment.name} · ${garment.colorName}` : '결과'}</h1>
        <Link to="/garments" className="button">
          다른 티셔츠 고르기
        </Link>
      </div>

      {error && (
        <p className="error" role="alert">
          진행 상태를 새로 받지 못했습니다: {error}
        </p>
      )}

      <FitReportView report={job.fitReport} />

      <section className="card" aria-labelledby="progress-title">
        <h2 id="progress-title">3D·실사 착장 진행</h2>
        <StageTimeline stages={job.stages} />
      </section>

      <div className="media-grid">
        <figure className="card media">
          <figcaption>{job.fallbackPreview ? '3D 착장 미리보기 · 대체 결과' : '3D 착장 미리보기'}</figcaption>
          {job.previewImageUrl ? (
            <img src={job.previewImageUrl} alt="3D 착장 미리보기" />
          ) : (
            <p className="placeholder">{previewMessage(job)}</p>
          )}
          {job.fallbackPreview && (
            <p className="note">실사화가 되지 않아 3D 미리보기를 대신 보여 드립니다. 실사 결과가 아닙니다.</p>
          )}
        </figure>

        <figure className="card media">
          <figcaption>실사 착장 결과</figcaption>
          {realization ? (
            <>
              {realization.videoUrl && <video src={realization.videoUrl} controls />}
              <div className="frames">
                {realization.frameUrl && <img src={realization.frameUrl} alt="실사 영상의 대표 프레임" />}
                {realization.renderFrameUrl && <img src={realization.renderFrameUrl} alt="같은 시점의 렌더 프레임" />}
              </div>
            </>
          ) : (
            <p className="placeholder">{realizeMessage(realizeStage)}</p>
          )}
          {verification && (
            <p className={verification.passed ? 'verify verify-pass' : 'verify verify-fail'}>
              검증 {verification.passed ? '통과' : '실패'} · 색 차이 {verification.colorDeltaE} · 실루엣 겹침{' '}
              {verification.silhouetteIoU}
            </p>
          )}
        </figure>
      </div>
    </section>
  );
}
