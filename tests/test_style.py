"""The neutral style object and letter classes."""

import pytest

from namebook.letters import (
    CLASSIC_CONSONANTS,
    CLASSIC_VOWELS,
    LATIN_LETTERS,
    LetterClasses,
    capitals,
    small,
)
from namebook.style import (
    SETTINGS,
    ApostropheMode,
    GenerationOptions,
    HyphenMode,
    NameList,
    Style,
    Template,
    YMode,
    pin_working_settings,
)


def test_letter_classes_keep_the_classic_letters():
    assert all(LATIN_LETTERS.is_vowel(letter) for letter in CLASSIC_VOWELS)
    assert all(LATIN_LETTERS.is_consonant(letter) for letter in CLASSIC_CONSONANTS)
    assert LATIN_LETTERS.is_vowel("'")  # the apostrophe is a vowel letter
    assert LATIN_LETTERS.is_consonant("-")  # the hyphen is a consonant letter
    assert not LATIN_LETTERS.is_letter("a")  # classes hold uppercase letters only
    assert not LATIN_LETTERS.is_letter("á")


# Every Latin letter (spec 13.8).
@pytest.mark.parametrize("letter", "ČŘŞĞŁĐĦŦŻĆŃŚĽŇŤĎƁƊƘƝȘȚ")
def test_a_letter_with_a_consonant_inside_is_a_consonant(letter):
    assert LATIN_LETTERS.is_consonant(letter)


@pytest.mark.parametrize("letter", "ŐŰĀĒĪŌŪĘĄĚŮƠƯẸỌỲŶǾƗ")
def test_a_letter_with_a_vowel_inside_is_a_vowel(letter):
    assert LATIN_LETTERS.is_vowel(letter)


def test_letters_with_no_letter_inside_have_a_class_of_their_own():
    assert all(LATIN_LETTERS.is_consonant(letter) for letter in "ŊẞƷǮƔƉʻ")  # ʻ: the ʻokina
    assert all(LATIN_LETTERS.is_vowel(letter) for letter in "ƏƐƆı")


@pytest.mark.parametrize("char", ["Ω", "Ж", "3", "#", "Ａ", "ʘ", "ǰ"])
def test_other_alphabets_and_symbols_are_not_letters(char):
    # Fullwidth Ａ is not a Latin letter, ʘ is phonetic, and ǰ has no capital.
    assert LATIN_LETTERS.invalid_characters(char) == [char]


def test_letters_are_kept_in_capitals_one_character_each():
    assert capitals("Łódź") == "ŁÓDŹ"
    assert capitals("Straße") == "STRAẞE"  # not STRASSE
    assert capitals("Işıl İlkay") == "IŞıL İLKAY"  # ı stays apart from i
    assert small("STRAẞE İLKAY") == "straße ilkay"
    assert LATIN_LETTERS.invalid_characters("Łukasz Ōta Straße Yıldız") == []


def test_letter_classes_can_be_replaced():
    letters = LetterClasses.from_strings("AEIOU", "KLMNPT")
    assert letters.is_vowel("E")
    assert not letters.is_letter("Y")
    assert letters.invalid_characters("Kyle") == ["y"]
    # A letter inside another follows the style's lists.
    assert letters.is_vowel("Ā") and letters.is_consonant("Ł")
    assert not letters.is_letter("Ŷ")


def test_the_apostrophe_setting_moves_it_between_the_letter_classes():
    style = Style(title="T", name_lists=(NameList("list", ("Ka'ren",)),))
    assert style.setting("letter_apostrophe") is ApostropheMode.VOWEL  # the usual choice
    changed = style.with_settings({"letter_apostrophe": ApostropheMode.CONSONANT})
    assert changed.setting("letter_apostrophe") is ApostropheMode.CONSONANT
    assert changed.letter_classes.is_consonant("'") and not changed.letter_classes.is_vowel("'")
    assert changed.letter_classes == LATIN_LETTERS.moved("'", vowel=False)  # nothing else moves
    assert changed.with_settings({"letter_apostrophe": ApostropheMode.VOWEL}).letter_classes == LATIN_LETTERS


def test_letter_cannot_be_in_both_classes():
    with pytest.raises(ValueError):
        LetterClasses.from_strings("AEY", "BCY")


def test_invalid_characters_allows_spaces_and_lists_each_once():
    assert LATIN_LETTERS.invalid_characters("Van Helsing") == []
    assert LATIN_LETTERS.invalid_characters("A1b1c2") == ["1", "2"]


@pytest.mark.parametrize("fit_level", [-1, 4])
def test_fit_level_must_be_in_range(fit_level):
    with pytest.raises(ValueError):
        GenerationOptions(fit_level=fit_level)


@pytest.mark.parametrize("validation_level", [-1, 3])
def test_validation_level_must_be_in_range(validation_level):
    with pytest.raises(ValueError):
        GenerationOptions(validation_level=validation_level)


def test_style_with_several_name_lists():
    style = Style(
        title="Two lists",
        name_lists=(NameList("given", ("Ana",)), NameList("family", ("Bel",))),
    )
    assert style.names == ("Ana",)  # the first list is the default
    assert style.name_list("family").names == ("Bel",)
    with pytest.raises(KeyError):
        style.name_list("nickname")


def test_style_needs_a_name_list_and_distinct_list_names():
    with pytest.raises(ValueError):
        Style(title="Empty", name_lists=())
    with pytest.raises(ValueError):
        Style(title="Twice", name_lists=(NameList("a", ("X",)), NameList("a", ("Y",))))


def test_settings_by_name():
    style = Style(title="T", name_lists=(NameList("list", ("Ana",)),))
    changed = style.with_settings({"fit_level": 1, "soft_consonants": True, "letter_y": YMode.SEMI})
    assert changed.setting("fit_level") == 1 and changed.options.fit_level == 1
    assert changed.setting("soft_consonants") is True
    assert changed.options.y_mode is YMode.SEMI
    assert changed.setting("templates") == ()
    assert style.options.fit_level == 3  # the original is unchanged


def test_unknown_settings_by_name():
    style = Style(title="T", name_lists=(NameList("list", ("Ana",)),))
    with pytest.raises(KeyError):
        style.with_settings({"fitlevel": 1})
    with pytest.raises(KeyError):
        style.setting("fitlevel")


# --- Several lists (spec 13.5) --------------------------------------------------


def two_lists(**fields):
    return Style(
        title="T",
        name_lists=(
            NameList("given", ("Ana", "Bel")),
            NameList("family", ("Cor-Dan",), {"letter_hyphen": HyphenMode.JOINT, "fit_level": 1}),
        ),
        **fields,
    )


def test_a_list_as_a_style_of_its_own_has_its_settings_over_the_styles():
    style = two_lists(pinned=frozenset({"fit_level", "prefixes"})).with_settings({"fit_level": 2})
    family = style.list_style("family")
    assert family.names == ("Cor-Dan",) and family.list_names == ("family",)
    assert family.options.fit_level == 1  # the list's own
    assert family.options.hyphen is HyphenMode.JOINT
    assert family.pinned == {"fit_level", "prefixes", "letter_hyphen"}
    assert family.name_lists[0].settings == {}  # already applied
    given = style.list_style("given")
    assert given.options.fit_level == 2 and given.options.hyphen is HyphenMode.CONSONANT
    assert given.pinned == {"fit_level", "prefixes"}


def test_by_default_names_come_from_the_first_list():
    assert two_lists().default_combination == Template("{given}")
    assert two_lists().list_style("given").default_combination == Template("{given}")


def test_a_list_can_only_pin_known_settings():
    with pytest.raises(ValueError):
        Style(title="T", name_lists=(NameList("a", ("X",), {"fitlevel": 1}),))


def test_pinning_working_settings_shares_what_every_list_has_alike():
    style = two_lists()
    working = {
        "given": style.list_style("given").with_settings({"markov_order": 3}),
        "family": style.list_style("family").with_settings({"markov_order": 2}),
    }
    pinned = pin_working_settings(style, working)
    assert pinned.pinned == set(SETTINGS) - {"markov_order", "letter_hyphen", "fit_level"}
    assert pinned.name_list("given").settings == {
        "letter_hyphen": HyphenMode.CONSONANT, "fit_level": 3, "markov_order": 3
    }
    assert pinned.name_list("family").settings == {
        "letter_hyphen": HyphenMode.JOINT, "fit_level": 1, "markov_order": 2
    }
    for name in ("given", "family"):
        assert pinned.list_style(name).options == working[name].options
