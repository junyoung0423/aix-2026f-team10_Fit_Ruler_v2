import type { Measurements, RegionResult } from '../api/types';
import type { BodyInput, GarmentShape } from './bodyModel';

/**
 * 3D 장면에 넘길 몸·옷 치수. three.js를 불러오지 않는 작은 모듈이라 화면 쪽에서 바로 쓴다.
 * - 몸: 입력 화면에서 넣은 치수(세션에 남아 있다). 새 탭 등으로 없으면 핏 리포트의 몸 치수, 그래도 없으면 3D 기본값.
 * - 옷: 핏 리포트의 옷 치수(가슴둘레·어깨너비·소매·총장). 없는 값은 몸보다 조금 크게 짐작한다.
 */

const bodyCm = (regions: readonly RegionResult[] | null | undefined, region: RegionResult['region']) =>
  regions?.find((r) => r.region === region)?.body_cm ?? undefined;

const garmentCm = (regions: readonly RegionResult[] | null | undefined, region: RegionResult['region']) =>
  regions?.find((r) => r.region === region)?.garment_cm ?? undefined;

export function bodyForScene(measurements: Measurements | null, regions: readonly RegionResult[] | null | undefined): Partial<BodyInput> {
  if (measurements) return { ...measurements };
  return {
    chest_cm: bodyCm(regions, 'chest'),
    waist_cm: bodyCm(regions, 'waist'),
    shoulder_cm: bodyCm(regions, 'shoulder'),
  };
}

export function garmentForScene(body: Partial<BodyInput>, regions: readonly RegionResult[] | null | undefined): GarmentShape {
  const chest = garmentCm(regions, 'chest') ?? (body.chest_cm ?? 100) + 10;
  const shoulder = garmentCm(regions, 'shoulder') ?? (body.shoulder_cm ?? 44) + 2;
  const sleeve = garmentCm(regions, 'sleeve') ?? 21;
  const length = garmentCm(regions, 'body_length') ?? 70;
  return { chestGirth: chest / 100, shoulderWidth: shoulder / 100, sleeveLength: sleeve / 100, bodyLength: length / 100 };
}
