"""The input check (spec 10.2): limits, hidden characters and letters, for every kind of file."""

import unicodedata

import pytest

from conftest import SAMPLE_CHAPTERS
from namebook.formats import load_style
from namebook.formats.check import (
    MAX_LIST_ITEMS,
    MAX_NAME_LENGTH,
    MAX_NAMES,
    MAX_RECIPE_LENGTH,
    MAX_REPORTED,
    check_style,
    style_problems,
)
from namebook.formats.common import LOOK_ALIKES, MAX_FILE_SIZE, read_file, shorten
from namebook.style import NameList, SpecialLetters, Style, StyleError, Template
from namebook.validation import load_word_list


def make_style(*names, **fields):
    return Style(title="T", name_lists=(NameList("list", names),), **fields)


def load_problems(path):
    with pytest.raises(StyleError) as caught:
        load_style(path)
    return caught.value.problems


@pytest.mark.parametrize("path", SAMPLE_CHAPTERS, ids=lambda path: path.name)
def test_the_sample_chapters_pass(path):
    assert style_problems(load_style(path)) == []


# --- Size limits -----------------------------------------------------------


@pytest.mark.parametrize("suffix", [".ebn", ".toml"])
def test_a_file_that_is_too_large_is_not_read(tmp_path, suffix):
    path = tmp_path / f"huge{suffix}"
    path.write_bytes(b"#" * (MAX_FILE_SIZE + 1))
    assert load_problems(path) == (
        "The file is larger than 5 MB, the most namebook reads. Is it the right file?",
    )


def test_a_file_at_the_limit_is_read(tmp_path):
    path = tmp_path / "big.txt"
    path.write_bytes(b"x" * MAX_FILE_SIZE)
    assert len(read_file(path)) == MAX_FILE_SIZE


def test_too_many_names():
    style = make_style(*(["Ana"] * (MAX_NAMES + 1)))
    assert style_problems(style) == [f"The style has 50,001 names; the most namebook accepts is {MAX_NAMES:,}."]


def test_a_name_that_is_too_long():
    long_name = "Ana" * 21
    assert style_problems(make_style("Bel", long_name)) == [
        f'Name 2 ("{long_name[:27]}...") is 63 characters long; the most is {MAX_NAME_LENGTH}.'
    ]


def test_too_many_templates_and_clusters():
    style = make_style(
        "Ana",
        templates=tuple(Template("#") for _ in range(MAX_LIST_ITEMS + 1)),
        special_letters=SpecialLetters(consonant_clusters=("RR",) * (MAX_LIST_ITEMS + 1)),
    )
    assert style_problems(style) == [
        "The style has 101 templates; the most is 100.",
        "The style has 101 consonant clusters; the most is 100.",
    ]


def test_a_template_that_is_too_long():
    recipe = "#" * (MAX_RECIPE_LENGTH + 1)
    (problem,) = style_problems(make_style("Ana", templates=(Template(recipe),)))
    assert problem.endswith(f"is 101 characters long; the most is {MAX_RECIPE_LENGTH}.")


# --- Hidden characters -----------------------------------------------------


def test_control_character_in_a_chapter_title(tmp_path):
    path = tmp_path / "escape.ebn"
    path.write_text('TITLE = "Evil\x1b[2J"; NAMES = "Ana";', encoding="utf-8")
    assert load_problems(path) == (
        "The title contains a control character (U+001B). Invisible and control characters "
        "are not allowed, because they can hide or disguise text.",
    )


def test_text_direction_character_in_a_description(tmp_path):
    path = tmp_path / "Mirror.toml"
    path.write_text('description = "abc‮def"\nnames = "Ana"', encoding="utf-8")
    (problem,) = load_problems(path)
    assert problem.startswith("The description contains a text-direction character (U+202E).")


def test_invisible_character_in_a_name_is_shown_as_a_question_mark(tmp_path):
    path = tmp_path / "Zero.toml"
    path.write_text('names = "Bel, A​na"', encoding="utf-8")
    assert load_problems(path) == (
        'Name 2 ("A?na") contains an invisible character (U+200B), '
        "which is not a vowel or consonant letter.",
    )


def test_hidden_character_in_a_template():
    (problem,) = style_problems(make_style("Ana", templates=(Template("5‮7"),)))
    assert problem.startswith('Template "5?7" contains a text-direction character (U+202E).')


def test_hidden_character_in_a_list_name():
    style = Style(title="T", name_lists=(NameList("given​", ("Ana",)), NameList("family", ("Bel",))))
    (problem,) = style_problems(style)
    assert problem.startswith('The list name "given?" contains an invisible character (U+200B).')


# --- Letters, and how problems are reported --------------------------------


def test_letters_outside_the_letter_classes(tmp_path):
    path = tmp_path / "Bad.toml"
    path.write_text('names = "Ana, B3l, C#r"', encoding="utf-8")
    assert load_problems(path) == (
        "Name 2 (\"B3l\") contains '3', which is not a vowel or consonant letter.",
        "Name 3 (\"C#r\") contains '#', which is not a vowel or consonant letter.",
    )


def test_every_latin_letter_is_accepted_and_other_alphabets_are_not(tmp_path):
    path = tmp_path / "World.txt"
    path.write_text("Łucja, Ōta, Ngozi Ọkọnkwọ, Straße, Işıl, Ƙanƙara\n", encoding="utf-8")
    assert len(load_style(path).names) == 6
    path.write_text("Łucja, Борис", encoding="utf-8")
    assert load_problems(path) == (
        "Name 2 (\"Борис\") contains 'Б', 'о', 'р', 'и' and 'с', which are not a vowel or consonant letter.",
    )


# --- Look-alike characters (spec 13.12) ------------------------------------


def test_look_alikes_in_a_list_are_read_as_plain_characters(tmp_path):
    path = tmp_path / "Copied.txt"
    path.write_text(
        "D’Arcy, Jean–Luc, O´Brien\nVan\u00a0Helsing, Marie\u2010Claire\n# a dash – in a comment\n",
        encoding="utf-8",
    )
    style = load_style(path)
    assert style.names == ("D'Arcy", "Jean-Luc", "O'Brien", "Van Helsing", "Marie-Claire")
    assert style.source.warnings == (
        "2 apostrophes, 2 hyphens and 1 space in the names were typed as look-alike characters, "
        "and are read as plain ones.",
    )


def test_look_alikes_in_a_style_file_and_a_chapter(tmp_path):
    style_file = tmp_path / "Two.toml"
    style_file.write_text(
        'description = "Names – from a web page"\n[names]\ngiven = "Ana, Ma’el"\nfamily = "Bel"',
        encoding="utf-8",
    )
    style = load_style(style_file)
    assert style.name_list("given").names == ("Ana", "Ma'el")
    assert style.description == ("Names – from a web page",)  # only names are made plain
    assert style.source.warnings == (
        "1 apostrophe in the names was typed as a look-alike character, and is read as a plain one.",
    )
    chapter = tmp_path / "Old.ebn"
    chapter.write_bytes('TITLE = "Old"; NAMES = "Ana", "Bel—Cor";'.encode("cp1252"))
    assert load_style(chapter).names == ("Ana", "Bel-Cor")


def test_a_list_without_look_alikes_has_no_warning(tmp_path):
    path = tmp_path / "Plain.txt"
    path.write_text("D'Arcy, Jean-Luc, Van  Helsing", encoding="utf-8")
    assert load_style(path).source.warnings == ()


def test_every_space_is_a_look_alike_of_the_plain_space():
    spaces = {chr(code) for code in range(0x10000) if unicodedata.category(chr(code)) == "Zs"}
    assert {char for char, plain in LOOK_ALIKES.items() if plain == " "} == spaces - {" "}


def test_letters_of_other_alphabets_are_not_look_alikes(tmp_path):
    path = tmp_path / "Mixed.txt"
    path.write_text("Ana, B\u0435l", encoding="utf-8")  # a Cyrillic e
    assert load_problems(path) == (
        "Name 2 (\"B\u0435l\") contains '\u0435', which is not a vowel or consonant letter.",
    )


def test_the_okina_is_a_consonant_not_an_apostrophe(tmp_path):
    path = tmp_path / "Hawaiian.txt"
    path.write_text("Kaʻiulani, ʻIolani", encoding="utf-8")
    style = load_style(path)
    assert style.names == ("Kaʻiulani", "ʻIolani")
    assert style.source.warnings == ()


def test_names_in_one_of_several_lists_say_which_list(tmp_path):
    path = tmp_path / "Two.toml"
    path.write_text('[names]\ngiven = "Ana"\nfamily = "Bel, D4n"', encoding="utf-8")
    assert load_problems(path) == (
        "Name 2 in list \"family\" (\"D4n\") contains '4', which is not a vowel or consonant letter.",
    )


def test_only_the_first_problems_are_listed():
    style = make_style(*(f"B{n}l" for n in range(MAX_REPORTED + 5)))
    with pytest.raises(StyleError) as caught:
        check_style(style)
    problems = caught.value.problems
    assert len(problems) == MAX_REPORTED + 1
    assert problems[-1] == "... and 5 more problems."
    assert caught.value.source == "T"


def test_duplicates_are_allowed():
    assert style_problems(make_style("Ana", "Ana", "Bel")) == []


# --- Word lists ------------------------------------------------------------


def test_word_list_in_windows_1252(tmp_path):
    path = tmp_path / "old.txt"
    path.write_bytes("# blocked\nAëlin\n*mog*\n".encode("cp1252"))
    assert load_word_list(path) == ("Aëlin", "*mog*")


def test_word_list_look_alikes_are_read_as_plain_characters(tmp_path):
    path = tmp_path / "blocked.txt"
    path.write_text("D’Arcy\n*–mog*\n", encoding="utf-8")
    assert load_word_list(path) == ("D'Arcy", "*-mog*")


def test_word_list_that_is_too_large(tmp_path):
    path = tmp_path / "huge.txt"
    path.write_bytes(b"a\n" * (MAX_FILE_SIZE // 2 + 1))
    with pytest.raises(StyleError) as caught:
        load_word_list(path)
    assert caught.value.problems[0].startswith("The file is larger than 5 MB")


# --- Several lists (spec 13.5) ----------------------------------------------------


def test_list_names_cannot_have_braces():
    style = Style(title="T", name_lists=(NameList("gi{ven", ("Ana",)), NameList("family", ("Bel",))))
    assert style_problems(style) == ['The list name "gi{ven" has a { or }, which list names cannot have.']


def test_too_many_lists():
    lists = tuple(NameList(f"l{n}", ("Ana",)) for n in range(MAX_LIST_ITEMS + 1))
    assert style_problems(Style(title="T", name_lists=lists)) == [
        f"The style has {MAX_LIST_ITEMS + 1} lists; the most is {MAX_LIST_ITEMS}."
    ]


def test_a_lists_own_templates_are_checked():
    recipe = "#" * (MAX_RECIPE_LENGTH + 1)
    style = Style(
        title="T",
        name_lists=(NameList("given", ("Ana",), {"templates": (Template(recipe),)}), NameList("family", ("Bel",))),
    )
    assert style_problems(style) == [
        f'Template "{shorten(recipe)}" in list "given" is {MAX_RECIPE_LENGTH + 1} characters long; '
        f"the most is {MAX_RECIPE_LENGTH}."
    ]
