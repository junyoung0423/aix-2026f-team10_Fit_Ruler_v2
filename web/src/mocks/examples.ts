/**
 * 팀 계약 예시(`docs/api/examples`, B1·B2가 실제 계산기·가짜 어댑터로 만든 파일).
 * 테스트가 가짜 API를 이 예시와 비교해 계약과 어긋나지 않았는지 확인한다. 화면 코드는 쓰지 않는다.
 */
import fitReport from '../../../docs/api/examples/fit_report.json';
import fitRequest from '../../../docs/api/examples/fit_request.json';
import fittingRequest from '../../../docs/api/examples/fitting_request.json';
import noConsent from '../../../docs/api/examples/fitting_status_no_consent.json';
import realizeFailed from '../../../docs/api/examples/fitting_status_realize_failed.json';
import succeeded from '../../../docs/api/examples/fitting_status_succeeded.json';
import garment from '../../../docs/api/examples/garment.json';
import garmentList from '../../../docs/api/examples/garment_list.json';

const FILES: Record<string, unknown> = {
  'fit_report.json': fitReport,
  'fit_request.json': fitRequest,
  'fitting_request.json': fittingRequest,
  'fitting_status_no_consent.json': noConsent,
  'fitting_status_realize_failed.json': realizeFailed,
  'fitting_status_succeeded.json': succeeded,
  'garment.json': garment,
  'garment_list.json': garmentList,
};

export function example<T>(name: string): T {
  const value = FILES[name];
  if (value === undefined) throw new Error(`계약 예시가 없습니다: ${name}`);
  return structuredClone(value) as T;
}
