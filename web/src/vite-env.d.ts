/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 'false'면 실제 서버를 부르고, 그 밖에는 가짜 API를 쓴다. */
  readonly VITE_USE_MOCK?: string;
  /** 실제 서버 주소 (예: http://localhost:8000) */
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
