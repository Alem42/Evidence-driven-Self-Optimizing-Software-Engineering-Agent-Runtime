import { defineConfig } from 'vite';
export default defineConfig({build:{outDir:'../src/masa/interfaces/http/static',emptyOutDir:false,assetsDir:'',rollupOptions:{output:{entryFileNames:'app.js',chunkFileNames:'chunk-[hash].js',assetFileNames:'style.[ext]'}}}});
