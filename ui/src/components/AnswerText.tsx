import React from "react";

/**
 * Render a markdown-ish answer string into safe React nodes. Supports:
 *   **bold**
 *   [N](url)           -> superscript link
 *   ([title](url))     -> GPT-style small inline link
 *   bare [N]           -> small reference badge
 *   newlines
 */

// One regex covering every token we care about. Order matters: longer
// patterns first so e.g. ([title](url)) matches before [N](url) before [N].
const TOKEN_RE = new RegExp(
  [
    /\*\*([^*\n]+)\*\*/.source,                              // 1: **bold**
    /\(\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)\)/.source,       // 2,3: ([title](url))
    /\[(\d+)\]\((https?:\/\/[^\s)]+)\)/.source,              // 4,5: [N](url)
    /\[(\d+)\]/.source,                                      // 6: bare [N]
  ].join("|"),
  "g",
);

function renderLine(line: string, lineKey: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let i = 0;

  TOKEN_RE.lastIndex = 0;
  while ((match = TOKEN_RE.exec(line)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(line.slice(lastIndex, match.index));
    }
    const [, bold, gptTitle, gptUrl, refN, refUrl, bareN] = match;
    const key = `${lineKey}-t${i++}`;
    if (bold !== undefined) {
      nodes.push(<strong key={key}>{bold}</strong>);
    } else if (gptTitle !== undefined && gptUrl !== undefined) {
      nodes.push(
        <a
          key={key}
          href={gptUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-brand hover:underline ml-0.5"
        >
          ({gptTitle})
        </a>,
      );
    } else if (refN !== undefined && refUrl !== undefined) {
      nodes.push(
        <sup key={key}>
          <a
            href={refUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-brand hover:underline"
          >
            [{refN}]
          </a>
        </sup>,
      );
    } else if (bareN !== undefined) {
      nodes.push(
        <sup key={key} className="text-text-dim">
          [{bareN}]
        </sup>,
      );
    }
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < line.length) {
    nodes.push(line.slice(lastIndex));
  }
  return nodes;
}

export function AnswerText({ text }: { text: string }) {
  if (!text) return null;
  const lines = text.split("\n");
  return (
    <div className="text-sm leading-relaxed whitespace-pre-wrap break-words">
      {lines.map((line, i) => (
        <React.Fragment key={i}>
          {renderLine(line, `l${i}`)}
          {i < lines.length - 1 && <br />}
        </React.Fragment>
      ))}
    </div>
  );
}
