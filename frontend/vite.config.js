import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base relativo: el sitio funciona en la raiz de S3/CloudFront o en una subcarpeta.
export default defineConfig({ base: "./", plugins: [react()] });
