import type { FitLabel, Region, RegionResult } from '../api/types';
import { signedCm } from '../format';
import { REGION_BANDS, REGION_LABEL, WARNING_LABEL, fitLabelText } from '../labels';

/** FitReport(POST /api/fit)와 작업 상태의 ReportPart가 공통으로 가진 부분 */
export interface ReportLike {
  rules_version: string | null;
  regions: RegionResult[];
  summary: string | null;
  warnings: string[];
}

const judged = (label: FitLabel) => label !== 'UNKNOWN' && label !== 'NOT_EVALUATED';

/** 기준표 구간 중 지금 판정이 어디인지. 판정 배지가 같은 내용을 글로 말하므로 화면 낭독기에서는 숨긴다. */
function FitScale({ region, label }: { region: Region; label: FitLabel }) {
  const bands = REGION_BANDS[region];
  if (!bands) return null;
  return (
    <ol className="fit-scale" aria-hidden="true" style={{ gridTemplateColumns: `repeat(${bands.length}, 1fr)` }}>
      {bands.map((band) => (
        <li key={band} className={band === label ? `on verdict-${band.toLowerCase()}` : undefined}>
          {fitLabelText(region, band)}
        </li>
      ))}
    </ol>
  );
}

function FitRow({ result }: { result: RegionResult }) {
  const { region, label } = result;
  if (!judged(label)) {
    return (
      <li className="fit-row fit-row-missing">
        <span className="fit-part">{REGION_LABEL[region]}</span>
        <span className={`verdict verdict-${label.toLowerCase()}`}>{fitLabelText(region, label)}</span>
        <p className="fit-sentence">{result.explanation}</p>
      </li>
    );
  }
  return (
    <li className="fit-row">
      <span className="fit-part">{REGION_LABEL[region]}</span>
      <span className={`verdict verdict-${label.toLowerCase()}`}>{fitLabelText(region, label)}</span>
      <span className="fit-diff num">
        {result.ease_cm === null ? '—' : `${signedCm(result.ease_cm)} cm`}
        <span className="sr-only"> (옷 − 몸)</span>
      </span>
      <FitScale region={region} label={label} />
      <p className="fit-sentence">
        {result.explanation}
        {result.is_estimated && (
          <span className="badge" title="몸 치수 입력이 없어 추정한 값과 비교했습니다.">
            추정
          </span>
        )}
      </p>
    </li>
  );
}

/** 핏 리포트: 한 줄 요약(서버 summary), 부위별 판정·차이·구간 위치, 읽는 법과 판정 기준. */
export function FitReportView({ report }: { report: ReportLike }) {
  return (
    <section className="card" aria-labelledby="fit-report-title">
      <h2 id="fit-report-title">핏 리포트</h2>
      {report.summary && <p className="fit-summary">{report.summary}</p>}

      <ul className="fit-list">
        {report.regions.map((result) => (
          <FitRow key={result.region} result={result} />
        ))}
      </ul>

      {report.warnings.map((code) => (
        <p key={code} className="note">
          {WARNING_LABEL[code] ?? code}
        </p>
      ))}
      <p className="note">
        차이는 옷 치수에서 몸 치수를 뺀 값입니다. +면 옷이 그만큼 크고, −면 작습니다. 가슴·허리는 둘레끼리, 어깨는 양쪽을
        합한 너비끼리 비교합니다.
      </p>
      <p className="note">판정 기준: {report.rules_version ?? '알 수 없음'} (초기 기준, 실제 착용 대조 후 조정)</p>
    </section>
  );
}
