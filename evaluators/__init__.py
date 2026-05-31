"""Benchmark evaluators.

Three judge-graded evaluators score each cached provider run. Every
signal comes from the judge LLM reading content — no provider self-
reports — so a provider can't game the score by lying about whether
it called search or which tools it used:

  source_relevance: each cited URL is fetched; judge says YES/MAYBE/NO
                    on whether the page is relevant to the question
  answer_quality:   judge reads question + answer and picks one of
                    RESPONSIVE / APPROPRIATE_DECLINE / EVASIVE /
                    WRONG_DECLINE / HALLUCINATED
  groundedness:     each factual claim in the answer is checked against
                    its cited page's actual content (SUPPORTED /
                    UNSUPPORTED / CONTRADICTED)

aggregator: composite = 0.40·groundedness + 0.35·source_relevance
                       + 0.25·answer_quality.
"""
