export type Metric = number | null;

export interface Profile {
  id: string;
  transport: string;
  engine?: string;
  mode?: string;
  placeholder?: boolean;
}

export interface Question {
  id: string;
  question: string;
  answer: string;
  answer_aliases?: string[];
  benchmark?: string;
  canary?: string;
  event_id?: string;
  event_date?: string;
  outlets?: number;
  reference_sources?: {
    url: string | null;
    title?: string;
    domain?: string;
    quote?: string;
  }[];
}

export interface Highlight {
  field: "title" | "text";
  quote: string;
  start: number;
  end: number;
}

export interface Basis {
  screen: string;
  status: string;
  hit: 0 | 1;
  fetched?: boolean;
  label: string | null;
  explanation: string | null;
  extracted_answer: string | null;
  highlights: Highlight[];
  evidence_title: string | null;
  evidence_text: string | null;
}

export interface GradedResult {
  rank: number;
  url: string | null;
  title: string;
  published?: string | null;
  mirror: boolean;
  gold?: string | null;
  page: Basis | null;
  snippet: Basis | null;
}

export interface ResultRow {
  run_id: string;
  question_id: string;
  profile_id: string;
  latency_seconds?: number | null;
  search_status: string;
  metrics: Record<string, number>;
  results?: GradedResult[];
  answer?: string | null;
  extracted_answer?: string | null;
  reason?: string;
  score?: number;
  searches?: number;
  turns?: number;
  seconds?: number;
  search_seconds?: number;
  canary?: string;
}

export interface ProfileScore {
  metrics: Record<string, Metric>;
  failed_searches?: number;
  missing_searches?: number;
  empty_responses?: number;
  results?: number;
  pages_fetched?: number;
  judge_errors?: number;
  pending_judgments?: number;
  mirrors?: number;
}

export interface Scoreboard {
  run_id: string;
  kind?: "retrieval" | "agent";
  model?: string;
  turns?: number;
  questions: number;
  judge: string | null;
  judge_calls: number | null;
  routes: Profile[];
  profiles: Record<string, ProfileScore>;
}

export interface LatestPointer {
  run_id: string;
  kind?: "retrieval" | "agent";
  label?: string;
  note?: string | null;
  path: string;
  questions: number;
  rows: number;
  profiles: string[];
}
