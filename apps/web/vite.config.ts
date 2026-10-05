import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The daemon serves the built bundle from apps/web/dist (see
// apps/daemon/metaharness/app.py WEB_BUNDLE_CANDIDATES), so the build output
// directory is part of the contract, not a preference.
//
// In `vite dev` the API is proxied to the daemon on its default port so the
// same relative fetches work in both modes.
const DAEMON = process.env.METAHARNESS_DEV_TARGET ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      "/health": DAEMON,
      "/version": DAEMON,
      "/v1": { target: DAEMON, ws: true },
    },
  },
});
