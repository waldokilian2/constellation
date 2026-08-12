/* ============================================================
   Mermaid rendering for chat messages and the planner preview
   panel. Renders mermaid source (after the shared repair pass in
   mermaidRepair.js); on invalid syntax shows the error plus the
   raw source so the code stays visible/recoverable.
   ============================================================ */

import React, { useEffect, useState } from "react";
import mermaid from "mermaid";
import { repairMermaid } from "./mermaidRepair.js";

let initialized = false;
function ensureMermaid() {
  if (initialized) return;
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: "base",
    themeVariables: {
      darkMode: true,
      background: "transparent",
      fontFamily: "'JetBrains Mono', 'Fira Code', ui-monospace, Menlo, Consolas, monospace",
      fontSize: "13px",

      // Nodes — glass panel fill, slate border, cyan for emphasized text
      primaryColor: "#111827",
      primaryTextColor: "#e8eefc",
      primaryBorderColor: "#566079",
      primaryBorderHover: "#00d4ff",
      secondaryColor: "#0d1326",
      secondaryTextColor: "#cbd5e1",
      secondaryBorderColor: "#334155",
      tertiaryColor: "#1e293b",
      tertiaryTextColor: "#cbd5e1",
      tertiaryBorderColor: "#334155",

      // Edges
      lineColor: "#64748b",
      edgeLabelBackground: "rgba(10, 14, 26, 0.92)",
      edgeLabelColor: "#cbd5e1",
      edgeLabelBorder: "#566079",

      // Subgraphs
      clusterBkg: "rgba(17, 24, 39, 0.45)",
      clusterBorder: "rgba(255, 255, 255, 0.12)",
      titleColor: "#8b97b5",

      // Sequence-diagram actors/notes (consistent when used)
      actorBkg: "#111827",
      actorBorder: "#566079",
      actorTextColor: "#e8eefc",
      signalColor: "#64748b",
      signalTextColor: "#cbd5e1",
      labelBoxBkgColor: "#111827",
      labelBoxBorderColor: "#566079",
      labelTextColor: "#cbd5e1",
      loopTextColor: "#8b97b5",
      noteBkgColor: "#1e293b",
      noteBorderColor: "#334155",
      noteTextColor: "#cbd5e1",
    },
    flowchart: { curve: "basis" },
  });
  initialized = true;
}

export default function MermaidDiagram({ code }) {
  const [state, setState] = useState({ status: "pending", svg: "", error: "" });

  useEffect(() => {
    let alive = true;
    ensureMermaid();
    setState({ status: "pending", svg: "", error: "" });
    const id = "mmd-" + Math.random().toString(36).slice(2, 10);
    mermaid.render(id, repairMermaid(code))
      .then(({ svg }) => {
        if (alive) setState({ status: "done", svg, error: "" });
      })
      .catch((e) => {
        if (alive) {
          const msg = (e && e.message) ? e.message : String(e);
          setState({ status: "error", svg: "", error: msg });
        }
      });
    return () => { alive = false; };
  }, [code]);

  if (state.status === "pending") {
    return <div className="mermaid-render mermaid-loading">Rendering diagram…</div>;
  }

  if (state.status === "error") {
    return (
      <div className="mermaid-error">
        <div className="mermaid-error-msg">⚠ Diagram failed to render — raw source shown instead:</div>
        <pre className="mermaid-fallback">{code}</pre>
        <div className="mermaid-error-detail">{state.error}</div>
      </div>
    );
  }

  return <div className="mermaid-render" dangerouslySetInnerHTML={{ __html: state.svg }} />;
}
