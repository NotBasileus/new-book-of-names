"""Punctuation modes (spec 7.6)."""

import pytest

from namebook.punctuation import add_accents, diaeresis, punctuate, vowel_harmony
from namebook.style import Punctuation


@pytest.mark.parametrize(
    "word, expected",
    [
        ("AICANAARO", "AICANÁRO"),  # the spec's examples
        ("CIIIRDAN", "CÎRDAN"),
        ("KULLAA", "KULLÁ"),  # doubled consonants stay
        ("TAAAAN", "TAAAAN"),  # runs of four or more are left alone
        ("NYY", "NÝ"),
        ("NYYY", "NYYY"),  # there is no Y with a circumflex
        ("AALBE'IL", "ÁLBE'IL"),
    ],
)
def test_accents(word, expected):
    assert add_accents(word) == expected


@pytest.mark.parametrize(
    "word, expected",
    [
        ("EARENDIL", "EÄRENDIL"),  # at the start: the second vowel is marked
        ("EONWE", "EÖNWË"),
        ("FEANOR", "FËANOR"),  # elsewhere: the E is marked
        ("MANWE", "MANWË"),  # a final E
        ("VARDA", "VARDA"),
    ],
)
def test_diaeresis(word, expected):
    assert diaeresis(word) == expected


@pytest.mark.parametrize(
    "word, expected",
    [
        ("VÄINO", "VÄINÖ"),  # the spec's example: Ä comes first, so the word is front
        ("UKKÖ", "UKKO"),  # U comes first, so the word is back
        ("KYLA", "KYLÄ"),  # Y counts as front
        ("EINO", "EINO"),  # E and I go either way; O decides
        ("TIINA", "TIINA"),
        ("ELI", "ELI"),  # nothing decides
    ],
)
def test_vowel_harmony(word, expected):
    assert vowel_harmony(word) == expected


def test_modes_combine_in_order():
    both = Punctuation.ACCENTS | Punctuation.DIAERESIS
    assert punctuate("EAARENDIL", both) == "EÁRENDIL"  # accents first, then no EA is left
    assert punctuate("MANWEE", both) == "MANWÉ"
    assert punctuate("MANWE", Punctuation.NONE) == "MANWE"
