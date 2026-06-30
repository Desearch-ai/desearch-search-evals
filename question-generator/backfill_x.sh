#!/usr/bin/env bash
# Backfill X questions for the last N days, one file per day, in CHUNK-day batches.
#   ./backfill_x.sh 30
set -uo pipefail
cd "$(dirname "$0")"

ENVFILE=""; for e in ../.env .env; do [ -f "$e" ] && { ENVFILE="$e"; break; }; done
getenv() { [ -n "$ENVFILE" ] && sed -n "s/^$1=//p" "$ENVFILE" | tail -1 | sed -e 's/^["'\'']//' -e 's/["'\'']$//'; }
PYTHON="${PYTHON:-$(getenv PYTHON)}"; PYTHON="${PYTHON:-python3}"
PROVIDER="${LLM_PROVIDER:-chutes}"
DAYS="${1:-30}"
CHUNK="${X_CHUNK:-3}"
GENC="${X_GEN_CONCURRENCY:-96}"

dateback() { date -u -v-"${1}"d +%F 2>/dev/null || date -u -d "-${1} days" +%F; }

mkdir -p output
done=0
while [ "$done" -lt "$DAYS" ]; do
  n=$(( DAYS - done < CHUNK ? DAYS - done : CHUNK ))
  if [ "$done" -eq 0 ]; then endarg=(); else endarg=(--end-day "$(dateback "$done")"); fi
  echo "[backfill-x] chunk: $n days ending $( [ "$done" -eq 0 ] && echo today || dateback "$done")"
  "$PYTHON" -u generate_x_questions.py \
    --provider "$PROVIDER" \
    --days "$n" ${endarg[@]+"${endarg[@]}"} \
    --per-query 150 \
    --max-tweets 12000 \
    --questions-per-tweet 4 \
    --gen-concurrency "$GENC" \
    --harvest-concurrency 48 || echo "[backfill-x] chunk ending $(dateback "$done") failed — re-run to resume"
  done=$(( done + n ))
done

TOTAL=$(cat output/questions/x-*.jsonl 2>/dev/null | wc -l | tr -d ' ')
echo "[backfill-x] done. total X questions on disk: ${TOTAL}"
echo "[backfill-x] push with: ${PYTHON} push_x_to_hf.py"
