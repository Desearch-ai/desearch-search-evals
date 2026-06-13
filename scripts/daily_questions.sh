#!/bin/bash
# Daily question gathering: turn the last 24h of news into diverse benchmark
# questions, save locally (output/), and push the questions (NOT the private
# golds) to the HuggingFace dataset so it accumulates one date file per day.
#
# Enable the daily schedule (macOS):
#   launchctl load ~/Library/LaunchAgents/com.desearch.daily-questions.plist
# Disable:
#   launchctl unload ~/Library/LaunchAgents/com.desearch.daily-questions.plist
# Run once by hand:
#   bash scripts/daily_questions.sh

set -uo pipefail
REPO="/Users/mirian/Documents/search-evals/desearch-search-evals"
PY="/Users/mirian/miniconda3/bin/python3"
MINER_ENV="/Users/mirian/Documents/sn22/neurons/miners/.env"

cd "$REPO" || exit 1
mkdir -p logs
DATE="$(date -u +%F)"
LOG="logs/daily_${DATE}.log"

# Idempotent: if today's batch already exists, don't clobber it (e.g. a second
# wake-up firing of the scheduler).
if [ -f "output/questions/${DATE}.jsonl" ] && \
   [ "$(wc -l < "output/questions/${DATE}.jsonl")" -ge 300 ]; then
  echo "[daily] $(date -u) $DATE already gathered ($(wc -l < "output/questions/${DATE}.jsonl") qs), skipping" >> "$LOG"
  exit 0
fi

# The repo .env's OPENAI_API_KEY is stale; use the miner's working key. HF_TOKEN,
# SCRAPINGDOG_API_KEY and the serp API_TOKEN load from the repo .env in-process.
KEY="$(grep -m1 '^OPENAI_API_KEY=' "$MINER_ENV" | cut -d= -f2-)"
KEY="${KEY%\"}"; KEY="${KEY#\"}"; KEY="${KEY%\'}"; KEY="${KEY#\'}"
export OPENAI_API_KEY="$KEY"

echo "=== [daily] $(date -u) gathering questions for $DATE ===" >> "$LOG"
# lookback 2 (a 24h cron can miss a slow news day; 2d + dedup vs prior days keeps
# it fresh) over the full expanded source list, via the geonode proxy from .env.
"$PY" -u scripts/generate_questions.py \
  --lookback 2 --sitemaps --articles 5000 --target 1100 \
  --hf --repo desearch/desearch-search-evals >> "$LOG" 2>&1
echo "=== [daily] $(date -u) done (exit $?) ===" >> "$LOG"
