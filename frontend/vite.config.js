import { defineConfig } from "vite";

export default defineConfig({
  base: "./",
  server: {
    port: 5173,
  },
  build: {
    outDir: "dist",
    rollupOptions: {
      input: {
        main: "index.html",
        about: "about.html",
        contact: "contact.html",
      },
    },
  },
});
