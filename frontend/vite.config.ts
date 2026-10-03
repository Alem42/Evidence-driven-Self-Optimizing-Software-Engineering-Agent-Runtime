import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 前端是独立应用：开发在 5173，直连后端 API（VITE_API_BASE），不再由后端托管。
// Standalone SPA: talks to the API directly; the backend serves no static files.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true, host: '127.0.0.1' },
  preview: { port: 4173, strictPort: true, host: '127.0.0.1' },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
});
