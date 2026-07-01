---
license: mit
task_categories:
- question-answering
- text-retrieval
language:
- en
tags:
- search
- benchmark
- news
- twitter
pretty_name: Desearch Benchmark Questions
size_categories:
- 100K<n<1M
---

# Desearch Benchmark Questions

Fresh, self-contained benchmark questions for evaluating web and X (Twitter) search.
Regenerated daily from recent news and tweets. Each question is answerable from public
sources within a dated window — there are no answer keys or source URLs in the public data,
so systems have to actually search rather than recall.

## Subsets

| Path | Lane | Built from |
| --- | --- | --- |
| `questions/` | Web / news | Recent news articles (RSS + news sitemaps) |
| `x/` | X / Twitter | High-signal tweets from the past 24h |

Files are one per day: `<YYYY-MM-DD>.jsonl` (web) and `x-<YYYY-MM-DD>.jsonl` (X).

## Schema

Every row is source-free:

```json
{
  "id": "…",
  "question": "…",
  "difficulty": "easy | medium | hard",
  "start_date": "YYYY-MM-DDTHH:MM:SSZ",
  "end_date": "YYYY-MM-DDTHH:MM:SSZ"
}
```

`start_date`/`end_date` bound the window in which the question is answerable — use them as a
date filter when searching. Gold answers are kept private and are never uploaded.

## Loading

```python
from datasets import load_dataset

web = load_dataset("desearch/dataset", data_dir="questions", split="train")
x = load_dataset("desearch/dataset", data_dir="x", split="train")
```

## Updates

Regenerated daily by an open-source generator (news twice daily, X once daily), so the set
grows one file per lane per day.
