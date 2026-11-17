import { defineConfig } from "@playwright/test";

// Not 3477: that is the dashboard a developer may already be running.
const port = Number(process.env.HEXIS_UI_PORT || 3479);

export default defineConfig({
  testDir: "./e2e",
  timeout: 120000,
  expect: { timeout: 20000 },
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: "retain-on-failure",
  },
  reporter: [["list"], ["html", { open: "never" }]],
});
