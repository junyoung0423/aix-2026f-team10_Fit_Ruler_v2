import type { Garment } from '../api/types';

/**
 * 화면 개발용 예시 티셔츠. 실제 데이터가 아니다.
 * 등록 의류(#3)가 준비되면 서버에서 받은 목록으로 바뀐다.
 */
export const SAMPLE_GARMENTS: Garment[] = [
  {
    id: 'sample-basic-white',
    name: '베이직 반팔 티',
    colorName: '흰색',
    colorHex: '#f4f4f2',
    fabric: '면 100%',
    fitLabel: '정핏',
    measurements: { chestHalfCm: 52, lengthCm: 70, shoulderWidthCm: 46, sleeveLengthCm: 20, hemHalfCm: 50 },
  },
  {
    id: 'sample-over-black',
    name: '오버핏 반팔 티',
    colorName: '검정',
    colorHex: '#26262a',
    fabric: '면 혼방',
    fitLabel: '오버핏',
    measurements: { chestHalfCm: 58, lengthCm: 74, shoulderWidthCm: 52, sleeveLengthCm: 23, hemHalfCm: 50 },
  },
  {
    id: 'sample-slim-gray',
    name: '슬림 반팔 티',
    colorName: '회색',
    colorHex: '#9a9ca1',
    fabric: '면 스판',
    fitLabel: '슬림',
    measurements: { chestHalfCm: 48, lengthCm: 66, shoulderWidthCm: 43, sleeveLengthCm: 19, hemHalfCm: 46 },
  },
  {
    id: 'sample-semi-navy',
    name: '세미오버 반팔 티',
    colorName: '남색',
    colorHex: '#2d3a5c',
    fabric: '면 100%',
    fitLabel: '세미오버',
    // 밑단단면이 없는 경우를 보여 주는 예시
    measurements: { chestHalfCm: 55, lengthCm: 72, shoulderWidthCm: 49, sleeveLengthCm: 21 },
  },
];
