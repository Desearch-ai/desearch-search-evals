import { useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  Loader2,
  Search,
} from "lucide-react";
import { loadDetails, loadRuns, loadScoreboard, type Details } from "./data";
import { withPlaceholders } from "./placeholders";
import type { LatestPointer, Scoreboard } from "./types";
import logo from "./assets/desearch-logo.png";
import { Filters } from "./components/Filters";
import { HeaderLinks } from "./components/HeaderLinks";
import { QuestionCard } from "./components/QuestionCard";
import { ResultsTable, type ColumnGroup } from "./components/ResultsTable";
import { ScoreChart } from "./components/ScoreChart";

const PAGE_SIZE = 20;

const AGENT_GROUP: ColumnGroup = {
  title: "Agent results",
  columns: [
    { key: "score", label: "Answer correct" },
    { key: "searches_per_task", label: "Searches per task", format: "number" },
    {
      key: "search_seconds_per_call",
      label: "Search latency",
      format: "seconds",
    },
    { key: "seconds_per_task", label: "Time per task", format: "seconds" },
    { key: "cost_per_task_usd", label: "Cost per task", format: "usd" },
  ],
};
const GOLD_GROUP: ColumnGroup = {
  title: "Gold URL found",
  columns: [
    { key: "gold_hit_at_1", label: "Top 1" },
    { key: "gold_hit_at_5", label: "Top 5" },
    { key: "gold_hit_at_10", label: "Top 10" },
    { key: "latency_p50_seconds", label: "Latency p50", format: "seconds" },
    { key: "latency_p90_seconds", label: "Latency p90", format: "seconds" },
  ],
};
const COVERAGE_GROUP: ColumnGroup = {
  title: "Gold pages found in the top 10",
  columns: [
    { key: "gold_recall_at_10", label: "Share of pages" },
    { key: "gold_all_at_10", label: "All pages" },
    { key: "latency_p50_seconds", label: "Latency p50", format: "seconds" },
    { key: "latency_p90_seconds", label: "Latency p90", format: "seconds" },
  ],
};

function describe(
  runId: string,
  kind?: string,
): { group: ColumnGroup; text: string } {
  if (kind === "agent")
    return {
      group: AGENT_GROUP,
      text: "Each provider backs the search tool of the same agent, which searches, reads pages and submits one answer. A judge marks the answer against the benchmark's reference.",
    };
  if (runId === "frames")
    return {
      group: COVERAGE_GROUP,
      text: "Each question needs several Wikipedia pages. We check what share of them one search returns in its top 10, matched by URL. Latency includes network and API time for every provider.",
    };
  return {
    group: GOLD_GROUP,
    text: "We check whether the benchmark's reference page is among the results, matched by URL. Latency includes network and API time for every provider.",
  };
}

export default function App() {
  const [runs, setRuns] = useState<LatestPointer[]>([]);
  const [runId, setRunId] = useState("");
  const [board, setBoard] = useState<{
    runId: string;
    data: Scoreboard;
  } | null>(null);
  const [details, setDetails] = useState<{
    runId: string;
    data: Details;
  } | null>(null);
  const [error, setError] = useState("");
  const [detailsError, setDetailsError] = useState<{
    runId: string;
    message: string;
  } | null>(null);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  useEffect(() => {
    const controller = new AbortController();
    loadRuns(controller.signal)
      .then((found) => {
        setRuns(found);
        setRunId(found[0].run_id);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(message(reason));
      });
    return () => controller.abort();
  }, []);

  // Each file is fetched once; switching tabs reads the cache instead of refetching.
  const boards = useRef(new Map<string, Promise<Scoreboard>>());
  const detailFiles = useRef(new Map<string, Promise<Details>>());

  function cached<T>(
    cache: Map<string, Promise<T>>,
    run: LatestPointer,
    load: (signal: AbortSignal, run: LatestPointer) => Promise<T>,
  ): Promise<T> {
    let pending = cache.get(run.run_id);
    if (!pending) {
      pending = load(new AbortController().signal, run);
      pending.catch(() => cache.delete(run.run_id));
      cache.set(run.run_id, pending);
    }
    return pending;
  }

  useEffect(() => {
    const selected = runs.find((run) => run.run_id === runId);
    if (!selected) return;
    let live = true;
    cached(boards.current, selected, loadScoreboard)
      .then((data) => {
        if (!live) return;
        setBoard({ runId: selected.run_id, data: withPlaceholders(data) });
        // Scoreboards are small, so every tab is ready before it is clicked.
        for (const run of runs)
          cached(boards.current, run, loadScoreboard).catch(() => undefined);
      })
      .catch((reason: unknown) => {
        if (live) setError(message(reason));
      });
    cached(detailFiles.current, selected, loadDetails)
      .then((data) => {
        if (live) setDetails({ runId: selected.run_id, data });
      })
      .catch((reason: unknown) => {
        if (live)
          setDetailsError({ runId: selected.run_id, message: message(reason) });
      });
    return () => {
      live = false;
    };
  }, [runs, runId]);

  function selectRun(id: string) {
    if (id === runId) return;
    setSearch("");
    setPage(1);
    setRunId(id);
  }

  const switching = board !== null && board.runId !== runId;
  const scoreboard = board?.data;
  const view = board ? describe(board.runId, board.data.kind) : null;
  const note = runs.find((run) => run.run_id === board?.runId)?.note;
  const isAgent = scoreboard?.kind === "agent";
  const current = details?.runId === runId ? details.data : null;
  const questions =
    current?.questions.filter((question) =>
      (question.question + " " + question.id)
        .toLowerCase()
        .includes(search.toLowerCase().trim()),
    ) ?? [];
  const pages = Math.max(1, Math.ceil(questions.length / PAGE_SIZE));

  return (
    <div className="app-shell">
      <header className="site-header">
        <div className="header-inner">
          <img src={logo} alt="Desearch" className="brand-logo" />
          <span className="header-divider" />
          <span className="header-product">Search benchmark</span>
          <HeaderLinks />
        </div>
      </header>

      <main id="main" className="page simple-page">
        <h1 className="simple-title">Search benchmark results</h1>

        <nav className="bench-tabs" aria-label="Benchmarks">
          {runs.map((run) => (
            <button
              key={run.run_id}
              type="button"
              aria-pressed={run.run_id === runId}
              onClick={() => selectRun(run.run_id)}
            >
              {run.label || run.run_id}
            </button>
          ))}
        </nav>

        {error ? (
          <div className="state-panel" role="alert">
            <AlertCircle size={24} />
            <h2>Results unavailable</h2>
            <p>{error}</p>
          </div>
        ) : !board || !scoreboard || !view ? (
          <section className="panel panel-loading" role="status">
            <Loader2 className="animate-spin" size={24} />
            <p>Loading results…</p>
          </section>
        ) : (
          <div
            className={"bench-content" + (switching ? " is-switching" : "")}
            aria-busy={switching}
          >
            <section className="panel" aria-labelledby="results-title">
              <div className="panel-header">
                <div>
                  <h2 id="results-title">{view.group.title}</h2>
                  <p>{view.text}</p>
                  {note && <p className="panel-note">{note}</p>}
                </div>
              </div>
              <ScoreChart data={scoreboard} groups={[view.group]} />
              <ResultsTable data={scoreboard} groups={[view.group]} />
            </section>

            <section aria-labelledby="browse-heading" className="browse">
              <h2 id="browse-heading" className="browse-title">
                Questions
              </h2>
              {detailsError?.runId === runId ? (
                <div className="state-panel" role="alert">
                  <AlertCircle size={24} />
                  <p>{detailsError.message}</p>
                </div>
              ) : !current ? (
                <div className="state-panel questions-loading" role="status">
                  <Loader2 className="animate-spin" size={22} />
                  <p>Loading questions…</p>
                </div>
              ) : (
                <>
                  <Filters
                    search={search}
                    setSearch={(value) => {
                      setSearch(value);
                      setPage(1);
                    }}
                    totalCount={current.questions.length}
                    visibleCount={questions.length}
                  />
                  <div className="question-list">
                    {questions
                      .slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)
                      .map((question) => (
                        <QuestionCard
                          key={question.id}
                          question={question}
                          profiles={scoreboard.routes}
                          rows={current.rows.get(question.id)!}
                          agent={isAgent}
                        />
                      ))}
                  </div>
                  {!questions.length && (
                    <div className="state-panel">
                      <Search size={25} />
                      <p>No matching questions.</p>
                    </div>
                  )}
                  {pages > 1 && (
                    <nav className="pagination" aria-label="Question pages">
                      <button
                        className="button secondary"
                        disabled={page === 1}
                        onClick={() => setPage(page - 1)}
                      >
                        <ChevronLeft size={15} />
                        Previous
                      </button>
                      <span>
                        Page {page} of {pages}
                      </span>
                      <button
                        className="button secondary"
                        disabled={page === pages}
                        onClick={() => setPage(page + 1)}
                      >
                        Next
                        <ChevronRight size={15} />
                      </button>
                    </nav>
                  )}
                </>
              )}
            </section>
          </div>
        )}
      </main>
    </div>
  );
}

function message(reason: unknown): string {
  return reason instanceof Error ? reason.message : "Could not load results.";
}
