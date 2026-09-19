# Evaluation protocol

## What is measured

Each question has a verified short answer. For every provider profile, the question
counts as a hit at k when at least one of the first k results states that answer for
exactly what the question asks. Any publisher counts, the reference article is not
required, and several results repeating the same fact still count as one hit.

Two views are scored separately:

- **Page:** the result's page, fetched once with one shared extractor for every
  provider. When a page cannot be fetched, the returned text is judged instead and the
  fallback is recorded.
- **Returned text:** only what the search API returned, which is what an agent sees
  without fetching.

## Grading one result

1. **String check.** Deterministic matching looks for the verified answer, its aliases
   and its SimpleQA tolerances (alternative wordings and numeric ranges), including
   spelled-out numbers and reformatted dates. It never decides a hit on its own.
2. **Which results are read.** Results are graded in rank order. A model judge reads a
   result when the string check finds the answer, and reads the top three results
   even without a match, to catch paraphrases. Grading stops at the first confirmed
   hit, since later ranks cannot change hit@k or reciprocal rank.
3. **Long pages.** A page over 6,000 characters is cut to its opening plus the passages
   that share the most terms with the question, contain the answer, or contain the
   provider's own snippet, so the page view never shows less than the snippet.
4. **Judge.** The judge sees the question, the verified answer with aliases, the
   publication date if known and the text split into numbered sections. It never sees
   the provider, rank or domain. It labels the source `answers`, `partial`,
   `no_answer` or `uncertain`, extracts the answer and selects the supporting
   section IDs; code derives the exact highlighted text and offsets.
5. **Quote check.** An `answers` verdict counts only if the extracted answer occurs in
   the judged text. This rejects the judge copying the verified answer into its reply
   when the source says something else.

The judge applies these rules:

- The source must be about the question's own entity and event; the same value for a
  different deal, match, case or place does not count.
- A date that only identifies the event does not need to be restated when the source
  clearly reports that event. A date that is the requested answer must be stated; a
  publication date alone does not establish it.
- The requested relation matters: being charged is not denying a charge, an
  announcement is not a completed action, a lower bound is not an exact count.
- The title is part of the source. Outside knowledge is not allowed.

## Metrics

| Metric | Meaning |
| --- | --- |
| Page states answer @k | At least one of the first k result pages states the answer |
| Returned text states answer @k | At least one of the first k returned snippets states it |
| MRR@10 | Reciprocal rank of the first result that states it |

k is 1, 5 and 10. Failed searches, missing searches and empty responses score zero and
are counted per profile, together with fetched pages, judge errors and benchmark
mirrors. Pages copying a benchmark's own questions (for example dataset mirrors of
SimpleQA) receive no credit and keep their rank.

## Judge calibration

Model judgments are checked against people before any score is published:

1. `scripts.calibration sample` draws judged texts from the run across four groups:
   accepted hits, answers rejected by the quote check, rejections with a string match,
   and rejections without one. The case file contains no judge verdict.
2. `scripts.calibration prelabel` lets a model from a different family suggest labels.
   Suggestions help the reviewer and are never counted as labels.
3. A person labels each case `yes` or `no`.
4. `scripts.calibration score` reports, weighted by group size, how often counted hits
   really state the answer, how often rejected results did state it, and the implied
   recall. The report prints these numbers next to the scores, or states that the run
   is uncalibrated.

Prompts are developed on a separate calibration run. Published accuracy must come from a
fresh sample of the evaluated run, never from the cases used to tune the prompts.

## Scope

These scores measure whether returned results carry a verified answer. They are not
recall over every relevant page on the web. Question selection decides what a
comparison can show; see [news sampling](news-sampling.md).

## Saved results

`searches/` holds every provider response. `page-cache/` holds fetched pages.
`evaluation/results.json` holds, for every result, both verdicts, the string-check level,
the judged text and the selected sections. `evaluation/calls/` caches judge calls by
model, prompt and input, so a rerun only pays for changed judgments. Reports and exports
are derived from these files.
