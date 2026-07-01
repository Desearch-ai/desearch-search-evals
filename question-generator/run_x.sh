#!/usr/bin/env bash
# Generate X benchmark questions from the past 24h of tweets and push them to HF.
# Run by hand to test:  ./run_x.sh
set -uo pipefail
cd "$(dirname "$0")"

ENVFILE=""; for e in ../.env .env; do [ -f "$e" ] && { ENVFILE="$e"; break; }; done
getenv() { [ -n "$ENVFILE" ] && sed -n "s/^$1=//p" "$ENVFILE" | tail -1 | sed -e 's/^["'\'']//' -e 's/["'\'']$//'; }

PYTHON="${PYTHON:-$(getenv PYTHON)}"; PYTHON="${PYTHON:-python3}"
TARGET="${X_TARGET:-$(getenv X_TARGET)}"; TARGET="${TARGET:-2000}"
WINDOW="${X_WINDOW_HOURS:-$(getenv X_WINDOW_HOURS)}"; WINDOW="${WINDOW:-24}"
PROVIDER="${LLM_PROVIDER:-$(getenv LLM_PROVIDER)}"; PROVIDER="${PROVIDER:-chutes}"
REPO="${HF_DATASET_REPO:-$(getenv HF_DATASET_REPO)}"

DATE="x-$(date -u +%F)"
mkdir -p output

if [ -f "output/questions/${DATE}.jsonl" ]; then
  echo "[daily-x] $(date -u) ${DATE} already generated — skipping generation"
else
  echo "[daily-x] $(date -u) generating ${TARGET} X questions for ${DATE} (provider=${PROVIDER})"
  "$PYTHON" -u generate_x_questions.py \
    --provider "$PROVIDER" \
    --target "$TARGET" \
    --window-hours "$WINDOW" \
    --per-query 150 \
    --max-tweets 10000 \
    --questions-per-tweet 4 \
    --gen-concurrency 24 || exit 1
fi

echo "[daily-x] pushing X questions to HF -> ${REPO:-desearch/dataset}"
PUSH_ARG=(); [ -n "$REPO" ] && PUSH_ARG=(--repo "$REPO")
"$PYTHON" -u push_x_to_hf.py ${PUSH_ARG[@]+"${PUSH_ARG[@]}"} || echo "[daily-x] push failed — files saved locally, re-run to retry"
