"""Inference: working out the settings a style does not pin, from its names."""

import re

import pytest

from conftest import CHAPTERS_DIR, SAMPLE_CHAPTERS
from namebook.formats import load_style
from namebook.formats.toml import parse_toml
from namebook.formats.txt import parse_txt
from namebook.inference import SMALL_LIST, infer, infer_lists
from namebook.style import (
    SETTINGS,
    ApostropheMode,
    GenerationOptions,
    HyphenMode,
    Markov,
    Punctuation,
    Template,
    UMode,
    YMode,
)


def plain(*names):
    return parse_txt("\n".join(names), "List.txt")


def made_up(count):
    """``count`` different names with no consonant + H, Y or QU."""
    syllables = [c + v for c in "BDGKLMNPRST" for v in "AEIOU"]
    return [(syllables[n % 55] + syllables[n // 55 % 55]).capitalize() for n in range(count)]


def with_soft_letters():
    """175 names in which TH, SH and KH each act as one consonant: each is
    followed by a vowel and then always by the same letter (Thara, Shane)."""
    finals = {"TH": "R", "SH": "N", "KH": "L", "B": "S", "D": "K", "L": "M", "M": "T"}
    return [(c + v + finals[c] + w).capitalize() for c in finals for v in "AEIOU" for w in "AEIOU"]


# --- What is inferred, and what is not ---------------------------------------


@pytest.mark.parametrize("path", SAMPLE_CHAPTERS, ids=lambda path: path.name)
def test_a_chapter_pins_everything_so_nothing_is_inferred(path):
    chapter = load_style(path)
    inference = infer(chapter)
    assert inference.working == chapter and inference.reasons == {}
    assert inference.written is chapter


def test_a_plain_list_has_every_setting_inferred():
    inference = infer(plain(*made_up(100)))
    assert set(inference.reasons) == set(SETTINGS)
    assert inference.working.pinned == frozenset()
    assert inference.written.options == GenerationOptions()  # the style as read is unchanged


def test_pinned_settings_are_kept():
    style = parse_toml('names = "Ana, Bel"\n[settings]\nfit_level = 2\nshuffle = false', path="x.toml")
    inference = infer(style)
    assert inference.working.options.fit_level == 2
    assert inference.working.options.shuffle is False  # pinned, though 2 names would turn it on
    assert "fit_level" not in inference.reasons and "shuffle" not in inference.reasons
    assert inference.working.pinned == {"fit_level", "shuffle"}


def test_ignoring_pins_infers_everything():
    chapter = load_style(CHAPTERS_DIR / "Demonic.ebn")
    inference = infer(chapter, ignore_pins=True)
    assert set(inference.reasons) == set(SETTINGS)
    assert inference.working.pinned == frozenset()


def test_where_inference_differs_from_the_sample_chapters():
    # Every chapter differs in its templates (D48, D53), the Markov chain and
    # refusing copies, which chapters leave off (D78, D81). Hiding names finds
    # TH and the like likelier as two letters, or no clearer either way, where
    # the chapters' author made them one (D80); Ancient Female pins shuffle on
    # and Demonic has consonant clusters, which are not inferred (D69).
    expected = {
        "Ancient Female": {"shuffle", "soft_consonants"},
        "Angelic": {"soft_consonants"},
        "Demonic": {"consonant_clusters", "soft_consonants"},
        "Fae Female": {"soft_consonants"},
        "Fae Male": {"soft_consonants"},
    }
    for path in SAMPLE_CHAPTERS:
        chapter = load_style(path)
        working = infer(chapter, ignore_pins=True).working
        differ = {key for key in SETTINGS if working.setting(key) != chapter.setting(key)}
        always = {"templates", "markov", "markov_order", "refuse_copies"}
        assert differ == {*always, *expected.get(chapter.title, ())}, chapter.title


# --- Generation settings: start full; the classic way backs off for small lists


@pytest.mark.parametrize("count", [3, SMALL_LIST - 1, SMALL_LIST])
def test_a_list_gets_the_fullest_settings(count):
    working = infer(plain(*made_up(count))).working
    assert working.options == GenerationOptions(
        structural=True, statistical=True, fit_level=3, validation_level=2,
        prefixes=True, suffixes=True, shuffle=False, refuse_copies=True,
        markov=Markov.FILL, markov_order=working.options.markov_order, markov_strength=1.0,
    )  # fmt: skip
    assert working.templates == ()


def test_a_small_list_backs_off_for_the_classic_way():
    inference = infer(plain(*made_up(SMALL_LIST - 1)), given={"markov": Markov.OFF})
    options = inference.working.options
    assert (options.fit_level, options.prefixes, options.suffixes, options.shuffle) == (1, False, False, True)
    assert options.structural and options.statistical and options.validation_level == 2
    assert inference.reasons["fit_level"].startswith(
        "1: with only 39 different names (fewer than 40), building without the chain"
    )
    assert infer(plain(*made_up(SMALL_LIST)), given={"markov": Markov.OFF}).working.options.fit_level == 3


def test_a_small_list_backs_off_for_weighted_choice():
    names = ", ".join(made_up(SMALL_LIST - 1))
    working = infer(parse_toml(f'names = "{names}"\n[settings]\nstructural = false', path="x.toml")).working
    assert working.options.markov is Markov.WEIGHTED and working.options.fit_level == 1


def test_repeated_names_do_not_make_a_list_bigger():
    names = made_up(SMALL_LIST - 1)
    assert infer(plain(*names, *names), given={"markov": Markov.OFF}).working.options.fit_level == 1


def test_given_settings_are_used_as_if_pinned():
    inference = infer(plain(*made_up(50)), given={"structural": False, "fit_level": 2})
    working = inference.working
    assert working.options.structural is False and working.options.fit_level == 2
    assert {"structural", "fit_level"} <= working.pinned and "structural" not in inference.reasons
    assert working.options.markov is Markov.WEIGHTED  # worked out around them: fill needs structural on
    assert inference.written.options == GenerationOptions()


# --- Spelling settings -----------------------------------------------------


def test_soft_consonants_when_hidden_names_are_clearly_likelier_with_them():
    inference = infer(plain(*with_soft_letters()))
    assert inference.working.special_letters.soft_consonants
    assert inference.reasons["soft_consonants"].startswith("on: names held back as a test read ")
    assert inference.reasons["soft_consonants"].endswith(
        "more naturally with pairs like TH, SH and KH as one letter."
    )


def test_no_soft_consonants_when_the_pairs_act_as_two_letters():
    # Each of these names pairs any first syllable with any second, so the H
    # tells the chain nothing that the letters on either side don't.
    syllables = [c + v for c in ["B", "D", "K", "L", "M", "N", "TH", "SH", "KH"] for v in "AEIOU"]
    names = [(syllables[n % 45] + syllables[n // 45 % 45]).capitalize() for n in range(60)]
    inference = infer(plain(*names))
    assert not inference.working.special_letters.soft_consonants
    assert "more naturally with pairs like TH, SH and KH as two letters" in inference.reasons["soft_consonants"]


def test_no_soft_consonants_without_pairs():
    reason = infer(plain(*made_up(50))).reasons["soft_consonants"]
    assert reason == "off: there are no consonant + H pairs (such as TH) in the names."


def test_the_plain_spelling_stays_without_a_clear_winner():
    inference = infer(plain("Yara", "Bel", "Cor", "Dan"))
    assert inference.working.options.y_mode is YMode.VOWEL
    assert inference.reasons["letter_y"].startswith("vowel: names held back as a test read about as naturally")


def test_a_y_that_leaves_a_name_with_no_consonant_is_chosen_by_hidden_names():
    # As a vowel, Y makes Ya one element (spec 13.10), so Ya no longer
    # decides by itself.
    inference = infer(plain(*made_up(50), "Ya", "Yu"))
    assert inference.working.options.y_mode is YMode.VOWEL
    assert inference.reasons["letter_y"].startswith("vowel: names held back as a test read about as naturally")


def test_u_after_q_when_hidden_names_are_clearly_likelier_with_it_a_consonant():
    syllables = [c + v for c in ["B", "D", "K", "L", "M", "N", "QU"] for v in "AEIOU"]
    names = [(syllables[n % 35] + syllables[n // 35 % 35]).capitalize() for n in range(120)]
    inference = infer(plain(*names))
    assert inference.working.options.u_mode is UMode.SEMI
    assert inference.reasons["letter_u"].startswith("semivowel: names held back as a test read ")
    assert infer(plain(*made_up(50))).reasons["letter_u"] == "vowel: there is no QU in the names."


# Hawaiian names, with the ʻokina typed as a plain apostrophe.
HAWAIIAN = (
    "Ka'iulani, Lili'uokalani, Ka'ahumanu, Ke'elikōlani, Hōkūle'a, Ka'imi, Ke'ala, Ka'ena, "
    "No'eau, Ka'ulana, Maka'ala, La'akea, Keali'i, Kale'a, Kama'ehu, 'Iolani, 'Ilima, "
    "'Aulani, Kekoa, Kalani, Leilani, Makana, Noelani, Keola, Kainoa, Pualani, Mahina, "
    "Iolana, Kawika, Kaimana, Malia, Nohea, Alana, Haunani, Kapena, Moana, Nalani, Ikaika, "
    "Kanoa, Lokelani, Kaipo"
).split(", ")


def test_the_apostrophe_is_a_vowel_without_one_in_the_names():
    inference = infer(plain(*made_up(50)))
    assert inference.working.setting("letter_apostrophe") is ApostropheMode.VOWEL
    assert inference.reasons["letter_apostrophe"] == "vowel, the usual choice: there is no apostrophe in the names."


def test_the_apostrophe_is_a_vowel_when_test_runs_make_likelier_names_with_it_one():
    inference = infer(load_style(CHAPTERS_DIR / "Angelic.ebn"), ignore_pins=True)  # Aalbe'il
    assert inference.working.setting("letter_apostrophe") is ApostropheMode.VOWEL
    assert inference.reasons["letter_apostrophe"].startswith(
        "vowel: in a test, names made with it a vowel read "
    )


def test_the_apostrophe_is_a_consonant_when_test_runs_make_likelier_names_with_it_one():
    inference = infer(plain(*HAWAIIAN))
    assert inference.working.setting("letter_apostrophe") is ApostropheMode.CONSONANT
    assert inference.working.letter_classes.is_consonant("'")
    assert inference.reasons["letter_apostrophe"].startswith(
        "consonant: in a test, names made with it a consonant read "
    )
    assert infer(plain(*HAWAIIAN)).reasons == inference.reasons  # the same every time


def test_an_apostrophe_that_leaves_a_name_with_no_consonant_is_chosen_by_a_test_run():
    inference = infer(plain(*made_up(50), "A'a"))
    assert inference.working.setting("letter_apostrophe") is ApostropheMode.CONSONANT
    assert inference.reasons["letter_apostrophe"].startswith("consonant: in a test,")


def test_a_pinned_apostrophe_is_kept():
    style = parse_toml('title = "T"\nnames = "' + ", ".join(HAWAIIAN) + '"\n[settings]\nletter_apostrophe = "vowel"')
    inference = infer(style)
    assert inference.working.setting("letter_apostrophe") is ApostropheMode.VOWEL
    assert "letter_apostrophe" not in inference.reasons


def test_a_long_list_is_measured_on_some_of_its_names(monkeypatch):
    import namebook.inference

    monkeypatch.setattr(namebook.inference, "MEASURED_NAMES", 80)
    style = plain(*with_soft_letters())  # 175 names
    reading = namebook.inference._Reading(style)
    assert len(reading.letters) == 175 and len(reading.hidden) == 80  # every name read, 80 hidden
    assert infer(style).working.special_letters.soft_consonants  # as clear on 80 of them


def test_copies_of_the_names_are_refused():
    inference = infer(plain(*made_up(50)))
    assert inference.working.options.refuse_copies
    assert inference.reasons["refuse_copies"].startswith("on: a name that is one of the list's names")


def test_never_inferred():
    working = infer(plain(*made_up(60))).working
    assert working.special_letters.consonant_clusters == ()
    assert not working.special_letters.multi_vowels
    assert working.options.punctuation == Punctuation.NONE


def test_pinned_clusters_follow_inferred_soft_consonants():
    names = ", ".join(with_soft_letters() + ["Althas"])
    style = parse_toml(f'names = "{names}"\n[settings]\nconsonant_clusters = ["LTH"]', path="x.toml")
    assert style.special_letters.consonant_clusters == ("LTH",)
    working = infer(style).working
    assert working.special_letters.soft_consonants
    assert working.special_letters.consonant_clusters == ("Lt",)  # the soft form, so it matches


# --- Word shapes and length ------------------------------------------------


def test_names_of_one_word_need_no_templates():
    inference = infer(plain(*made_up(50)))
    assert inference.working.templates == ()
    assert inference.reasons["templates"] == "none: every name is one word, of any shape the names' words have."


def test_names_of_several_words_get_templates_for_each_count():
    names = [*made_up(6), "Bel Cor", "Dan Eli", "Fen Gor", "Hal Ivo Jon"]
    inference = infer(plain(*names))
    assert inference.working.templates == (Template("#", 6), Template("# #", 3), Template("# # #", 1))
    assert inference.reasons["templates"].startswith(
        "names of one word or more, as in the list (6 of one, 3 of two and 1 of three words)"
    )


# --- The hyphen (spec 13.15) ---------------------------------------------------


def test_the_hyphen_is_a_consonant_when_the_names_have_none():
    inference = infer(plain(*made_up(50)))
    assert inference.working.options.hyphen is HyphenMode.CONSONANT
    assert inference.reasons["letter_hyphen"] == "consonant, the usual choice: there is no hyphen in the names."


def test_a_hyphen_in_the_names_is_a_joint_with_templates_joined_by_it():
    names = [*made_up(6), "Bel-Cor", "Dan-Eli Fen", "Gor Hal", "Ivo-Jon-Kel"]
    inference = infer(plain(*names))
    assert inference.working.options.hyphen is HyphenMode.JOINT
    assert inference.reasons["letter_hyphen"] == (
        "joint: it joins two words of a name, as a space does, in 3 of 10 names (such as Bel-Cor)."
    )
    assert inference.working.templates == (
        Template("#", 6), Template("# #", 1), Template("#-#", 1), Template("#-# #", 1), Template("#-#-#", 1)
    )  # fmt: skip
    assert inference.reasons["templates"].startswith(
        "names of one word or more, as in the list (6 of one word, 1 of two words, 1 of two words "
        "joined by a hyphen, 1 of three words, with a hyphen and 1 of three words joined by hyphens)"
    )


def test_a_hyphen_that_joins_nothing_keeps_it_a_consonant():
    inference = infer(plain(*made_up(50), "Bel-"))
    assert inference.working.options.hyphen is HyphenMode.CONSONANT
    assert inference.reasons["letter_hyphen"] == "consonant, the usual choice: the hyphen in Bel- does not join two words."
    assert inference.working.templates == ()


def test_a_pinned_hyphen_is_kept_and_templates_follow_it():
    style = parse_toml('title = "T"\nnames = "Bel-Cor, Dana, Eli"\n[settings]\nletter_hyphen = "consonant"')
    inference = infer(style)
    assert inference.working.options.hyphen is HyphenMode.CONSONANT
    assert "letter_hyphen" not in inference.reasons
    assert inference.working.templates == ()  # Bel-Cor is one word
    given = infer(style, given={"letter_hyphen": HyphenMode.JOINT})
    assert given.working.templates == (Template("#", 2), Template("#-#", 1))


def test_no_length_range_is_inferred():
    inference = infer(plain(*made_up(50)))
    assert inference.working.options.length_range is None
    assert inference.reasons["min_length"] == "0, no limit: lengths follow the names."


def test_the_chain_is_used_at_full_strength():
    inference = infer(plain(*made_up(50)))
    options = inference.working.options
    assert options.markov in (Markov.FILL, Markov.WEIGHTED) and options.markov_strength == 1.0
    assert 1 <= options.markov_order <= 5  # worked out by hiding names


def test_a_small_list_is_filled_from_the_chain_without_a_test_run():
    inference = infer(plain(*made_up(SMALL_LIST - 1)))
    assert inference.working.options.markov is Markov.FILL
    assert inference.reasons["markov"].startswith(f"fill: with only {SMALL_LIST - 1} different names")
    options = inference.working.options  # no back-off: that is for the classic way
    assert (options.fit_level, options.prefixes, options.suffixes, options.shuffle) == (3, True, True, False)


def test_weighted_choice_when_its_names_are_clearly_likelier_in_a_test_run():
    inference = infer(load_style(CHAPTERS_DIR / "Demonic.ebn"), ignore_pins=True)
    assert inference.working.options.markov is Markov.WEIGHTED
    assert re.fullmatch(
        r"weighted: in a test, names made from half the list with weighted choice read \d+% "
        r"more naturally, judged against the other half, than names made by fill\.",
        inference.reasons["markov"],
    )


@pytest.mark.parametrize(
    "count, reason",
    [
        (50, "fill: in a test, names made by fill from half the list read"),
        (100, "fill, the usual choice: in a test, names made from half the list with weighted choice "
         "read about as naturally"),
    ],
)  # fmt: skip
def test_fill_when_weighted_choice_is_not_clearly_likelier(count, reason):
    inference = infer(plain(*made_up(count)))
    assert inference.working.options.markov is Markov.FILL
    assert inference.reasons["markov"].startswith(reason)


def test_the_test_run_gives_the_same_choice_each_time():
    names = plain(*made_up(60))
    assert infer(names).reasons["markov"] == infer(names).reasons["markov"]


def test_no_test_run_when_markov_is_pinned_or_given():
    names = ", ".join(made_up(60))
    pinned = infer(parse_toml(f'names = "{names}"\n[settings]\nmarkov = "fill"', path="x.toml"))
    assert pinned.working.options.markov is Markov.FILL and "markov" not in pinned.reasons
    given = infer(plain(*made_up(60)), given={"markov": Markov.FILL})
    assert given.working.options.markov is Markov.FILL and "markov" not in given.reasons


def test_weighted_choice_when_the_style_pins_structural_off():
    names = ", ".join(made_up(50))
    inference = infer(parse_toml(f'names = "{names}"\n[settings]\nstructural = false', path="x.toml"))
    assert inference.working.options.markov is Markov.WEIGHTED
    assert "needs structural on" in inference.reasons["markov"]


def test_the_look_back_is_worked_out_when_the_chain_is_used():
    names = ", ".join(made_up(60))
    inference = infer(parse_toml(f'names = "{names}"\n[settings]\nmarkov = "weighted"', path="x.toml"))
    look_back = inference.working.options.markov_order
    assert 1 <= look_back <= 5
    letters = "1 letter" if look_back == 1 else f"{look_back} letters"
    assert inference.reasons["markov_order"] == (
        f"{look_back}: names held back as a test read most naturally when the chain looks back {letters}."
    )


def test_a_pinned_look_back_is_kept():
    names = ", ".join(made_up(60))
    text = f'names = "{names}"\n[settings]\nmarkov = "weighted"\nmarkov_order = 5'
    inference = infer(parse_toml(text, path="x.toml"))
    assert inference.working.options.markov_order == 5 and "markov_order" not in inference.reasons


# --- Several lists (spec 13.5) ----------------------------------------------------


def test_each_list_is_worked_out_on_its_own():
    names = "\n".join(made_up(60))
    hyphenated = "\n".join(f"{a}-{b}" for a, b in zip(made_up(60), reversed(made_up(60))))
    style = parse_toml(
        f'title = "T"\n[settings]\nfit_level = 2\n[settings.family]\nmarkov = "off"\n'
        f'[names]\ngiven = """\n{names}\n"""\nfamily = """\n{hyphenated}\n"""'
    )
    inferences = infer_lists(style)
    given, family = inferences["given"], inferences["family"]
    assert list(inferences) == ["given", "family"]
    assert given.working.options.hyphen is HyphenMode.CONSONANT
    assert family.working.options.hyphen is HyphenMode.JOINT  # only this list has hyphens
    assert given.working.options.markov is not Markov.OFF and "markov" in given.reasons
    assert family.working.options.markov is Markov.OFF and "markov" not in family.reasons
    assert given.working.options.fit_level == family.working.options.fit_level == 2  # pinned for both
    assert given.working.pinned == {"fit_level"} and family.working.pinned == {"fit_level", "markov"}
    # Settings given apply to every list.
    assert all(i.working.options.prefixes is False for i in infer_lists(style, given={"prefixes": False}).values())
