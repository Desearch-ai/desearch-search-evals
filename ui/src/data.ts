import type { LatestPointer, Question, ResultRow, Scoreboard } from "./types";

export interface Details {
  questions: Question[];
  rows: Map<string, Map<string, ResultRow>>;
}

async function read(path: string, signal: AbortSignal): Promise<string> {
  const response = await fetch(import.meta.env.BASE_URL + "data/" + path, {
    signal,
    cache: "no-cache",
  });
  if (!response.ok)
    throw new Error("Could not load " + path + " (" + response.status + ").");
  return response.text();
}

export function parseRows(
  questions: Question[],
  rows: ResultRow[],
  latest: LatestPointer,
) {
  const indexed = new Map<string, Map<string, ResultRow>>();
  for (const question of questions) {
    if (indexed.has(question.id))
      throw new Error("Duplicate question in benchmark data.");
    indexed.set(question.id, new Map());
  }
  if (
    questions.length !== latest.questions ||
    rows.length !== latest.rows ||
    new Set(latest.profiles).size !== latest.profiles.length
  ) {
    throw new Error("Benchmark counts do not match the published run.");
  }
  for (const row of rows) {
    const group = indexed.get(row.question_id);
    if (
      !group ||
      !latest.profiles.includes(row.profile_id) ||
      group.has(row.profile_id) ||
      row.run_id !== latest.run_id
    ) {
      throw new Error("Benchmark contains an unknown or duplicate result.");
    }
    group.set(row.profile_id, row);
  }
  for (const group of indexed.values()) {
    if (group.size !== latest.profiles.length)
      throw new Error("Benchmark is missing a provider result.");
  }
  return indexed;
}

export async function loadRuns(signal: AbortSignal): Promise<LatestPointer[]> {
  try {
    const runs: LatestPointer[] = JSON.parse(
      await read("search/runs.json", signal),
    );
    if (Array.isArray(runs) && runs.length) return runs;
  } catch (error) {
    if (signal.aborted) throw error;
  }
  return [JSON.parse(await read("search/latest.json", signal))];
}

function checkPath(latest: LatestPointer) {
  if (
    !/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(latest.run_id) ||
    latest.path !== "search/runs/" + latest.run_id
  ) {
    throw new Error("Invalid benchmark run path.");
  }
}

function jsonl<T>(text: string): T[] {
  return text
    .split("\n")
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line));
}

/** The scoreboard alone is a few KB, so the chart and table render before the per-question data. */
export async function loadScoreboard(
  signal: AbortSignal,
  latest: LatestPointer,
): Promise<Scoreboard> {
  checkPath(latest);
  const scoreboard: Scoreboard = JSON.parse(
    await read(latest.path + "/scoreboard.json", signal),
  );
  if (
    scoreboard.run_id !== latest.run_id ||
    scoreboard.questions !== latest.questions ||
    latest.profiles.some(
      (id) =>
        !scoreboard.profiles[id] ||
        !scoreboard.routes.some((route) => route.id === id),
    )
  ) {
    throw new Error("Scoreboard does not match the published run.");
  }
  return scoreboard;
}

export async function loadDetails(
  signal: AbortSignal,
  latest: LatestPointer,
): Promise<Details> {
  checkPath(latest);
  const [questionsText, resultsText] = await Promise.all([
    read(latest.path + "/questions.jsonl", signal),
    read(latest.path + "/results.jsonl", signal),
  ]);
  const questions = await Promise.all(
    jsonl<Question>(questionsText).map((q) =>
      unsealFields(q, ["question", "answer"]),
    ),
  );
  const rows = await Promise.all(
    jsonl<ResultRow>(resultsText).map((row) =>
      unsealFields(row, ["answer", "extracted_answer", "reason"]),
    ),
  );
  return { questions, rows: parseRows(questions, rows, latest) };
}

const keys = new Map<string, Promise<Uint8Array>>();

/** Reverses BrowseComp's scheme: base64, then XOR with the canary's SHA-256, repeated. */
export async function unseal(text: string, canary: string): Promise<string> {
  let key = keys.get(canary);
  if (!key) {
    key = crypto.subtle
      .digest("SHA-256", new TextEncoder().encode(canary))
      .then((digest) => new Uint8Array(digest));
    keys.set(canary, key);
  }
  const bytes = await key;
  const raw = Uint8Array.from(atob(text), (c) => c.charCodeAt(0));
  return new TextDecoder().decode(
    raw.map((value, i) => value ^ bytes[i % bytes.length]),
  );
}

async function unsealFields<T extends { canary?: string }>(
  row: T,
  fields: (keyof T)[],
): Promise<T> {
  if (!row.canary) return row;
  const out = { ...row };
  for (const field of fields) {
    const value = out[field];
    if (typeof value === "string")
      out[field] = (await unseal(value, row.canary)) as T[keyof T];
  }
  return out;
}
