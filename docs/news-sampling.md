# News sampling

## Define the slice

A news comparison is scoped to a declared slice: a publisher roster and a date window,
chosen before any question is written. The claim it supports is "for news published by
these publishers in this window, which provider returns results stating the answer". It
does not establish coverage of the open web.

Collect every article the roster published in the window and report completeness per
publisher: articles known to exist from sitemaps and archives, articles indexed, and the
reason for every gap (not in English, too thin to index, dated outside the window, or a
failed fetch). Fix or drop publishers with real fetch gaps before writing questions.
Because questions are drawn from the slice, high completeness is what keeps the sample
from favouring the index that produced it.

Use complete days. A window that ends a day or two before searching gives every provider
time to index the articles; measure same-day freshness separately.

## One question per event

Group articles about the same real-world event, for example by embedding similarity
within a few days, and treat each group as one candidate. Syndicated copies and follow-up
coverage never create extra questions. Exclude very large groups, which are usually a
running topic rather than one event.

Count independent owners, not hostnames: the same article on two hosts, or sister sites
of one publisher, confirm nothing. Only events covered by at least two owners are
sampled, spread evenly across days with a fixed seed.

## Write and verify questions

For each sampled event the generator sees up to three articles from different owners and
asks about the central fact most coverage states. A question is kept only when:

- articles from at least two owners state the answer, checked in code;
- the question names the entity and event, gives the month and year or exact date, and
  avoids relative dates and references to "the article";
- the answer does not appear in the question and has at most eight words;
- aliases are other names for exactly the same answer, never roles or descriptions;
- a verifier from another model family confirms it is new within the window,
  newsworthy, self-contained, uniquely answerable and supported.

## Freeze

Freeze questions, answers, aliases and provider profiles before any provider searches.
Keep events used for pilots and calibration out of the evaluation set with `--exclude`.
Add nothing to the Desearch index after the questions exist.

## Run and interpret

Use the same questions, result count and profiles for every provider, with no date
filters; the question carries the event's date. Any publisher may supply the answer.
Report failures and empty responses in the denominator, report judge accuracy from human
labels, and avoid declaring a winner from small differences.
