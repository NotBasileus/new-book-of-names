"""Style files (TOML): converting chapters, reading and writing."""

import dataclasses
import random

import pytest

from conftest import CHAPTERS_DIR, SAMPLE_CHAPTERS
from namebook.analysis import analyze
from namebook.formats import load_style, save_style
from namebook.formats.toml import parse_toml, read_toml, write_toml
from namebook.generator import NameGenerator
from namebook.style import (
    DEFAULT_LIST,
    SETTINGS,
    ApostropheMode,
    GenerationOptions,
    HyphenMode,
    Markov,
    Punctuation,
    StyleError,
    Template,
    UMode,
    YMode,
)


def problems_of(text):
    with pytest.raises(StyleError) as caught:
        parse_toml(text, path="test.toml")
    return caught.value.problems


def made_names(style, count=300, seed=5):
    generator = NameGenerator(analyze(style), rng=random.Random(seed))
    return [generator.generate().text for _ in range(count)]


# --- Converting the sample chapters ----------------------------------------


@pytest.mark.parametrize("path", SAMPLE_CHAPTERS, ids=lambda path: path.name)
def test_a_converted_chapter_is_the_same_style(path, tmp_path):
    chapter = load_style(path)
    save_style(chapter, tmp_path / f"{path.stem}.toml")
    converted = load_style(tmp_path / f"{path.stem}.toml")
    assert converted.source.format == "toml"
    assert converted.pinned == set(SETTINGS)
    assert dataclasses.replace(converted, source=chapter.source) == chapter


@pytest.mark.parametrize("path", SAMPLE_CHAPTERS, ids=lambda path: path.name)
def test_a_converted_chapter_makes_the_same_names(path, tmp_path):
    chapter = load_style(path)
    save_style(chapter, tmp_path / "converted.toml")
    assert made_names(load_style(tmp_path / "converted.toml")) == made_names(chapter)


def test_saving_again_gives_the_same_file(tmp_path):
    save_style(load_style(CHAPTERS_DIR / "Demonic.ebn"), tmp_path / "first.toml")
    save_style(load_style(tmp_path / "first.toml"), tmp_path / "second.toml")
    assert (tmp_path / "second.toml").read_text(encoding="utf-8") == (
        tmp_path / "first.toml"
    ).read_text(encoding="utf-8")


# --- Writing ---------------------------------------------------------------


def test_written_file_has_presentation_settings_and_names_in_order():
    text = write_toml(load_style(CHAPTERS_DIR / "Angelic.ebn"))
    assert text.index('title = "Angelic"') < text.index("[settings]") < text.index("[names]")
    assert 'description = """\nProper Names\nof the Celestial Watchers\n"""' in text
    assert "soft_consonants = true" in text and "# CH, KH, TH" in text
    assert 'list = """\nAalbe\'il\nAanfe\'il\n' in text
    assert text.endswith('Zuphlasah\n"""\n')


def test_markov_settings_are_written_and_read_back():
    style = parse_toml(
        'names = "Ana"\n[settings]\nmarkov = "weighted"\nmarkov_order = 2\nmarkov_strength = 1',
        path="x.toml",
    )
    text = write_toml(style)
    assert 'markov = "weighted"' in text and "markov_order = 2" in text
    assert "markov_strength = 1.0" in text
    assert parse_toml(text, path="x.toml").options == style.options


def test_only_pinned_settings_are_written():
    text = write_toml(parse_toml('names = "Ana, Bel"\n[settings]\nfit_level = 2', path="x.toml"))
    assert "fit_level = 2" in text
    assert "prefixes" not in text and "templates" not in text


def test_no_settings_section_without_pinned_settings():
    text = write_toml(parse_toml('description = "One line"\nnames = "Ana, Bel"', path="x.toml"))
    assert "[settings]" not in text
    assert 'description = "One line"' in text


def test_long_lists_are_wrapped_and_read_back():
    templates = [f'"{n}:{n % 3 + 1}"' for n in range(14)]
    style = parse_toml(
        f"title = 'T'\n[settings]\ntemplates = [{', '.join(templates)}]\n[names]\nlist = 'Ana'"
    )
    text = write_toml(style)
    assert "templates = [  " in text and all(len(line) <= 100 for line in text.splitlines())
    assert parse_toml(text).templates == style.templates


def test_soft_letters_in_clusters_are_written_out_in_full():
    style = parse_toml(
        'title = "T"\n[settings]\nsoft_consonants = true\nconsonant_clusters = ["lth", "RR"]\n'
        '[names]\nlist = "Ana"'
    )
    assert style.special_letters.consonant_clusters == ("Lt", "RR")  # the soft form (spec 4.1)
    assert 'consonant_clusters = ["LTH", "RR"]' in write_toml(style)


def test_without_soft_consonants_clusters_stay_as_written():
    style = parse_toml('title = "T"\n[settings]\nconsonant_clusters = ["LTH"]\n[names]\nlist = "Ana"')
    assert style.special_letters.consonant_clusters == ("LTH",)


# --- Reading ---------------------------------------------------------------


def test_the_smallest_style_is_just_names():
    style = parse_toml('names = """\nAna\nBel\n"""', path="styles/Mini.toml")
    assert style.title == "Mini"  # from the file name
    assert style.description == ()
    assert [name_list.name for name_list in style.name_lists] == [DEFAULT_LIST]
    assert style.names == ("Ana", "Bel")
    assert style.pinned == frozenset()
    assert style.options == GenerationOptions()
    assert style.templates == ()


def test_names_section_with_several_lists():
    style = parse_toml('[names]\ngiven = "Ana, Bel"\nfamily = """\nCor\n"""', path="x.toml")
    assert [(name_list.name, name_list.names) for name_list in style.name_lists] == [
        ("given", ("Ana", "Bel")),
        ("family", ("Cor",)),
    ]
    assert style.default_combination == Template("{given}")  # the first list


def test_names_by_line_or_comma_skipping_comments_and_blank_lines():
    style = parse_toml('names = """\n# Short ones\nAna, Bel\n\n  Cor   Dan \nAna\n"""', path="x.toml")
    assert style.names == ("Ana", "Bel", "Cor Dan", "Ana")  # repeats are kept


def test_presentation():
    style = parse_toml(
        'title = "  Fae  Female "\ndescription = """\nGiven Names\n\nof the Fae\n"""\nnames = "Ana"'
    )
    assert style.title == "Fae Female"
    assert style.description == ("Given Names", "of the Fae")


def test_every_setting_is_read_and_pinned():
    style = parse_toml(
        """
        title = "T"
        names = "Ana"
        [settings]
        soft_consonants = true
        consonant_clusters = ["RR"]
        multi_vowels = true
        letter_y = "semivowel"
        letter_u = "semivowel"
        letter_apostrophe = "consonant"
        letter_hyphen = "joint"
        structural = true
        statistical = true
        fit_level = 1
        prefixes = true
        suffixes = true
        shuffle = true
        validation_level = 0
        refuse_copies = true
        markov = "weighted"
        markov_order = 3
        markov_strength = 0.5
        templates = ["5'7:3", "#"]
        min_length = 4
        max_length = 9
        punctuation = ["accents", "vowel_harmony"]
        """
    )
    assert style.pinned == set(SETTINGS)
    assert style.options == GenerationOptions(
        structural=True, statistical=True, fit_level=1, validation_level=0, prefixes=True,
        suffixes=True, shuffle=True, punctuation=Punctuation.ACCENTS | Punctuation.VOWEL_HARMONY,
        y_mode=YMode.SEMI, u_mode=UMode.SEMI, hyphen=HyphenMode.JOINT, min_length=4, max_length=9,
        refuse_copies=True, markov=Markov.WEIGHTED, markov_order=3, markov_strength=0.5,
    )  # fmt: skip
    assert style.special_letters.soft_consonants and style.special_letters.multi_vowels
    assert style.templates == (Template("5'7", 3), Template("#", 1))
    assert style.setting("letter_apostrophe") is ApostropheMode.CONSONANT
    assert style.letter_classes.is_consonant("'")


def test_the_apostrophe_setting_is_written_and_read_back():
    style = parse_toml(
        """
        title = "T"
        names = "Ka'ren"
        [settings]
        letter_apostrophe = "consonant"
        """
    )
    text = write_toml(style)
    assert 'letter_apostrophe = "consonant"' in text
    assert parse_toml(text) == dataclasses.replace(style, source=parse_toml(text).source)


def test_the_hyphen_setting_is_written_and_read_back():
    style = parse_toml('title = "T"\nnames = "Bel-Abrean"\n[settings]\nletter_hyphen = "joint"')
    assert style.options.hyphen is HyphenMode.JOINT
    assert style.setting("letter_hyphen") is HyphenMode.JOINT
    text = write_toml(style)
    assert 'letter_hyphen = "joint"' in text
    assert parse_toml(text) == dataclasses.replace(style, source=parse_toml(text).source)
    assert "letter_hyphen must be" in problems_of('title = "T"\nnames = "A"\n[settings]\nletter_hyphen = "space"')[0]


def test_punctuation_may_be_one_word():
    style = parse_toml('title = "T"\nnames = "Ana"\n[settings]\npunctuation = "diaeresis"')
    assert style.options.punctuation == Punctuation.DIAERESIS


def test_read_from_a_file(tmp_path):
    path = tmp_path / "Fae.toml"
    path.write_text('names = "Aëlin, Ëa"', encoding="utf-8-sig")  # with a byte order mark
    style = read_toml(path)
    assert style.title == "Fae" and style.names == ("Aëlin", "Ëa")
    assert style.source.path == path and style.source.encoding == "utf-8"


# --- Problems --------------------------------------------------------------


def test_not_valid_toml():
    (problem,) = problems_of('names = "Ana"\ntitle =\n')
    assert problem.startswith("This is not a valid style file:") and "line" in problem


def test_unknown_key_with_suggestion():
    assert problems_of('titel = "T"\nnames = "Ana"') == ("Unknown key 'titel'. Did you mean title?",)


def test_setting_outside_settings_section():
    assert problems_of('fit_level = 2\nnames = "Ana"') == (
        "fit_level is a setting, so it belongs under [settings].",
    )


def test_unknown_setting_with_suggestion():
    assert problems_of('names = "Ana"\n[settings]\nfit_levle = 2') == (
        "[settings] has an unknown setting 'fit_levle'. Did you mean fit_level?",
    )


@pytest.mark.parametrize(
    ("setting", "problem"),
    [
        ("fit_level = 5", "fit_level must be a whole number from 0 to 3, not 5."),
        ("fit_level = true", "fit_level must be a whole number from 0 to 3, not true."),
        ('prefixes = "yes"', 'prefixes must be true or false, not "yes".'),
        ('letter_y = "maybe"',
         'letter_y must be "vowel", "consonant" or "semivowel", not "maybe".'),
        ('punctuation = ["bold"]',
         'punctuation must be a list of any of "accents", "diaeresis" or "vowel_harmony", not ["bold"].'),
        ('consonant_clusters = ["RA"]',
         "Consonant cluster \"RA\" contains 'A', which is not a consonant letter."),
        ('templates = ["5:x"]', 'Template "5:x" must be a recipe and a weight, such as "5:1".'),
        ("templates = [3]", "templates must be a list of recipes, such as [\"5'7:3\", \"#:1\"], not [3]."),
        ('markov = "on"', 'markov must be "off", "weighted" or "fill", not "on".'),
        ("markov_order = 6", "markov_order must be a whole number from 0 to 5, not 6."),
        ("markov_strength = 1.5", "markov_strength must be a number from 0 to 1, such as 0.5, not 1.5."),
        ("markov_strength = true",
         "markov_strength must be a number from 0 to 1, such as 0.5, not true."),
    ],
)  # fmt: skip
def test_bad_settings(setting, problem):
    assert problems_of(f'names = "Ana"\n[settings]\n{setting}') == (problem,)


def test_bad_template_recipe():
    (problem,) = problems_of('names = "Ana"\n[settings]\ntemplates = ["5|"]')
    assert problem.startswith("Template \"5|\" has a '|'")


def test_no_names():
    assert problems_of('title = "T"') == (
        "The style has no names. Add them under [names], one per line.",
    )


def test_empty_name_list():
    assert problems_of('names = """\n# nothing yet\n"""') == ("The name list is empty.",)


def test_names_must_be_text():
    assert problems_of("[names]\nlist = 3") == ('[names] list must be names in triple quotes ("""), not 3.',)


def test_title_needed_without_a_file_name():
    with pytest.raises(StyleError) as caught:
        parse_toml('names = "Ana"')
    assert caught.value.problems == ("The style needs a title.",)


def test_every_problem_is_reported_at_once():
    problems = problems_of('titel = "T"\nnames = ""\n[settings]\nfit_level = 9\nshuffle = 1')
    assert len(problems) == 4


def test_file_that_is_not_utf8(tmp_path):
    path = tmp_path / "old.toml"
    path.write_bytes('names = "Aëlin"'.encode("cp1252"))
    with pytest.raises(StyleError) as caught:
        read_toml(path)
    assert caught.value.problems == (
        "The file is not UTF-8 text (byte 11 cannot be read). Save it as UTF-8.",
    )


def test_missing_file(tmp_path):
    with pytest.raises(StyleError) as caught:
        read_toml(tmp_path / "none.toml")
    assert caught.value.problems[0].startswith("Could not open the file")


def test_a_length_range_is_read_and_written():
    style = parse_toml('names = "Ana"\n[settings]\nmin_length = 4\nmax_length = 9', path="x.toml")
    assert style.options.length_range == (4, 9)
    text = write_toml(style)
    assert "min_length = 4" in text and "# fewest letters in a name; 0 for no limit" in text


def test_a_length_range_the_wrong_way_round():
    assert problems_of('names = "Ana"\n[settings]\nmin_length = 9\nmax_length = 4') == (
        "min_length (9) is more than max_length (4).",
    )


# --- Several lists (spec 13.5) ----------------------------------------------------

TWO_LISTS = '''title = "Two"

[settings]
fit_level = 2

[settings.family]
letter_hyphen = "joint"
fit_level = 1

[names]
given = """
Ana
Bel
"""
family = "Cor-Dan, Eli"
'''


def test_a_section_for_one_list_pins_settings_for_that_list_alone():
    style = parse_toml(TWO_LISTS)
    assert style.pinned == {"fit_level"}
    assert style.name_list("given").settings == {}
    assert style.name_list("family").settings == {"letter_hyphen": HyphenMode.JOINT, "fit_level": 1}
    family = style.list_style("family")
    assert (family.options.fit_level, family.options.hyphen) == (1, HyphenMode.JOINT)
    given = style.list_style("given")
    assert (given.options.fit_level, given.options.hyphen) == (2, HyphenMode.CONSONANT)


def test_several_lists_are_written_and_read_back():
    style = parse_toml(TWO_LISTS)
    text = write_toml(style)
    assert "[settings]\n# Pinned settings, for every list." in text
    assert '[settings.family]\n# Pinned for the list family alone.\nletter_hyphen = "joint"' in text
    assert text.index("[settings]") < text.index("[settings.family]") < text.index("[names]")
    assert '[names]\ngiven = """\nAna\nBel\n"""\n\nfamily = """\nCor-Dan\nEli\n"""\n' in text
    again = parse_toml(text)
    assert again.name_lists == style.name_lists and again.pinned == style.pinned


def test_a_style_of_one_list_is_written_as_before():
    text = write_toml(parse_toml('title = "T"\nnames = "Ana, Bel"'))
    assert text.endswith('[names]\nlist = """\nAna\nBel\n"""\n')


def test_a_lists_clusters_follow_soft_consonants_for_every_list():
    style = parse_toml(
        'title = "T"\n[settings]\nsoft_consonants = true\n[settings.given]\nconsonant_clusters = ["LTH"]\n'
        '[names]\ngiven = "Ana"\nfamily = "Bel"'
    )
    assert style.name_list("given").settings["consonant_clusters"] == ("Lt",)
    assert 'consonant_clusters = ["LTH"]' in write_toml(style)


@pytest.mark.parametrize(
    "text, problems",
    [
        ('[settings]\nfit_level = 2\ngiven = """\nAna\n"""\n[names]\nfamily = "Bel"', (
            "[settings] has an unknown setting 'given'. Lists of names go under [names]: a line [names], "
            'then given = """ ... """.',
        )),
        ('given = "Ana"\n[names]\nfamily = "Bel"', (
            "Unknown key 'given'. Lists of names go under [names]: a line [names], then given = \"\"\" ... \"\"\".",
        )),
        ('[settings.famly]\nfit_level = 1\n[names]\ngiven = "Ana"\nfamily = "Bel"', (
            "[settings.famly] is for a list called famly, but there is no such list under [names].",
        )),
        ('[settings.given]\nfit = 1\n[names]\ngiven = "Ana"', (
            "[settings.given] has an unknown setting 'fit'.",
        )),
        ('[settings]\nmax_length = 6\n[settings.given]\nmin_length = 8\n[names]\ngiven = "Ana"', (
            "In [settings.given], min_length (8) is more than max_length (6).",
        )),
    ],
)  # fmt: skip
def test_bad_lists(text, problems):
    assert problems_of(text) == problems
