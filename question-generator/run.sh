#!/usr/bin/env bash
# Generate news benchmark questions, merge into today's file, push public rows to HF.
# Run by hand to test:  ./run.sh
set -uo pipefail
cd "$(dirname "$0")"

getenv() { [ -f ../.env ] && sed -n "s/^$1=//p" ../.env | tail -1 | sed -e 's/^["'\'']//' -e 's/["'\'']$//'; }

PYTHON="${PYTHON:-$(getenv PYTHON)}"; PYTHON="${PYTHON:-python3}"
HF_DATASET_REPO="${HF_DATASET_REPO:-$(getenv HF_DATASET_REPO)}"
LOOKBACK="${LOOKBACK:-$(getenv LOOKBACK)}"; LOOKBACK="${LOOKBACK:-2}"
ARTICLES="${ARTICLES:-$(getenv ARTICLES)}"; ARTICLES="${ARTICLES:-8000}"
TARGET="${TARGET:-$(getenv TARGET)}"; TARGET="${TARGET:-5000}"
PROVIDER="${LLM_PROVIDER:-$(getenv LLM_PROVIDER)}"; export LLM_PROVIDER="${PROVIDER:-chutes}"

DATE="$(date -u +%F)"
mkdir -p output

REPO_ARG=()
[ -n "${HF_DATASET_REPO:-}" ] && REPO_ARG=(--repo "$HF_DATASET_REPO")

echo "[daily] $(date -u) generating + merging questions for ${DATE}"
"$PYTHON" -u generate_questions.py \
  --lookback "$LOOKBACK" \
  --sitemaps \
  --articles "$ARTICLES" \
  --target "$TARGET" \
  --cache \
  --append || exit 1

# --only is REQUIRED: without it, older sitemap articles overwrite past days' files on HF.
"$PYTHON" -u to_hf_dataset.py --label "$DATE" --only "$DATE" || exit 1

"$PYTHON" -u push_to_hf.py "${REPO_ARG[@]}"
echo "[daily] $(date -u) pushed $(wc -l < "output/hf_dataset/questions/${DATE}.jsonl" 2>/dev/null | tr -d ' ') questions for ${DATE}"
