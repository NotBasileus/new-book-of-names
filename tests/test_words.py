"""Marking special letters and splitting names into elements."""

import pytest

from namebook.letters import LATIN_LETTERS, LetterClasses
from namebook.style import HyphenMode, SpecialLetters, UMode, YMode
from namebook.words import (
    WordSplitError,
    WordSplitter,
    element_count,
    format_substructure,
    structure_number,
    structure_shape,
    structure_starts_with_vowel,
)

PLAIN = WordSplitter(LATIN_LETTERS)
SOFT = WordSplitter(LATIN_LETTERS, SpecialLetters(soft_consonants=True))


def splitter(soft=False, clusters=(), multi=False, y=YMode.VOWEL, u=UMode.VOWEL):
    return WordSplitter(LATIN_LETTERS, SpecialLetters(soft, tuple(clusters), multi), y, u)


def split(text, using=PLAIN):
    return using.split_word(text).describe()


# --- Structures --------------------------------------------------------------


@pytest.mark.parametrize(
    "structure, shape",
    [
        (-2, "C"),
        (-1, "V"),
        (0, "CV"),
        (1, "VC"),
        (2, "CVC"),
        (3, "VCV"),
        (6, "CVCVC"),
        (9, "VCVCVC"),
        (16, "CVCVCVCVCV"),
    ],
)
def test_structure_numbers_and_shapes(structure, shape):
    assert structure_shape(structure) == shape
    assert element_count(structure) == len(shape)
    assert structure_starts_with_vowel(structure) == shape.startswith("V")
    assert structure_number(len(shape), shape.startswith("V")) == structure


def test_a_structure_needs_an_element():
    with pytest.raises(ValueError):
        structure_number(0, True)


def test_format_substructure():
    assert format_substructure((1, 1, 1, 2, 1)) == "11121"
    assert format_substructure((1, 12, 1)) == "1-12-1"


# --- The spec's worked examples (5.1) ----------------------------------------


def test_mikhail_with_soft_consonants():
    word = SOFT.split_word("Mikhail")
    assert word.elements == (("M",), ("I",), ("KH",), ("A", "I"), ("L",))
    assert word.describe() == "M · I · KH · AI · L"
    assert word.structure == 6
    assert word.substructure == (1, 1, 1, 2, 1)
    assert not word.starts_with_vowel
    assert word.text == "Mikhail"


def test_aalbeil_apostrophe_is_a_vowel():
    word = SOFT.split_word("Aalbe'il")
    assert word.describe() == "AA · LB · E'I · L"
    assert word.structure == 5
    assert word.substructure == (2, 2, 3, 1)
    assert word.letters == ("A", "A", "L", "B", "E", "'", "I", "L")


# --- Letters -----------------------------------------------------------------


def test_names_are_uppercased_and_keep_accents():
    assert split("áed") == "ÁE · D"
    assert split("Maškaššaptu") == "M · A · ŠK · A · ŠŠ · A · PT · U"


def test_every_latin_letter_is_one_letter():
    assert split("Łukasz") == "Ł · U · K · A · SZ"
    assert split("Straße") == "STR · A · ẞ · E"  # ß stays one letter
    assert split("Işıl") == "I · Ş · ı · L"  # the dotless ı is not I
    assert split("Ŋgə") == "ŊG · Ə"
    assert split("Kaʻiulani") == "K · A · ʻ · IU · L · A · N · I"  # the ʻokina is a consonant


def test_the_apostrophe_is_a_vowel_or_a_consonant():
    assert split("Aalbe'il") == "AA · LB · E'I · L"  # a vowel, the usual choice
    assert split("D'Arcy") == "D · 'A · RC · Y"
    consonant = WordSplitter(LATIN_LETTERS.moved("'", vowel=False))
    assert split("Aalbe'il", consonant) == "AA · LB · E · ' · I · L"
    assert split("D'Arcy", consonant) == "D' · A · RC · Y"


def test_hyphen_is_a_consonant():
    assert split("Jean-Luc") == "J · EA · N-L · U · C"


def test_soft_consonants_off_keeps_h_separate():
    word = PLAIN.split_word("Mikhail")
    assert word.elements[2] == ("K", "H")
    assert word.substructure == (1, 1, 2, 2, 1)


def test_hh_is_not_a_soft_consonant():
    assert SOFT.split_word("Ahha").elements[1] == ("H", "H")


def test_h_after_a_vowel_stays_a_letter():
    assert split("Chemorah", SOFT) == "CH · E · M · O · R · A · H"
    assert SOFT.split_word("Chemorah").elements[0] == ("CH",)


def test_hyphen_before_h_is_not_soft():
    assert split("Ana-Hel", SOFT) == "A · N · A · -H · E · L"
    assert SOFT.split_word("Ana-Hel").elements[3] == ("-", "H")


# --- Custom special consonants -----------------------------------------------


def test_cluster_counts_as_one_letter():
    word = splitter(soft=True, clusters=["RR"]).split_word("Chemorrah")
    assert word.elements[4] == ("RR",)
    assert word.substructure == (1, 1, 1, 1, 1, 1, 1)


def test_cluster_with_a_soft_consonant():
    # Soft marking comes first, so L followed by TH is written {Lt}.
    word = splitter(soft=True, clusters=["Lt"]).split_word("Waltheof")
    assert word.elements == (("W",), ("A",), ("LTH",), ("E", "O"), ("F",))


def test_cluster_written_with_th_never_matches_under_soft():
    word = splitter(soft=True, clusters=["LTH"]).split_word("Waltheof")
    assert word.elements[2] == ("L", "TH")


def test_cluster_written_with_th_matches_without_soft():
    word = splitter(clusters=["LTH"]).split_word("Waltheof")
    assert word.elements[2] == ("LTH",)


def test_longer_clusters_are_tried_first():
    word = splitter(clusters=["RR", "RRN"]).split_word("Barrna")
    assert word.elements[2] == ("RRN",)


def test_clusters_do_not_overlap():
    word = splitter(clusters=["RR"]).split_word("Barrrna")
    assert word.elements[2] == ("RR", "R", "N")


# --- Y and U -----------------------------------------------------------------


def test_y_is_a_vowel_by_default():
    assert split("Yorath", SOFT) == "YO · R · A · TH"
    assert split("Bryn") == "BR · Y · N"


def test_y_as_consonant():
    assert split("Yorath", splitter(soft=True, y=YMode.CONSONANT)) == "Y · O · R · A · TH"
    assert split("Llyr", splitter(y=YMode.CONSONANT)) == "LLYR"  # one consonant element


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Yorath", "Y · O · R · A · TH"),  # begins the word, before a vowel: consonant
        ("Maya", "M · A · Y · A"),  # between vowels: consonant
        ("Ayibah", "A · Y · I · B · A · H"),
        ("Bryan", "BR · YA · N"),  # after a consonant: vowel
        ("Gwyn", "GW · Y · N"),  # before a consonant: vowel
        ("Kay", "K · AY"),  # at the end: vowel
        ("Ysolt", "Y · S · O · LT"),  # begins the word, before a consonant: vowel
    ],
)
def test_y_as_semi_vowel(name, expected):
    assert split(name, splitter(soft=True, y=YMode.SEMI)) == expected


def test_u_as_semi_vowel_after_q():
    semi = splitter(soft=True, u=UMode.SEMI)
    assert split("Sarquamichai", semi) == "S · A · RQU · A · M · I · CH · AI"
    assert split("Sarquamichai", SOFT) == "S · A · RQ · UA · M · I · CH · AI"
    assert split("Ulam", semi) == "U · L · A · M"


# --- Multi vowels ------------------------------------------------------------


def test_multi_vowel_is_one_letter_with_length_zero():
    word = splitter(multi=True).split_word("Hereman")
    assert word.elements == (("H",), ("ERE",), ("M",), ("A",), ("N",))
    assert word.substructure == (1, 0, 1, 1, 1)


def test_multi_vowel_around_a_soft_consonant():
    word = splitter(soft=True, multi=True).split_word("Ethelbald")
    assert word.describe() == "ETHE · LB · A · LD"
    assert word.substructure == (0, 2, 1, 2)
    assert word.structure == 5


def test_multi_vowel_needs_the_same_vowel_and_one_consonant():
    multi = splitter(multi=True)
    assert multi.split_word("Hirema").substructure == (1, 1, 1, 1, 1, 1)
    assert multi.split_word("Herrema").substructure == (1, 1, 2, 1, 1, 1)


def test_multi_vowel_joins_neighboring_vowels():
    word = splitter(multi=True).split_word("Taereon")
    assert word.elements == (("T",), ("A", "ERE", "O"), ("N",))
    assert word.substructure == (1, 0, 1)


# --- Names, spaces and problems ----------------------------------------------


def test_names_with_spaces_are_split_into_words():
    words = PLAIN.split_name("Van Helsing")
    assert [word.text for word in words] == ["Van", "Helsing"]
    assert [word.describe() for word in words] == ["V · A · N", "H · E · LS · I · NG"]


JOINT = WordSplitter(LATIN_LETTERS, hyphen=HyphenMode.JOINT)


def test_the_hyphen_is_a_consonant_letter_by_default():
    assert PLAIN.words_and_joints("Jean-Luc") == (("Jean-Luc",), ())
    assert split("Jean-Luc") == "J · EA · N-L · U · C"


@pytest.mark.parametrize(
    "name, words, joints",
    [
        ("Bel-Abrean", ("Bel", "Abrean"), ("-",)),
        ("Jean-Luc Picard", ("Jean", "Luc", "Picard"), ("-", " ")),
        ("Van Helsing", ("Van", "Helsing"), (" ",)),
        ("O-Ren", ("O", "Ren"), ("-",)),  # O is a word of one element (spec 13.10)
        ("Mira", ("Mira",), ()),
    ],
)
def test_the_hyphen_as_a_joint_splits_a_name_into_words(name, words, joints):
    assert JOINT.words_and_joints(name) == (words, joints)
    assert tuple(word.text for word in JOINT.split_name(name)) == words


@pytest.mark.parametrize("name", ["Ana-", "-Ana", "An--a", "Ana - Bel"])
def test_a_hyphen_as_a_joint_must_join_two_words(name):
    with pytest.raises(WordSplitError, match="has a hyphen that does not join two words"):
        JOINT.split_name(name)
    assert PLAIN.split_name(name)  # as a consonant letter, it is part of a word


@pytest.mark.parametrize(
    "text, elements, structure", [("Ao", "AO", -1), ("Aei", "AEI", -1), ("Ng", "NG", -2), ("Y", "Y", -1)]
)
def test_a_word_of_only_vowels_or_only_consonants_is_one_element(text, elements, structure):
    word = PLAIN.split_word(text)
    assert word.describe() == elements
    assert word.structure == structure
    assert word.substructure == (len(text),)
    assert word.starts_with_vowel == (structure == -1)


def test_a_word_of_one_element_may_hold_a_multi_vowel():
    word = splitter(multi=True).split_word("Aha")
    assert word.describe() == "AHA"
    assert (word.structure, word.substructure) == (-1, (0,))


def test_invalid_character():
    with pytest.raises(WordSplitError, match="'1' is not a vowel or consonant letter"):
        PLAIN.split_name("An1a")


def test_empty_name():
    with pytest.raises(WordSplitError, match="empty"):
        PLAIN.split_name("  ")


def test_custom_letter_classes_decide_y_unless_a_mode_is_chosen():
    letters = LetterClasses.from_strings("AEIOU", "KLMNPTY")
    # The default Y setting leaves Y as these letter classes say: a consonant.
    assert WordSplitter(letters).split_word("Kyla").describe() == "KYL · A"
    # y:semi decides by position: here a consonant follows Y, so Y is a vowel.
    semi = WordSplitter(letters, y_mode=YMode.SEMI)
    assert semi.split_word("Kyla").describe() == "K · Y · L · A"
