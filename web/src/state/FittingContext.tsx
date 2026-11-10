import { createContext, useContext, useMemo, useState, type ReactNode } from 'react';
import type { BodyMeasurements } from '../api/types';

const STORAGE_KEY = 'fit-ruler.body';

interface FittingState {
  body: BodyMeasurements | null;
  setBody: (body: BodyMeasurements) => void;
}

const FittingContext = createContext<FittingState | null>(null);

function loadBody(): BodyMeasurements | null {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as BodyMeasurements) : null;
  } catch {
    return null;
  }
}

function saveBody(body: BodyMeasurements): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(body));
  } catch {
    // 저장소를 쓸 수 없으면 새로고침 때 입력값이 사라질 뿐이다.
  }
}

/** 화면 사이에 신체 치수를 넘긴다. 새로고침해도 남도록 sessionStorage에도 둔다. */
export function FittingProvider({ children }: { children: ReactNode }) {
  const [body, setBodyState] = useState<BodyMeasurements | null>(loadBody);

  const value = useMemo<FittingState>(
    () => ({
      body,
      setBody: (next) => {
        setBodyState(next);
        saveBody(next);
      },
    }),
    [body],
  );

  return <FittingContext.Provider value={value}>{children}</FittingContext.Provider>;
}

export function useFitting(): FittingState {
  const context = useContext(FittingContext);
  if (!context) throw new Error('useFitting은 FittingProvider 안에서만 쓸 수 있습니다.');
  return context;
}
