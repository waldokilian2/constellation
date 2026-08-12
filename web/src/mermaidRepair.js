/* Shared Mermaid "repair" pass — single source of truth for both the browser
 * (web/src/mermaid.jsx render-time fallback) and the server-side validator
 * (engine/mermaid_validate.mjs), which validates AFTER this repair so a
 * diagram that the browser would render is never rejected at tool-call time.
 *
 * Plain ESM, no JSX, no deps — importable from Node and Vite alike.
 */

/**
 * Repair common LLM mermaid mistakes.
 *
 * 1. Bare edge labels containing punctuation (`@`, `{ }`, `( )`) are
 *    rejected by the lexer, so quote them: |@EventListener| →
 *    |"@EventListener"|. Single-quoted labels become double quotes
 *    (|'x'| is invalid; |"x"| is). The bare-label match is anchored to
 *    arrow context (`>`, `-`, `=` before the opening pipe), single-line,
 *    and skips already-quoted text, so it can't span two labels.
 *
 * 2. A node written as a quoted string used as an id with a trailing
 *    shape/label bracket is invalid:
 *      A -.->|REST| "/api/orders/{id}"[http]      ✗ (id is a quoted path)
 *    Rewrite it to a synthetic id with the path as the label:
 *      A -.->|REST| auto_1["/api/orders/{id}"]    ✓
 *    The pattern `"text"[...]` (closing quote immediately followed by
 *    `[`) never occurs in valid mermaid, so this only ever turns invalid
 *    source valid. Repeated identical quoted nodes collapse to one id so
 *    the same endpoint doesn't fork into two nodes.
 *
 * Idempotent: running it again on repaired output is a no-op.
 */
export function repairMermaid(src) {
  if (!src) return src;
  let out = src
    .replace(/\|'([^'|]*?)'\|/g, '|"$1"|')
    .replace(/(?<=[>=\-])\|[^\n|"]{1,120}\|/g, (m) => `|"${m.slice(1, -1)}"|`);

  const seen = Object.create(null);
  let n = 0;
  out = out.replace(/"([^"\n]*)"\[([^\]\n]*)\]/g, (_m, label) => {
    if (!seen[label]) {
      n += 1;
      seen[label] = "auto_" + n;
    }
    return seen[label] + '["' + label + '"]';
  });

  return out;
}
