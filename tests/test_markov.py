"""The Markov chain (spec 13.2)."""

import itertools
import math

import pytest

from conftest import CHAPTERS_DIR
from namebook.formats import load_style
from namebook.letters import LATIN_LETTERS
from namebook.markov import (
    END,
    MAX_LOOK_BACK,
    SPACE,
    Chain,
    internal_chain,
    internal_letters,
    letter_symbol,
    plain_letters,
    symbol_is_vowel,
    word_letters,
)
from namebook.style import HyphenMode, SpecialLetters, YMode
from namebook.words import WordSplitter


def chain_of(*names, look_back=None):
    return Chain((plain_letters(name) for name in names), look_back)


@pytest.fixture(scope="module")
def angelic():
    style = load_style(CHAPTERS_DIR / "Angelic.ebn")
    return Chain(plain_letters(name) for name in style.name_lists[0].names)


def test_plain_letters_are_capitals_with_one_space_between_words():
    assert plain_letters("Aalbe'il") == tuple("AALBE'IL")
    assert plain_letters(" van  Helsing-Ra ") == tuple("VAN HELSING-RA")


def test_chances_after_a_run_add_up_to_just_under_one():
    chain = chain_of("Anna", "Hanna", "Nana")
    for before in ("", "A", "AN", "ANN", "HANNA"):
        chances = chain.chances_after(tuple(before))
        assert set(chances) == {"A", "N", "H", END}
        assert 0.9 < sum(chances.values()) < 1  # the rest is for letters never seen


def test_seen_letters_are_likelier_than_unseen_ones():
    chain = chain_of("Anna", "Hanna", "Nana")
    after_n = chain.chances_after(("A", "N"))
    assert after_n["N"] > after_n["H"]  # NN was seen, NH never
    assert chain.chances_after(())["H"] > 0  # but unseen joins still get a chance


def test_a_name_starts_and_ends_as_the_seeds_do():
    chain = chain_of("Anna", "Hanna", "Nana")
    start = chain.chances_after(())
    assert start["A"] > start[END] and start["H"] > start[END]
    assert chain.chances_after(tuple("ANNA"))[END] > chain.chances_after(tuple("AN"))[END]


def test_seed_like_names_score_higher_than_jumbled_ones(angelic):
    assert angelic.score(plain_letters("Raphael")) > angelic.score(plain_letters("Xqzrtkp"))


def test_hiding_a_name_is_the_same_as_learning_without_it():
    names = ["Anael", "Ariel", "Lariel", "Nael", "Rael", "Ilana", "Lerina"]
    chain = chain_of(*names, "Lariel", look_back=2)  # Lariel twice: both copies are hidden
    without = chain_of(*(name for name in names if name != "Lariel"), look_back=2)
    assert math.isclose(chain.hidden_score(plain_letters("Lariel")),
                        without.score(plain_letters("Lariel")))  # fmt: skip
    assert chain.hidden_score(plain_letters("Lariel")) < chain.score(plain_letters("Lariel"))


def test_hidden_score_of_a_name_that_is_not_a_seed_is_its_score():
    chain = chain_of("Anna", "Hanna")
    assert chain.hidden_score(plain_letters("Nan")) == chain.score(plain_letters("Nan"))


def test_look_back_is_the_one_under_which_hidden_names_are_likeliest(angelic):
    totals = angelic.hidden_totals
    assert len(totals) == MAX_LOOK_BACK + 1
    assert totals[angelic.look_back] == max(totals[1:])


def test_look_back_grows_when_letters_depend_on_more_than_the_last_one():
    # After A comes Y if X came before it, and W if Z did: one letter of
    # look-back cannot tell, two can.
    units = ("XAY", "ZAW")
    names = ["".join(parts) for size in (1, 2, 3) for parts in itertools.product(units, repeat=size)]
    assert chain_of(*names).look_back >= 2


def test_the_hidden_log_chance_is_the_hidden_score_for_every_letter():
    chain = chain_of("Anael", "Ariel", "Lariel", "Nael", "Rael", "Ilana", "Lerina")
    letters = plain_letters("Lariel")
    assert math.isclose(chain.hidden_log_chance(letters), chain.hidden_score(letters) * (len(letters) + 1))


def test_the_look_back_can_be_chosen_by_hiding_only_some_names(angelic):
    names = list(angelic.copies)
    hiding = names[::3]
    chain = Chain(names, hiding=hiding)
    totals = chain.hidden_totals
    assert totals[chain.look_back] == max(totals[1:])
    # Only the names hidden count towards the totals, each scored as by the full chain.
    size = chain.look_back
    full = Chain(names, look_back=size)
    assert math.isclose(totals[size], sum(full.hidden_log_chance(name) for name in hiding))


def test_a_given_look_back_is_used():
    assert chain_of("Anna", "Hanna", look_back=4).look_back == 4
    with pytest.raises(ValueError, match="look-back"):
        chain_of("Anna", look_back=MAX_LOOK_BACK + 1)


# --- The internal form, for building words -------------------------------------


def test_a_letter_used_as_the_other_class_is_its_own_letter():
    assert letter_symbol("Y", vowel=False, classes=LATIN_LETTERS) == "y"  # Y as a consonant
    assert letter_symbol("Y", vowel=True, classes=LATIN_LETTERS) == "Y"
    assert letter_symbol("TH", vowel=False, classes=LATIN_LETTERS) == "TH"
    assert word_letters((("Y",), ("A",), ("TH",)), False, LATIN_LETTERS) == ("y", "A", "TH")


def test_the_dotless_i_is_a_vowel_of_its_own():
    assert symbol_is_vowel("ı", LATIN_LETTERS)
    elements = (("Y", "ı"), ("L", "D"), ("ı",), ("Z",))  # Yıldız
    assert word_letters(elements, True, LATIN_LETTERS) == ("Y", "ı", "L", "D", "ı", "Z")
    assert plain_letters("Işıl Straße") == ("I", "Ş", "ı", "L", SPACE, "S", "T", "R", "A", "ẞ", "E")


def test_a_name_the_splitter_cannot_split_has_no_internal_letters():
    splitter = WordSplitter(LATIN_LETTERS)
    assert internal_letters(splitter, "An4") is None
    assert internal_letters(splitter, "Ana Bel") == ("A", "N", "A", SPACE, "B", "E", "L")
    assert internal_letters(splitter, "Ya") == ("Y", "A")  # one vowel element


def test_the_building_chain_reads_names_as_the_style_splits_them():
    splitter = WordSplitter(LATIN_LETTERS, SpecialLetters(soft_consonants=True), YMode.SEMI)
    chain = internal_chain(splitter, ["Yara Thal", "Ao", "R2"])  # R2 cannot be split: left out
    assert list(chain.copies) == [("y", "A", "R", "A", SPACE, "TH", "A", "L"), ("A", "O")]


def test_the_building_chain_reads_a_hyphen_as_a_joint_between_words():
    joint = WordSplitter(LATIN_LETTERS, hyphen=HyphenMode.JOINT)
    assert internal_letters(joint, "Bel-Ira Cor") == ("B", "E", "L", "-", "I", "R", "A", SPACE, "C", "O", "R")
    assert internal_letters(joint, "Bel-") is None  # the hyphen joins nothing
    # As a consonant, the hyphen is a letter of the word, with the same symbol.
    assert internal_letters(WordSplitter(LATIN_LETTERS), "Bel-Ira") == ("B", "E", "L", "-", "I", "R", "A")
