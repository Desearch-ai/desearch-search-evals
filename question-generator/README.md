# Question generator

Turns the last ~2 days of news into self-contained, web-answerable benchmark
questions. Each day it gathers fresh article URLs (RSS + news sitemaps), fetches
the full text, asks an LLM to invert each article into a few diverse
question + gold-answer pairs, then filters for leakage, length, near-duplicates,
and per-publisher balance.

It writes two files per run:

- `output/questions/<date>.jsonl` — **public**: `{id, difficulty, answer_type, question, source, date, lane}`
- `output/golds/<date>.jsonl` — **private gold answers**, never uploaded, never committed

Optionally it pushes only the public questions to a HuggingFace dataset so the
set accumulates one file per day.

## Files

| File | What it is |
| --- | --- |
| `generate_questions.py` | Main script — the full pipeline + CLI |
| `crawler.py` | Gather article URLs (RSS + sitemaps) and fetch text |
| `sources.py` | The source registries: `FEEDS` (RSS) and `SITEMAPS` |
| `utils.py` | `.env` loader + dataset-leakage guard |
| `run.sh` | Daily wrapper pm2 fires (reads run-knobs from `.env`, idempotent) |
| `ecosystem.config.js` | pm2 process file (daily cron) |
| `.env.example` | Copy to `.env` and fill in |
| `requirements.txt` | Python dependencies |

## Quick start (local)

```bash
cd question-generator
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # then put your OPENAI_API_KEY in .env

# small experiment (~150 questions, RSS only, no upload)
python generate_questions.py --articles 60 --target 150
```

Results land in `output/questions/<date>.jsonl` and `output/golds/<date>.jsonl`.

## Run on a server with pm2

The job is a **daily batch run**, not a long-lived daemon: pm2 starts it on a
cron schedule, it runs to completion, and stays "stopped" until the next day.

```bash
# 0. prerequisites: Node + pm2, Python 3.10+
npm install -g pm2

# 1. get the code and install Python deps (a venv keeps it isolated)
cd question-generator
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt

# 2. configure
cp .env.example .env
#    edit .env: set OPENAI_API_KEY (required).
#    to publish daily, also set HF_TOKEN + HF_DATASET_REPO.
#    to use the venv's python, set PYTHON=./.venv/bin/python in .env

# 3. register with pm2 and start the daily schedule
pm2 start ecosystem.config.js

# 4. survive reboots
pm2 save
pm2 startup        # run the command it prints (sets up the boot service)
```

Useful afterwards:

```bash
pm2 logs question-generator     # follow output
pm2 list                        # status (shows "stopped" between daily runs — expected)
pm2 trigger question-generator  # not supported for cron; to run now, see below
./run.sh                        # run today's batch by hand (respects the skip-if-exists guard)
pm2 restart question-generator  # force a run now
pm2 delete question-generator   # unregister
```

### Schedule

The schedule lives in `ecosystem.config.js` as `cron_restart` (default `0 6 * * *`
— 06:00 server time, which is UTC on most servers). Edit it, then
`pm2 restart question-generator` (or `pm2 reload ecosystem.config.js`) to apply.

### Logs

pm2 captures stdout/stderr to its default location (`~/.pm2/logs/`); follow with
`pm2 logs question-generator`. To cap log size:

```bash
pm2 install pm2-logrotate
```

## Configuration (`.env`)

| Key | Required | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | yes | Question generation + embedding dedup |
| `HF_TOKEN` | no | Write-scoped token to push questions to HuggingFace |
| `HF_DATASET_REPO` | no | Target dataset, e.g. `you/your-dataset`. Set it (with `HF_TOKEN`) to enable the daily push; leave blank to stay local-only |
| `GEN_MODEL` | no | OpenAI model (default `gpt-4.1-nano`; `gpt-5*` runs as a reasoning model) |
| `FETCH_PROXY` | no | Outbound HTTP proxy for article fetches, e.g. `http://user:pass@host:port` — helps when outlets rate-limit one server IP |
| `PYTHON` | no | Python to run (default `python3`; point at your venv) |
| `ARTICLES` / `TARGET` / `LOOKBACK` | no | Daily-run size knobs read by `run.sh` (defaults `5000` / `1100` / `2`) |

`run.sh` pushes to HuggingFace only when `HF_DATASET_REPO` is set; otherwise the
run is local-only. Either way the private golds never leave the machine, and a
failed push never loses the local batch.

## Tuning the run

`generate_questions.py` takes more flags than `run.sh` sets — see
`python generate_questions.py --help`. Common ones:

- `--articles N` how many articles to fetch text for (more = more questions, slower)
- `--target N` final question count
- `--sitemaps` also crawl news sitemaps (much more volume, slower discovery)
- `--lookback D` days back to consider recent
- `--append` merge into today's existing file instead of overwriting (top up toward target)
- `--hf --repo you/your-dataset` push the public questions

Add or drop outlets by editing `sources.py`.
