# Search benchmark UI

React and Vite UI for provider scores, question results, exact source highlights,
and evaluator decisions.

- Overview: sortable provider scores, top-1/5/10 controls, and coverage charts.
- Evidence explorer: searchable questions, provider comparisons, and source passages.
- Methodology: scoring definitions and evaluation details.

The light theme follows the Desearch landing page and console. Schibsted Grotesk
and Geist Mono are served locally; their licenses are in `public/fonts/`.

## Run locally

Use Node.js 22.13 or newer.

```bash
cd ui
npm ci
npm run data:fetch
npm run dev
```

The data command downloads the latest published run from
`desearch/desearch-search-evals` on Hugging Face. Set `HF_DATASET_REPO` to use
another dataset and `HF_TOKEN` if authentication is required.

To inspect an unpublished local run, export it first from the repository root:

```bash
python -m scripts.upload_to_hf --run runs/my-run --dry-run
cd ui
npm run data:fetch -- ../runs/my-run/public-export
npm run dev
```

Both paths load the same questions, results, and scoreboard into `public/data/`.
A missing or invalid run produces an error; the UI never substitutes sample scores.

## Build and check

```bash
npm test
npm run lint
npm run build
npm run preview
```

The build uses the data already loaded and makes no API calls. Deploy `dist/`
to a static host. Refresh the data before building to publish a new result set.
Generated data and builds are excluded from Git.

The UI displays exported scores without recomputing them. Page and returned-text
results remain separate. Source highlights preserve the original text and offsets.
