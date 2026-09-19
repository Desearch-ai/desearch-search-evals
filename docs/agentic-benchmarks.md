# Agentic benchmarks

SimpleQA Verified, SealQA and FRAMES ship reference URLs, so they are scored by retrieval: did
the provider return the page, no model involved. BrowseComp ships only a question and an answer.
Finding the page is the task, so the only thing to score is the answer an agent submits after
searching.

That makes the number a property of search plus agent plus model, which is why everything except
the search API is held fixed.

## The harness

```
scripts/run_agent_benchmark.py
  agents/loop.py    one model, N turns, three tools
  agents/tools.py   web_search (the provider under test) and web_fetch, both cached on disk
  agents/grade.py   exact answer for BrowseComp, item F1 for answer-set benchmarks
```

Held constant across providers: the model (GPT-5.6 Luna, medium reasoning), 25 turns, 10 results
per search, the same page fetcher, and the same judge. The agent calls `finish` to submit; running
out of turns submits nothing and scores zero, which is how Artificial Analysis scores it too.

Benchmark copies are filtered out of search results and fetched pages before the model sees them,
so a leaked dataset mirror cannot answer the question.

## Running one

```bash
python -m scripts.prepare_agent_questions --benchmark browsecomp --out runs/browsecomp/dataset/questions100.jsonl --sample 100
python -m scripts.run_agent_benchmark \
  --questions runs/browsecomp/dataset/questions100.jsonl \
  --config configs/agent_browsecomp.json \
  --run runs/browsecomp/run100 --grader exact --concurrency 6
```

Answers are appended to `answers.jsonl` as each task lands, and a rerun skips what is already
there, so an interrupted run resumes without paying twice. Searches are cached per provider and
query, so repeated runs cost only model tokens.

- `--regrade` re-scores stored answers after a grader change, with no agent runs.
- `--report-only` rebuilds `report.json` from stored answers.

## Reading the report

| Field | Meaning |
|---|---|
| `score` | Share of questions answered correctly |
| `searches_per_task` | How many searches the agent needed |
| `turns_per_task` | Model turns used, out of the budget |
| `model_cost_per_task_usd` | Agent tokens, the dominant cost |
| `search_cost_per_task_usd` | Reported provider spend, or its list price where it reports none |
| `ungraded` | Answers the judge could not score, which are not counted as wrong |

## Publishing

```bash
python -m scripts.upload_to_hf --run runs/browsecomp/run100 --kind agent \
  --run-id browsecomp --label BrowseComp --note "..." --dry-run
```

`--kind agent` writes the same file layout the UI already reads, with `kind: "agent"` in the
scoreboard so the UI shows answer scores instead of gold URL coverage. Use `--upload` only when
publishing to Hugging Face is intended.

## Caveats worth repeating in any writeup

- A 100-question sample moves 1 point per question. Vendor tables often use 50, which moves 2.
- BrowseComp answers are single short strings, so the judge is close to string matching. Twenty
  gradings were hand-checked on the first 100-question run and all twenty were right.
- Our sample is drawn at random from all 1,266 questions. Artificial Analysis uses a private hard
  200-question subset, so their numbers are not directly comparable to ours even at equal size.
