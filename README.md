# AI Search Benchmark

An open benchmark for AI-search providers that scores them on what they actually return: do their answers respond to the question, are the cited sources relevant, and do those sources actually back the claims. Every score comes from an LLM judge reading the fetched pages, not from string-matching against a fixed answer key.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](#license)
[![🤗 Dataset](https://img.shields.io/badge/%F0%9F%A4%97%20Dataset-desearch%2Fdesearch--search--evals-ff9d00.svg)](https://huggingface.co/datasets/desearch/desearch-search-evals)

**[View the live leaderboard →](https://22.desearch.ai)**

It compares Desearch against GPT-5-mini, Perplexity sonar-pro, Tavily, and Exa on the same 250 questions, and re-runs every week so the numbers reflect how each provider answers _today_, not how it answered on a static test set months ago.

## Latest results (2026-07-15)

| #   | Provider             | Source relevance | Answer quality | Groundedness | Composite |
| --- | -------------------- | ---------------- | -------------- | ------------ | --------- |
| 1   | Desearch             | 84.4%            | 90.4%          | 68.8%        | **81.5%** |
| 2   | Exa                  | 85.5%            | 90.0%          | 56.3%        | **78.1%** |
| 3   | GPT-5-mini           | 76.8%            | 88.8%          | 62.0%        | **75.9%** |
| 4   | Tavily               | 73.4%            | 86.8%          | 59.0%        | **73.1%** |
| 5   | Perplexity sonar-pro | 67.8%            | 88.4%          | 49.2%        | **68.4%** |

250 same-day news questions — the hardest regime for groundedness, since every cited page is hours old. Desearch answered at a 5.5s median (9.7s p90), down from 8.3s in the previous run. The [live leaderboard](https://22.desearch.ai) shows the current week and lets you expand any question to compare each provider's answer, sources, and the judge's verdicts side by side. These numbers move week to week as the question set refreshes.

## Why weekly, live questions

Most published search benchmarks are fixed `(question, gold_answer)` sets released once. Two problems follow: providers can memorize them, and a frozen answer key can't tell whether a provider actually searched or just recited training data.

This benchmark uses questions phrased to stay valid while their answers move. A fresh set runs every week, so:

- **There's no answer key to train on.** The right answer this week isn't the right answer next week.
- **You can see real search working (or not).** Because grading reads the cited pages, a provider that skips the web and answers from memory gets caught.
- **Trends show up over time.** Each run is dated and kept, so you can watch a provider improve or regress instead of trusting a single snapshot.

## What gets measured

Each question is scored by three independent judge-graded evaluators. A provider has to do well on all three to rank well.

**Source relevance (40%)**: for each cited URL, the judge fetches the page and rates how relevant it is to the question. This catches lazy citations that are on-topic but useless.

**Answer quality (30%)**: the judge reads the question and the answer and decides whether it actually responds: a direct answer to an answerable question, or an honest decline to a genuinely unanswerable one. Dodging, refusing answerable questions, or confidently making things up all score zero.

**Groundedness (30%)**: for each factual claim in the answer, the judge fetches the cited page and decides whether the page actually supports the claim. This catches hallucinated citations: an invented answer with a real-looking link doesn't pass, because the judge reads the link.

```
composite = 0.40 * source_relevance + 0.30 * answer_quality + 0.30 * groundedness
```

Full grading details, including the answer-quality verdict rubric, are in [`evaluators/`](./evaluators).

The judge is `gpt-5.4-mini`, pinned with deterministic settings in [`evaluators/common.py`](./evaluators/common.py) so the same answer always grades the same way.

## Use the data

Every run is published to the HuggingFace dataset, accumulating week over week:

**[`desearch/desearch-search-evals`](https://huggingface.co/datasets/desearch/desearch-search-evals)** (CC-BY-4.0)

```python
from datasets import load_dataset

# Latest run: one row per (question, provider) with answers, sources, and scores
ds = load_dataset("desearch/desearch-search-evals", "results", split="latest")
```

Each results row has the question, provider, answer, cited sources, the three evaluator scores, and the answer-quality verdict. The per-run scoreboards and full question sets are in the same repo.

## Run it yourself

```bash
pip install -r requirements.txt
cp .env.example .env          # add your API keys

python3 scripts/weekly_run.py
```

This calls every provider, grades them, writes the run, and (with an `HF_TOKEN` set) uploads it to HuggingFace. Useful flags: `--limit 5` for a quick smoke test, `--providers desearch perplexity` to run a subset, `--no-upload` to keep a run local.

Keys go in `.env`:

```
DESEARCH_API_KEY=
OPENAI_API_KEY=          # GPT-5-mini provider + judge model
OPENROUTER_API_KEY=      # Perplexity sonar-pro
TAVILY_API_KEY=
EXA_API_KEY=
HF_TOKEN=                # optional, write-scoped, to upload runs
```

## Add your own provider

Any web-search system can be graded by the same pipeline. Add a module in [`providers/`](./providers) with an async `query()` returning the shared shape, then register it. The evaluators never special-case a provider; behavior is judged from the answer and sources alone. See [`providers/desearch.py`](./providers/desearch.py) for the reference implementation.

```python
async def query(question: str) -> dict:
    return {
        "model": "my-searcher",
        "answer": "...markdown with [N](url) inline citations...",
        "sources": [{"url": "...", "title": "...", "snippet": "..."}],
        "elapsed_seconds": 12.4,
        "raw": {"web_search_called": True},
    }
```

Leave `sources` empty when the model honestly declines an unanswerable question, so it isn't graded on irrelevant search noise.

## How it fits together

```
questions  ->  providers  ->  3 evaluators  ->  aggregator  ->  results + scoreboard
                                                                      |
                                                          HuggingFace dataset + live leaderboard
```

- [`providers/`](./providers): one module per provider, unified output shape
- [`evaluators/`](./evaluators): source relevance, answer quality, groundedness, and the composite aggregator
- [`scripts/weekly_run.py`](./scripts/weekly_run.py): runs a full week end to end
- [`scripts/upload_to_hf.py`](./scripts/upload_to_hf.py): publishes a run to HuggingFace
- [`ui/`](./ui): the live leaderboard

Question sets and run outputs live on HuggingFace rather than in git, so the repo stays small as runs accumulate. `weekly_run.py` pulls the question set from HuggingFace when it isn't present locally.

## License

Code is MIT. The dataset on HuggingFace (questions and results) is CC-BY-4.0.
