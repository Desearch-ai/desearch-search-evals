export type Provider =
  | "desearch"
  | "gpt5mini"
  | "perplexity"
  | "tavily"
  | "exa";

export interface Question {
  id: string;
  category: string;
  source: string;
  question: string;
  expected_answer: string | null;
  difficulty?: string | null;
}

export interface SourceItem {
  url: string;
  title: string;
  snippet?: string;
}

export interface NormalizedAnswer {
  provider: Provider;
  answer: string;
  sources: SourceItem[];
  elapsed: number;
  model: string;
  searchCalled: boolean | null;
  error?: string;
  // Per-evaluator per-question scores (filled in by data.ts)
  sourceRelevance?: number | null;
  groundedness?: number | null;
  answerQuality?: number | null;
  answerQualityVerdict?: string | null;
}

export interface ScoreboardRow {
  provider: Provider;
  source_relevance: number | null;
  groundedness: number | null;
  answer_quality: number | null;
  composite: number | null;
}

export interface Scoreboard {
  evaluators_present: string[];
  weights: Record<string, number>;
  rows: ScoreboardRow[];
}

export interface LatestPointer {
  date: string;
  dates?: string[];
  questions?: number;
  rows?: number;
  providers?: Provider[];
  evaluators?: string[];
  weights?: Record<string, number>;
  isFallback?: boolean;
}
