// E2E coverage for the graph diff & compare UI (issue #13).
//
// The suite is self-provisioning: it copies the sample repos into
// output/e2e-fixture (gitignored), registers them as a fresh project through
// the API, runs the first analysis, then introduces a source change and
// re-scans — producing a deterministic "what changed since last scan" state.
// The UI is then driven through the full compare flow and the project is
// deleted afterwards.
//
// Requires the API server: python -m uvicorn server:app --port 8765
const { test, expect } = require("@playwright/test");
const fs = require("fs");
const path = require("path");

const BASE = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:8765";
const REPO_ROOT = path.resolve(__dirname, "../..");
const FIXTURE = path.join(REPO_ROOT, "output", "e2e-fixture");
const SAMPLE_REPOS = ["order-service", "fulfillment-service", "notification-service"];
const PROJECT_NAME = "E2E Diff Project";

// POST a JSON body and read the SSE stream; returns the `done` event.
async function postSSE(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) throw new Error("POST " + url + " -> HTTP " + res.status);
  const text = await res.text();
  const events = text
    .split("\n")
    .filter((l) => l.startsWith("data:"))
    .map((l) => { try { return JSON.parse(l.slice(5)); } catch { return null; } })
    .filter(Boolean);
  const done = events.find((e) => e.type === "done");
  if (!done) throw new Error("No `done` event in SSE from " + url + ": " + text.slice(0, 400));
  return done;
}

// Add a DELETE endpoint to the fixture's OrderController (mirrors the demo
// change used to validate issue #13 originally). Brace-anchored and
// EOL-agnostic (works on CRLF and LF checkouts).
function patchOrderController(reposDir) {
  const file = path.join(
    reposDir, "order-service", "src", "main", "java", "com", "example", "orders", "OrderController.java"
  );
  let code = fs.readFileSync(file, "utf8");
  if (code.includes("deleteOrder")) return;
  const insert = '\n    @DeleteMapping("/{id}")\n' +
    '    public void deleteOrder(@PathVariable("id") String id) {\n' +
    "        orderService.getOrder(id);\n" +
    "    }\n";
  const last = code.lastIndexOf("}");
  if (last < 0) throw new Error("No closing brace in OrderController.java");
  fs.writeFileSync(file, code.slice(0, last) + insert + code.slice(last));
}

let project = null;

// Open the project from the landing page and switch compare mode on.
async function enterProjectCompare(page) {
  await page.goto("/");
  await page.locator(".project-card", { hasText: PROJECT_NAME }).click();
  await page.locator(".compare-bar .compare-action").click();
  await expect(page.locator(".compare-bar.active .compare-title")).toHaveText("COMPARING");
}

// Drill from the galaxy into the order-service solar system (compare mode on).
// Click the visible .repo-node button — the .repo-wrap is a 0x0 positioning
// div and is not clickable by Playwright.
async function drillToOrderService(page) {
  const repo = page.locator(".repo-wrap", { has: page.locator(".repo-label", { hasText: "order-service" }) });
  await repo.locator(".repo-node").click();
  await expect(page.locator(".star").first()).toBeVisible();
}

test.describe.serial("Graph diff & compare UI", () => {
  test.beforeAll(async () => {
    const health = await fetch(BASE + "/health").catch(() => null);
    if (!health || !health.ok) {
      throw new Error("API server not reachable at " + BASE + " — start it first (python -m uvicorn server:app --port 8765)");
    }

    // Fresh fixture: copies of the sample repos.
    fs.rmSync(FIXTURE, { recursive: true, force: true });
    fs.mkdirSync(path.join(FIXTURE, "repos"), { recursive: true });
    for (const r of SAMPLE_REPOS) {
      fs.cpSync(path.join(REPO_ROOT, "tests", "repos", r), path.join(FIXTURE, "repos", r), { recursive: true });
    }

    // First analysis (no previous version yet).
    const done = await postSSE(BASE + "/api/projects", {
      name: PROJECT_NAME,
      repos: SAMPLE_REPOS.map((r) => "local:" + path.join(FIXTURE, "repos", r).replace(/\\/g, "/")),
    });
    project = done.project;
    if (!project || !project.id) throw new Error("Project create failed: " + JSON.stringify(done));
  });

  test.afterAll(async () => {
    if (project && project.id) {
      await fetch(BASE + "/api/projects/" + project.id, { method: "DELETE" }).catch(() => {});
    }
    fs.rmSync(FIXTURE, { recursive: true, force: true });
  });

  test("fresh project shows no compare UI (no previous version)", async ({ page }) => {
    await page.goto("/");
    await page.locator(".project-card", { hasText: PROJECT_NAME }).click();
    await expect(page.locator(".compare-bar")).toHaveCount(0);
    await expect(page.locator(".repo-node").first()).toBeVisible();
  });

  test("source change + rescan produces the diff banner with chips", async ({ page }) => {
    patchOrderController(path.join(FIXTURE, "repos"));
    await postSSE(BASE + "/api/projects/" + project.id + "/rescan", {});

    await page.goto("/");
    await page.locator(".project-card", { hasText: PROJECT_NAME }).click();
    const bar = page.locator(".compare-bar");
    await expect(bar).toHaveCount(1);
    await expect(bar.locator(".compare-text")).toContainText("Since last scan");
    await expect(bar.locator(".diff-chip.added").first()).toContainText("+1");
    await expect(bar.locator(".compare-action")).toHaveText("Compare");
  });

  test("project card shows the change chips", async ({ page }) => {
    await page.goto("/");
    const card = page.locator(".project-card", { hasText: PROJECT_NAME });
    await expect(card.locator(".pc-diff .diff-chip.added")).toContainText("+1 entry point");
  });

  test("compare mode: repo badge + edge popup in the galaxy", async ({ page }) => {
    await enterProjectCompare(page);

    // The changed repo carries the added-endpoint badge and ring.
    const repo = page.locator(".repo-wrap", { has: page.locator(".repo-label", { hasText: "order-service" }) });
    await expect(repo.locator(".repo-diff-badge .diff-chip.added")).toContainText("+1");
    await expect(repo.locator(".repo-node")).toHaveClass(/st-added/);

    // Edge hover popup still works in compare mode.
    await page.locator(".edge").first().hover();
    await expect(page.locator(".edge-popup")).toBeVisible();
    await expect(page.locator(".legend")).toContainText("Since last scan");
  });

  test("solar system: added star is diff-marked", async ({ page }) => {
    await enterProjectCompare(page);
    await drillToOrderService(page);

    const addedStar = page.locator(".star.st-added");
    await expect(addedStar).toHaveCount(1);
    await expect(addedStar.locator(".star-badge")).toHaveText("▲");
    await expect(page.locator(".view-hint")).toContainText(/was \d+ entry points before/);
  });

  test("path view: new entry point is diff-marked", async ({ page }) => {
    await enterProjectCompare(page);
    await drillToOrderService(page);

    await page.locator(".star.st-added").click();
    await expect(page.locator(".pv-node").first()).toBeVisible();
    await expect(page.locator(".view-hint")).toContainText("new since last scan");
    await expect(page.locator(".pv-node.st-added .pv-diff-chip").first()).toHaveText("▲ new");
  });

  test("detail panel: changes section on a selected node", async ({ page }) => {
    await enterProjectCompare(page);
    await drillToOrderService(page);
    await page.locator(".star.st-added").click();
    await page.locator(".pv-node-body").first().click();

    const changes = page.locator(".detail-panel .dp-changes");
    await expect(changes).toBeVisible();
    await expect(changes.locator(".dp-changes-title")).toHaveText("Changes since last scan");
    await expect(changes.locator(".dp-changes-line.st-added").first()).toContainText("new");
  });

  test("snapshot selector: dropdown lists snapshots and keeps comparing", async ({ page }) => {
    await enterProjectCompare(page);

    const select = page.locator(".compare-versions select");
    const options = select.locator("option");
    await expect(options.first()).toContainText("previous snapshot");
    const count = await options.count();
    if (count > 1) {
      await select.selectOption({ index: 1 });
      await expect(page.locator(".compare-bar .compare-text")).toContainText("+1");
    }
  });
});
