/* ============================================================
   Shared markdown rendering for chat messages.
   Splits ```mermaid and ```html fenced blocks out of the marked
   stream: mermaid renders as a live diagram (error fallback),
   html renders as a DOMPurify-sanitized live preview. Everything
   else renders as sanitized markdown HTML. LLM output is untrusted
   — nothing touches the DOM unsanitized.
   ============================================================ */

import React from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";
import MermaidDiagram from "./mermaid.jsx";

const PURIFY_CONFIG = {
  USE_PROFILES: { html: true },
  FORBID_TAGS: ["script", "style", "iframe", "object", "embed", "link", "meta", "form", "input", "button"],
  FORBID_ATTR: ["onerror", "onload", "onclick", "onsubmit", "formaction", "srcdoc"],
};

export function sanitizeHTML(html) {
  return DOMPurify.sanitize(html || "", PURIFY_CONFIG);
}

export function renderMarkdown(src) {
  if (!src) return "";
  return sanitizeHTML(marked.parse(src, { breaks: true }));
}

/**
 * Split markdown into render segments. Mermaid and html code blocks
 * become {kind:"mermaid"|"html"} segments; everything else becomes
 * {kind:"html"} sanitized markdown. During streaming (`live`) marked
 * lexes an unclosed fence as a code block, so live segments render as
 * plain text instead of triggering render errors or layout flashes.
 */
export function markdownSegments(src, live = false) {
  if (!src) return [];
  const tokens = marked.lexer(src, { breaks: true });
  const segs = [];
  for (const t of tokens) {
    if (t.type === "code" && t.lang === "mermaid") {
      segs.push({ kind: "mermaid", code: t.text });
    } else if (t.type === "code" && t.lang === "html") {
      segs.push({ kind: "html-block", code: t.text });
    } else {
      segs.push({ kind: "html", html: sanitizeHTML(marked.parser([t], { breaks: true })) });
    }
  }
  return segs;
}

export default function MarkdownContent({ src, live = false, className = "" }) {
  const segs = markdownSegments(src, live);
  return (
    <span className={className}>
      {segs.map((s, i) => {
        if (s.kind === "mermaid") {
          return live
            ? <pre className="mermaid-fallback" key={i}>{s.code}</pre>
            : <MermaidDiagram code={s.code} key={i} />;
        }
        if (s.kind === "html-block") {
          return live
            ? <pre className="mermaid-fallback" key={i}>{s.code}</pre>
            : <div className="html-preview" key={i} dangerouslySetInnerHTML={{ __html: sanitizeHTML(s.code) }} />;
        }
        return <span key={i} dangerouslySetInnerHTML={{ __html: s.html }} />;
      })}
    </span>
  );
}
