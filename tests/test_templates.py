"""Template recipes: parsing and putting names together (spec 7.1)."""

import pytest

from namebook.style import Punctuation, Template
from namebook.templates import (
    Join,
    ListPart,
    Recipe,
    Slot,
    Space,
    TemplateError,
    Text,
    assemble,
    capitalize,
    combination_problem,
    parse_combination,
    parse_recipe,
    parse_template_text,
)

# --- Parsing -----------------------------------------------------------------


@pytest.mark.parametrize(
    "recipe, parts",
    [
        ("5", (Slot((5,)),)),
        ("13", (Slot((13,)),)),
        ("9|13", (Slot((9, 13)),)),
        ("2|3|4|5", (Slot((2, 3, 4, 5)),)),
        ("#", (Slot(None),)),
        ("5 2", (Slot((5,)), Space(), Slot((2,)))),
        ("5  2", (Slot((5,)), Space(), Slot((2,)))),  # several spaces count as one
        ("5'7", (Slot((5,)), Text("'"), Slot((7,)))),
        ("5_7", (Slot((5,)), Join(merge=True), Slot((7,)))),
        ("0507", (Slot((5,)), Join(merge=False), Slot((7,)))),  # two slots back to back
        ("#5", (Slot(None), Join(merge=False), Slot((5,)))),
        ("Mac6", (Text("Mac"), Slot((6,)))),
        ("5 of 7", (Slot((5,)), Space(), Text("of"), Space(), Slot((7,)))),
        ("5:7", (Slot((5,)), Text(":"), Slot((7,)))),
        (" 5 ", (Slot((5,)),)),  # spaces at the ends are dropped
    ],
)
def test_parse_recipe(recipe, parts):
    assert parse_recipe(recipe).parts == parts


def test_numbers_have_at_most_two_digits():
    assert parse_recipe("123").slots == (Slot((12,)), Slot((3,)))


def test_recipe_slots_and_start():
    recipe = parse_recipe("9|13 6")
    assert recipe.slots == (Slot((9, 13)), Slot((6,)))
    assert recipe.starts_with_slot
    assert not parse_recipe("Mac6").starts_with_slot
    assert recipe.text == "9|13 6"


def test_single_slot_recipe():
    assert Recipe.single_slot([5, 6]) == Recipe("5|6", (Slot((5, 6)),))
    assert Recipe.single_slot(None) == Recipe("#", (Slot(None),))


@pytest.mark.parametrize(
    "recipe, joints",
    [("#", ()), ("# #", (" ",)), ("#-#", ("-",)), ("#-# #", ("-", " ")), ("5'7", (" ",)),
     ("5--7", (" ",)), ("#_#", (" ",)), ("0507", (" ",))],
)  # fmt: skip
def test_the_joint_after_each_slot_as_the_chain_reads_it(recipe, joints):
    # Only a hyphen standing alone between two slots is shown to the chain
    # (spec 13.15); other text and joins are read as a space, as before.
    assert parse_recipe(recipe).joints == joints


def test_a_hyphen_between_slots_gives_each_word_a_capital():
    assert assemble(parse_recipe("#-#"), ["BEL", "ITHRA"]) == ("BEL-ITHRA", "Bel-Ithra")


@pytest.mark.parametrize(
    "recipe, message",
    [
        ("|5", "'|' that is not between two structure numbers"),
        ("5|", "'|' that is not between two structure numbers"),
        ("5||6", "'|' that is not between two structure numbers"),
        ("5|#", "'|' that is not between two structure numbers"),
        ("#|5", "# already allows any structure"),
        ("_5", "'_' that is not directly between two word slots"),
        ("5_", "'_' that is not directly between two word slots"),
        ("5 _7", "'_' that is not directly between two word slots"),
        ("5_'7", "'_' that is not directly between two word slots"),
        ("abc", "has no word slot"),
        ("", "has no word slot"),
    ],
)
def test_bad_recipes(recipe, message):
    with pytest.raises(TemplateError, match=message.replace("|", r"\|")):
        parse_recipe(recipe)


def test_parse_template_text():
    assert parse_template_text("5'7:3") == Template("5'7", 3)
    assert parse_template_text("5'7") == Template("5'7", 1)
    assert parse_template_text(" 9|13 6:2 ") == Template("9|13 6", 2)


# --- Putting names together --------------------------------------------------


def letters(text):
    return list(text)


def finished(recipe, *words, punctuation=Punctuation.NONE):
    return assemble(parse_recipe(recipe), [letters(word) for word in words], punctuation)[1]


def test_one_word():
    assert assemble(parse_recipe("5"), [letters("AALBE'IL")]) == ("AALBE'IL", "Aalbe'il")


def test_words_separated_by_a_space():
    assert finished("5 2", "RENHO", "DELAR") == "Renho Delar"


def test_literal_characters_and_each_word_capitalized():
    assert finished("5'7", "RENHO", "DELAR") == "Renho'Delar"
    assert finished("5-7", "RENHO", "DELAR") == "Renho-Delar"


def test_literal_text_is_kept_as_written():
    assert finished("Mac6 of 2", "DONAL", "ISLA") == "MacDonal of Isla"


def test_join_merges_a_shared_letter():
    assert finished("5_7", "ANAR", "RION") == "Anarion"
    assert finished("5_7", "ANAR", "TION") == "Anartion"


def test_join_merges_special_letters_as_letters():
    words = [["A", "TH"], ["TH", "A"]]
    assert assemble(parse_recipe("1_0"), words)[1] == "Atha"
    # H at the end and TH at the start are different letters, so both stay.
    assert assemble(parse_recipe("1_0"), [["A", "H"], ["TH", "A"]])[1] == "Ahtha"


def test_back_to_back_slots_join_without_merging():
    assert finished("0507", "ANAR", "RION") == "Anarrion"


def test_joined_words_are_one_word_with_one_capital():
    assert finished("5_7", "ANAR", "ION") == "Anarion"
    assert finished("5_7 2", "ANAR", "ION", "BEL") == "Anarion Bel"


def test_a_non_letter_literal_is_not_doubled():
    # A word may end with the apostrophe, a vowel letter by default.
    assert finished("5'7", "ANA'", "DEL") == "Ana'Del"
    # Letters in literal text are always copied.
    assert finished("5o", "RANO") == "Ranoo"


def test_punctuation_applies_to_each_word():
    assert finished("5 5", "AICANAARO", "CIIIRDAN", punctuation=Punctuation.ACCENTS) == (
        "Aicanáro Cîrdan"
    )
    assert finished("5", "EARENDIL", punctuation=Punctuation.DIAERESIS) == "Eärendil"
    # A joined word is punctuated as one word: its final E is the word's end.
    assert finished("5_5", "MANE", "TIL", punctuation=Punctuation.DIAERESIS) == "Manetil"


def test_capitalize():
    assert capitalize("AALBE'IL") == "Aalbe'il"
    assert capitalize("ÁED") == "Áed"
    assert capitalize("'ALI") == "'Ali"


def test_capitalize_turkish_i_and_sharp_s():
    assert capitalize("ıŞıL") == "Işıl"  # the dotless ı has the capital I
    assert capitalize("İLKAY") == "İlkay"  # and the dotted İ keeps its dot
    assert capitalize("EMİNE") == "Emine"
    assert capitalize("STRAẞE") == "Straße"


def test_capitalize_passes_over_a_letter_with_no_capital():
    assert capitalize("ʻIOLANI") == "ʻIolani"  # the ʻokina
    assert capitalize("KAʻIULANI") == "Kaʻiulani"


# --- Combinations (spec 13.5) ---------------------------------------------------


@pytest.mark.parametrize(
    "text, parts",
    [
        ("{given}", (ListPart("given"),)),
        ("{given} {family}", (ListPart("given"), Text(" "), ListPart("family"))),
        ("  {family},   {given} ", (ListPart("family"), Text(", "), ListPart("given"))),
        ("Lord { given } of {house}", (Text("Lord "), ListPart("given"), Text(" of "), ListPart("house"))),
        ("{given}-{given}", (ListPart("given"), Text("-"), ListPart("given"))),
        ("{a}{b}", (ListPart("a"), ListPart("b"))),
    ],
)
def test_parse_combination(text, parts):
    assert parse_combination(text).parts == parts


def test_a_combinations_lists_in_order():
    combination = parse_combination("{given} {given} {family}")
    assert combination.lists == ("given", "given", "family")
    assert combination.starts_with_list
    assert not parse_combination("Lord {given}").starts_with_list
    assert parse_combination(" {given}  {family} ").text == "{given}  {family}"


@pytest.mark.parametrize(
    "text, problem",
    [
        ("{given", "has a '{' with no '}' to close it"),
        ("{a{b}", "has a '{' with no '}' to close it"),
        ("given}", "has a '}' with no '{' before it"),
        ("{} {given}", "has '{}' with no list's name in it"),
        ("Lord Given", "has no list: write a list's name in braces, such as {given}"),
    ],
)
def test_bad_combinations(text, problem):
    with pytest.raises(TemplateError) as caught:
        parse_combination(text)
    assert str(caught.value) == problem
    assert combination_problem(Template(text), ["given"]) == problem


def test_a_combination_must_name_the_styles_lists_and_have_a_weight():
    lists = ["given", "family"]
    assert combination_problem(Template("{given} {family}"), lists) is None
    assert combination_problem(Template("{given} {surname}"), lists) == (
        'has "surname", which is not one of the style\'s lists (given, family)'
    )
    assert combination_problem(Template("{given}", 0), lists) == "has weight 0, so it is never used"
