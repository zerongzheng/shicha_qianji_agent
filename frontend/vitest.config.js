import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

// 测试沿用 Vite 的 Vue 编译链，避免开发环境和测试环境解析 .vue 文件的方式不一致。
export default defineConfig({
  plugins: [vue()],
  test: {
    environment: "happy-dom",
    include: ["src/**/*.test.js"],
    clearMocks: true,
    restoreMocks: true,
  },
});
