// Mermaid syntax validator — runs under jsdom with the bundled mermaid package.
//
// Reads Mermaid source from stdin, applies the SAME repair pass the browser
// uses (web/src/mermaidRepair.js), then runs mermaid.parse. Printing a single
// JSON line to stdout:
//   {"valid": true, "code": "<repaired>"}      — parses cleanly (code = repaired)
//   {"valid": false, "error": "..."}           — parse error (first line)
//   {"valid": true, "note": "..."}             — toolchain unavailable (accept)
//
// Invoked by engine/mermaid_validator.py so the server can validate a diagram
// at tool-call time. Validating the REPAIRED source means a diagram the
// browser would render (e.g. a bare edge label |GET /api/x/{id}| that
// repairMermaid quotes) is never rejected at tool-call time — that mismatch
// used to send the AI into fix-and-retry loops. Only genuinely broken syntax
// is rejected and returned to the AI as an error.
//
// IMPORTANT: the jsdom globals (window/document) MUST be installed BEFORE
// `mermaid` is imported — mermaid binds DOMPurify against the global DOM at
// module load, so importing it first yields "DOMPurify.addHook is not a
// function" under Node.
try {
  const { JSDOM } = await import("jsdom");
  const dom = new JSDOM("<!doctype html><html><body></body></html>", { pretendToBeVisual: true });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
} catch {
  console.log(JSON.stringify({ valid: true, note: "jsdom unavailable" }));
  process.exit(0);
}

let mermaid;
try {
  mermaid = (await import("mermaid")).default;
} catch {
  console.log(JSON.stringify({ valid: true, note: "mermaid unavailable" }));
  process.exit(0);
}

let repairMermaid;
try {
  ({ repairMermaid } = await import("../web/src/mermaidRepair.js"));
} catch {
  repairMermaid = (s) => s; // no repair module → validate raw
}

try {
  const { readFileSync } = await import("node:fs");
  mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });
  const raw = readFileSync(0, "utf8");
  const repaired = repairMermaid(raw);
  await mermaid.parse(repaired);
  console.log(JSON.stringify({ valid: true, code: repaired }));
} catch (e) {
  const msg = e && e.message ? String(e.message).split("\n")[0] : String(e);
  console.log(JSON.stringify({ valid: false, error: msg }));
}
