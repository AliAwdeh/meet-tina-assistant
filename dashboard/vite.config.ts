import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const allowedHosts = ["claw.meettina.net", "sami.meettina.net", "tasks.maidscc.app"];

export default defineConfig({
  plugins: [react],
  server: {
    host: "0.0.0.0",
    port: 5174,
    strictPort: true,
    allowedHosts
  },
  preview: {
    allowedHosts
  }
});
