/** 화면 표시용 cm 값: 절댓값을 소수 첫째 자리로 반올림(ROUND_HALF_UP). 서버 `display_cm`과 같다. */
export const displayCm = (value: number) => (Math.round(Math.abs(value) * 10 + 1e-9) / 10).toFixed(1);

/**
 * 핏 리포트 행의 몸·옷 치수. 예: "몸 100.0 · 옷 110.0 cm". 있는 값만 보여 주고, 둘 다 없으면 null.
 * 차이(ease_cm)가 옷 − 몸인 것을 바로 볼 수 있게 한다.
 */
export function bodyGarmentText(result: { body_cm: number | null; garment_cm: number | null }): string | null {
  const parts = [
    result.body_cm !== null ? `몸 ${displayCm(result.body_cm)}` : null,
    result.garment_cm !== null ? `옷 ${displayCm(result.garment_cm)}` : null,
  ].filter(Boolean);
  return parts.length > 0 ? `${parts.join(' · ')} cm` : null;
}

/** 부호가 있는 cm 값. 예: +8.0, −2.5, 0.0 */
export const signedCm = (value: number) =>
  value > 0 ? `+${displayCm(value)}` : value < 0 ? `−${displayCm(value)}` : '0.0';
