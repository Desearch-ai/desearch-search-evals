import { useState } from "react";
import { ChevronDown, ChevronRight, Info } from "lucide-react";

interface EvaluatorInfo {
  key: string;
  label: string;
  weight: number;
  short: string;
  full: string;
  verdicts: Array<{ label: string; meaning: string; good: boolean }>;
  catches: string;
}

const EVALUATORS: EvaluatorInfo[] = [
  {
    key: "groundedness",
    label: "Groundedness",
    weight: 0.40,
    short: "Do the cited pages actually support each claim in the answer?",
    full:
      "For every factual sentence in the answer, the judge LLM fetches the cited URL and asks: " +
      "does this page's actual content back this exact claim? A claim with multiple citations passes if any one page supports it.",
    verdicts: [
      { label: "SUPPORTED", meaning: "Page content (or title + excerpt) clearly establishes the claim.", good: true },
      { label: "UNSUPPORTED", meaning: "Page is on-topic but doesn't contain evidence for this exact claim.", good: false },
      { label: "CONTRADICTED", meaning: "Page makes a different specific claim about the same fact.", good: false },
    ],
    catches:
      "Hallucinated citations. A provider that invented an answer and bolted on a real-looking URL fails here. " +
      "The judge reads the page and confirms or denies. This is the proof that real-time search actually happened.",
  },
  {
    key: "source_relevance",
    label: "Source Relevance",
    weight: 0.35,
    short: "Are the URLs you cited actually relevant to the question?",
    full:
      "For each cited URL, the judge LLM reads the page and asks: is this page relevant to the question? " +
      "Mean across all cited URLs.",
    verdicts: [
      { label: "YES (1.0)", meaning: "Page is on-topic and could plausibly contain the answer.", good: true },
      { label: "MAYBE (0.5)", meaning: "Page is loosely related but unlikely to contain the answer.", good: false },
      { label: "NO (0.0)", meaning: "Page is off-topic or unrelated.", good: false },
    ],
    catches:
      "Lazy citation. Citing the Wikipedia article on \"Tennis\" for \"who is ATP #1?\" gets a MAYBE. " +
      "Padding sources[] with irrelevant SERP results drags the mean down.",
  },
  {
    key: "answer_quality",
    label: "Answer Quality",
    weight: 0.25,
    short: "Did the answer actually respond to the question?",
    full:
      "The judge reads question + answer (no sources) and picks one verdict. " +
      "Catches evasion, wrong refusals, and confident fabrication on unanswerable questions, " +
      "the failure modes the other two evaluators can miss.",
    verdicts: [
      { label: "RESPONSIVE", meaning: "Answerable question, answer addresses it head-on in the right shape.", good: true },
      { label: "APPROPRIATE_DECLINE", meaning: "Unanswerable question (future event, mythological, undefined math, etc.), honestly declined.", good: true },
      { label: "EVASIVE", meaning: "Answerable question, but the answer dodges (\"sources don't contain this\", hedging).", good: false },
      { label: "WRONG_DECLINE", meaning: "Easy question wrongly refused as if unanswerable.", good: false },
      { label: "HALLUCINATED", meaning: "Unanswerable question (e.g. \"who won the 2099 World Cup?\") confidently answered with a fictional fact.", good: false },
    ],
    catches:
      "Confident fabrication on unanswerable questions, the single most-criticized AI-search failure mode. " +
      "If the cited source happens to back the false claim (e.g. a prediction article), groundedness alone would miss it.",
  },
];


export function MethodologyCard() {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <div className="glass rounded-2xl mb-4 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="w-full px-5 py-3 flex items-center gap-2 hover:bg-surface-2/30 transition text-left"
      >
        {open ? <ChevronDown size={14} className="text-text-dim" /> : <ChevronRight size={14} className="text-text-dim" />}
        <Info size={14} className="text-brand" />
        <span className="font-medium text-sm">How the composite is scored</span>
        <span className="text-xs text-text-dim ml-2">
          (three judge-graded evaluators, no provider self-reports)
        </span>
      </button>

      {open && (
        <div className="px-5 pb-4 pt-2 border-t border-border">
          <p className="text-xs text-text-muted leading-relaxed mb-3">
            Every score comes from a judge LLM (<code className="text-text bg-surface-2 px-1 py-0.5 rounded">gpt-5.4-mini</code>) reading content:
            page text, answer text, or both. Nothing is taken on a provider's word
            (no <code className="text-text bg-surface-2 px-1 py-0.5 rounded">web_search_called</code> flag, no self-reported authority signal).
            A provider has to do well on all three to win the composite.
          </p>

          <div className="grid gap-2">
            {EVALUATORS.map(ev => (
              <div key={ev.key} className="rounded-lg border border-border bg-surface/50 overflow-hidden">
                <button
                  type="button"
                  onClick={() => setExpanded(x => x === ev.key ? null : ev.key)}
                  className="w-full px-3 py-2.5 flex items-start gap-3 hover:bg-surface-2/30 transition text-left"
                >
                  {expanded === ev.key
                    ? <ChevronDown size={12} className="text-text-dim mt-1 shrink-0" />
                    : <ChevronRight size={12} className="text-text-dim mt-1 shrink-0" />}
                  <span className="text-xs font-mono text-brand w-10 shrink-0 mt-0.5">
                    {Math.round(ev.weight * 100)}%
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-semibold">{ev.label}</div>
                    <div className="text-xs text-text-muted mt-0.5">{ev.short}</div>
                  </div>
                </button>

                {expanded === ev.key && (
                  <div className="px-3 pb-3 border-t border-border bg-surface-2/30 text-xs">
                    <p className="text-text-muted leading-relaxed mt-2.5 mb-3">{ev.full}</p>

                    <div className="text-text-dim uppercase tracking-wider text-[10px] mb-1.5">Verdicts</div>
                    <ul className="space-y-1 mb-3">
                      {ev.verdicts.map(v => (
                        <li key={v.label} className="flex items-start gap-2">
                          <span
                            className={`font-mono text-[10px] px-1.5 py-0.5 rounded shrink-0 mt-0.5 ${
                              v.good
                                ? "bg-success/15 text-success border border-success/30"
                                : "bg-danger/15 text-danger border border-danger/30"
                            }`}
                          >
                            {v.label}
                          </span>
                          <span className="text-text-muted leading-relaxed">{v.meaning}</span>
                        </li>
                      ))}
                    </ul>

                    <div className="text-text-dim uppercase tracking-wider text-[10px] mb-1">What it catches</div>
                    <p className="text-text-muted leading-relaxed">{ev.catches}</p>
                  </div>
                )}
              </div>
            ))}
          </div>

          <p className="text-[11px] text-text-dim leading-relaxed mt-4">
            <span className="font-mono">composite = 0.40·groundedness + 0.35·source_relevance + 0.25·answer_quality</span>.
            Missing evaluators are skipped and weights renormalized.
          </p>
        </div>
      )}
    </div>
  );
}
