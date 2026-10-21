import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";

const harnessProxy = {
  target: process.env.HARNESS_SERVER_URL || 'http://127.0.0.1:8001',
  rewrite: (path: string) => path.replace(/^\/harness/, ''),
};

export default defineConfig({
  root: "ui",
  plugins: [svelte()],
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      "/harness": harnessProxy,
      "/api": "http://127.0.0.1:8001",
      "/workspace": { target: "http://127.0.0.1:8002", ws: true },
    },
  },
  preview: {
    port: 5174,
    strictPort: true,
    proxy: {
      "/harness": harnessProxy,
      "/api": "http://127.0.0.1:8001",
      "/workspace": { target: "http://127.0.0.1:8002", ws: true },
    },
  },
  build: { outDir: "../dist", emptyOutDir: true },
});
