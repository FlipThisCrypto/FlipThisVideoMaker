import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 120_000 },
  outputDir: "../output/playwright/test-results",
  reporter: [
    ["list"],
    ["html", { outputFolder: "../output/playwright/report", open: "never" }],
  ],
  use: {
    ...devices["Desktop Chrome"],
    baseURL: process.env.FTVM_E2E_BASE_URL ?? "http://127.0.0.1:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
