# AI Search Benchmark UI

Single-page React + Vite app for the benchmark. Reads the latest
`runs/<date>/` outputs + per-evaluator grades + scoreboard.

## Quick start

```bash
cd ui
npm install              # one-time
npm run dev              # http://localhost:5173 (or 5174 if in use)
npm run build            # static build to dist/
```

## Updating data after a new benchmark run

```bash
# from the repo root — weekly_run.py runs the benchmark and refreshes the UI:
python3 scripts/weekly_run.py
```

To re-point the UI at a specific run without re-running:

```bash
python3 ui/refresh-data.py --run-dir runs/2026-05-31
```

`refresh-data.py` picks the most recent `runs/<date>/` by default. Any
`runs/` subdirectory whose name starts with `_` is ignored.

## Layout

```
ui/
  public/data/                  # JSON the UI fetches at runtime
    manifest.json               # which run is loaded + headline line
    questions.json              # the run's question set
    {desearch,gpt5mini,...}.json
    grades_source_relevance.json
    grades_answer_quality.json
    grades_groundedness.json
    scoreboard.json             # aggregator output
  src/
    App.tsx                     # composite scorecard + filterable question list
    data.ts                     # fetch + normalize, splice in per-Q grades
    providers.ts                # provider config (colors, ordering)
    types.ts
    components/
      Scorecard.tsx             # 3-evaluator + composite table with explainers
      MethodologyCard.tsx       # expandable "what each metric means" panel
      Filters.tsx               # search bar + category pills
      QuestionCard.tsx          # collapsible row, evaluator pills per provider
      ProviderColumn.tsx        # one provider's answer, sources, scores
      AnswerText.tsx            # citation-aware text renderer
  refresh-data.py
```
