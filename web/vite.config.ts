import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Dev: run Flask (server.py, :5050) + `npm run dev` (:5174). /api is proxied.
// Prod: `npm run build` -> dist/, served by Flask as static.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  server: {
    port: 5174,
    strictPort: false,
    proxy: {
      "/api": {
        target: "http://localhost:5050",
        // Preserve the browser authority so Flask can enforce exact
        // same-origin Host/Origin checks during development.
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
});
