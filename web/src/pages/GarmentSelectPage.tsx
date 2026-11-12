import { useEffect, useState, type FormEvent } from 'react';
import { Link, Navigate, useNavigate } from 'react-router-dom';
import { ApiError, errorMessage, isFittingApiMissing } from '../api/api';
import { api } from '../api/client';
import { MEASUREMENT_FIELDS, MEASUREMENT_LABEL, measurementFieldOf } from '../api/contract';
import type { FitRequest, FittingStatus, GarmentListItem, Measurements } from '../api/types';
import { MEASUREMENT_PROTOCOL_VERSION } from '../api/types';
import { sizeItems, sortGarments, sourceBadge } from '../garmentCard';
import { fabricText, garmentName, swatchColor } from '../labels';
import { useFitting } from '../state/fittingStore';
import type { InputPageState } from './InputPage';

const summary = (m: Measurements) =>
  MEASUREMENT_FIELDS.map((field) => `${MEASUREMENT_LABEL[field]} ${m[field]}`).join(' · ') + ' cm';

function GarmentCardBody({ item }: { item: GarmentListItem }) {
  const { garment } = item;
  const [chest, ...rest] = sizeItems(garment.measurements);
  const badge = sourceBadge(item);
  const fabric = fabricText(garment.fabric);
  return (
    <>
      <span className="swatch" style={{ background: swatchColor(garment) }} aria-hidden="true" />
      <span className="garment-name">{garmentName(garment)}</span>
      <span className="garment-chest num">
        {chest.label} {chest.text} cm
      </span>
      <span className="garment-size">
        {rest.map((size) => `${size.label} ${size.text}`).join(' · ')} cm
      </span>
      {fabric && <span className="garment-meta">{fabric}</span>}
      {badge?.kind === 'demo' && <span className="badge demo-badge">{badge.text}</span>}
      {badge?.kind === 'measured' && (
        <>
          <span className="badge demo-badge measured-badge" title={badge.source}>
            {badge.text}
          </span>
          <span className="garment-source">{badge.source}</span>
        </>
      )}
    </>
  );
}

export function GarmentSelectPage() {
  const { measurements, saveResult } = useFitting();
  const navigate = useNavigate();
  const [garments, setGarments] = useState<GarmentListItem[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
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
      saveResult(id, { report, garmentId: item.garment.id, garmentName: garmentName(item.garment), jobId: job?.job_id ?? null });
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
    <section>
      <h1>티셔츠 고르기</h1>
      <p className="lead">
        내 치수: {summary(current)} <Link to="/">수정</Link>
      </p>

      {loadError && (
        <p className="error" role="alert">
          {loadError}
        </p>
      )}
      {!garments && !loadError && <p aria-busy="true">티셔츠 목록을 불러오는 중…</p>}
      {garments && garments.length === 0 && <p>지금 고를 수 있는 티셔츠가 없습니다.</p>}

      {garments && garments.length > 0 && (
        <form onSubmit={onSubmit}>
          <fieldset className="garment-grid">
            <legend className="sr-only">티셔츠</legend>
            {garments.map((item) => {
              const { garment } = item;
              return (
                <label key={garment.id} className={`garment-card${selectedId === garment.id ? ' selected' : ''}`}>
                  <input
                    type="radio"
                    name="garment"
                    value={garment.id}
                    checked={selectedId === garment.id}
                    onChange={() => setSelectedId(garment.id)}
                  />
                  <GarmentCardBody item={item} />
                </label>
              );
            })}
          </fieldset>

          <label className="consent">
            <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
            <span>
              <strong>실사 착장 영상도 만들기</strong>
              <br />
              신체 치수가 반영된 렌더 클립이 외부 서버(fal)로 전송되어 처리됩니다. 체크하지 않으면 핏 리포트와 3D
              미리보기까지만 만듭니다.
            </span>
          </label>

          {submitError && (
            <div className="error" role="alert">
              {submitError.map((line) => (
                <p key={line}>{line}</p>
              ))}
            </div>
          )}
          <div className="actions">
            <button type="submit" className="primary" disabled={!selectedId || submitting}>
              {submitting ? '요청 중…' : '핏 보기'}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
