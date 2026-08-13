import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const API_PATHS = [
  "/documents", "/notes", "/ask", "/search", "/nodes",
  "/stats", "/retry", "/rebuild", "/backup", "/graph",
  "/events", "/dedupe", "/analysis", "/analyze", "/vault",
];

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: Object.fromEntries(
      API_PATHS.map((path) => [path, "http://127.0.0.1:8000"])
    ),
  },
});
