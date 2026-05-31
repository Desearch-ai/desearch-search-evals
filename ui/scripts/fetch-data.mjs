// Bake the latest run from the HF dataset into public/data/ at build time so the
// browser reads same-origin only. On any failure, the committed sample is kept and
// the build still succeeds.

import { mkdir, writeFile, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DATA_DIR = path.join(HERE, "..", "public", "data");
const REPO = process.env.HF_DATASET_REPO || "desearch/desearch-search-evals";
const BASE = `https://huggingface.co/datasets/${REPO}/resolve/main`;

async function get(file) {
  const res = await fetch(`${BASE}/${file}`);
  if (!res.ok) throw new Error(`${file}: HTTP ${res.status}`);
  return res.text();
}

try {
  const latest = JSON.parse(await get("latest.json"));
  const date = latest.date;
  if (!date) throw new Error("latest.json has no date");
  const results = await get(`results/${date}.jsonl`);
  const scoreboard = await get(`scoreboards/${date}.json`);
  latest.isFallback = false;

  await rm(DATA_DIR, { recursive: true, force: true });
  await mkdir(path.join(DATA_DIR, "results"), { recursive: true });
  await mkdir(path.join(DATA_DIR, "scoreboards"), { recursive: true });
  await writeFile(path.join(DATA_DIR, "latest.json"), JSON.stringify(latest, null, 2));
  await writeFile(path.join(DATA_DIR, "results", `${date}.jsonl`), results);
  await writeFile(path.join(DATA_DIR, "scoreboards", `${date}.json`), scoreboard);

  console.log(`Baked run ${date} from ${REPO} (${(results.length / 1e6).toFixed(1)} MB)`);
} catch (err) {
  console.warn(`fetch-data: ${err.message}. Using committed sample.`);
}
