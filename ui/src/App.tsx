import { useEffect, useMemo, useState } from "react";
import { AlertCircle, Loader2 } from "lucide-react";
import {
  loadBenchmarkMeta,
  loadResults,
  type BenchmarkMeta,
  type BenchmarkResults,
} from "./data";
import { DesearchMark, DesearchWordmark } from "./components/Logo";
import { Scorecard } from "./components/Scorecard";
import { Charts } from "./components/Charts";
import { MethodologyCard } from "./components/MethodologyCard";
import { Filters } from "./components/Filters";
import { QuestionCard } from "./components/QuestionCard";

export default function App() {
  const [meta, setMeta] = useState<BenchmarkMeta | null | "missing">(null);
  const [results, setResults] = useState<BenchmarkResults | "loading" | "missing">("loading");
  const [search, setSearch] = useState("");
  const [forceOpenAll, setForceOpenAll] = useState<boolean | undefined>(undefined);

  useEffect(() => {
    loadBenchmarkMeta().then((m) => {
      if (!m) {
        setMeta("missing");
        setResults("missing");
        return;
      }
      setMeta(m);
      loadResults(m).then((r) => setResults(r ?? "missing"));
    });
  }, []);

  const resultsReady = results !== "loading" && results !== "missing";
  const questions = resultsReady ? results.questions : [];

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return questions;
    return questions.filter(
      (q) => q.question.toLowerCase().includes(term) || q.id.toLowerCase().includes(term),
    );
  }, [questions, search]);

  if (meta === null) {
    return (
      <div className="min-h-screen flex items-center justify-center text-text-muted bg-page-glow">
        <div className="animate-pulse">Loading…</div>
      </div>
    );
  }

  if (meta === "missing") {
    return (
      <div className="min-h-screen flex items-center justify-center bg-page-glow p-8">
        <div className="max-w-md text-center text-sm glass-strong rounded-2xl p-8">
          <AlertCircle className="text-warn mx-auto mb-3" size={28} />
          <p className="font-semibold text-text mb-2">No benchmark data found.</p>
          <p className="text-text-muted">
            Couldn't reach HuggingFace or the local sample. Run{" "}
            <code className="bg-surface-2 px-1.5 py-0.5 rounded text-brand">python3 scripts/weekly_run.py</code>{" "}
            to generate and upload a run.
          </p>
        </div>
      </div>
    );
  }

  const questionCount = resultsReady ? questions.length : meta.questionCount;

  return (
    <div className="min-h-screen bg-page-glow">
      <header className="border-b border-border bg-surface/70 backdrop-blur-md sticky top-0 z-10">
        <div className="max-w-screen-2xl mx-auto px-6 py-3 flex items-center justify-between gap-4">
          <div className="flex items-center gap-2.5">
            <DesearchMark className="text-text" size={20} />
            <DesearchWordmark className="text-text" height={15} />
            <span className="h-5 w-px bg-border-strong mx-1.5" aria-hidden />
            <span className="text-sm font-medium text-text-muted tracking-tight">Search Benchmark</span>
          </div>
          <div className="flex flex-col items-end leading-tight shrink-0">
            <span className="text-[10px] font-semibold tracking-[0.16em] text-text-dim uppercase flex items-center gap-1.5">
              {meta.isFallback ? (
                <span className="text-warn" title="Showing the committed offline sample (HuggingFace was unreachable).">Offline sample</span>
              ) : (
                <span className="text-success" title="Live data from the HuggingFace dataset.">Live · HuggingFace</span>
              )}
            </span>
            <span className="text-xs font-mono text-text-muted mt-0.5">
              {meta.date}
              {questionCount != null && <span className="text-text-dim"> · {questionCount} questions</span>}
            </span>
          </div>
        </div>
      </header>

      <main className="max-w-screen-2xl mx-auto px-6 py-6">
        {meta.scoreboard ? (
          <>
            <Scorecard data={meta.scoreboard} />
            <Charts data={meta.scoreboard} />
          </>
        ) : (
          <div className="mb-6 p-3 text-sm glass rounded-xl flex items-center gap-2 text-warn">
            <AlertCircle size={14} />
            <span>scoreboard.json not present for {meta.date}.</span>
          </div>
        )}

        <MethodologyCard />

        {resultsReady ? (
          <>
            <Filters
              search={search}
              setSearch={setSearch}
              totalCount={questions.length}
              visibleCount={filtered.length}
              onExpandAll={() => setForceOpenAll(true)}
              onCollapseAll={() => setForceOpenAll(false)}
            />
            <div className="space-y-1.5">
              {filtered.map((q) => (
                <QuestionCard
                  key={q.id}
                  question={q}
                  answers={results.answersByQuestion.get(q.id) ?? {}}
                  forceOpen={forceOpenAll}
                />
              ))}
            </div>
          </>
        ) : results === "loading" ? (
          <div className="flex items-center justify-center gap-2 text-text-muted text-sm py-16">
            <Loader2 size={16} className="animate-spin" />
            Loading per-question results…
          </div>
        ) : (
          <div className="p-3 text-sm glass rounded-xl flex items-center gap-2 text-warn">
            <AlertCircle size={14} />
            <span>Per-question results for {meta.date} couldn't be loaded.</span>
          </div>
        )}

        <footer className="mt-12 mb-6 text-center text-xs text-text-dim">
          Open-source search benchmark ·{" "}
          <a href="https://github.com/Desearch-ai/desearch-search-evals" className="text-brand hover:underline">
            Desearch-ai/desearch-search-evals
          </a>
          {" · "}
          <a href="https://huggingface.co/datasets/desearch/desearch-search-evals" className="text-brand hover:underline">
            dataset on HuggingFace
          </a>
        </footer>
      </main>
    </div>
  );
}
