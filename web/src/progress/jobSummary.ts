import { ERROR_LABEL } from '../labels';

/** 진행 표시에 쓰는 문구. 서버는 상태 코드만 주므로 화면이 문장을 고른다. */

/** 오류 코드 → 안내 문구. 화면 표에 없는 코드는 코드를 그대로 쓰지 않고 "알 수 없는 오류(코드)"로 보여 준다. */
export function errorText(code: string): string {
  return (ERROR_LABEL as Record<string, string | undefined>)[code] ?? `알 수 없는 오류(${code})`;
}

/** 경과 시간. 예: 0:42, 12:05, 1:02:03 */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = String(total % 60).padStart(2, '0');
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, '0')}:${seconds}` : `${minutes}:${seconds}`;
}
