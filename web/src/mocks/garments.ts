import type { GarmentListItem, GarmentVariant } from '../api/types';

/**
 * 가짜 API의 등록 의류. 첫 벌은 팀 저장소 `data/garments/tee_regular_m_navy_v1.json`(B2)과 같고,
 * 그다음 세 벌은 화면 개발용으로 만든 시연용 의류다(실제 의류가 아니다). 마지막 한 벌은 AI Hub 변환 의류
 * 모양의 합성 의류다(아래 `MOCK_MEASURED_GARMENT`).
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

/**
 * AI Hub 변환 의류(C1 `tools.register_aihub_garment`)와 **모양만** 같은 합성 의류.
 * 실제 서버의 AI Hub 의류처럼 핏형·사이즈가 `unknown`이고, 실측(`is_measured: true`)이라 시연용이 아니며,
 * 총장이 없고(null), 허리 측정 위치가 확인되지 않았고(`waist_position_matched: false`), 원단·`color_hex`가 있다.
 * 치수·ID·문구는 지어낸 값이다. AI Hub 원본·변환 값을 옮기지 않았다(docs/decisions.md "AI Hub 변환 의류 공개").
 */
export const MOCK_MEASURED_GARMENT: GarmentVariant = {
  schema_version: 'v1',
  id: 'mock_tee_unknown_unknown_red_measured',
  style: 'unknown',
  size: 'unknown',
  color: 'red',
  color_hex: '#c0392b',
  fabric_preset: 'cotton_jersey_v0',
  fabric: {
    fibers: ['cotton', 'polyurethane'],
    composition: null,
    stretch: 'contain little',
    description_en: 'plain red cotton jersey',
  },
  garment_family: 'basic_short_sleeve_crew',
  status: 'READY',
  measurements: {
    chest_girth_cm: 103.5,
    waist_girth_cm: 99.0,
    shoulder_width_cm: 45.5,
    sleeve_length_cm: 20.5,
    body_length_cm: null,
  },
  measurement_meta: {
    measurement_protocol_version: 'measure_v1',
    source: '화면 개발용 합성 의류(A3 mock). 실측 의류의 모양만 흉내 낸 지어낸 값이다.',
    is_measured: true,
    chest_from_flat_width: false,
    waist_from_flat_width: false,
    waist_position_matched: false,
    measured_by: null,
    measured_on: null,
    notes: 'SYNTHETIC: 실제 의류가 아니다.',
  },
  asset_version: 'mock_v1',
  source_ref: 'SYNTHETIC_NOT_A_REAL_GARMENT',
};

export const MOCK_GARMENTS: readonly GarmentVariant[] = [
  {
    // data/garments/tee_regular_m_navy_v1.json 과 같은 내용
    schema_version: 'v1',
    id: 'tee_regular_m_navy_v1',
    style: 'regular',
    size: 'M',
    color: 'navy',
    color_hex: '#24324f',
    fabric_preset: 'cotton_jersey_v0',
    fabric: {
      fibers: ['cotton'],
      composition: { cotton: 100.0 },
      stretch: 'contain little',
      description_en: 'plain navy cotton jersey',
    },
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
  MOCK_MEASURED_GARMENT,
];

export const mockGarmentList = (): GarmentListItem[] =>
  MOCK_GARMENTS.filter((garment) => garment.status === 'READY').map((garment) => ({
    garment: structuredClone(garment),
    is_demo: !garment.measurement_meta.is_measured,
  }));
