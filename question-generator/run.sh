#!/usr/bin/env bash
# Daily question generation: invert the last ~2 days of news into benchmark
# questions, save to output/, and (when HF_DATASET_REPO is set) push the
# questions — never the private golds — to a HuggingFace dataset.
#
# pm2 fires this once a day (see ecosystem.config.js). Run it by hand to test:
#   ./run.sh
set -uo pipefail
cd "$(dirname "$0")"

# Read run-knobs without sourcing .env; Python's load_env owns the secrets/proxy.
getenv() { [ -f .env ] && sed -n "s/^$1=//p" .env | tail -1 | sed -e 's/^["'\'']//' -e 's/["'\'']$//'; }

PYTHON="${PYTHON:-$(getenv PYTHON)}"; PYTHON="${PYTHON:-python3}"
HF_DATASET_REPO="${HF_DATASET_REPO:-$(getenv HF_DATASET_REPO)}"
LOOKBACK="${LOOKBACK:-$(getenv LOOKBACK)}"; LOOKBACK="${LOOKBACK:-2}"
ARTICLES="${ARTICLES:-$(getenv ARTICLES)}"; ARTICLES="${ARTICLES:-5000}"
TARGET="${TARGET:-$(getenv TARGET)}"; TARGET="${TARGET:-1100}"

DATE="$(date -u +%F)"
mkdir -p output

# Re-run safe: skip if today's batch already exists.
if [ -f "output/questions/${DATE}.jsonl" ]; then
  echo "[daily] $(date -u) ${DATE} already generated — skipping"
  exit 0
fi

# Push to HF only when a target repo is configured; otherwise stay local-only.
HF_ARGS=()
if [ -n "${HF_DATASET_REPO:-}" ]; then
  HF_ARGS=(--hf --repo "$HF_DATASET_REPO")
fi

echo "[daily] $(date -u) generating questions for ${DATE}"
exec "$PYTHON" -u generate_questions.py \
  --lookback "$LOOKBACK" \
  --sitemaps \
  --articles "$ARTICLES" \
  --target "$TARGET" \
  "${HF_ARGS[@]}"
