import { createContext, useContext } from 'react';
import type { FitReport, Measurements } from '../api/types';

/** 결과 화면 한 장에 필요한 것. 핏 리포트는 POST /api/fit 응답이다. */
export interface StoredResult {
  report: FitReport;
  garmentId: string;
  garmentName: string;
  /** 3D·실사화 작업 ID. 작업을 만들지 못했으면(B3 전 실제 서버 등) null */
  jobId: string | null;
}

export interface FittingState {
  measurements: Measurements | null;
  setMeasurements: (measurements: Measurements) => void;
  results: Record<string, StoredResult>;
  saveResult: (id: string, result: StoredResult) => void;
}

export const FittingContext = createContext<FittingState | null>(null);

export function useFitting(): FittingState {
  const context = useContext(FittingContext);
  if (!context) throw new Error('useFitting은 FittingProvider 안에서만 쓸 수 있습니다.');
  return context;
}
