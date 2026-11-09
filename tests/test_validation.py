"""Validation checks (spec 8)."""

import pytest

from namebook.analysis import LetterLimits
from namebook.validation import (
    LetterFrequencyCheck,
    LetterSpacingCheck,
    RepeatedSequenceCheck,
    Validator,
    WordListCheck,
    load_word_list,
)

LIMITS = LetterLimits(
    min_distance={"A": 3, "TH": 4},
    max_share={"A": 0.4},
    fallback_distance=2,
    fallback_share=0.5,
)


def code(check, word):
    rejection = check.check(tuple(word), "".join(word))
    return rejection.code if rejection else None


# --- Repeated sequences --------------------------------------------------------


@pytest.mark.parametrize(
    "word, expected",
    [
        ("ABCDXABCD", "!REP"),
        ("AAAAA", "!REP"),  # overlapping occurrences count
        ("ABCDABC", None),  # only three letters repeat
        ("ABCDEFGHIJKLMNOABCD", "!REP"),  # found right up to the last letter
        ("ABC", None),
    ],
)
def test_repeated_sequences(word, expected):
    assert code(RepeatedSequenceCheck(), word) == expected


def test_repeated_sequences_count_special_letters_as_one():
    check = RepeatedSequenceCheck()
    assert code(check, ["TH", "A", "TH", "A", "TH", "A"]) == "!REP"  # TH·A·TH·A twice
    assert code(check, list("THATHA")) is None


def test_repeated_sequence_reason():
    rejection = RepeatedSequenceCheck().check(tuple("ABCDXABCD"), "Abcdxabcd")
    assert rejection.reason == "the letters ABCD appear twice"


# --- Letter spacing ----------------------------------------------------------


@pytest.mark.parametrize(
    "word, expected",
    [
        ("ANA", "!DIST/A"),  # gap 2, but A needs 3
        ("ANNA", None),  # gap 3
        ("AAN", None),  # doubled letters are ignored
        ("ENE", None),  # E has no limit of its own, so the fallback 2 applies
    ],
)
def test_letter_spacing(word, expected):
    assert code(LetterSpacingCheck(LIMITS), word) == expected


def test_letter_spacing_uses_special_letters():
    check = LetterSpacingCheck(LIMITS)
    assert code(check, ["TH", "A", "R", "TH"]) == "!DIST/TH"
    assert code(check, ["TH", "A", "R", "O", "TH"]) is None


def test_letter_spacing_reason():
    rejection = LetterSpacingCheck(LIMITS).check(tuple("ANA"), "Ana")
    assert rejection.reason == "two As are 2 apart; in the seeds they are at least 3 apart"


# --- Letter frequency --------------------------------------------------------


@pytest.mark.parametrize(
    "word, expected",
    [
        ("ANNA", "!FREQ/A"),  # 50% A, more than 40%
        ("ANBCA", None),  # 40%: not more than the limit
        ("AB", None),  # letters appearing once are never flagged
        ("ENNE", None),  # 50% E and N: the fallback 50% applies
        ("ENNNE", "!FREQ/N"),  # 60% N
    ],
)
def test_letter_frequency(word, expected):
    assert code(LetterFrequencyCheck(LIMITS), word) == expected


def test_letter_frequency_reason():
    rejection = LetterFrequencyCheck(LIMITS).check(tuple("ANNA"), "Anna")
    assert rejection.reason == "A makes up 50% of the word; in the seeds at most 40%"


# --- Word list ---------------------------------------------------------------


def test_word_list_matches_whole_words_ignoring_case():
    check = WordListCheck(["Mog"])
    assert code(check, "MOG") == "!LIST"
    assert code(check, "MOGA") is None


def test_word_list_wildcards():
    check = WordListCheck(["*mog*", "zar*"])
    assert code(check, "AMOGU") == "!LIST"
    assert code(check, "ZARAN") == "!LIST"
    assert code(check, "AZAR") is None


def test_load_word_list(tmp_path):
    path = tmp_path / "words.txt"
    path.write_text("# words never to make\n\nmog\n  *zar*  \n", encoding="utf-8")
    assert load_word_list(path) == ("mog", "*zar*")


# --- Validation levels -------------------------------------------------------


def make_validator():
    return Validator([
        WordListCheck(["mog"]),
        RepeatedSequenceCheck(),
        LetterSpacingCheck(LIMITS),
        LetterFrequencyCheck(LIMITS),
    ])  # fmt: skip


def check_at(word, level):
    rejection = make_validator().check(tuple(word), word, level)
    return rejection.code if rejection else None


def test_level_zero_checks_only_the_word_list():
    assert check_at("MOG", 0) == "!LIST"
    assert check_at("ANA", 0) is None


def test_level_one_adds_repeats_and_spacing():
    assert check_at("ANA", 1) == "!DIST/A"
    assert check_at("ABCDXABCD", 1) == "!REP"
    assert check_at("ANNA", 1) is None  # frequency is not checked yet


def test_level_two_adds_frequency():
    assert check_at("ANNA", 2) == "!FREQ/A"


def test_levels_above_two_are_like_two():
    assert check_at("ANNA", 3) == "!FREQ/A"


def test_the_first_failing_check_decides():
    # AMOGAA fails both checks: it contains "mog", and A fills half of it
    # (the limit is 40%). The word list comes first in the chain.
    word = tuple("AMOGAA")
    assert LetterFrequencyCheck(LIMITS).check(word, "Amogaa").code == "!FREQ/A"
    validator = Validator([WordListCheck(["*mog*"]), LetterFrequencyCheck(LIMITS)])
    assert validator.check(word, "Amogaa", 2).code == "!LIST"
