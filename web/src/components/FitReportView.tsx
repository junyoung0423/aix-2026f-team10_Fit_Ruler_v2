import type { FitLabel, Region, RegionResult } from '../api/types';
import { bodyGarmentText, signedCm } from '../format';
import { REGION_BANDS, REGION_LABEL, WARNING_LABEL, fitLabelText } from '../labels';

/** FitReport(POST /api/fit)와 작업 상태의 ReportPart가 공통으로 가진 부분 */
export interface ReportLike {
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
        <li key={band} className={band === label ? 'is-on' : undefined}>
          {fitLabelText(region, band)}
        </li>
      ))}
    </ol>
  );
}

function FitRow({ result }: { result: RegionResult }) {
  const { region, label } = result;
  const values = bodyGarmentText(result);
  if (!judged(label)) {
    // 판정하지 않은 부위(소매·총장 등): 있는 값만 보여 주고 서버 설명 문장을 그대로 쓴다.
    return (
      <li className="fit-row is-missing">
        <div className="fit-row-head">
          <span className="fit-part">{REGION_LABEL[region]}</span>
          <span className={`verdict verdict-${label.toLowerCase()}`}>{fitLabelText(region, label)}</span>
          {values && <span className="fit-diff is-muted num">{values}</span>}
        </div>
        <p className="fit-sentence">{result.explanation}</p>
      </li>
    );
  }
  return (
    <li className="fit-row">
      <div className="fit-row-head">
        <span className="fit-part">{REGION_LABEL[region]}</span>
        <span className={`verdict verdict-${label.toLowerCase()}`}>{fitLabelText(region, label)}</span>
        <span className="fit-diff num">
          {result.ease_cm === null ? '—' : `${signedCm(result.ease_cm)} cm`}
          <span className="sr-only"> (옷 − 몸)</span>
        </span>
      </div>
      <FitScale region={region} label={label} />
      {values && <p className="fit-values num">{values}</p>}
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

/**
 * 핏 리포트: 한 줄 요약(서버 summary), 경고(시연용 의류·허리 미판정 등), 부위별 판정·차이·구간 위치·설명,
 * 판정하지 않은 부위(소매·총장)와 그 이유, 차이 읽는 법. 3D·실사를 기다리지 않고 바로 보인다.
 */
export function FitReportView({ report }: { report: ReportLike }) {
  return (
    <section className="report" aria-labelledby="fit-report-title">
      <div className="report-head">
        <h2 id="fit-report-title">핏 리포트</h2>
        <span className="report-tag">치수로 바로 계산</span>
      </div>
      {report.summary && <p className="report-summary">{report.summary}</p>}

      {report.warnings.length > 0 && (
        <ul className="report-warnings">
          {report.warnings.map((code) => (
            <li key={code} className="notice notice-warn">
              {WARNING_LABEL[code] ?? code}
            </li>
          ))}
        </ul>
      )}

      <ul className="fit-list">
        {report.regions.map((result) => (
          <FitRow key={result.region} result={result} />
        ))}
      </ul>

      <div className="report-foot">
        <p>
          차이는 옷 치수에서 몸 치수를 뺀 값이에요. +면 옷이 그만큼 크고, −면 작아요. 가슴·허리는 둘레끼리, 어깨는 양쪽을
          합한 너비끼리 비교해요.
        </p>
      </div>
    </section>
  );
}
