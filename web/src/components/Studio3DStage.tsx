import { useEffect, useRef } from 'react';
import type { BodyInput, GarmentShape } from '../three/bodyModel';
import type { StudioEngine, StudioState } from '../three/StudioEngine';
import { prefersReducedMotion } from '../three/support';

interface Props {
  state: StudioState;
  body: Partial<BodyInput>;
  garment: GarmentShape | null;
  /** WebGL을 만들지 못하면 2D 그림으로 돌아간다 */
  onFail: () => void;
}

/**
 * 피팅룸 3D 장면을 붙이는 자리. three.js는 이 컴포넌트가 처음 쓰일 때 따로 불러온다(첫 화면을 가볍게).
 * 몸·옷 치수가 바뀌면 장면을 새로 만들고, 단계만 바뀌면 그대로 둔 채 알려 준다.
 */
export function Studio3DStage(props: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const engineRef = useRef<StudioEngine | null>(null);
  const latest = useRef(props);
  latest.current = props;

  const bodyKey = JSON.stringify(props.body);
  const garmentKey = JSON.stringify(props.garment);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let alive = true;
    let engine: StudioEngine | null = null;
    import('../three/StudioEngine')
      .then(({ StudioEngine }) => {
        if (!alive) return;
        const now = latest.current;
        engine = new StudioEngine(host, {
          body: now.body,
          garment: now.garment,
          reducedMotion: prefersReducedMotion(),
        });
        engine.setState(now.state);
        engineRef.current = engine;
      })
      .catch(() => {
        if (alive) latest.current.onFail();
      });
    return () => {
      alive = false;
      engine?.dispose();
      engineRef.current = null;
    };
  }, [bodyKey, garmentKey]);

  const { step, preparing, checking, failed } = props.state;
  useEffect(() => {
    engineRef.current?.setState({ step, preparing, checking, failed });
  }, [step, preparing, checking, failed]);

  return (
    <div className="stage-3d" aria-hidden="true">
      <div className="stage-3d-canvas" ref={hostRef} />
    </div>
  );
}
