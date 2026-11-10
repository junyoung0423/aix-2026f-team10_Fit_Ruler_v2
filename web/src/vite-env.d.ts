/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 'false'면 실제 서버를 부르고, 그 밖에는 가짜 API를 쓴다. */
  readonly VITE_USE_MOCK?: string;
  /** 실제 서버 주소. 비우면 같은 출처의 /api로 보내고 개발 서버 프록시가 넘긴다. */
  readonly VITE_API_BASE_URL?: string;
  /** 개발 서버 프록시가 /api를 넘길 곳 (기본 http://127.0.0.1:8000) */
  readonly VITE_API_PROXY_TARGET?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
