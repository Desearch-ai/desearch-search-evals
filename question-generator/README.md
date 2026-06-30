# Question generator

Turns the last ~2 days of news into self-contained, web-answerable benchmark
questions. Each day it gathers fresh article URLs (RSS + news sitemaps), fetches
the full text, asks an LLM to invert each article into a few diverse
question + gold-answer pairs, then filters for leakage, length, near-duplicates,
and per-publisher balance.

It writes two files per run:

- `output/questions/<date>.jsonl` — the generated questions (`{id, difficulty, answer_type, question, source, date, lane}`)
- `output/golds/<date>.jsonl` — **private gold answers**, never uploaded, never committed

The daily HuggingFace push (via `run.sh`) re-emits the public rows source-free as
`{id, question, difficulty, start_date, end_date}`, so the set accumulates one file
per day and the source URL never leaves the machine.

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

cp .env.example .env        # default provider is chutes: set CHUTES_API_TOKEN (or LLM_PROVIDER=openai + OPENAI_API_KEY)

# small experiment (~150 questions, RSS only, no upload)
python generate_questions.py --articles 60 --target 150
```

Results land in `output/questions/<date>.jsonl` and `output/golds/<date>.jsonl`.

## Run on a server with pm2

The job is a **scheduled batch run** (the web lane runs twice a day for freshness),
not a long-lived daemon: pm2 starts it on a cron schedule, it runs to completion,
and stays "stopped" until the next run.

```bash
# 0. prerequisites: Node + pm2, Python 3.10+
npm install -g pm2

# 1. get the code and install Python deps (a venv keeps it isolated)
cd question-generator
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt

# 2. configure
cp .env.example .env
#    edit .env: set CHUTES_API_TOKEN (default provider), or LLM_PROVIDER=openai + OPENAI_API_KEY.
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
./run.sh                        # run today's batch by hand (merges into today's file)
pm2 restart question-generator  # force a run now
pm2 delete question-generator   # unregister
```

### Schedule

The schedule lives in `ecosystem.config.js` as `cron_restart` (default `0 11,23 * * *`
— 11:00 + 23:00 server time, twice a day; both runs merge into the same day's file).
Edit it, then `pm2 restart question-generator` to apply.

### Logs

pm2 captures stdout/stderr to its default location (`~/.pm2/logs/`); follow with
`pm2 logs question-generator`. To cap log size:

```bash
pm2 install pm2-logrotate
```

## Configuration (`.env`)

| Key | Required | Purpose |
| --- | --- | --- |
| `LLM_PROVIDER` | no | `chutes` (default) or `openai` — picks the gen/grade model |
| `CHUTES_API_TOKEN` | if chutes | Token for Chutes Qwen3 (the default provider) |
| `OPENAI_API_KEY` | if openai | OpenAI key; also enables embedding-based dedup |
| `HF_TOKEN` | no | Write-scoped token to push questions to HuggingFace |
| `HF_DATASET_REPO` | no | Target dataset, e.g. `you/your-dataset`. Set it (with `HF_TOKEN`) to enable the daily push; leave blank to stay local-only |
| `GEN_MODEL` | no | OpenAI model (default `gpt-4.1-nano`; `gpt-5*` runs as a reasoning model) |
| `FETCH_PROXY` | no | Outbound HTTP proxy for article fetches, e.g. `http://user:pass@host:port` — helps when outlets rate-limit one server IP |
| `PYTHON` | no | Python to run (default `python3`; point at your venv) |
| `ARTICLES` / `TARGET` / `LOOKBACK` | no | Run-size knobs read by `run.sh` (defaults `8000` / `5000` / `2`) |

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

## X (Twitter) lane

`generate_x_questions.py` harvests the **past 24h** of high-signal tweets from the
desearch Twitter search API (`GET https://api.desearch.ai/twitter`), inverts each
tweet into self-contained, X-answerable questions, and runs the same
filter → dedup → quality-grade → balance → save stages.

**Public output carries NO source.** Miners see the public dataset, so exposing
the source tweet would let them cache it instead of searching. Each row is the
locked schema `{id, question, difficulty, start_date, end_date}`; the date window
is anchored from the source tweet's own timestamp (the validator's date filter
scopes the search). The tweet URL/handle stays only in the **private** local gold
(`output/golds/`), never uploaded.

**LLM provider is pluggable.** `--provider chutes` (or `LLM_PROVIDER=chutes`)
routes gen+grade to Chutes Qwen3 with `enable_thinking` off (token from
`CHUTES_API_TOKEN`); `--provider openai` uses OpenAI / any OpenAI-compatible base
(`OPENAI_BASE_URL`, e.g. OpenRouter). Dedup falls back to embedding-free lexical
when no embedding backend is available (the default under Chutes).

```bash
# repo-root .env needs DESEARCH_API_KEY + (CHUTES_API_TOKEN or OPENAI_API_KEY).
python generate_x_questions.py --provider chutes --target 2000               # today, past 24h
python generate_x_questions.py --provider chutes --day 2026-06-10 --target 2000  # backfill one past day
python push_x_to_hf.py                                                        # -> HF desearch/dataset under x/
```

What it harvests is the registry in `x_sources.py`: `ACCOUNTS` (authoritative
handles, `from:<handle>`) + `TOPICS` (event keywords/hashtags with an engagement
floor — the high-yield half). A tweet quality gate (≥500 followers, ≥100 views,
no retweets/spam, ≥80 chars) mirrors SN13's spam filter so junk is never inverted.

**Daily run:** `run_x.sh` (pm2: `ecosystem_x.config.js`; set `X_PUSH=1` to push).
**30-day backfill:** `./backfill_x.sh 30` — one `x-<day>.jsonl` per day (~2000 each
→ ~50k+), then `python push_x_to_hf.py`.

Key flags: `--day` (backfill a past UTC day), `--window-days` (validator search
window, default 7), `--domains`, `--per-query`/`--max-tweets` (volume),
`--questions-per-tweet`, `--per-tweet`/`--per-author-frac` (diversity caps),
`--min-followers`/`--min-likes` (quality floor).
