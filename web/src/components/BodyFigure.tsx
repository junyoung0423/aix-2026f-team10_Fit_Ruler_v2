import { useEffect, useRef, useState } from 'react';
import type { MeasurementField } from '../api/contract';
import type { FigureEngine } from '../three/FigureEngine';
import { prefersReducedMotion, webglAvailable } from '../three/support';
import { BodyOutline } from './BodyOutline';

interface Props {
  /** 맞게 입력한 칸의 숫자(cm). 빈칸·틀린 칸은 3D에서 기본값을 쓴다 */
  numbers: Partial<Record<MeasurementField, number>>;
  /** 이름표에 보일 값. 빈칸·틀린 칸은 null("—") */
  values: Partial<Record<MeasurementField, string | null>>;
  active: MeasurementField | null;
}

/**
 * 입력 화면 왼쪽의 몸 그림. WebGL이 되면 입력한 치수대로 만드는 3D 전신 마네킹(끌어서 돌려 보기)이고,
 * 3D를 불러오는 동안은 빈 자리로 두었다가 마네킹이 만들어지면 서서히 보여 준다.
 * WebGL이 안 되거나 3D를 불러오지 못하면 2D 전신 선 그림을 보여 준다.
 */
export function BodyFigure({ numbers, values, active }: Props) {
  const [mode, setMode] = useState<'3d' | '2d'>(() => (webglAvailable() ? '3d' : '2d'));
  const [ready, setReady] = useState(false);
  const hostRef = useRef<HTMLDivElement>(null);
  const labelsRef = useRef<HTMLDivElement>(null);
  const engineRef = useRef<FigureEngine | null>(null);
  const latest = useRef({ numbers, values, active });
  latest.current = { numbers, values, active };

  useEffect(() => {
    if (mode !== '3d') return;
    const host = hostRef.current;
    const labels = labelsRef.current;
    if (!host || !labels) return;
    let alive = true;
    let engine: FigureEngine | null = null;
    import('../three/FigureEngine')
      .then(({ FigureEngine }) => {
        if (!alive) return;
        engine = new FigureEngine(host, {
          labels,
          reducedMotion: prefersReducedMotion(),
          onReady: () => {
            if (alive) setReady(true);
          },
        });
        engine.setBody(latest.current.numbers, latest.current.values);
        engine.setActive(latest.current.active);
        engineRef.current = engine;
      })
      .catch(() => {
        if (alive) setMode('2d');
      });
    return () => {
      alive = false;
      engine?.dispose();
      engineRef.current = null;
    };
  }, [mode]);

  // 입력이 멈추고 잠깐 뒤에 몸을 다시 만든다(글자마다 다시 만들지 않게).
  const bodyKey = JSON.stringify(numbers);
  const valuesKey = JSON.stringify(values);
  useEffect(() => {
    const timer = setTimeout(() => engineRef.current?.setBody(latest.current.numbers, latest.current.values), 320);
    return () => clearTimeout(timer);
  }, [bodyKey, valuesKey]);

  useEffect(() => {
    engineRef.current?.setActive(active);
  }, [active]);

  if (mode === '2d') return <BodyOutline active={active} />;
  return (
    <div className={`figure-3d${ready ? ' is-ready' : ''}`}>
      <div className="figure-3d-canvas" ref={hostRef} />
      <div className="figure-3d-labels" ref={labelsRef} aria-hidden="true" />
    </div>
  );
}
