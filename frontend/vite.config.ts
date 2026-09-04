import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        demo: resolve(__dirname, "demo.html"),
      },
    },
  },
  server: {
    proxy: {
      "/v1": "http://127.0.0.1:8400",
      // Same hop as nginx in the built image: the proxy's control surface has
      // its own credential, and it is attached HERE rather than held by the
      // browser. Without this, `AXOR_PROXY_TOKEN` broke `pnpm dev` exactly as
      // it broke the deployed UI.
      "/axor": {
        target: "http://127.0.0.1:8401",
        headers: process.env.AXOR_PROXY_TOKEN
          ? { Authorization: `Bearer ${process.env.AXOR_PROXY_TOKEN}` }
          : undefined,
      },
    },
  },
});
