"""Gathering statistics and validation data from seed names."""

from collections import Counter

import pytest

from conftest import SAMPLE_CHAPTERS
from namebook.analysis import (
    ElementPair,
    analyze,
    repeated_letter_shares,
    smallest_gaps,
)
from namebook.formats import load_style
from namebook.style import NameList, SpecialLetters, Style, StyleError


def style(*names, soft=True):
    return Style(
        title="Test",
        name_lists=(NameList("names", names),),
        special_letters=SpecialLetters(soft_consonants=soft),
    )


@pytest.fixture
def spec_example():
    """The two names used as examples in spec 5.1 to 5.4."""
    return analyze(style("Mikhail", "Aalbe'il"))


# --- Words -------------------------------------------------------------------


def test_words_remember_their_seed_name(spec_example):
    assert [(seed.name_number, seed.name) for seed in spec_example.words] == [
        (1, "Mikhail"),
        (2, "Aalbe'il"),
    ]


def test_names_with_spaces_give_several_words():
    analysis = analyze(style("Van Helsing", "Mina"))
    assert [(seed.name_number, seed.word.text) for seed in analysis.words] == [
        (1, "Van"),
        (1, "Helsing"),
        (2, "Mina"),
    ]


def test_problems_with_every_bad_name_are_reported():
    # The input check refuses such names in files; a style made in code is
    # checked here.
    with pytest.raises(StyleError) as caught:
        analyze(style("Ana", "An4", "B2b"))
    assert caught.value.problems == (
        "Name 2 (\"An4\"): '4' is not a vowel or consonant letter.",
        "Name 3 (\"B2b\"): '2' is not a vowel or consonant letter.",
    )


def test_a_word_of_one_element_is_its_first_and_last():
    analysis = analyze(style("Ao", "Aola", "Bao", soft=False))
    assert analysis.structures == Counter({-1: 1, 3: 1, 0: 1})
    ao = analysis.vowel_elements[("A", "O")]
    assert (ao.count, ao.first, ao.last, ao.middle) == (3, 2, 2, 0)
    # A word of one element has no prefix or suffix.
    assert sum(analysis.prefixes.values()) == sum(analysis.suffixes.values()) == 2


def test_other_name_lists_can_be_analyzed():
    two_lists = Style(
        title="Two", name_lists=(NameList("given", ("Ana",)), NameList("family", ("Bel", "Cor")))
    )
    assert len(analyze(two_lists).words) == 1
    family = analyze(two_lists, "family")
    assert family.list_name == "family"
    assert [seed.name for seed in family.words] == ["Bel", "Cor"]


# --- Structures, prefixes and suffixes (spec 5.4) -----------------------------


def test_structures_and_substructures(spec_example):
    assert spec_example.structures == Counter({6: 1, 5: 1})
    assert spec_example.substructures == {
        6: Counter({(1, 1, 1, 2, 1): 1}),
        5: Counter({(2, 2, 3, 1): 1}),
    }


def test_substructures_are_counted():
    analysis = analyze(style("Ana", "Ema", "Alba"))
    assert analysis.structures == Counter({3: 3})
    assert analysis.substructures[3] == Counter({(1, 1, 1): 2, (1, 2, 1): 1})


def test_prefixes_and_suffixes(spec_example):
    assert spec_example.prefixes == Counter({
        ElementPair(False, ("M",), ("I",)): 1,
        ElementPair(True, ("A", "A"), ("L", "B")): 1,
    })  # fmt: skip
    assert spec_example.suffixes == Counter({
        ElementPair(True, ("A", "I"), ("L",)): 1,
        ElementPair(True, ("E", "'", "I"), ("L",)): 1,
    })  # fmt: skip


def test_two_element_words_have_the_same_prefix_and_suffix():
    analysis = analyze(style("Ab"))
    assert analysis.prefixes == analysis.suffixes == Counter({ElementPair(True, ("A",), ("B",)): 1})


# --- Elements and positions (spec 5.2) ---------------------------------------


def test_element_inventories(spec_example):
    assert set(spec_example.vowel_elements) == {("I",), ("A", "I"), ("A", "A"), ("E", "'", "I")}
    assert set(spec_example.consonant_elements) == {("M",), ("KH",), ("L",), ("L", "B")}
    assert spec_example.consonant_elements[("L",)].count == 2
    assert spec_example.consonant_elements[("L", "B")].length == 2
    assert spec_example.vowel_elements[("E", "'", "I")].length == 3


def test_positions(spec_example):
    # M · I · KH · AI · L
    m = spec_example.consonant_elements[("M",)]
    assert (m.first, m.last, m.middle) == (1, 0, 0)
    i = spec_example.vowel_elements[("I",)]
    assert (i.first, i.last) == (0, 0)
    assert i.from_start == Counter({1: 1})  # the 1st vowel element from the start
    assert i.from_end == Counter({2: 1})  # and the 2nd from the end
    kh = spec_example.consonant_elements[("KH",)]
    assert kh.from_start == Counter({2: 1})
    assert kh.from_end == Counter({2: 1})
    ai = spec_example.vowel_elements[("A", "I")]
    assert ai.from_start == Counter({2: 1})
    assert ai.from_end == Counter({1: 1})
    # L is last in both names.
    l_stats = spec_example.consonant_elements[("L",)]
    assert (l_stats.first, l_stats.last, l_stats.middle) == (0, 2, 0)


def test_middle_count_pools_all_middle_positions():
    analysis = analyze(style("Banana"))  # B · A · N · A · N · A
    a = analysis.vowel_elements[("A",)]
    assert (a.first, a.last, a.middle, a.count) == (0, 1, 2, 3)
    assert a.from_start == Counter({1: 1, 2: 1})
    assert a.from_end == Counter({3: 1, 2: 1})


# --- Fitting data (spec 5.3) -------------------------------------------------


def test_fitting_data_for_mikhail():
    fitting = analyze(style("Mikhail")).fitting
    # Level 1: M→I, I→KH, KH→AI, AI→L.
    assert fitting.consonant_to_vowel == Counter({("M", ("I",)): 1, ("KH", ("A", "I")): 1})
    assert fitting.vowel_to_consonant == Counter({(("I",), "KH"): 1, (("A", "I"), "L"): 1})
    # Level 2: M…KH, KH…L.
    assert fitting.consonant_to_consonant == Counter({("M", "KH"): 1, ("KH", "L"): 1})
    # Level 3: I…AI.
    assert fitting.vowel_to_vowel == Counter({(("I",), ("A", "I")): 1})


def test_consonant_elements_fit_by_their_first_and_last_letters():
    fitting = analyze(style("Aalbe'il")).fitting  # AA · LB · E'I · L
    assert fitting.vowel_to_consonant == Counter({(("A", "A"), "L"): 1, (("E", "'", "I"), "L"): 1})
    assert fitting.consonant_to_vowel == Counter({("B", ("E", "'", "I")): 1})
    assert fitting.consonant_to_consonant == Counter({("B", "L"): 1})
    assert fitting.vowel_to_vowel == Counter({(("A", "A"), ("E", "'", "I")): 1})


def test_fitting_counts_add_up():
    fitting = analyze(style("Ana", "Ana", "Ena")).fitting
    assert fitting.consonant_to_vowel[("N", ("A",))] == 3


# --- Validation data (spec 5.5) ----------------------------------------------


def test_smallest_gaps():
    assert smallest_gaps("ANANA") == {"A": 2, "N": 2}
    assert smallest_gaps("ANNA") == {"A": 3}  # NN is doubled, so it is ignored
    assert smallest_gaps("ANNAN") == {"A": 3, "N": 2}
    assert smallest_gaps("ABCDA") == {"A": 4}
    assert smallest_gaps(["TH", "A", "TH"]) == {"TH": 2}  # special letters count as one


def test_repeated_letter_shares():
    assert repeated_letter_shares("ANNA") == {"A": 0.5, "N": 0.5}
    assert repeated_letter_shares("ABCDA") == {"A": 0.4}


def test_letter_limits_take_the_loosest_value_per_letter():
    limits = analyze(style("Abcda", "Anaxx")).letter_limits
    assert limits.min_distance == {"A": 2}
    assert limits.max_share == {"A": 0.4, "X": 0.4}
    assert limits.distance_for("A") == 2
    assert limits.share_for("A") == 0.4


def test_letters_that_never_repeat_get_the_fallback():
    limits = analyze(style("Abcdab", "Lanna")).letter_limits
    assert limits.min_distance == {"A": 3, "B": 4}
    assert limits.fallback_distance == 3
    assert limits.fallback_share == 0.4
    assert limits.distance_for("Z") == 3
    assert limits.share_for("Z") == 0.4


def test_no_repeats_at_all_means_no_limits():
    limits = analyze(style("Ab", "Cod")).letter_limits
    assert limits.distance_for("A") == 2
    assert limits.share_for("A") == 1.0


def test_letter_limits_use_special_letters():
    limits = analyze(style("Thatha")).letter_limits  # TH · A · TH · A
    assert limits.min_distance == {"TH": 2, "A": 2}


# --- Starting letters (spec 13.11) -------------------------------------------


def test_start_lengths_give_the_elements_that_can_spell_the_letters():
    analysis = analyze(style("Kara", "Bran", "Ela", "Aidan"))  # K·A·R·A, BR·A·N, E·L·A, AI·D·A·N
    assert analysis.start_lengths("B", False, first=True) == {(2,)}
    # No element is KA, but K, then A or AI, can spell it.
    assert analysis.start_lengths("KA", False, first=True) == {(1, 1), (1, 2)}
    assert analysis.start_lengths("BRA", False, first=True) == {(2, 1), (2, 2)}
    assert analysis.start_lengths("BRAN", False, first=True) == {(2, 1, 1)}
    assert analysis.start_lengths("A", True, first=True) == {(2,)}  # only AI began a word
    assert analysis.start_lengths("A", True) == {(1,), (2,)}
    assert analysis.start_lengths("R", False, first=True) == frozenset()
    assert analysis.start_lengths("KA", True, first=True) == frozenset()
    assert analysis.start_lengths("KX", False, first=True) == frozenset()


def test_start_lengths_count_a_special_letter_as_one():
    assert analyze(style("Thea", "Tom")).start_lengths("TH", False, first=True) == {(1,)}
    assert analyze(style("Thea", "Tom", soft=False)).start_lengths("TH", False, first=True) == {(2,)}


# --- The sample chapters -----------------------------------------------------


@pytest.mark.parametrize("path", SAMPLE_CHAPTERS, ids=lambda path: path.name)
def test_sample_chapters_analyze(path):
    style = load_style(path)
    analysis = analyze(style)
    # None of the sample names contain spaces, so each gives one word.
    assert len(analysis.words) == len(style.names)
    assert sum(analysis.structures.values()) == len(style.names)
    assert sum(analysis.prefixes.values()) == len(style.names)
    for structure, substructures in analysis.substructures.items():
        assert sum(substructures.values()) == analysis.structures[structure]


def test_angelic_details(chapters_dir):
    analysis = analyze(load_style(chapters_dir / "Angelic.ebn"))
    mikhail = next(seed.word for seed in analysis.words if seed.name == "Mikhail")
    assert mikhail.describe() == "M · I · KH · AI · L"
    assert analysis.suffixes.most_common(1) == [(ElementPair(True, ("E", "'", "I"), ("L",)), 103)]


def test_demonic_clusters(chapters_dir):
    analysis = analyze(load_style(chapters_dir / "Demonic.ebn"))
    assert ("RR",) in analysis.consonant_elements
    assert ("ZZ",) in analysis.consonant_elements
    assert ("R", "R") not in analysis.consonant_elements
