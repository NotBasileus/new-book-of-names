"""The .ebn reader, tested on small chapters written inline."""

import pytest

from namebook.formats import load_style
from namebook.formats.ebn import UTF8, WINDOWS_1252, decode_ebn, parse_ebn, read_ebn
from namebook.style import GenerationOptions, Punctuation, StyleError, Template, UMode, YMode

MINIMAL = 'TITLE = "Test"; NAMES = "Ana", "Bel";'


def parse(text):
    return parse_ebn(text)


def problems_of(text):
    with pytest.raises(StyleError) as caught:
        parse_ebn(text)
    return caught.value.problems


def chapter_with(**tags):
    """A minimal chapter text plus the given extra tags, e.g. GENOPT='"fit:2"'."""
    extra = " ".join(f"{tag} = {value};" for tag, value in tags.items())
    return parse(f"{MINIMAL} {extra}")


# --- Basic structure -------------------------------------------------------


def test_minimal_chapter_gets_defaults():
    chapter = parse(MINIMAL)
    assert chapter.title == "Test"
    assert chapter.names == ("Ana", "Bel")
    assert chapter.description == ()
    assert chapter.options == GenerationOptions()
    assert chapter.options.fit_level == 3
    assert chapter.options.validation_level == 2
    assert chapter.templates == ()
    assert chapter.source.warnings == ()


def test_single_name_list_is_called_list():
    chapter = parse(MINIMAL)
    assert [name_list.name for name_list in chapter.name_lists] == ["list"]
    assert chapter.name_list("list").names == ("Ana", "Bel")


def test_descriptive_tags():
    chapter = chapter_with(
        LINE1='"Proper Names"', LINE2='"of the Celestial Watchers"',
        AUTHOR='"Someone"', DATE='"26-Sep-2012"', TOPIC='""',
    )  # fmt: skip
    assert chapter.description == ("Proper Names", "of the Celestial Watchers")
    assert chapter.source.warnings == ()  # AUTHOR, DATE and TOPIC are read but not kept
    assert not hasattr(chapter, "author") and not hasattr(chapter, "date")


def test_empty_description_lines_are_dropped():
    chapter = chapter_with(LINE1='""', LINE2='"Only line"')
    assert chapter.description == ("Only line",)


def test_comments_are_ignored_but_not_inside_quotes():
    chapter = parse(
        '// A comment line\n'
        'TITLE = "Test"; // a comment after a statement\n'
        'LINE1 = "see http://example.com";\n'
        'NAMES = "Ana", // comment inside a list\n'
        '        "Bel";'
    )
    assert chapter.description == ("see http://example.com",)
    assert chapter.names == ("Ana", "Bel")


def test_statements_may_span_lines_and_tags_ignore_case():
    chapter = parse('title\n=\n"Test"\n;\nNames =\n "Ana",\n "Bel"\n;')
    assert chapter.title == "Test"
    assert chapter.names == ("Ana", "Bel")


def test_windows_line_endings():
    chapter = parse('TITLE = "Test";\r\nNAMES = "Ana",\r\n"Bel";\r\n')
    assert chapter.names == ("Ana", "Bel")


def test_whitespace_in_values_is_tidied():
    chapter = parse('TITLE = "  Two   Words "; NAMES = " Ana ", "Van  Helsing";')
    assert chapter.title == "Two Words"
    assert chapter.names == ("Ana", "Van Helsing")


def test_repeated_names_are_kept():
    assert parse('TITLE = "T"; NAMES = "Ana", "Ana", "Bel";').names == ("Ana", "Ana", "Bel")


def test_names_keep_their_case():
    assert parse('TITLE = "T"; NAMES = "aNa", "Aalbe\'il";').names == ("aNa", "Aalbe'il")


# --- Required tags and tag problems ------------------------------------------


def test_missing_title():
    problems = problems_of('NAMES = "Ana";')
    assert problems == ("The chapter has no TITLE tag, which every chapter needs.",)


def test_missing_names():
    assert any("no NAMES tag" in problem for problem in problems_of('TITLE = "T";'))


def test_empty_title():
    assert problems_of('TITLE = ""; NAMES = "Ana";') == ("Line 1: TITLE is empty.",)


def test_all_problems_are_reported_together():
    problems = problems_of('TITLE = ""; NAMES = "An4", "B@l";')
    assert len(problems) == 3


def test_duplicate_tag_is_an_error():
    problems = problems_of('TITLE = "T";\nNAMES = "Ana";\nTITLE = "Again";')
    assert problems == (
        "Line 3: TITLE appears a second time (first on line 1). Each tag may appear only once.",
    )


def test_single_value_tag_with_several_values_is_an_error():
    problems = problems_of('TITLE = "A", "B"; NAMES = "Ana";')
    assert problems == ("Line 1: TITLE should have one quoted value, but has 2.",)


def test_unknown_tag_is_a_warning_with_suggestion():
    chapter = chapter_with(AUTOR='"Someone"')
    assert chapter.source.warnings == (
        "Line 1: unknown tag AUTOR was ignored. Did you mean AUTHOR?",
    )


# --- Syntax errors -----------------------------------------------------------


def test_missing_comma_between_names():
    problems = problems_of('TITLE = "T";\nNAMES = "Ana",\n"Bel"\n"Cor";')
    assert problems == ('Line 4: a \',\' is missing before the value "Cor" in NAMES.',)


def test_missing_semicolon_between_statements():
    problems = problems_of('TITLE = "T"\nNAMES = "Ana";')
    assert len(problems) == 1
    assert problems[0].startswith("Line 2: expected ',' or ';' after the last value of TITLE")


def test_unclosed_quote():
    assert problems_of('TITLE = "T";\nNAMES = "Ana;') == ("Line 2: this quote is never closed.",)


def test_missing_closing_quote_in_a_list_is_pointed_at():
    problems = problems_of('TITLE = "T";\nNAMES = "Ana,\n"Bel", "Cor";')
    assert problems[0].startswith("Line 3: expected ',' or ';'")


def test_missing_equals_sign():
    assert problems_of('TITLE "T";') == (
        "Line 1: expected '=' after TITLE, but found the value \"T\".",
    )


def test_value_without_quotes():
    problems = problems_of("TITLE = Test;")
    assert problems == ("Line 1: expected a quoted value after 'TITLE =', but found 'Test'.",)


def test_stray_character():
    assert problems_of('TITLE = "T"; NAMES = "Ana"; #') == (
        "Line 1: unexpected '#' outside quotes.",
    )


def test_missing_final_semicolon_is_a_warning():
    chapter = parse('TITLE = "T";\nNAMES = "Ana",\n"Bel"')
    assert chapter.names == ("Ana", "Bel")
    assert chapter.source.warnings == ("Line 3: the NAMES statement has no ';' at the end.",)


def test_trailing_comma_is_a_warning():
    chapter = parse('TITLE = "T"; NAMES = "Ana", "Bel",;')
    assert chapter.names == ("Ana", "Bel")
    assert chapter.source.warnings == ("Line 1: an extra ',' at the end of NAMES was ignored.",)


# --- Names -------------------------------------------------------------------


def test_invalid_character_in_name():
    problems = problems_of('TITLE = "T";\nNAMES = "Ana",\n"Be1l";')
    assert problems == (
        'Line 3: name 2 ("Be1l") contains \'1\', which is not a vowel or consonant letter.',
    )


def test_several_invalid_characters_are_listed_once_each():
    (problem,) = problems_of('TITLE = "T"; NAMES = "A.b.c!";')
    assert "contains '.' and '!', which are not" in problem


def test_names_written_in_one_string_get_a_hint():
    (problem,) = problems_of('TITLE = "T"; NAMES = "Ana,Bel";')
    assert 'Each name needs its own quotes, like "Ana","Bel".' in problem


def test_empty_name():
    assert problems_of('TITLE = "T"; NAMES = "Ana", "";') == ("Line 1: name 2 is empty.",)


def test_name_running_onto_next_line():
    (problem,) = problems_of('TITLE = "T"; NAMES = "Ana\nBel";')
    assert "name 1" in problem and "runs onto the next line" in problem


def test_all_classic_letters_are_accepted_in_either_case():
    vowels = "AEIOUYÄËÏÖÜŸÁÉÍÓÚÝÂÊÎÔÛÀÈÌÒÙÃÕÅÆØŒ'"
    consonants = "BCDFGHJKLMNPQRSTVWXZÇÐÑÞŠ-"
    letters = vowels + consonants
    chapter = parse(f'TITLE = "T"; NAMES = "{letters}", "{letters.lower()}";')
    assert chapter.names == (letters, letters.lower())


def test_chapters_accept_every_latin_letter():
    # Chapters never had them, but every style may use any Latin letter (spec 13.8).
    assert parse('TITLE = "T"; NAMES = "Strauß", "Łucja";').names == ("Strauß", "Łucja")


# --- GENOPT ------------------------------------------------------------------


def test_all_genopt_options():
    chapter = chapter_with(
        GENOPT='"structgen,statgen,fit:1,val:0,prefix,suffix,shuffle,punct:5,y:semi,u:semi"'
    )
    assert chapter.options == GenerationOptions(
        structural=True,
        statistical=True,
        fit_level=1,
        validation_level=0,
        prefixes=True,
        suffixes=True,
        shuffle=True,
        punctuation=Punctuation.ACCENTS | Punctuation.VOWEL_HARMONY,
        y_mode=YMode.SEMI,
        u_mode=UMode.SEMI,
    )
    assert chapter.source.warnings == ()


def test_genopt_ignores_spaces_case_and_line_breaks():
    chapter = chapter_with(GENOPT='"StructGen, statgen,fit:2,\n  val:1"')
    assert chapter.options == GenerationOptions(
        structural=True, statistical=True, fit_level=2, validation_level=1
    )


def test_genopt_may_be_split_into_several_quoted_values():
    chapter = chapter_with(GENOPT='"structgen", "fit:0"')
    assert chapter.options.structural
    assert chapter.options.fit_level == 0


def test_y_consonant_mode():
    assert chapter_with(GENOPT='"y:con"').options.y_mode is YMode.CONSONANT


def test_validation_levels_above_two_mean_two():
    chapter = chapter_with(GENOPT='"val:3"')
    assert chapter.options.validation_level == 2
    assert chapter.source.warnings == ()


def test_fit_level_above_three_is_lowered_with_warning():
    chapter = chapter_with(GENOPT='"fit:5"')
    assert chapter.options.fit_level == 3
    assert chapter.source.warnings == (
        "Line 1: GENOPT option 'fit:5' is above the highest level, so 3 is used.",
    )


@pytest.mark.parametrize(
    "option, message",
    [
        ("fit:x", "GENOPT option 'fit:x' was ignored; the level must be a whole number."),
        ("punct:9", "GENOPT option 'punct:9' was ignored; the number must be 0 to 7."),
        ("y:maybe", "GENOPT option 'y:maybe' was ignored; use y:vow, y:con or y:semi."),
        ("u:con", "GENOPT option 'u:con' was ignored; use u:vow or u:semi."),
        ("structgne", "unknown GENOPT option 'structgne' was ignored. Did you mean structgen?"),
    ],
)
def test_bad_genopt_options_are_warnings(option, message):
    chapter = chapter_with(GENOPT=f'"{option}"')
    assert chapter.options == GenerationOptions()
    assert chapter.source.warnings == (f"Line 1: {message}",)


# --- Special letters ---------------------------------------------------------


def test_soft_consonants_and_clusters_in_braces_or_angle_brackets():
    chapter = chapter_with(SPCCON='"soft, {SZ}, <Lt>"')
    special = chapter.special_letters
    assert special.soft_consonants
    assert special.consonant_clusters == ("SZ", "Lt")
    assert chapter.source.warnings == ()


def test_repeated_clusters_are_kept_once():
    chapter = chapter_with(SPCCON='"<RR>,{RR}"')
    assert chapter.special_letters.consonant_clusters == ("RR",)


def test_empty_special_letter_tags():
    chapter = chapter_with(SPCCON='""', SPCVOW='""')
    assert not chapter.special_letters.soft_consonants
    assert chapter.special_letters.consonant_clusters == ()
    assert not chapter.special_letters.multi_vowels


def test_multi_vowels():
    assert chapter_with(SPCVOW='"multi"').special_letters.multi_vowels


def test_cluster_with_th_under_soft_gets_a_warning():
    chapter = chapter_with(SPCCON='"soft, <LTH>"')
    assert chapter.special_letters.consonant_clusters == ("LTH",)
    (warning,) = chapter.source.warnings
    assert "can never match" in warning
    assert "Write {Lt} instead." in warning


def test_cluster_with_th_without_soft_is_fine():
    chapter = chapter_with(SPCCON='"<LTH>"')
    assert chapter.source.warnings == ()


def test_lowercase_cluster_without_soft_gets_a_warning():
    (warning,) = chapter_with(SPCCON='"<Lt>"').source.warnings
    assert "does not include 'soft'" in warning


def test_single_letter_cluster_gets_a_warning():
    (warning,) = chapter_with(SPCCON='"<R>"').source.warnings
    assert "has no effect" in warning


def test_cluster_with_a_vowel_is_an_error():
    problems = problems_of(f'{MINIMAL} SPCCON = "soft,<RR>,<RA>";')
    assert problems == (
        "Line 1: special consonant 2 ('RA') contains 'A', which is not a consonant letter.",
    )


def test_lowercase_h_is_not_a_soft_consonant():
    (problem,) = problems_of(f'{MINIMAL} SPCCON = "soft,<Lh>";')
    assert "contains 'h'" in problem


def test_unknown_special_letter_items_are_warnings():
    chapter = chapter_with(SPCCON='"soft,LF"', SPCVOW='"many"')
    assert chapter.special_letters.soft_consonants
    assert chapter.special_letters.consonant_clusters == ()
    assert len(chapter.source.warnings) == 2
    assert "SPCCON item 'LF' was ignored" in chapter.source.warnings[0]
    assert "SPCVOW item 'many' was ignored" in chapter.source.warnings[1]


# --- Templates ---------------------------------------------------------------


def test_templates():
    chapter = chapter_with(TEMPLATES='"7:4", "3 6:2", "2|3|4:1", "5\'7:3", "#:1", "0507:2"')
    assert chapter.templates == (
        Template("7", 4),
        Template("3 6", 2),
        Template("2|3|4", 1),
        Template("5'7", 3),
        Template("#", 1),
        Template("0507", 2),
    )


def test_the_last_colon_separates_the_weight():
    assert chapter_with(TEMPLATES='"5:7:3"').templates == (Template("5:7", 3),)


def test_empty_templates_tag_means_no_templates():
    chapter = chapter_with(TEMPLATES='""')
    assert chapter.templates == ()
    assert chapter.source.warnings == ()


def test_template_without_weight_gets_weight_one_and_a_warning():
    chapter = chapter_with(TEMPLATES='"5 2"')
    assert chapter.templates == (Template("5 2", 1),)
    assert chapter.source.warnings == (
        "Line 1: template '5 2' has no weight, so 1 is used. Write it as '5 2:1'.",
    )


def test_template_with_zero_weight_gets_a_warning():
    chapter = chapter_with(TEMPLATES='"5:0", "6:1"')
    assert chapter.templates == (Template("5", 0), Template("6", 1))
    assert "will never be chosen" in chapter.source.warnings[0]


@pytest.mark.parametrize("template", ["5:x", "5:", "5:1.5", "5:-1"])
def test_template_with_bad_weight_is_an_error(template):
    (problem,) = problems_of(f'{MINIMAL} TEMPLATES = "{template}";')
    assert "needs a whole number after the last ':'" in problem


@pytest.mark.parametrize(
    "template, message",
    [
        ("5|:1", "has a '|' that is not between two structure numbers"),
        ("5_:1", "has a '_' that is not directly between two word slots"),
        ("abc:1", "has no word slot (a structure number or #)"),
    ],
)
def test_template_recipe_mistakes_are_errors(template, message):
    (problem,) = problems_of(f'{MINIMAL}\nTEMPLATES = "7:1",\n"{template}";')
    assert problem == f"Line 3: template '{template}' {message}."


def test_template_with_no_recipe_is_an_error():
    (problem,) = problems_of(f'{MINIMAL} TEMPLATES = ":3";')
    assert "has nothing before the ':'" in problem


# --- Files and encodings -----------------------------------------------------


def test_decode_utf8_with_and_without_bom():
    assert decode_ebn("Áed".encode("utf-8")) == ("Áed", UTF8)
    assert decode_ebn("﻿Áed".encode("utf-8")) == ("Áed", UTF8)


def test_decode_windows_1252():
    assert decode_ebn("Maškaššaptu Áed".encode("cp1252")) == ("Maškaššaptu Áed", WINDOWS_1252)


def test_decode_joins_separate_accents():
    # "E" followed by a combining acute accent becomes the single letter "É".
    text, _ = decode_ebn("Éimhear".encode("utf-8"))
    assert text == "Éimhear"


def test_unreadable_byte_in_a_name_is_reported(tmp_path):
    path = tmp_path / "bad.ebn"
    path.write_bytes(b'TITLE = "T"; NAMES = "An\x81a";')
    with pytest.raises(StyleError) as caught:
        read_ebn(path)
    (problem,) = caught.value.problems
    assert "an unreadable character" in problem


def test_read_utf8_file(tmp_path):
    path = tmp_path / "utf8.ebn"
    path.write_text('TITLE = "Fae"; NAMES = "Áed", "Siôn";', encoding="utf-8")
    chapter = read_ebn(path)
    assert chapter.names == ("Áed", "Siôn")
    assert chapter.source.encoding == UTF8
    assert chapter.source.path == path


def test_missing_file(tmp_path):
    with pytest.raises(StyleError) as caught:
        load_style(tmp_path / "nothing.ebn")
    assert caught.value.problems[0].startswith("Could not open the file")


def test_unknown_file_type(tmp_path):
    with pytest.raises(StyleError) as caught:
        load_style(tmp_path / "names.csv")
    assert caught.value.problems == ("Unknown style file type '.csv'. Supported types: .ebn, .toml, .txt.",)


def test_extension_is_matched_without_case(tmp_path):
    path = tmp_path / "SHOUT.EBN"
    path.write_text(MINIMAL, encoding="utf-8")
    assert load_style(path).title == "Test"
