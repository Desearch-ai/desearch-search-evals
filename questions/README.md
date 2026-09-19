# Benchmark inputs

Keep question, article and result files under the ignored `runs/` directory.

## Questions

One JSON object per line, with unique filesystem-safe IDs:

```json
{"id":"news-e00602","question":"What new price did Trump Mobile set for the T1 Phone in September 2026?","answer":"$749","answer_aliases":[]}
```

`id`, `question` and `answer` are required. `answer_aliases` lists other names for
exactly the same answer. Optional provenance fields include `benchmark`, `event_id`,
`event_date`, `outlets`, `reference_sources` (`url`, `title`, `domain`, `quote`) and
`reference_urls`. Providers receive only the question text; everything else is used for
grading and audit.

SimpleQA answers may carry tolerances, which grading understands:

- `tramp steamer (or collier)`: either written form counts;
- `200 (acceptable range: anything between 198 and 202)`: any value in the range counts.

## News articles

Input to `scripts.prepare_news`, one article per line:

```json
{"url":"https://example.org/story","title":"Headline","published":"2026-09-10","text":"Full article text","domain":"example.org","owner":"example-group","event_id":"e00602"}
```

`event_id` groups coverage of the same real-world event. `owner` groups sister sites of
one publisher and defaults to `domain`.

## Local Desearch results

One JSON object per question and local profile:

```json
{"profile_id":"desearch-standard","question_id":"news-e00602","query":"What new price did Trump Mobile set for the T1 Phone in September 2026?","status":"ok","results":[{"rank":1,"url":"https://example.org/story","title":"Headline","published":"2026-09-10","text":"Returned passage"}]}
```

Provide a row for every configured local profile. `query` must equal the question text
exactly. Preserve ranks, returned passages, failures and empty results. Imported
retrieval costs are recorded as zero; report index and embedding costs separately.

## Calibration cases

Written by `scripts.calibration sample`, one case per line. Reviewers set
`human_label` to `yes` or `no` and fill `labeler`; `suggested_*` fields from `prelabel`
are hints only. The matching `cases.key.json` holds the judge verdicts and must not be
shown to reviewers.
