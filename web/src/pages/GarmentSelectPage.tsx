import { useEffect, useState, type FormEvent } from 'react';
import { Link, Navigate, useNavigate } from 'react-router-dom';
import { errorMessage } from '../api/api';
import { api } from '../api/client';
import type { BodyMeasurements, Garment } from '../api/types';
import { useFitting } from '../state/FittingContext';

const summary = (body: BodyMeasurements) =>
  `키 ${body.heightCm} · 가슴 ${body.chestCircumferenceCm} · 허리 ${body.waistCircumferenceCm} · ` +
  `어깨 ${body.shoulderWidthCm} · 팔 ${body.armLengthCm} cm`;

export function GarmentSelectPage() {
  const { body } = useFitting();
  const navigate = useNavigate();
  const [garments, setGarments] = useState<Garment[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [consent, setConsent] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .listGarments()
      .then((list) => alive && setGarments(list))
      .catch((error: unknown) => alive && setLoadError(errorMessage(error)));
    return () => {
      alive = false;
    };
  }, []);

  if (!body) return <Navigate to="/" replace />;
  const currentBody = body;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedId) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const job = await api.createFitting({ body: currentBody, garmentId: selectedId, realizeConsent: consent });
      navigate(`/fittings/${job.jobId}`);
    } catch (error) {
      setSubmitError(errorMessage(error));
      setSubmitting(false);
    }
  }

  return (
    <section>
      <h1>티셔츠 고르기</h1>
      <p className="lead">
        내 치수: {summary(currentBody)} <Link to="/">수정</Link>
      </p>

      {loadError && (
        <p className="error" role="alert">
          {loadError}
        </p>
      )}
      {!garments && !loadError && <p aria-busy="true">티셔츠 목록을 불러오는 중…</p>}

      {garments && (
        <form onSubmit={onSubmit}>
          <fieldset className="garment-grid">
            <legend className="sr-only">티셔츠</legend>
            {garments.map((garment) => {
              const m = garment.measurements;
              return (
                <label key={garment.id} className={`garment-card${selectedId === garment.id ? ' selected' : ''}`}>
                  <input
                    type="radio"
                    name="garment"
                    value={garment.id}
                    checked={selectedId === garment.id}
                    onChange={() => setSelectedId(garment.id)}
                  />
                  <span className="swatch" style={{ background: garment.colorHex }} aria-hidden="true" />
                  <span className="garment-name">{garment.name}</span>
                  <span className="garment-meta">
                    {[garment.fitLabel, garment.colorName, garment.fabric].filter(Boolean).join(' · ')}
                  </span>
                  <span className="garment-size">
                    가슴단면 {m.chestHalfCm} · 총장 {m.lengthCm} · 어깨 {m.shoulderWidthCm} cm
                  </span>
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
            <p className="error" role="alert">
              {submitError}
            </p>
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
