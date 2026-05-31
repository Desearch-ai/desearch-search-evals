# AI Search Benchmark

**An open-source benchmark that grades any web-search system on behavior — not string-matching — and ships a live interactive scoreboard.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](#license)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![UI: React + Vite](https://img.shields.io/badge/UI-React%20%2B%20Vite-61dafb.svg)](./ui)
[![🤗 Dataset](https://img.shields.io/badge/%F0%9F%A4%97%20Dataset-desearch%2Fdesearch--search--evals-ff9d00.svg)](https://huggingface.co/datasets/desearch/desearch-search-evals)

Compares **Desearch** against the major AI-search APIs — GPT-5-mini, Perplexity sonar-pro, Tavily, and Exa — on the same 250 questions, with grading honest enough that the result can't be gamed.

### What makes this different

Other search-eval repos are CLI-only, graded by string similarity against a fixed gold answer that providers can memorize. This one is built the opposite way:

- **A live interactive web UI** — a React + Vite scoreboard with charts and per-question, side-by-side provider comparison. Competitors ship none.
- **Behavioral judge-graded evaluation** — every score is a judge LLM reading fetched page content. Three evaluators (groundedness, source relevance, answer quality) combine into one composite. No signal comes from a provider's self-reported flag.
- **Contamination-resistant live data** — durable, date-stamped, weekly-refreshed question sets whose answers shift week to week. There is no static answer key to train on.

---

## Interactive leaderboard

The headline feature. A single-page React + Vite app reads the latest run and renders:

- **Composite scorecard** — every provider across all three evaluators plus the weighted composite, with inline explainers for what each metric means.
- **Per-question drill-down** — expand any question to see each provider's answer, its cited sources, and the judge's evaluator verdicts side by side.
- **Filters** — a search bar to slice the question list by text or question id.

```bash
cd ui
npm install              # one-time
npm run dev              # http://localhost:5173
```

<!-- screenshot: docs/ui.png -->

The UI reads the latest run from HuggingFace at runtime (with a small committed sample as an offline fallback), so the scoreboard always reflects current results rather than a number baked into this README.

---

## Quick start

```bash
# 1. Install
pip install -r requirements.txt
(cd ui && npm install)

# 2. Configure
cp .env.example .env             # then fill in your API keys

# 3. Run the benchmark — providers, evaluators, aggregator, UI refresh, HF upload
python3 scripts/weekly_run.py

# 4. View the interactive leaderboard
cd ui && npm run dev             # http://localhost:5173
```

`.env` holds one key per provider plus the judge key:

```
DESEARCH_API_KEY=
OPENAI_API_KEY=          # GPT-5-mini provider + judge model
OPENROUTER_API_KEY=      # Perplexity sonar-pro
TAVILY_API_KEY=
EXA_API_KEY=
HF_TOKEN=                # optional — write-scoped, to upload runs to HuggingFace
```

Quick smoke test on the first few questions: `python3 scripts/weekly_run.py --limit 5`.

---

## How it works

Every question runs through three independent judge-graded evaluators. A provider has to do well on **all three** to win the composite — and no score comes from a provider self-reported flag, because a provider can lie. Every signal is the judge LLM reading content.

### 1. Groundedness — _do your sources actually back your claims?_

For each factual claim in the answer, the eval fetches the cited page and asks the judge:

> _Is this claim supported by the page's actual content?_ SUPPORTED / CONTRADICTED / UNSUPPORTED

Per-question score is the fraction SUPPORTED; mean across questions. A claim with multiple citations is SUPPORTED if **any** cited page supports it (best-of).

**Catches:** hallucinated citations. A provider that invented an answer and bolted on a Wikipedia link doesn't pass — the judge reads the cited page and confirms or denies the claim. This is the proof that a real-time search actually happened.

Code: [`evaluators/groundedness.py`](./evaluators/groundedness.py)

### 2. Source relevance — _are your sources on-topic?_

For each URL the provider cited, the eval fetches the page and asks the judge:

> _Is this page relevant to the question?_ YES / MAYBE / NO

Scored 1.0 / 0.5 / 0.0; mean across all cited URLs.

**Catches:** lazy citation. Citing the Wikipedia article on "Tennis" for "who is ATP #1?" is on-topic-but-useless — MAYBE or NO.

Code: [`evaluators/source_relevance.py`](./evaluators/source_relevance.py)

### 3. Answer quality — _did you actually answer my question?_

The judge reads the question + the answer text and picks exactly one:

| Verdict                 | Meaning                                                                                                                                      | Score |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- | ----- |
| **RESPONSIVE**          | Answerable question, answer addresses it directly. The shape matches (name for "who", number for "how many").                                | 1.0   |
| **APPROPRIATE_DECLINE** | Genuinely unanswerable question (anachronism, future event, mythological, undefined math) and the answer cleanly declines.                   | 1.0   |
| **EVASIVE**             | Answerable question, but the answer dodges — restates it, says "sources don't contain this", hedges so nothing concrete is said.             | 0.0   |
| **WRONG_DECLINE**       | Answerable question, but the answer declined as if it were unanswerable ("I don't have current info").                                       | 0.0   |
| **HALLUCINATED**        | Unanswerable question, but the answer confidently asserted a fictional fact (named a winner of the 2099 World Cup, quoted Einstein on LLMs). | 0.0   |

**Catches:** evasion, refusing to answer easy questions, and confident fabrication on unanswerables — the failure mode LLM-based search gets the most criticism for.

### Composite

```
composite = 0.40 · groundedness        (load-bearing — proves real search)
          + 0.35 · source_relevance    (retrieval quality)
          + 0.25 · answer_quality      (did you respond to me?)
```

Weights live in [`evaluators/aggregator.py`](./evaluators/aggregator.py). Missing evaluators are skipped and weights renormalized, so partial runs still produce a scoreboard.

The composite is what the leaderboard ranks on. An illustrative shape (no real numbers — the live UI shows current results):

| Provider   | Groundedness | Source relevance | Answer quality | **Composite** |
| ---------- | ------------ | ---------------- | -------------- | ------------- |
| desearch   | —            | —                | —              | **—**         |
| gpt5mini   | —            | —                | —              | —             |
| perplexity | —            | —                | —              | —             |
| tavily     | —            | —                | —              | —             |
| exa        | —            | —                | —              | —             |

### Judge model

`gpt-5.4-mini` with `reasoning_effort=none` for deterministic grading. Pinned in [`evaluators/common.py`](./evaluators/common.py).

| Repo                      | Judge                                                        |
| ------------------------- | ------------------------------------------------------------ |
| Perplexity `search_evals` | `gpt-4.1`                                                    |
| Tavily `search_evals`     | `gpt-4.1` + 3rd-party QuotientAI for doc relevance           |
| Exa benchmarks            | `gpt-5.4`                                                    |
| **This repo**             | **`gpt-5.4-mini`** (same gen as Exa, cheaper, deterministic) |

---

## Why not static datasets

The published benchmarks from Perplexity, Tavily, Exa, and OpenAI share three structural issues that this benchmark is designed to defeat:

1. **Static datasets are memorizable.** SimpleQA / FRAMES / BrowseComp are fixed `(question, gold_answer)` pairs released years ago — providers can train on them and replay answers without searching.
2. **String-similarity grading is blind to behavior.** It doesn't catch when a provider skips web search and answers from training data.
3. **No source verification.** A cited URL that contradicts the claim still grades CORRECT against the reference string.

The answer here is **live data + behavioral grading**. Questions are phrased durably — "current", "latest", "most recent" — so each one stays valid while its answer moves week to week (this week's "current US 30-year fixed mortgage rate" is not last week's). There is no answer key to memorize, and grading reads fetched pages instead of comparing strings. The `evaluators/common.py` contamination filter additionally strips any search hit that points back at a known benchmark dataset (SimpleQA, FRAMES, BrowseComp, HuggingFace dataset cards, …) so a provider can't score by citing the leaderboard itself.

---

## Weekly runs

Questions are 250 per run, one JSON object per line:

```json
{
  "difficulty": "easy",
  "question": "What phrase did Japan's defense minister reject in response to accusations of rising militarism?"
}
```

`difficulty` is `easy`, `medium`, or `hard`. To track providers over time, copy the latest set to a new date, edit it, and run:

```bash
python3 scripts/weekly_run.py --date 2026-05-31
```

It runs the providers, grades them with all three evaluators, refreshes the UI's offline sample, and **uploads the run to HuggingFace**:

```
results/<date>.jsonl            one line per (question, provider) with grades
results/<date>.scoreboard.json  per-provider composite for that run
```

These files are assembled locally, then pushed to the dataset repo on HuggingFace (see below) — they are **not** committed to git. Per-run provider and grade files land in `runs/<date>/`, which is gitignored. Useful flags: `--providers desearch perplexity` to run a subset, `--limit N` for a quick test, `--skip-run` to re-assemble an existing `runs/<date>/` without re-calling the APIs, `--no-upload` to skip the HuggingFace push.

## Data hosting

The dataset (question sets) and run outputs (results + scoreboards) live in a HuggingFace **dataset** repo, not in git — so the code repo stays small while runs accumulate weekly:

**[`desearch/desearch-search-evals`](https://huggingface.co/datasets/desearch/desearch-search-evals)** · CC-BY-4.0

```
questions/<date>.jsonl     question_id, difficulty, question
results/<date>.jsonl       per (question × provider): answer, sources, three evaluator scores
scoreboards/<date>.json    per-provider composite for the run
latest.json                pointer to the newest date (the UI reads this first)
```

The leaderboard UI fetches the latest run from HuggingFace at runtime, with a small committed sample under `ui/public/data/` as an offline fallback. Uploads happen via `scripts/upload_to_hf.py` (folded into `weekly_run.py`), which reads a write-scoped `HF_TOKEN` from `.env`; without a token the upload is a no-op and runs stay local. Question sets are gitignored locally — `weekly_run.py` pulls the set from HuggingFace when a local `questions/<date>.jsonl` isn't present.

Load the latest run with the `datasets` library:

```python
from datasets import load_dataset
ds = load_dataset("desearch/desearch-search-evals", "results", split="latest")
```

---

## Add your own provider

Any web-search system can be graded by the same pipeline. Drop a module in `providers/` exposing an async `query()` that returns the unified shape — the evaluators never special-case anyone. See [`providers/desearch.py`](./providers/desearch.py) for the canonical implementation.

```python
async def query(question: str) -> dict:
    ...
    return {
        "model": "my-searcher",
        "answer": "...markdown with [N](url) inline citations...",
        "sources": [
            {"url": "...", "title": "...", "snippet": "..."},
            # ...
        ],
        "elapsed_seconds": 12.4,
        "raw": {
            "web_search_called": True,
            "declined": False,
        },
    }
```

Then register it in `PROVIDERS` in [`providers/run_providers.py`](./providers/run_providers.py) and add it to the `PROVIDERS` list in [`scripts/weekly_run.py`](./scripts/weekly_run.py).

Notes:

- `sources[]` should be **empty** when the model honestly declined an unanswerable question, so source-relevance and groundedness skip it instead of grading irrelevant SERP noise.
- The `declined` boolean in `raw` is informational — the evaluators never read it. Behavior is judged from the answer and sources alone.

Five providers ship today: `desearch`, `gpt5mini` (GPT-5-mini), `perplexity` (sonar-pro via OpenRouter), `tavily`, `exa`.

---

## Repo layout

```
desearch-search-evals/
├── providers/                # one module per provider, unified output shape
│   ├── desearch.py             Desearch AI Search API (canonical shape)
│   ├── gpt5mini.py             OpenAI Responses API + web_search tool
│   ├── perplexity.py           sonar-pro via OpenRouter
│   ├── tavily.py               Tavily search + answer synthesis
│   ├── exa.py                  Exa /answer endpoint
│   ├── common.py               shared key loading
│   └── run_providers.py        parallel runner → runs/<date>/<provider>.json
│
├── evaluators/               # 3 evaluators + aggregator
│   ├── common.py               judge wrapper, page-fetch cache, contamination filter
│   ├── groundedness.py
│   ├── source_relevance.py
│   ├── answer_quality.py
│   └── aggregator.py           composite weights → runs/<date>/scoreboard.json
│
├── questions/                # working dir for the question set (jsonl gitignored → HF)
│   └── README.md
│
├── results/                  # run snapshots → pushed to HF, gitignored
│   ├── <date>.jsonl            per (question, provider) with grades
│   └── <date>.scoreboard.json  per-provider composite
│
├── scripts/
│   ├── weekly_run.py         # providers → 3 evaluators → aggregator → results + UI + HF upload
│   └── upload_to_hf.py       # push questions + results + scoreboard to the HF dataset
├── ui/                       # React + Vite scoreboard + per-question drill-down
└── runs/                     # created at run time, gitignored working dir
```

---

## License

Code is MIT. The dataset on HuggingFace (questions + results) is CC-BY-4.0.
