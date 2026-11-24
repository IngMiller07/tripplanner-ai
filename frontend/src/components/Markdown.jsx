import React from "react";

// Mini renderer de Markdown (negrita, listas, titulos) sin dangerouslySetInnerHTML.
function inline(text, key) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((p, i) => (p.startsWith("**") && p.endsWith("**") ? <strong key={`${key}-${i}`}>{p.slice(2, -2)}</strong> : <React.Fragment key={`${key}-${i}`}>{p}</React.Fragment>));
}

// Segunda defensa: quita razonamiento interno <thinking>...</thinking> (incluso uno aun sin cerrar mientras llega el stream).
export const stripThinking = (t) =>
  (t || "").replace(/<thinking>[\s\S]*?<\/thinking>\s*/g, "").replace(/<thinking>[\s\S]*$/, "").replace(/<\/?thinking>?/g, "");

export default function Markdown({ text }) {
  const lines = stripThinking(text).split("\n");
  const out = [];
  let list = null;
  const flush = () => {
    if (list) {
      const Tag = list.ordered ? "ol" : "ul";
      out.push(<Tag key={`l${out.length}`}>{list.items.map((it, i) => <li key={i}>{inline(it, `li${out.length}-${i}`)}</li>)}</Tag>);
      list = null;
    }
  };
  lines.forEach((raw, idx) => {
    const line = raw.trimEnd();
    let m;
    if ((m = /^\s*[-*]\s+(.*)$/.exec(line))) {
      if (!list || list.ordered) { flush(); list = { ordered: false, items: [] }; }
      list.items.push(m[1]);
    } else if ((m = /^\s*\d+[.)]\s+(.*)$/.exec(line))) {
      if (!list || !list.ordered) { flush(); list = { ordered: true, items: [] }; }
      list.items.push(m[1]);
    } else if ((m = /^#{1,4}\s+(.*)$/.exec(line))) {
      flush();
      out.push(<h4 key={`h${idx}`}>{inline(m[1], `h${idx}`)}</h4>);
    } else if (!line.trim()) {
      flush();
    } else {
      flush();
      out.push(<p key={`p${idx}`}>{inline(line, `p${idx}`)}</p>);
    }
  });
  flush();
  return <div className="md">{out}</div>;
}
