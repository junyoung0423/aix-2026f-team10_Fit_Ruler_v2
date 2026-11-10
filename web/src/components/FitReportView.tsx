import type { FitReport } from '../api/types';
import { PART_LABEL, VERDICT_LABEL } from '../labels';

const signed = (value: number) => (value > 0 ? `+${value}` : `${value}`);

/** 핏 리포트 표. A1에서는 기본 표만 두고, 읽기 쉬운 배치는 A2에서 다듬는다. */
export function FitReportView({ report }: { report: FitReport }) {
  const temporary = report.criteriaVersion.startsWith('temp');

  return (
    <section className="card" aria-labelledby="fit-report-title">
      <h2 id="fit-report-title">핏 리포트</h2>
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
          {report.parts.map((part) => (
            <tr key={part.part}>
              <th scope="row">{PART_LABEL[part.part]}</th>
              <td>
                <span className={`verdict verdict-${part.verdict}`}>{VERDICT_LABEL[part.verdict]}</span>
              </td>
              <td className="num">{signed(part.easeCm)} cm</td>
              <td>
                {part.sentence}
                {part.estimated && <span className="badge">추정</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="note">판정 기준: {temporary ? '임시 기준표 (팀 기준표가 정해지면 바뀝니다)' : report.criteriaVersion}</p>
    </section>
  );
}
