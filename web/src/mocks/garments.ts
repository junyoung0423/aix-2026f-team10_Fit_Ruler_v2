import type { GarmentListItem, GarmentVariant } from '../api/types';

/**
 * 가짜 API의 등록 의류. 첫 벌은 팀 저장소 `data/garments/tee_regular_m_navy_v1.json`(B2)과 같고,
 * 나머지 세 벌은 화면 개발용으로 만든 시연용 의류다(실제 의류가 아니다).
 */
function demoGarment(
  id: string,
  style: string,
  size: string,
  color: string,
  measurements: GarmentVariant['measurements'],
  waistPositionMatched: boolean,
): GarmentVariant {
  return {
    schema_version: 'v1',
    id,
    style,
    size,
    color,
    fabric_preset: 'cotton_jersey_v0',
    garment_family: 'basic_short_sleeve_crew',
    status: 'READY',
    measurements,
    measurement_meta: {
      measurement_protocol_version: 'measure_v1',
      source: '화면 개발용 가짜 의류(A1 mock). 실제 의류를 실측한 값이 아니다.',
      is_measured: false,
      chest_from_flat_width: false,
      waist_from_flat_width: false,
      waist_position_matched: waistPositionMatched,
      measured_by: null,
      measured_on: null,
      notes: 'SAMPLE ONLY',
    },
    asset_version: 'sample_v1',
    source_ref: 'SAMPLE_NOT_A_REAL_GARMENT',
  };
}

export const MOCK_GARMENTS: readonly GarmentVariant[] = [
  {
    // data/garments/tee_regular_m_navy_v1.json 과 같은 내용
    schema_version: 'v1',
    id: 'tee_regular_m_navy_v1',
    style: 'regular',
    size: 'M',
    color: 'navy',
    fabric_preset: 'cotton_jersey_v0',
    garment_family: 'basic_short_sleeve_crew',
    status: 'READY',
    measurements: {
      chest_girth_cm: 108.0,
      waist_girth_cm: 96.0,
      shoulder_width_cm: 46.0,
      sleeve_length_cm: 22.0,
      body_length_cm: 70.0,
    },
    measurement_meta: {
      measurement_protocol_version: 'measure_v1',
      source: '명세서 4장 리포트 예시 값을 옮긴 시연용 샘플. 실제 의류를 실측한 값이 아니다.',
      is_measured: false,
      chest_from_flat_width: false,
      waist_from_flat_width: false,
      waist_position_matched: true,
      measured_by: null,
      measured_on: null,
      notes: 'SAMPLE ONLY: 개발용 고정 샘플이며 실제 SKU와 대조되지 않았다.',
    },
    asset_version: 'sample_v1',
    source_ref: 'SAMPLE_NOT_A_REAL_GARMENT',
  },
  demoGarment(
    'mock_tee_relaxed_l_black',
    'relaxed',
    'L',
    'black',
    { chest_girth_cm: 118.0, waist_girth_cm: 112.0, shoulder_width_cm: 52.0, sleeve_length_cm: 24.0, body_length_cm: 74.0 },
    true,
  ),
  demoGarment(
    'mock_tee_slim_s_gray',
    'slim',
    'S',
    'gray',
    { chest_girth_cm: 96.0, waist_girth_cm: 90.0, shoulder_width_cm: 42.0, sleeve_length_cm: 19.0, body_length_cm: 66.0 },
    false,
  ),
  demoGarment(
    'mock_tee_regular_l_white',
    'regular',
    'L',
    'white',
    { chest_girth_cm: 112.0, waist_girth_cm: null, shoulder_width_cm: 48.0, sleeve_length_cm: 22.0, body_length_cm: 72.0 },
    false,
  ),
];

export const mockGarmentList = (): GarmentListItem[] =>
  MOCK_GARMENTS.filter((garment) => garment.status === 'READY').map((garment) => ({
    garment: structuredClone(garment),
    is_demo: !garment.measurement_meta.is_measured,
  }));
