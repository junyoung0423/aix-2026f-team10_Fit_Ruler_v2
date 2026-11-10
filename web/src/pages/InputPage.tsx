import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import type { BodyMeasurements } from '../api/types';
import { BODY_LABEL } from '../labels';
import { useFitting } from '../state/FittingContext';

// A1 뼈대: 숫자만 받아 다음 화면으로 넘긴다.
// 범위 검사, 칸별 오류 문구, 재는 법 안내는 A2에서 붙인다.

const FIELDS = Object.keys(BODY_LABEL) as (keyof BodyMeasurements)[];

const SAMPLE_BODY: BodyMeasurements = {
  heightCm: 175,
  chestCircumferenceCm: 96,
  waistCircumferenceCm: 82,
  shoulderWidthCm: 45,
  armLengthCm: 60,
};

type Draft = Record<keyof BodyMeasurements, string>;

const toDraft = (body: BodyMeasurements): Draft => ({
  heightCm: String(body.heightCm),
  chestCircumferenceCm: String(body.chestCircumferenceCm),
  waistCircumferenceCm: String(body.waistCircumferenceCm),
  shoulderWidthCm: String(body.shoulderWidthCm),
  armLengthCm: String(body.armLengthCm),
});

const toBody = (draft: Draft): BodyMeasurements => ({
  heightCm: Number(draft.heightCm),
  chestCircumferenceCm: Number(draft.chestCircumferenceCm),
  waistCircumferenceCm: Number(draft.waistCircumferenceCm),
  shoulderWidthCm: Number(draft.shoulderWidthCm),
  armLengthCm: Number(draft.armLengthCm),
});

export function InputPage() {
  const { body, setBody } = useFitting();
  const navigate = useNavigate();
  const [draft, setDraft] = useState<Draft>(() => toDraft(body ?? SAMPLE_BODY));

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBody(toBody(draft));
    navigate('/garments');
  }

  return (
    <section>
      <h1>내 치수 입력</h1>
      <p className="lead">줄자로 잰 값을 cm로 넣어 주세요.</p>
      <form className="card form" onSubmit={onSubmit}>
        {FIELDS.map((field) => (
          <div className="field" key={field}>
            <label htmlFor={field}>{BODY_LABEL[field]}</label>
            <div className="input-unit">
              <input
                id={field}
                name={field}
                type="number"
                inputMode="decimal"
                step="0.1"
                min="1"
                required
                value={draft[field]}
                onChange={(event) => setDraft((prev) => ({ ...prev, [field]: event.target.value }))}
              />
              <span>cm</span>
            </div>
          </div>
        ))}
        <div className="actions">
          <button type="submit" className="primary">
            다음: 티셔츠 고르기
          </button>
        </div>
      </form>
    </section>
  );
}
