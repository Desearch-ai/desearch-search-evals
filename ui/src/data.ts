import type {
  LatestPointer,
  NormalizedAnswer,
  Provider,
  Question,
  Scoreboard,
} from "./types";

// Live data on HuggingFace; ./data is the committed offline sample. Same layout for both.
const HF_BASE =
  "https://huggingface.co/datasets/desearch/desearch-search-evals/resolve/main";
const LOCAL_BASE = "./data";

export interface BenchmarkMeta {
  date: string;
  scoreboard: Scoreboard | null;
  source: "huggingface" | "local";
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

// Pointer is revalidated every load; the dated files it names are immutable, so cached.
async function resolvePointer(): Promise<
  { base: string; source: "huggingface" | "local"; latest: LatestPointer } | null
> {
  const hf = await fetchJson<LatestPointer>(`${HF_BASE}/latest.json`, { cache: "no-store" });
  if (hf?.date) return { base: HF_BASE, source: "huggingface", latest: hf };
  const local = await fetchJson<LatestPointer>(`${LOCAL_BASE}/latest.json`, { cache: "no-store" });
  if (local?.date) return { base: LOCAL_BASE, source: "local", latest: local };
  return null;
}

export async function loadBenchmarkMeta(): Promise<BenchmarkMeta | null> {
  const resolved = await resolvePointer();
  if (!resolved) return null;
  const { base, source, latest } = resolved;
  const scoreboard = await fetchJson<Scoreboard>(`${base}/scoreboards/${latest.date}.json`);
  return {
    date: latest.date,
    scoreboard,
    source,
    isFallback: source === "local" || latest.isFallback === true,
    questionCount: latest.questions ?? null,
    base,
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

// ~5 MB, loaded after the leaderboard paints. Falls back to the local sample on failure.
export async function loadResults(meta: BenchmarkMeta): Promise<BenchmarkResults | null> {
  const primary = await fetchText(`${meta.base}/results/${meta.date}.jsonl`);
  if (primary) return parseResults(primary);
  if (meta.base !== LOCAL_BASE) {
    const fallback = await fetchText(`${LOCAL_BASE}/results/${meta.date}.jsonl`);
    if (fallback) return parseResults(fallback);
  }
  return null;
}
