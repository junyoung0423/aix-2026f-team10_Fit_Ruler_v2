import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { MEASUREMENT_FIELDS, MEASUREMENT_LABEL, MEASUREMENT_RANGES, type MeasurementField } from '../api/contract';
import type { Measurements } from '../api/types';
import { useFitting } from '../state/fittingStore';

// A1 뼈대: 숫자만 받아 다음 화면으로 넘긴다.
// 범위 검사, 칸별 오류 문구, 재는 법 안내는 A2에서 붙인다.

/** 팀 예시 docs/api/examples/fit_request.json 의 치수 */
const SAMPLE: Measurements = { height_cm: 175, chest_cm: 100, waist_cm: 80, shoulder_cm: 44, arm_length_cm: 60 };

type Draft = Record<MeasurementField, string>;

const toDraft = (m: Measurements): Draft => ({
  height_cm: String(m.height_cm),
  chest_cm: String(m.chest_cm),
  waist_cm: String(m.waist_cm),
  shoulder_cm: String(m.shoulder_cm),
  arm_length_cm: String(m.arm_length_cm),
});

const toMeasurements = (d: Draft): Measurements => ({
  height_cm: Number(d.height_cm),
  chest_cm: Number(d.chest_cm),
  waist_cm: Number(d.waist_cm),
  shoulder_cm: Number(d.shoulder_cm),
  arm_length_cm: Number(d.arm_length_cm),
});

export function InputPage() {
  const { measurements, setMeasurements } = useFitting();
  const navigate = useNavigate();
  const [draft, setDraft] = useState<Draft>(() => toDraft(measurements ?? SAMPLE));

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMeasurements(toMeasurements(draft));
    navigate('/garments');
  }

  return (
    <section>
      <h1>내 치수 입력</h1>
      <p className="lead">줄자로 잰 값을 cm로 넣어 주세요.</p>
      <form className="card form" onSubmit={onSubmit}>
        {MEASUREMENT_FIELDS.map((field) => (
          <div className="field" key={field}>
            <label htmlFor={field}>{MEASUREMENT_LABEL[field]}</label>
            <div className="input-unit">
              <input
                id={field}
                name={field}
                type="number"
                inputMode="decimal"
                step="0.1"
                min={MEASUREMENT_RANGES[field].min}
                max={MEASUREMENT_RANGES[field].max}
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
