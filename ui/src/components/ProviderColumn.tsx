import { useState } from "react";
import { Check, X, CircleDashed, AlertTriangle, ExternalLink, Globe, Clock, Link2 } from "lucide-react";
import type { NormalizedAnswer } from "../types";
import { PROVIDER_BY_KEY } from "../providers";
import { AnswerText } from "./AnswerText";

function answerQualityBadge(verdict?: string | null) {
  if (!verdict) return null;
  if (verdict === "RESPONSIVE")
    return <span className="px-1.5 py-0.5 rounded-md text-[10px] font-semibold bg-success/15 text-success border border-success/30 flex items-center gap-1" title="answer addresses the question"><Check size={10} strokeWidth={3} /> RESPONSIVE</span>;
  if (verdict === "APPROPRIATE_DECLINE")
    return <span className="px-1.5 py-0.5 rounded-md text-[10px] font-semibold bg-success/15 text-success border border-success/30 flex items-center gap-1" title="correctly declined an unanswerable question"><Check size={10} strokeWidth={3} /> APPROPRIATE_DECLINE</span>;
  if (verdict === "HALLUCINATED")
    return <span className="px-1.5 py-0.5 rounded-md text-[10px] font-semibold bg-danger/15 text-danger border border-danger/30 flex items-center gap-1" title="confidently asserted something fictional"><X size={10} strokeWidth={3} /> HALLUCINATED</span>;
  if (verdict === "WRONG_DECLINE")
    return <span className="px-1.5 py-0.5 rounded-md text-[10px] font-semibold bg-danger/15 text-danger border border-danger/30 flex items-center gap-1" title="declined an answerable question"><X size={10} strokeWidth={3} /> WRONG_DECLINE</span>;
  if (verdict === "EVASIVE")
    return <span className="px-1.5 py-0.5 rounded-md text-[10px] font-semibold bg-danger/15 text-danger border border-danger/30 flex items-center gap-1" title="dodged the question"><X size={10} strokeWidth={3} /> EVASIVE</span>;
  return <span className="px-1.5 py-0.5 rounded-md text-[10px] font-semibold bg-surface-2 text-text-muted border border-border flex items-center gap-1"><CircleDashed size={10} /> {verdict}</span>;
}

function pctBadge(label: string, v: number | null | undefined) {
  if (v === null || v === undefined) return null;
  const pct = Math.round(v * 100);
  const cls = pct >= 80
    ? "bg-success/15 text-success border-success/30"
    : pct >= 50
    ? "bg-warn/15 text-warn border-warn/30"
    : "bg-danger/15 text-danger border-danger/30";
  return (
    <span className={`px-1.5 py-0.5 rounded-md text-[10px] font-medium border tabular ${cls}`}>
      {label} {pct}%
    </span>
  );
}

export function ProviderColumn({
  providerKey,
  answer,
}: {
  providerKey: NormalizedAnswer["provider"];
  answer: NormalizedAnswer | undefined;
}) {
  const cfg = PROVIDER_BY_KEY[providerKey];
  const [showSources, setShowSources] = useState(false);

  if (!answer) {
    return (
      <div className={`rounded-xl border ${cfg.columnAccent} p-3 text-sm text-text-dim italic`}>
        <div className="flex items-center gap-2 mb-1">
          <span className={`w-2 h-2 rounded-full ${cfg.dot}`}></span>
          <span className="font-semibold text-text not-italic">{cfg.label}</span>
        </div>
        (no data)
      </div>
    );
  }

  const noSearch = answer.searchCalled === false && answer.sources.length === 0;

  return (
    <div className={`rounded-xl border ${cfg.columnAccent} p-3 flex flex-col gap-2`}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className={`w-2 h-2 rounded-full ${cfg.dot}`}></span>
        <span className="font-semibold text-text">{cfg.label}</span>
        {answerQualityBadge(answer.answerQualityVerdict)}
        {pctBadge("ground", answer.groundedness)}
        {pctBadge("sources", answer.sourceRelevance)}
      </div>
      <div className="text-xs text-text-muted tabular flex items-center gap-3 flex-wrap">
        <span className="flex items-center gap-1"><Link2 size={11} /> {answer.sources.length}</span>
        <span className="flex items-center gap-1"><Clock size={11} /> {answer.elapsed.toFixed(1)}s</span>
        {noSearch && (
          <span className="flex items-center gap-1 text-warn font-medium">
            <AlertTriangle size={11} /> skipped search
          </span>
        )}
      </div>
      {answer.error && (
        <div className="text-xs text-danger bg-danger/10 border border-danger/20 p-2 rounded-md">
          {answer.error}
        </div>
      )}
      <div className="text-sm text-text/90">
        {answer.answer ? <AnswerText text={answer.answer} /> : (
          <span className="text-sm italic text-text-dim">(empty answer)</span>
        )}
      </div>
      {answer.sources.length > 0 && (
        <div className="mt-1 border-t border-border pt-2">
          <button
            type="button"
            onClick={() => setShowSources(s => !s)}
            className="text-[11px] text-text-muted hover:text-text inline-flex items-center gap-1"
          >
            <Globe size={11} />
            {showSources ? "Hide" : "Show"} sources ({answer.sources.length})
          </button>
          {showSources && (
            <ul className="mt-1.5 text-xs space-y-1 max-h-44 overflow-y-auto">
              {answer.sources.slice(0, 15).map((s, i) => (
                <li key={i} className="truncate">
                  <a
                    href={s.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-brand hover:underline inline-flex items-center gap-1"
                    title={s.title}
                  >
                    <ExternalLink size={10} />
                    {s.title || s.url}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
