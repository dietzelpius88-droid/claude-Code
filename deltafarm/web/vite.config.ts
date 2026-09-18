import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Vorschau und Entwicklungsserver nutzen denselben Proxy zum Backend.
  preview: {
    host: "127.0.0.1",
    port: 4173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8787", changeOrigin: false },
    },
  },
  server: {
    // Nur lokal erreichbar - wie das Backend.
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8787", changeOrigin: false },
    },
  },
});
