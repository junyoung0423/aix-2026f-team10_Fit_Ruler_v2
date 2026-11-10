import react from '@vitejs/plugin-react';
import { loadEnv } from 'vite';
import { defineConfig } from 'vitest/config';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '');
  return {
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: true,
      // 실제 서버를 쓸 때(VITE_USE_MOCK=false) /api를 핏 API 서버로 넘긴다. 서버에 CORS 설정이 필요 없다.
      proxy: { '/api': env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000' },
    },
    test: { environment: 'node' },
  };
});
