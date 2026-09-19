from evaluators.answer_match import contains, screen


def test_exact_match_ignores_case_accents_and_sentence_punctuation():
    assert contains("The award went to Serban Ghénea.", "Serban Ghenea") == "exact"
    assert contains("Booking.com is the seventh gatekeeper", "Booking") == "exact"


def test_alias_counts_as_exact():
    assert (
        contains("Ivana Trump married him in 1977", "Ivana Zelníčková", ["Ivana Trump"])
        == "exact"
    )


def test_numbers_match_across_separators_scales_and_words():
    assert (
        contains("It will create about 500 terabytes a year", "500 terabytes")
        == "exact"
    )
    assert contains("delivered 10,661 vehicles in the quarter", "10661") == "numeric"
    assert contains("seeking a $400 billion valuation", "$400,000,000,000") == "numeric"
    assert contains("two people died", "2") == "numeric"


def test_percent_and_currency_symbols_match_their_words():
    assert contains("the AfD won nearly 44 percent", "44%") == "exact"


def test_dates_match_across_formats():
    assert contains("The eruption began on August 7, 2026", "7 August 2026") == "date"
    assert contains("published 2026-08-07", "August 7, 2026") == "date"


def test_substring_of_a_longer_word_is_not_a_match():
    assert contains("the Booker prize", "Book") == "none"
    assert contains("won 125 goals", "12") == "none"


def test_screen_labels_every_source():
    sources = [{"text": "Homebrew led the round"}, {"text": "No mention"}, {}]
    assert screen(sources, "Homebrew") == ["exact", "none", "none"]


def test_simpleqa_alternatives_count_as_written_forms():
    assert (
        contains("It was disguised as a collier.", "tramp steamer (or collier)")
        == "exact"
    )
    assert (
        contains("made to look like a tramp steamer", "tramp steamer (or collier)")
        == "exact"
    )


def test_simpleqa_numeric_ranges_accept_values_inside_only():
    answer = "200 (acceptable range: anything between 198 and 202)"
    assert contains("roughly 201 people attended", answer) == "numeric"
    assert contains("roughly 150 people attended", answer) == "none"
    assert (
        contains(
            "the total was 3,320 square kilometers",
            "3,342.49 (acceptable range: anything between 3309.07 and 3375.91 square kilometers)",
        )
        == "numeric"
    )
