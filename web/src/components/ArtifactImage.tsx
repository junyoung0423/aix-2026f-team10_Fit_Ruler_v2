import { useState, type MouseEvent } from 'react';

/** data: 주소는 브라우저가 새 탭으로 바로 열지 못하게 막아서, 누를 때 blob 주소로 바꿔 연다(가짜 API의 그림). */
function openDataUrl(src: string) {
  const comma = src.indexOf(',');
  const head = src.slice(5, comma); // "image/svg+xml;charset=utf-8" 또는 "...;base64"
  const body = src.slice(comma + 1);
  const type = head.split(';')[0] || 'application/octet-stream';
  const bytes = head.endsWith(';base64')
    ? Uint8Array.from(atob(body), (char) => char.charCodeAt(0))
    : decodeURIComponent(body);
  const url = URL.createObjectURL(new Blob([bytes], { type }));
  window.open(url, '_blank', 'noopener');
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/**
 * 산출물 이미지(`GET /api/artifacts/{id}`). 정지 이미지만 보여 준다(웹 3D 뷰어는 만들지 않는다).
 * - 불러오는 동안 같은 크기(3:4)의 빈 자리를 두어 화면이 흔들리지 않게 한다.
 * - 주소가 열리지 않으면(404 등) 깨진 그림 대신 안내 문구를 보여 준다.
 * - 누르면 새 탭에서 원본 크기로 연다.
 * 주소가 바뀌면 상태를 새로 시작하도록 부르는 쪽에서 `key={src}`를 준다.
 */
export function ArtifactImage({ src, alt, failText }: { src: string; alt: string; failText: string }) {
  const [state, setState] = useState<'loading' | 'loaded' | 'error'>('loading');

  if (state === 'error') {
    return (
      <div className="artifact-frame artifact-error">
        <p>{failText}</p>
      </div>
    );
  }

  const onClick = (event: MouseEvent<HTMLAnchorElement>) => {
    if (!src.startsWith('data:')) return;
    event.preventDefault();
    openDataUrl(src);
  };

  return (
    <a
      className={`artifact-frame${state === 'loading' ? ' artifact-loading' : ''}`}
      href={src}
      target="_blank"
      rel="noopener noreferrer"
      onClick={onClick}
      aria-busy={state === 'loading'}
      title="새 탭에서 원본 크기로 보기"
    >
      <img src={src} alt={alt} onLoad={() => setState('loaded')} onError={() => setState('error')} />
      <span className="sr-only">(새 탭에서 원본 크기로 열림)</span>
    </a>
  );
}
