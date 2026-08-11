// Playwright config for Constellation e2e tests.
// Requires the API server to be running (default http://localhost:8765,
// override with PLAYWRIGHT_BASE_URL).
const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: ".",
  testMatch: "**/*.spec.js",
  timeout: 240_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://localhost:8765",
    headless: true,
  },
});
