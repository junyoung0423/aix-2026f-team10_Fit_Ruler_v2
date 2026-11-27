import { useEffect, useState, type FormEvent } from 'react';
import { Link, Navigate, useNavigate } from 'react-router-dom';
import { ApiError, errorMessage, isFittingApiMissing } from '../api/api';
import { api } from '../api/client';
import { MEASUREMENT_FIELDS, MEASUREMENT_LABEL, measurementFieldOf } from '../api/contract';
import type { FitRequest, FittingStatus, GarmentListItem } from '../api/types';
import { MEASUREMENT_PROTOCOL_VERSION } from '../api/types';
import { TeeIllustration } from '../components/TeeIllustration';
import { sizeItems, sizeText, sortGarments, sourceBadge } from '../garmentCard';
import { fabricText, garmentName, swatchColor } from '../labels';
import { useFitting } from '../state/fittingStore';
import type { InputPageState } from './InputPage';

function GarmentCardBody({ item }: { item: GarmentListItem }) {
  const { garment } = item;
  const badge = sourceBadge(item);
  const fabric = fabricText(garment.fabric);
  return (
    <>
      <span className="garment-visual">
        <TeeIllustration color={swatchColor(garment)} />
        {badge && <span className={`garment-badge${badge.kind === 'measured' ? ' is-measured' : ''}`}>{badge.text}</span>}
      </span>
      <span className="garment-info">
        <span className="garment-name">{garmentName(garment)}</span>
        <span className="garment-sizes num">{sizeText(sizeItems(garment.measurements))}</span>
        {fabric && <span className="garment-meta">{fabric}</span>}
        {badge?.kind === 'measured' && <span className="garment-source">{badge.source}</span>}
      </span>
      <span className="garment-check" aria-hidden="true">
        <svg viewBox="0 0 16 16" focusable="false">
          <path d="M4 8.5 L7 11.2 L12.2 5.4" fill="none" />
        </svg>
      </span>
    </>
  );
}

export function GarmentSelectPage() {
  const { measurements, saveResult } = useFitting();
  const navigate = useNavigate();
  const [garments, setGarments] = useState<GarmentListItem[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // 실사화는 렌더 클립을 외부 서버로 보내므로 동의해야 한다. 기본은 꺼짐(wireframes.md 규칙 2).
  const [consent, setConsent] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string[] | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .listGarments()
      .then((list) => alive && setGarments(sortGarments(list.garments)))
      .catch((error: unknown) => alive && setLoadError(errorMessage(error)));
    return () => {
      alive = false;
    };
  }, []);

  if (!measurements) return <Navigate to="/" replace />;
  const current = measurements;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const item = garments?.find((g) => g.garment.id === selectedId);
    if (!item) return;
    setSubmitting(true);
    setSubmitError(null);
    const fitRequest: FitRequest = {
      garment_variant_id: item.garment.id,
      measurements: current,
      measurement_protocol_version: MEASUREMENT_PROTOCOL_VERSION,
    };
    try {
      // 1) 핏 리포트는 3D·AI 없이 바로 계산한다 (POST /api/fit, B2)
      const report = await api.fit(fitRequest);
      // 2) 3D·실사화 작업 (POST /api/fittings, B3). 작업 서버에 닿지 못하거나 경로가 없을 때(경로 없음 404·연결 안 됨)만
      //    리포트만 보여 준다(안전장치). 그 밖의 실패(422·409·500 등)는 아래 catch에서 이 화면에 오류로 보여 준다.
      let job: FittingStatus | null = null;
      try {
        job = await api.createFitting({ ...fitRequest, mode: 'full', realize_consent: consent });
      } catch (error) {
        if (!isFittingApiMissing(error)) throw error;
      }
      const id = job?.job_id ?? report.job_id;
      saveResult(id, {
        report,
        garmentId: item.garment.id,
        garmentName: garmentName(item.garment),
        garmentColor: swatchColor(item.garment),
        jobId: job?.job_id ?? null,
        // 결과 화면의 "같은 조건으로 다시 시도"가 실사화 동의까지 그대로 다시 보낸다.
        request: job ? { ...fitRequest, mode: 'full', realize_consent: consent } : undefined,
      });
      navigate(`/fittings/${encodeURIComponent(id)}`);
    } catch (error) {
      // 서버가 치수를 거절하면(422) 칸별 오류를 들고 입력 화면으로 돌아간다.
      if (error instanceof ApiError && error.code === 'INPUT_INVALID' && error.fields.some((f) => measurementFieldOf(f.field))) {
        const state: InputPageState = { message: error.message, fields: error.fields };
        navigate('/', { state });
        return;
      }
      // 없는 의류(404)·계산할 수 없는 의류(409) 등은 서버 문구를 이 화면에 보여 준다.
      const lines = [errorMessage(error)];
      if (error instanceof ApiError) lines.push(...error.fields.map((field) => field.message));
      setSubmitError(lines);
      setSubmitting(false);
    }
  }

  return (
    <section className="page">
      <header className="page-head">
        <p className="kicker">2 / 3</p>
        <h1>티셔츠 고르기</h1>
        <div className="measure-summary">
          <span className="summary-title">내 치수</span>
          <ul className="summary-chips">
            {MEASUREMENT_FIELDS.map((field) => (
              <li key={field}>
                {MEASUREMENT_LABEL[field]} <span className="num">{current[field]}</span>
              </li>
            ))}
          </ul>
          <Link to="/" className="text-link">
            수정
          </Link>
        </div>
      </header>

      {loadError && (
        <p className="notice notice-error" role="alert">
          {loadError}
        </p>
      )}
      {!garments && !loadError && (
        <p className="loading-line" aria-busy="true">
          티셔츠 목록을 불러오는 중…
        </p>
      )}
      {garments && garments.length === 0 && <p>지금 고를 수 있는 티셔츠가 없어요.</p>}

      {garments && garments.length > 0 && (
        <form onSubmit={onSubmit}>
          <fieldset className="garment-grid">
            <legend className="sr-only">티셔츠</legend>
            {garments.map((item) => {
              const { garment } = item;
              const selected = selectedId === garment.id;
              return (
                <label key={garment.id} className={`garment-card${selected ? ' is-selected' : ''}`}>
                  <input
                    className="card-radio"
                    type="radio"
                    name="garment"
                    value={garment.id}
                    checked={selected}
                    onChange={() => setSelectedId(garment.id)}
                  />
                  <GarmentCardBody item={item} />
                </label>
              );
            })}
          </fieldset>

          <label className={`consent-row${consent ? ' is-on' : ''}`}>
            <span className="consent-text">
              <strong id="consent-title">실사 사진·회전 영상도 만들기</strong>
              <span id="consent-desc">
                켜면 신체 치수가 반영된 렌더 클립과 그로 만든 사진이 외부 서버(fal)로 전송되어 처리됩니다. 사진과 영상은
                fal에서 Google(Veo·Nano Banana Pro)과 Lightricks(LTX) 모델로 만듭니다. 끄면 핏 리포트와 3D 미리보기까지만
                만들어요.
              </span>
            </span>
            <input
              className="switch"
              type="checkbox"
              role="switch"
              aria-labelledby="consent-title"
              aria-describedby="consent-desc"
              checked={consent}
              onChange={(event) => setConsent(event.target.checked)}
            />
          </label>

          {submitError && (
            <div className="notice notice-error" role="alert">
              {submitError.map((line) => (
                <p key={line}>{line}</p>
              ))}
            </div>
          )}
          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={!selectedId || submitting}>
              {submitting ? '요청 중…' : '입어 보기'}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
