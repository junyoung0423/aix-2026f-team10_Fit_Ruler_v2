import { useMemo, useState, type ReactNode } from 'react';
import type { Measurements } from '../api/types';
import { FittingContext, type FittingState, type StoredResult } from './fittingStore';

const BODY_KEY = 'fit-ruler.measurements';
const RESULTS_KEY = 'fit-ruler.results';

function load<T>(key: string, fallback: T): T {
  try {
    const raw = window.sessionStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function save(key: string, value: unknown): void {
  try {
    window.sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    // 저장소를 쓸 수 없으면 새로고침 때 값이 사라질 뿐이다.
  }
}

/** 화면 사이에 신체 치수와 결과를 넘긴다. 새로고침해도 남도록 sessionStorage에도 둔다. */
export function FittingProvider({ children }: { children: ReactNode }) {
  const [measurements, setMeasurementsState] = useState<Measurements | null>(() => load(BODY_KEY, null));
  const [results, setResults] = useState<Record<string, StoredResult>>(() => load(RESULTS_KEY, {}));

  const value = useMemo<FittingState>(
    () => ({
      measurements,
      setMeasurements: (next) => {
        setMeasurementsState(next);
        save(BODY_KEY, next);
      },
      results,
      saveResult: (id, result) => {
        setResults((prev) => {
          const next = { ...prev, [id]: result };
          save(RESULTS_KEY, next);
          return next;
        });
      },
    }),
    [measurements, results],
  );

  return <FittingContext.Provider value={value}>{children}</FittingContext.Provider>;
}
