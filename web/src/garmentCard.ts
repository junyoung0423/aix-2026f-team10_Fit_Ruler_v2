import type { GarmentListItem, GarmentMeasurements } from './api/types';

/** 티셔츠 선택 카드에 보이는 값. 화면(GarmentSelectPage)과 테스트가 같이 쓴다. */

export interface SizeItem {
  key: keyof GarmentMeasurements;
  label: string;
  /** 예: "108", 값이 없으면 "—" */
  text: string;
  missing: boolean;
}

/** 카드 치수 줄의 순서. 허리는 의류마다 측정 위치가 달라 카드에 보이지 않는다(핏 리포트에서 다룬다). */
const SIZE_FIELDS: readonly { key: keyof GarmentMeasurements; label: string }[] = [
  { key: 'chest_girth_cm', label: '가슴둘레' },
  { key: 'shoulder_width_cm', label: '어깨' },
  { key: 'sleeve_length_cm', label: '소매' },
  { key: 'body_length_cm', label: '총장' },
];

/** 가슴둘레 · 어깨 · 소매 · 총장. 값이 없는 치수도 빼지 않고 "—"로 보여 준다. */
export function sizeItems(measurements: GarmentMeasurements): SizeItem[] {
  return SIZE_FIELDS.map(({ key, label }) => {
    const value = measurements[key];
    return { key, label, text: value === null ? '—' : String(value), missing: value === null };
  });
}

/** 예: "가슴둘레 108 · 어깨 46 · 소매 22 · 총장 —" (단위 cm) */
export const sizeText = (measurements: GarmentMeasurements) =>
  sizeItems(measurements)
    .map((item) => `${item.label} ${item.text}`)
    .join(' · ');

export type SourceBadge = { kind: 'demo'; text: string } | { kind: 'measured'; text: string; source: string };

/** 시연용 의류면 "시연용 의류", 아니고 실측이면 "실측"(출처 문자열 포함). 둘 다 아니면 null */
export function sourceBadge(item: GarmentListItem): SourceBadge | null {
  if (item.is_demo) return { kind: 'demo', text: '시연용 의류' };
  const meta = item.garment.measurement_meta;
  if (meta.is_measured) return { kind: 'measured', text: '실측', source: meta.source };
  return null;
}

/** 시연용 의류 먼저, 그다음 가슴둘레 오름차순(없으면 뒤), 같으면 ID 순. 원래 배열은 바꾸지 않는다. */
export function sortGarments(items: readonly GarmentListItem[]): GarmentListItem[] {
  const chest = (item: GarmentListItem) => item.garment.measurements.chest_girth_cm ?? Number.POSITIVE_INFINITY;
  return [...items].sort(
    (a, b) =>
      Number(b.is_demo) - Number(a.is_demo) ||
      chest(a) - chest(b) ||
      (a.garment.id < b.garment.id ? -1 : a.garment.id > b.garment.id ? 1 : 0),
  );
}
