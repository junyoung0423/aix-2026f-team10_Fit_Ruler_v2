import type { RegionResult } from '../api/types';
import { signedCm } from '../format';
import { REGION_LABEL, WARNING_LABEL, fitLabelText } from '../labels';

/** FitReport(POST /api/fit)와 작업 상태의 ReportPart가 공통으로 가진 부분 */
export interface ReportLike {
  rules_version: string | null;
  regions: RegionResult[];
  summary: string | null;
  warnings: string[];
}

/** 핏 리포트 표. A1에서는 기본 표만 두고, 읽기 쉬운 배치는 A2에서 다듬는다. */
export function FitReportView({ report }: { report: ReportLike }) {
  return (
    <section className="card" aria-labelledby="fit-report-title">
      <h2 id="fit-report-title">핏 리포트</h2>
      {report.summary && <p className="fit-summary">{report.summary}</p>}
      <table className="fit-table">
        <thead>
          <tr>
            <th scope="col">부위</th>
            <th scope="col">판정</th>
            <th scope="col">차이</th>
            <th scope="col">설명</th>
          </tr>
        </thead>
        <tbody>
          {report.regions.map((result) => (
            <tr key={result.region}>
              <th scope="row">{REGION_LABEL[result.region]}</th>
              <td>
                <span className={`verdict verdict-${result.label.toLowerCase()}`}>
                  {fitLabelText(result.region, result.label)}
                </span>
              </td>
              <td className="num">{result.ease_cm === null ? '—' : `${signedCm(result.ease_cm)} cm`}</td>
              <td>
                {result.explanation}
                {result.is_estimated && <span className="badge">추정</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {report.warnings.map((code) => (
        <p key={code} className="note">
          {WARNING_LABEL[code] ?? code}
        </p>
      ))}
      <p className="note">판정 기준: {report.rules_version ?? '알 수 없음'} (초기 기준, 실제 착용 대조 후 조정)</p>
    </section>
  );
}
