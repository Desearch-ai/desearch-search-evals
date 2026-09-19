# Desearch Search Evals

Compare ranked search results from Desearch, Exa, Parallel, Perplexity and Tavily on
questions with verified answers. A question counts as answered when at least one of a
provider's top results states the verified answer, whichever publisher it comes from.

```text
Freeze questions with verified answers
            ↓
Search every provider profile with the same questions
            ↓
Fetch result pages and grade page and returned text separately
            ↓
Report hit@1/5/10, then calibrate the judge against human labels
            ↓
Export results for the UI and, separately, publish
```

## Setup

```bash
python -m pip install -r requirements.txt
cp .env.example .env
```

Set `EXA_API_KEY`, `PARALLEL_API_KEY`, `TAVILY_API_KEY` and `OPENROUTER_API_KEY` (Perplexity search and the judge). Set `HF_TOKEN` for publishing.

## 1. Freeze questions

### Public benchmark: SimpleQA Verified

```bash
python -m scripts.prepare_benchmark \
  --benchmark simpleqa_verified \
  --source simpleqa_verified.csv \
  --original-simpleqa simple_qa_test_set.csv \
  --output runs/simpleqa-verified/dataset \
  --evaluation-count 463 --calibration-count 60
```

The importer keeps the official wording and answers, including tolerances such as
`(or collier)` and `(acceptable range: anything between 198 and 202)`. It uses only the
held-out partition for evaluation and never filters questions by index coverage.

### News: one question per event

```bash
python -m scripts.prepare_news \
  --articles runs/news/articles.jsonl \
  --out runs/news/dataset \
  --start-date 2026-09-09 --end-date 2026-09-15 \
  --target 500 --reserve 250 \
  --exclude runs/news/pilot/questions.jsonl runs/news/pilot/rejected.jsonl
```

Articles carry an `event_id` grouping coverage of the same real-world event and an
`owner` so sister sites of one publisher count once. The generator asks about each
event's central fact, keeps a question only when articles from at least two
independent owners state its answer, and has a second model verify it. `--exclude`
keeps events used for pilots or calibration out of the evaluation set. See
[news sampling](docs/news-sampling.md) for what these questions can and cannot show.

## 2. Run searches and grading

```bash
python -m scripts.run_benchmark \
  --questions runs/news/dataset/questions.jsonl \
  --config configs/search.json \
  --run runs/news/run \
  --import-local runs/news/desearch_results.jsonl \
  --concurrency 12
```

Desearch results are produced by the Desearch index and imported as frozen files; the
[input formats](questions/README.md) describe them. The command writes:

- `searches/PROFILE/QUESTION.json`: every provider response, including failures.
- `evaluation/results.json`: per result, the verdict for the fetched page and for the
  returned text, with the judged text and highlighted sections.
- `evaluation/report.md` and `report.json`: scores and operational counts.

Run the same command to resume. Searches, page fetches and judge calls are cached.
Use a new run directory when questions or provider profiles change.

## 3. Calibrate the judge

Scores are not published until the judge has been checked against human labels drawn
from the same run.

```bash
python -m scripts.calibration sample --evaluation runs/news/run/evaluation --out runs/news/labels/cases.jsonl --n 200
python -m scripts.calibration prelabel --cases runs/news/labels/cases.jsonl --model anthropic/claude-sonnet-4.5
# a person sets human_label to "yes" or "no" and fills labeler for every case
python -m scripts.calibration score --cases runs/news/labels/cases.jsonl --out runs/news/run/evaluation/calibration.json
python -m evaluators.source_report --run runs/news/run/evaluation
```

Cases are sampled across four groups (accepted hits, answers rejected by the quote
check, rejections despite a string match, rejections without one) and written without
the judge's verdict. Model pre-labels are suggestions for the reviewer, never labels.
The report then states the judge's precision on counted hits and the share of rejected
results that did state the answer, weighted by group size.

## 4. Export, view and publish

```bash
python -m scripts.upload_to_hf --run runs/news/run --dry-run
cd ui && npm ci && npm run data:fetch -- ../runs/news/run/public-export && npm run dev
```

Publishing is a separate command:

```bash
python -m scripts.upload_to_hf --run runs/news/run --repo desearch/desearch-search-evals --upload
```

It uploads questions, graded results with their evidence, scores and the report under
`search/runs/RUN_ID/`, and updates `search/latest.json`, `search/leaderboard.md` and the
dataset README in one commit. Raw API traces, credentials and page caches stay local.

To publish several benchmarks together, bundle their exports. The dataset then holds exactly
those runs, a `search/runs.json` index (the first run is shown by default) and one README;
`--upload` replaces everything else in the repository except `.gitattributes`:

```bash
python -m scripts.upload_to_hf --bundle runs/simpleqa-verified/run/public-export \
  runs/sealqa/run/public-export runs/frames/run/public-export \
  runs/browsecomp/final/public-export
cd ui && npm run data:fetch -- ../runs/hf-bundle
python -m scripts.upload_to_hf --bundle ... --upload
```

## Providers and modes

| Provider | Transport | Modes |
| --- | --- | --- |
| Desearch | Frozen local results | fast, standard |
| Exa | Direct API (`/search`, highlights) | fast, auto |
| Parallel | Direct API (`/v1/search`, excerpts) | fast, basic |
| Perplexity | OpenRouter Search | one search profile |
| Tavily | Direct API | fast, basic |

Every profile gets the same question text, returns up to 10 results and sends no date
filters. Deep-research and generated-answer products are excluded.

## Scores

| Metric | Meaning |
| --- | --- |
| Page states answer @k | One of the top k result pages states the verified answer |
| Returned text states answer @k | One of the top k returned snippets states it |
| Page MRR@10 | Reciprocal rank of the first page that states it |

Any publisher counts and repeated coverage adds nothing. Failed and empty searches score
zero and stay in the denominator. Pages that copy a benchmark's own questions earn no
credit. These scores measure whether results carry the answer, not recall over every
relevant page on the web. See the [evaluation protocol](docs/source-evaluation.md).

## Agentic benchmarks

BrowseComp ships no reference URLs, so retrieval cannot be scored: finding the page is the
task. Those benchmarks run an agent that searches, reads pages and submits one answer, with
the model, turn budget, page fetcher and judge identical across providers, so the search API
is the only variable. See [agentic benchmarks](docs/agentic-benchmarks.md).

```bash
python -m scripts.run_agent_benchmark --questions runs/browsecomp/dataset/questions100.jsonl \
  --config configs/agent_browsecomp.json --run runs/browsecomp/run100 --grader exact
```

## Structure

```text
providers/          API calls, mode validation, local imports
evaluators/         Answer matching, judge prompt, grading, page fetching, reports
scripts/            Question preparation, runner, calibration, publishing
configs/            Provider profiles
ui/                 React results dashboard and evidence viewer
question-generator/ Additional news preparation tools
lowtier/            News collection tools
runs/               Local datasets, responses, caches, reports and exports (ignored)
```

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

## License

Code: MIT. Dataset licensing follows the source dataset.
