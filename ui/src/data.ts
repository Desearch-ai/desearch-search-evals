import type {
  LatestPointer,
  NormalizedAnswer,
  Provider,
  Question,
  Scoreboard,
} from "./types";

// Data is baked into ./data at build time (ui/scripts/fetch-data.mjs pulls the
// latest run from HF). The browser only ever reads same-origin, never HF.
const LOCAL_BASE = "./data";

export interface BenchmarkMeta {
  date: string;
  scoreboard: Scoreboard | null;
  isFallback: boolean;
  questionCount: number | null;
  base: string;
}

export interface BenchmarkResults {
  questions: Question[];
  answersByQuestion: Map<string, Partial<Record<Provider, NormalizedAnswer>>>;
}

interface ResultRow {
  question_id: string;
  difficulty?: string | null;
  question?: string;
  provider: Provider;
  model?: string;
  answer?: string;
  sources?: { url?: string; title?: string; snippet?: string }[];
  elapsed_seconds?: number;
  web_search_called?: boolean | null;
  source_relevance?: number | null;
  answer_quality?: number | null;
  answer_quality_verdict?: string | null;
  groundedness?: number | null;
  error?: string | null;
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T | null> {
  try {
    const res = await fetch(url, init);
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

async function fetchText(url: string): Promise<string | null> {
  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    return await res.text();
  } catch {
    return null;
  }
}

export async function loadBenchmarkMeta(): Promise<BenchmarkMeta | null> {
  // no-store: a stale cached pointer after a deploy flip 404s the dated files.
  const latest = await fetchJson<LatestPointer>(`${LOCAL_BASE}/latest.json`, { cache: "no-store" });
  if (!latest?.date) return null;
  const scoreboard = await fetchJson<Scoreboard>(`${LOCAL_BASE}/scoreboards/${latest.date}.json`);
  return {
    date: latest.date,
    scoreboard,
    isFallback: latest.isFallback === true,
    questionCount: latest.questions ?? null,
    base: LOCAL_BASE,
  };
}

function normalize(provider: Provider, row: ResultRow): NormalizedAnswer {
  return {
    provider,
    answer: row.answer ?? "",
    sources: (row.sources ?? []).map((s) => ({
      url: s.url ?? "",
      title: s.title ?? "",
      snippet: s.snippet,
    })),
    elapsed: row.elapsed_seconds ?? 0,
    model: row.model ?? provider,
    searchCalled: row.web_search_called ?? null,
    error: row.error ?? undefined,
    sourceRelevance: row.source_relevance ?? null,
    groundedness: row.groundedness ?? null,
    answerQuality: row.answer_quality ?? null,
    answerQualityVerdict: row.answer_quality_verdict ?? null,
  };
}

function parseResults(text: string): BenchmarkResults {
  const questionsById = new Map<string, Question>();
  const answersByQuestion = new Map<string, Partial<Record<Provider, NormalizedAnswer>>>();

  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    let row: ResultRow;
    try {
      row = JSON.parse(line) as ResultRow;
    } catch {
      continue;
    }
    const qid = row.question_id;
    if (!qid) continue;
    if (!questionsById.has(qid)) {
      questionsById.set(qid, {
        id: qid,
        category: row.difficulty ?? "",
        source: "",
        question: row.question ?? "",
        expected_answer: null,
        difficulty: row.difficulty ?? null,
      });
      answersByQuestion.set(qid, {});
    }
    answersByQuestion.get(qid)![row.provider] = normalize(row.provider, row);
  }

  const questions = [...questionsById.values()].sort((a, b) => a.id.localeCompare(b.id));
  return { questions, answersByQuestion };
}

// ~5 MB, loaded after the leaderboard paints.
export async function loadResults(meta: BenchmarkMeta): Promise<BenchmarkResults | null> {
  const text = await fetchText(`${meta.base}/results/${meta.date}.jsonl`);
  return text ? parseResults(text) : null;
}
