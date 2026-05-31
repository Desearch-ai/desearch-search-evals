# Question sets

The benchmark question sets are published to the HuggingFace dataset as
`questions/<date>.jsonl`, not committed here:

https://huggingface.co/datasets/desearch/desearch-search-evals

Each line:

```json
{"difficulty": "easy", "question": "What phrase did Japan's defense minister reject in response to accusations of rising militarism?"}
```

`difficulty` is `easy`, `medium`, or `hard`. Author a new dated file locally and
run `python3 scripts/weekly_run.py --date <date>`; the run uploads the set to
HuggingFace. A local `questions/<date>.jsonl` is gitignored — `weekly_run.py`
pulls the set from HuggingFace when it isn't present.
