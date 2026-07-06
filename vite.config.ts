import { defineConfig } from 'vite';

export default defineConfig({
  build: {
    sourcemap: false,
    target: 'es2022'
  },
  server: {
    host: '127.0.0.1'
  },
  preview: {
    host: '127.0.0.1'
  }
});
