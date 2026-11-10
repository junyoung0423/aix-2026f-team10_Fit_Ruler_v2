/** 화면 표시용 cm 값: 절댓값을 소수 첫째 자리로 반올림(ROUND_HALF_UP). 서버 `display_cm`과 같다. */
export const displayCm = (value: number) => (Math.round(Math.abs(value) * 10 + 1e-9) / 10).toFixed(1);

/** 부호가 있는 cm 값. 예: +8.0, −2.5, 0.0 */
export const signedCm = (value: number) =>
  value > 0 ? `+${displayCm(value)}` : value < 0 ? `−${displayCm(value)}` : '0.0';
