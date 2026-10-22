import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Served by FastAPI: mount "/assets/static" -> web/dist
export default defineConfig({
  base: "/assets/static/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
