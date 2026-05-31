import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { NormalizedAnswer, Provider, Question } from "../types";
import { PROVIDERS } from "../providers";
import { ProviderColumn } from "./ProviderColumn";

const VERDICT_HELP: Record<string, string> = {
  RESPONSIVE: "Answerable question, answer addresses it directly.",
  APPROPRIATE_DECLINE: "Unanswerable question (future, mythical, anachronism, undefined math), honestly declined.",
  EVASIVE: "Answerable question, but the answer dodges or restates instead of answering.",
  WRONG_DECLINE: "Answerable question wrongly refused as if unanswerable.",
  HALLUCINATED: "Unanswerable question confidently answered with a fictional fact.",
};

const GOOD = new Set(["RESPONSIVE", "APPROPRIATE_DECLINE"]);
const BAD = new Set(["EVASIVE", "WRONG_DECLINE", "HALLUCINATED"]);

function gradePill(provider: Provider, a?: NormalizedAnswer) {
  const cfg = PROVIDERS.find((p) => p.key === provider);
  const short = cfg?.short ?? provider;
  let tone = "bg-surface-2 text-text-dim border-border";
  let title = `${cfg?.label ?? provider}: no answer`;

  if (a && !a.error) {
    const v = a.answerQualityVerdict;
    if (v && GOOD.has(v)) {
      tone = "bg-success/12 text-success border-success/25";
      title = `${cfg?.label}: ${v}. ${VERDICT_HELP[v] ?? ""}`;
    } else if (v && BAD.has(v)) {
      tone = "bg-danger/12 text-danger border-danger/25";
      title = `${cfg?.label}: ${v}. ${VERDICT_HELP[v] ?? ""}`;
    } else if (typeof a.groundedness === "number") {
      const pct = Math.round(a.groundedness * 100);
      tone = pct >= 80 ? "bg-success/12 text-success border-success/25"
           : pct >= 50 ? "bg-warn/12 text-warn border-warn/25"
           : "bg-danger/12 text-danger border-danger/25";
      title = `${cfg?.label}: groundedness ${pct}%`;
    } else {
      tone = "bg-surface-2 text-text-muted border-border";
      title = cfg?.label ?? provider;
    }
  }

  return (
    <span key={provider} title={title}
      className={`px-1.5 py-0.5 rounded-md text-[10px] font-medium border flex items-center gap-1 cursor-help ${tone}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${cfg?.dot}`}></span>
      {short}
    </span>
  );
}

export function QuestionCard({
  question,
  answers,
  forceOpen,
}: {
  question: Question;
  answers: Partial<Record<Provider, NormalizedAnswer>>;
  forceOpen?: boolean;
}) {
  const [internalOpen, setInternalOpen] = useState(false);
  const open = forceOpen ?? internalOpen;

  return (
    <div className="glass rounded-xl overflow-hidden hover-lift">
      <button
        type="button"
        onClick={() => setInternalOpen((o) => !o)}
        className="w-full text-left px-3 py-2.5 flex items-center gap-3 hover:bg-surface-2/40 transition"
      >
        {open ? <ChevronDown size={14} className="text-text-dim shrink-0" />
              : <ChevronRight size={14} className="text-text-dim shrink-0" />}
        <span className="font-mono text-xs text-text-dim w-16 shrink-0 truncate">{question.id}</span>
        <span className="flex-1 text-sm truncate">{question.question}</span>
        <span className="flex items-center gap-1 shrink-0">
          {PROVIDERS.map((p) => gradePill(p.key, answers[p.key]))}
        </span>
      </button>

      {open && (
        <div className="px-3 pb-3 border-t border-border bg-surface/40">
          {question.expected_answer && (
            <div className="my-3 text-sm border-l-2 border-warn bg-warn/5 px-3 py-2 rounded-r">
              <span className="font-semibold text-warn">Expected:</span>{" "}
              <span className="text-text-muted">{question.expected_answer}</span>
            </div>
          )}
          <div className="grid gap-3 mt-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))" }}>
            {PROVIDERS.map((p) => (
              <ProviderColumn key={p.key} providerKey={p.key} answer={answers[p.key]} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
