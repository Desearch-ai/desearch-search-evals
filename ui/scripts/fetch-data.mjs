import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const destination = fileURLToPath(new URL("../public/data/", import.meta.url));
const locals = process.argv.slice(2);
const repo = process.env.HF_DATASET_REPO || "desearch/desearch-search-evals";
const headers = process.env.HF_TOKEN
  ? { Authorization: "Bearer " + process.env.HF_TOKEN }
  : {};

async function read(root, name) {
  if (root) return readFile(path.resolve(root, name), "utf8");
  const response = await fetch(
    "https://huggingface.co/datasets/" + repo + "/resolve/main/" + name,
    { headers, signal: AbortSignal.timeout(60_000) },
  );
  if (!response.ok) throw new Error(name + ": HTTP " + response.status);
  return response.text();
}

async function pointers(root) {
  try {
    return JSON.parse(await read(root, "search/runs.json"));
  } catch {
    return [JSON.parse(await read(root, "search/latest.json"))];
  }
}

async function copyRun(root, latest) {
  if (
    !/^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$/.test(latest.run_id) ||
    latest.path !== "search/runs/" + latest.run_id
  ) {
    throw new Error("Invalid benchmark run path.");
  }
  for (const name of ["questions.jsonl", "results.jsonl", "scoreboard.json"]) {
    const filename = latest.path + "/" + name;
    const contents = await read(root, filename);
    if (
      name === "scoreboard.json" &&
      JSON.parse(contents).run_id !== latest.run_id
    )
      throw new Error("Scoreboard does not match the requested run.");
    const target = path.join(destination, filename);
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, contents);
  }
  return latest;
}

// Each source is an exported run or a bundle of runs; the first run is shown by default.
const runs = [];
for (const root of locals.length ? locals : [null])
  for (const latest of await pointers(root))
    runs.push(await copyRun(root, latest));
await mkdir(path.join(destination, "search"), { recursive: true });
await writeFile(
  path.join(destination, "search/latest.json"),
  JSON.stringify(runs[0], null, 2),
);
await writeFile(
  path.join(destination, "search/runs.json"),
  JSON.stringify(runs, null, 2),
);
console.log(
  "Loaded " +
    runs.map((run) => run.run_id).join(", ") +
    " from " +
    (locals.join(", ") || repo),
);
