"""Plain name lists, and finding a style by its name."""

import dataclasses
from pathlib import Path

import pytest

from namebook.formats import find_style, load_style
from namebook.formats.toml import parse_toml
from namebook.formats.txt import parse_txt, read_txt
from namebook.style import DEFAULT_LIST, GenerationOptions, StyleError

# --- Reading plain name lists ----------------------------------------------


def test_a_plain_list_is_just_names():
    style = parse_txt("Ana\nBel\n", "styles/Mini.txt")
    assert style.title == "Mini"  # from the file name
    assert style.description == ()
    assert [name_list.name for name_list in style.name_lists] == [DEFAULT_LIST]
    assert style.names == ("Ana", "Bel")
    assert style.pinned == frozenset()
    assert style.options == GenerationOptions()
    assert style.templates == ()
    assert style.source.format == "txt"


def test_names_by_line_or_comma_skipping_comments_and_blank_lines():
    style = parse_txt("# Short ones\nAna, Bel\n\n  Cor   Dan \nAna,\n", "x.txt")
    assert style.names == ("Ana", "Bel", "Cor Dan", "Ana")  # repeats are kept


def test_a_plain_list_is_the_same_as_a_style_file_of_only_names():
    from_txt = parse_txt("Aalbe'il\nAanfe'il\n", "Angelic.txt")
    from_toml = parse_toml('names = """\nAalbe\'il\nAanfe\'il\n"""', "Angelic.toml")
    assert dataclasses.replace(from_txt, source=from_toml.source) == from_toml


def test_utf8_with_or_without_a_byte_order_mark(tmp_path):
    for encoding in ("utf-8", "utf-8-sig"):
        path = tmp_path / f"{encoding}.txt"
        path.write_text("Aëlin\nËa\n", encoding=encoding)
        style = read_txt(path)
        assert style.names == ("Aëlin", "Ëa") and style.source.encoding == "utf-8"


def test_windows_1252_lists_are_read_too(tmp_path):
    path = tmp_path / "old.txt"
    path.write_bytes("Aëlin\r\nËa\r\n".encode("cp1252"))
    style = read_txt(path)
    assert style.names == ("Aëlin", "Ëa") and style.source.encoding == "windows-1252"


@pytest.mark.parametrize("text", ["", "\n\n", "# only a comment\n"])
def test_a_list_without_names(text):
    with pytest.raises(StyleError) as caught:
        parse_txt(text, "empty.txt")
    assert caught.value.problems == ("The file has no names. Write them one per line.",)


def test_plain_lists_pass_the_input_check(tmp_path):
    path = tmp_path / "bad.txt"
    path.write_text("Ana\nB3l\n", encoding="utf-8")
    with pytest.raises(StyleError) as caught:
        load_style(path)
    assert caught.value.problems == (
        "Name 2 (\"B3l\") contains '3', which is not a vowel or consonant letter.",
    )


# --- Finding a style by its name -------------------------------------------


@pytest.fixture
def folder(tmp_path):
    folder = tmp_path / "styles"
    folder.mkdir()
    return folder


def test_an_existing_file_is_used_as_it_is(folder):
    path = folder / "Fae.txt"
    path.write_text("Ana", encoding="utf-8")
    assert find_style(str(path), folder).path == path


def test_a_path_with_a_folder_is_used_as_it_is_even_if_missing(folder):
    # The reader then reports that the file cannot be opened.
    assert find_style("elsewhere/Fae.txt", folder).path == Path("elsewhere/Fae.txt")


def test_a_name_finds_a_style_file_or_a_plain_list(folder):
    (folder / "Angelic.toml").write_text('names = "Ana"', encoding="utf-8")
    (folder / "Fae.txt").write_text("Ana", encoding="utf-8")
    assert find_style("Angelic", folder).path == folder / "Angelic.toml"
    assert find_style("Fae", folder).path == folder / "Fae.txt"


def test_a_name_ignores_capitals_and_gives_the_file_its_own(folder):
    (folder / "Angelic.toml").write_text('names = "Ana"', encoding="utf-8")
    found = find_style("aNGELIC", folder)
    assert found.path.name == "Angelic.toml"  # so the title comes from the real file name
    assert found.note is None


def test_a_style_file_wins_over_a_plain_list_with_a_note(folder):
    (folder / "Fae.txt").write_text("Ana", encoding="utf-8")
    (folder / "Fae.toml").write_text('names = "Bel"', encoding="utf-8")
    found = find_style("fae", folder)
    assert found.path == folder / "Fae.toml"
    assert found.note == f"Note: {folder / 'Fae.txt'} is also there; using {folder / 'Fae.toml'}."


def test_a_name_with_an_extension_finds_that_file(folder):
    (folder / "Fae.txt").write_text("Ana", encoding="utf-8")
    (folder / "Fae.toml").write_text('names = "Bel"', encoding="utf-8")
    found = find_style("Fae.txt", folder)
    assert found.path == folder / "Fae.txt" and found.note is None


def test_other_files_in_the_folder_are_not_styles(folder):
    (folder / "README.md").write_text("# Styles", encoding="utf-8")
    (folder / "Demonic.ebn").write_text('TITLE = "D"; NAMES = "Ana";', encoding="utf-8")
    with pytest.raises(StyleError) as caught:
        find_style("Demonic", folder)
    assert caught.value.problems == (f'There is no file "Demonic", and no style called "Demonic" in {folder}.',)


def test_an_unknown_name_lists_the_styles_there(folder):
    for name in ("Fae.txt", "Fae.toml", "Angelic.toml"):
        (folder / name).write_text('names = "Ana"', encoding="utf-8")
    with pytest.raises(StyleError) as caught:
        find_style("Angel", folder)
    assert caught.value.problems == (
        f'There is no file "Angel", and no style called "Angel" in {folder}. Styles there: Angelic, Fae.',
    )


def test_no_styles_folder(tmp_path):
    with pytest.raises(StyleError) as caught:
        find_style("Angelic", tmp_path / "styles")
    assert caught.value.problems[0].endswith("in " + str(tmp_path / "styles") + ".")
