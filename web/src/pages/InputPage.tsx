import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  MEASUREMENT_FIELDS,
  MEASUREMENT_LABEL,
  MEASUREMENT_RANGES,
  measurementFieldOf,
  type MeasurementField,
} from '../api/contract';
import type { FieldError, Measurements } from '../api/types';
import { MeasureGuide } from '../components/MeasureGuide';
import { useFitting } from '../state/fittingStore';
import {
  EXAMPLE_MEASUREMENTS,
  validateDraft,
  validateField,
  type BodyDraft,
  type BodyErrors,
} from '../validation/body';

/** 서버가 치수를 거절하면(422 INPUT_INVALID) 티셔츠 선택 화면이 이 상태를 담아 돌려보낸다. */
export interface InputPageState {
  message: string;
  fields: FieldError[];
}

const EMPTY_DRAFT: BodyDraft = { height_cm: '', chest_cm: '', waist_cm: '', shoulder_cm: '', arm_length_cm: '' };

const toDraft = (m: Measurements): BodyDraft => ({
  height_cm: String(m.height_cm),
  chest_cm: String(m.chest_cm),
  waist_cm: String(m.waist_cm),
  shoulder_cm: String(m.shoulder_cm),
  arm_length_cm: String(m.arm_length_cm),
});

/** 서버 오류 fields[]("measurements.chest_cm") → 칸별 오류 */
function serverErrors(fields: FieldError[]): BodyErrors {
  const errors: BodyErrors = {};
  for (const item of fields) {
    const field = measurementFieldOf(item.field);
    if (field && !errors[field]) errors[field] = item.message;
  }
  return errors;
}

export function InputPage() {
  const { measurements, setMeasurements } = useFitting();
  const navigate = useNavigate();
  const location = useLocation();
  const [returned] = useState(() => location.state as InputPageState | null);

  const [draft, setDraft] = useState<BodyDraft>(() => (measurements ? toDraft(measurements) : EMPTY_DRAFT));
  const [errors, setErrors] = useState<BodyErrors>(() => (returned ? serverErrors(returned.fields) : {}));
  // 폼 위 알림의 출처. 남은 오류 수로 문구를 만들고, 오류가 모두 사라지면 알림도 사라진다.
  const [alertSource, setAlertSource] = useState<'submit' | 'server' | null>(returned ? 'server' : null);
  const [openGuide, setOpenGuide] = useState<MeasurementField | null>(null);
  const inputs = useRef<Partial<Record<MeasurementField, HTMLInputElement | null>>>({});

  useEffect(() => {
    if (!returned) return;
    // 서버가 돌려보낸 오류는 이번에만 보여 주고, 새로고침하면 다시 나오지 않게 지운다.
    navigate(location.pathname, { replace: true, state: null });
    const first = MEASUREMENT_FIELDS.find((field) => serverErrors(returned.fields)[field]);
    if (first) inputs.current[first]?.focus();
  }, [returned, navigate, location.pathname]);

  const errorCount = MEASUREMENT_FIELDS.filter((field) => errors[field]).length;
  const alert =
    errorCount === 0 || alertSource === null
      ? null
      : alertSource === 'server' && returned
        ? returned.message
        : `입력값 ${errorCount}개를 확인해 주세요.`;

  const setFieldError = (field: MeasurementField, message: string | null) =>
    setErrors((prev) => ({ ...prev, [field]: message ?? undefined }));

  function onChange(field: MeasurementField, value: string) {
    setDraft((prev) => ({ ...prev, [field]: value }));
    // 이미 오류가 보이는 칸은 고치는 즉시 다시 검사해 오류를 지운다.
    if (errors[field]) setFieldError(field, validateField(field, value));
  }

  function onBlur(field: MeasurementField) {
    // 빈칸은 다음 버튼을 누를 때 알려 준다. 칸을 지나가기만 해도 오류가 뜨지 않게 한다.
    if (draft[field].trim() === '') return;
    setFieldError(field, validateField(field, draft[field]));
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const result = validateDraft(draft);
    if (!result.ok) {
      setErrors(result.errors);
      setAlertSource('submit');
      inputs.current[result.firstInvalid]?.focus();
      return;
    }
    setMeasurements(result.measurements);
    navigate('/garments');
  }

  function fillExample() {
    setDraft(toDraft(EXAMPLE_MEASUREMENTS));
    setErrors({});
    setAlertSource(null);
  }

  return (
    <section>
      <h1>내 치수 입력</h1>
      <p className="lead">줄자로 잰 값을 cm로 넣어 주세요. 얇은 옷을 입고 재면 더 정확합니다.</p>

      <form className="card form" onSubmit={onSubmit} noValidate>
        {alert && (
          <p className="form-alert" role="alert">
            {alert}
          </p>
        )}

        {MEASUREMENT_FIELDS.map((field) => {
          const range = MEASUREMENT_RANGES[field];
          const error = errors[field];
          const guideOpen = openGuide === field;
          const describedBy = [`${field}-range`, error ? `${field}-error` : null].filter(Boolean).join(' ');

          return (
            <div className={`field${error ? ' field-invalid' : ''}`} key={field}>
              <label htmlFor={field}>{MEASUREMENT_LABEL[field]}</label>
              <div className="field-control">
                <div className="input-row">
                  <div className="input-unit">
                    <input
                      ref={(element) => {
                        inputs.current[field] = element;
                      }}
                      id={field}
                      name={field}
                      type="text"
                      inputMode="decimal"
                      autoComplete="off"
                      placeholder={`예: ${EXAMPLE_MEASUREMENTS[field]}`}
                      value={draft[field]}
                      onChange={(event) => onChange(field, event.target.value)}
                      onBlur={() => onBlur(field)}
                      aria-invalid={error ? true : undefined}
                      aria-describedby={describedBy}
                    />
                    <span>cm</span>
                  </div>
                  <span id={`${field}-range`} className="range-hint">
                    {range.min}~{range.max} cm 사이
                  </span>
                  <button
                    type="button"
                    className="guide-toggle"
                    aria-label={`${MEASUREMENT_LABEL[field]} 재는 법`}
                    aria-expanded={guideOpen}
                    aria-controls={`${field}-guide`}
                    onClick={() => setOpenGuide(guideOpen ? null : field)}
                  >
                    <span className="guide-icon" aria-hidden="true">
                      ?
                    </span>
                    재는 법
                  </button>
                </div>
                {error && (
                  <p id={`${field}-error`} className="field-error">
                    {error}
                  </p>
                )}
                <MeasureGuide id={`${field}-guide`} field={field} hidden={!guideOpen} />
              </div>
            </div>
          );
        })}

        <div className="actions actions-split">
          <button type="button" onClick={fillExample}>
            예시 치수 넣기
          </button>
          <button type="submit" className="primary">
            다음: 티셔츠 고르기
          </button>
        </div>
      </form>
      <p className="note">입력 범위와 재는 위치는 팀 계약(measure_v1)을 따릅니다.</p>
    </section>
  );
}
