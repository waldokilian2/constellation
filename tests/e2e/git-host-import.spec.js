// E2E coverage for the universal git-host import picker (create-project flow).
//
// Fully offline: the repo-discovery endpoint (/api/remotes/repos) and the
// project-create SSE stream are mocked with page.route — no real GitHub /
// GitLab / Bitbucket / Azure DevOps host is contacted, and no repos are
// cloned. The fixture payloads mirror the real per-provider API shapes the
// backend normalizes into {provider, owner, repos}.
//
// Requires the API server (for the static frontend + health):
//   python -m uvicorn server:app --port 8765
const { test, expect } = require("@playwright/test");

const BASE = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:8765";
const OWNER = "acme";

const FIXTURE_REPOS = [
  { name: "order-service",    full_name: "acme/order-service",    description: "Order processing", default_branch: "main",    clone_url: "https://github.com/acme/order-service.git" },
  { name: "fulfillment-service", full_name: "acme/fulfillment-service", description: "Fulfillment engine", default_branch: "main", clone_url: "https://github.com/acme/fulfillment-service.git" },
  { name: "notification-service", full_name: "acme/notification-service", description: "Notifications", default_branch: "main", clone_url: "https://github.com/acme/notification-service.git" },
  { name: "payment-service",  full_name: "acme/payment-service",  description: "Payments", default_branch: "develop", clone_url: "https://github.com/acme/payment-service.git" },
  { name: "auth-service",     full_name: "acme/auth-service",     description: "Authn/z", default_branch: "main",    clone_url: "https://github.com/acme/auth-service.git" },
  { name: "infra-tooling",    full_name: "acme/infra-tooling",    description: "K8s tooling", default_branch: "main", clone_url: "https://github.com/acme/infra-tooling.git" },
  { name: "docs-site",        full_name: "acme/docs-site",        description: "Documentation", default_branch: "main", clone_url: "https://github.com/acme/docs-site.git" },
  { name: "legacy-monolith",  full_name: "acme/legacy-monolith",  description: "Old monolith", default_branch: "main", clone_url: "https://github.com/acme/legacy-monolith.git" },
];

function orgPayload() {
  return { provider: "github", owner: OWNER, repos: FIXTURE_REPOS };
}

test.describe.serial("Import from a git host", () => {
  let lastCreateBody = null;

  test.beforeAll(async () => {
    const health = await fetch(BASE + "/health").catch(() => null);
    if (!health || !health.ok) {
      throw new Error("API server not reachable at " + BASE + " — start it first (python -m uvicorn server:app --port 8765)");
    }
  });

  test.beforeEach(async ({ page }) => {
    // Mock repo discovery: only the fixture org resolves; anything else is an
    // unsupported/bad link (matches the backend's validation errors).
    await page.route("**/api/remotes/repos?**", async (route) => {
      const link = decodeURIComponent(new URL(route.request().url()).searchParams.get("link") || "");
      if (!link.includes("github.com/" + OWNER)) {
        await route.fulfill({
          status: 400,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Unsupported link — paste an org/workspace link from github.com, gitlab.com, bitbucket.org or dev.azure.com (or add the repos manually)" }),
        });
        return;
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(orgPayload()) });
    });

    // Mock project creation with a synthetic SSE `done` event so nothing is
    // actually cloned; capture the request body for assertions.
    lastCreateBody = null;
    await page.route("**/api/projects", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      const body = route.request().postDataJSON();
      lastCreateBody = body;
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: "data: " + JSON.stringify({ type: "done", project: { id: "mock-" + Date.now(), name: body.name } }) + "\n\n",
      });
    });
  });

  // Open the New-project modal and switch to the git-host import tab.
  async function openHostPicker(page) {
    await page.goto("/");
    await page.locator("button", { hasText: /New project/ }).first().click();
    await page.locator(".import-tab", { hasText: "Import from a git host" }).click();
  }

  test("git-host link loads the repo list with all repos checked", async ({ page }) => {
    await openHostPicker(page);

    await page.locator(".remote-link").fill("https://github.com/acme");
    await page.locator("button", { hasText: "Load repos" }).click();

    await expect(page.locator(".remote-picker")).toBeVisible();
    await expect(page.locator(".remote-provider")).toHaveText("GitHub");
    await expect(page.locator(".remote-owner")).toHaveText(OWNER);
    await expect(page.locator(".remote-row")).toHaveCount(FIXTURE_REPOS.length);
    await expect(page.locator(".remote-selected")).toHaveText("8 of 8 selected");
    // Project name is prefilled with the owner.
    await expect(page.locator('input[placeholder="e.g. Order Platform"]')).toHaveValue(OWNER);
  });

  test("search filters repos; unchecking and select-all update the counter", async ({ page }) => {
    await openHostPicker(page);
    await page.locator(".remote-link").fill("https://github.com/acme");
    await page.locator("button", { hasText: "Load repos" }).click();
    await expect(page.locator(".remote-picker")).toBeVisible();

    // Search narrows the list (only matches are rendered).
    await page.locator(".remote-search").fill("order");
    await expect(page.locator(".remote-row")).toHaveCount(1);
    await expect(page.locator(".remote-row")).toContainText("order-service");

    // Unchecking a row reduces the selected counter.
    await page.locator(".remote-row", { hasText: "order-service" }).locator("input[type=checkbox]").uncheck();
    await expect(page.locator(".remote-selected")).toHaveText("7 of 8 selected");

    // Clear the search; select-all re-checks the unchecked row (7/8 → 8/8).
    await page.locator(".remote-search").fill("");
    await expect(page.locator(".remote-row")).toHaveCount(FIXTURE_REPOS.length);
    await page.locator(".remote-select-all").click();
    await expect(page.locator(".remote-selected")).toHaveText("8 of 8 selected");
    // Toggling off then on again.
    await page.locator(".remote-select-all").click(); // deselect all
    await expect(page.locator(".remote-selected")).toHaveText("0 of 8 selected");
    await page.locator(".remote-select-all").click(); // select all
    await expect(page.locator(".remote-selected")).toHaveText("8 of 8 selected");
  });

  test("create posts exactly the checked repo URLs", async ({ page }) => {
    await openHostPicker(page);
    await page.locator(".remote-link").fill("https://github.com/acme");
    await page.locator("button", { hasText: "Load repos" }).click();
    await expect(page.locator(".remote-picker")).toBeVisible();

    // Deselect all, then pick exactly two services.
    await page.locator(".remote-select-all").click();
    await expect(page.locator(".remote-selected")).toHaveText("0 of 8 selected");
    for (const name of ["order-service", "fulfillment-service"]) {
      await page.locator(".remote-row", { hasText: name }).locator("input[type=checkbox]").check();
    }
    await expect(page.locator(".remote-selected")).toHaveText("2 of 8 selected");

    await page.locator("button", { hasText: "Create & import" }).click();
    await expect(page.locator(".modal-card")).toHaveCount(0); // modal closes after done

    expect(lastCreateBody.name).toBe(OWNER);
    expect(lastCreateBody.repos).toEqual([
      "https://github.com/acme/order-service.git",
      "https://github.com/acme/fulfillment-service.git",
    ]);
  });

  test("unsupported link shows an error and no picker", async ({ page }) => {
    await openHostPicker(page);

    await page.locator(".remote-link").fill("https://example.com/foo");
    await page.locator("button", { hasText: "Load repos" }).click();

    await expect(page.locator(".ingest-error")).toContainText("Unsupported link");
    await expect(page.locator(".remote-picker")).toHaveCount(0);
  });
});
