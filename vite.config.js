import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// ── Constellation Vite config ───────────────────────────────────
// Build output goes to web/dist/ so server.py can serve it as static
// files with zero changes to the API layer.
//
//   npm run dev    → Vite dev server with HMR (port 5173)
//                    API calls proxy to the Python backend on :8765
//   npm run build  → Production bundle to web/dist/
//   npm run preview→ Preview the production build
//
export default defineConfig({
  plugins: [react()],
  root: "web",
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://localhost:8765",
      "/health": "http://localhost:8765",
    },
  },
  preview: {
    proxy: {
      "/api": "http://localhost:8765",
      "/health": "http://localhost:8765",
    },
  },
});
