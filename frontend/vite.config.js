import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite serves the React app on http://localhost:5173 during development.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
});