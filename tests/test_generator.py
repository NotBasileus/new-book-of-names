"""Generating names: relaxation, validation, repeats, and explain mode."""

import dataclasses
import random
from collections import Counter

import pytest

from conftest import SAMPLE_CHAPTERS, STYLES_DIR
from namebook.analysis import analyze
from namebook.building import BuildAttempt, Shape
from namebook.explain import explain_name
from namebook.formats import load_style
from namebook.formats.txt import parse_txt
from namebook.inference import infer
from namebook.generator import (
    COPIES_REFUSED,
    COPY_FAILURE,
    FAILURES_ALLOWED,
    MAX_BUILD_ATTEMPTS,
    MAX_WORD_TRIES,
    Checked,
    CopyRefused,
    Finished,
    GaveUp,
    GeneratedName,
    GenerationError,
    HistoryConflict,
    LengthRejected,
    NameGenerator,
    Relaxed,
    ShapeChosen,
    TemplateChosen,
    ValidationLowered,
    relax,
    template_problem,
)
from namebook.letters import capitals, small
from namebook.markov import plain_letters
from namebook.quality import SeedIndex
from namebook.style import (
    GenerationOptions,
    Markov,
    NameList,
    Punctuation,
    SpecialLetters,
    Style,
    Template,
    YMode,
)
from namebook.validation import Rejection, Validator, WordListCheck
from namebook.words import letter_count, plain

EVERYTHING_ON = GenerationOptions(
    structural=True, statistical=True, fit_level=3, prefixes=True, suffixes=True
)


def generator_for(*names, options=None, seed=1, y_mode=YMode.VOWEL, **kwargs):
    style = Style(
        title="Test",
        name_lists=(NameList("names", names),),
        options=GenerationOptions(y_mode=y_mode),
    )
    return NameGenerator(analyze(style), options, random.Random(seed), **kwargs)


def sample_generator(index=2, seed=1, options=None):
    """A generator for a sample style; index 2 is Angelic."""
    return NameGenerator(analyze(load_style(SAMPLE_CHAPTERS[index])), options, random.Random(seed))


def events_of(name, kind):
    return [event for event in name.events if isinstance(event, kind)]


# --- Basics ------------------------------------------------------------------


def test_generates_names():
    generator = sample_generator()
    names = [generator.generate() for _ in range(50)]
    assert all(name.ok for name in names)
    assert all(name.text[0].isupper() and name.text[1:] == name.text[1:].lower() for name in names)
    assert all(name.words[0].text == name.text for name in names)


def test_same_seed_gives_same_names():
    first = [sample_generator(seed=42).generate().text for _ in range(20)]
    again = [sample_generator(seed=42).generate().text for _ in range(20)]
    other = [sample_generator(seed=43).generate().text for _ in range(20)]
    assert first == again
    assert first != other


def test_uses_the_style_options_unless_given_others():
    generator = sample_generator()
    assert generator.options == generator.analysis.style.options
    assert sample_generator(options=GenerationOptions(fit_level=0)).options.fit_level == 0


def test_shape_without_structural_has_no_substructure():
    generator = sample_generator(options=GenerationOptions(structural=False))
    assert generator.choose_shape(None, None).substructure is None


def test_shape_from_requested_structures_only():
    generator = sample_generator()
    assert {generator.choose_shape([5, 6], None).structure for _ in range(100)} == {5, 6}
    assert {generator.generate(structures=[5]).words[0].shape.structure for _ in range(5)} == {5}


def test_requested_structures_that_never_occur():
    with pytest.raises(GenerationError, match="None of the requested structures"):
        sample_generator().choose_shape([99], None)


def test_every_sample_chapter_always_makes_a_name():
    for path in SAMPLE_CHAPTERS:
        generator = NameGenerator(analyze(load_style(path)), rng=random.Random(9))
        assert all(generator.generate().ok for _ in range(200)), path.name


ONE_ELEMENT_SEEDS = ("Ai", "Ou", "Bai", "Lao", "Tao", "Mei", "Aolin", "Bolan", "Ouli", "Kaili")


@pytest.mark.parametrize("markov", list(Markov))
def test_words_of_one_element_are_made_when_the_seeds_have_them(markov):
    options = GenerationOptions(structural=True, statistical=True, refuse_copies=True, markov=markov)
    style = Style(title="Test", name_lists=(NameList("names", ONE_ELEMENT_SEEDS),), options=options)
    generator = NameGenerator(analyze(style), rng=random.Random(1))
    made = [generator.generate() for _ in range(300)]
    assert all(name.ok for name in made)
    alone = [name.text for name in made if name.words[0].shape.structure == -1]
    assert alone and all(set(text.upper()) <= set("AEIOU") for text in alone)
    assert not any(name.words[0].shape.structure == -2 for name in made)  # no seed word is C


HYPHENATED_SEEDS = (
    "Bel-Abrean", "Bel-Amy", "Ceithin-Har", "Yr-Kithar", "Arandor", "Melian", "Thalion", "Ilsevel",
    "Corin", "Dorlas", "Elenwe", "Faelan",
)  # fmt: skip


def hyphen_generator(markov=Markov.FILL, seed=1, **given):
    style = infer(parse_txt("\n".join(HYPHENATED_SEEDS), "Hyphens.txt"), given={"markov": markov, **given})
    return NameGenerator(analyze(style.working), rng=random.Random(seed))


def shapes_of(words):
    return tuple((word.shape.structure, word.shape.substructure) for word in words)


@pytest.mark.parametrize("markov", list(Markov))
def test_a_name_takes_the_shape_of_one_seed_name_joined_the_same_way(markov):
    generator = hyphen_generator(markov)
    seeds = {}
    for name in HYPHENATED_SEEDS:
        words = generator.analysis.splitter.split_name(name)
        seeds.setdefault(len(words), set()).add(tuple((word.structure, word.substructure) for word in words))
    made = [generator.generate() for _ in range(300)]
    assert all(name.ok for name in made)
    hyphenated = [name for name in made if len(name.words) == 2]
    assert hyphenated and len(hyphenated) < len(made)
    for name in made:
        # Both words' shapes come from one seed name; a one-word name's from a
        # one-word seed name, never from a part such as Bel or Yr.
        assert shapes_of(name.words) in seeds[len(name.words)], name.text
    for name in hyphenated:
        first, second = name.text.split("-")
        assert first[0].isupper() and second[0].isupper(), name.text


def test_fill_never_puts_a_hyphen_inside_a_word_when_it_joins_words():
    generator = hyphen_generator()
    words = [word for name in (generator.generate() for _ in range(300)) for word in name.words]
    assert not any("-" in word.text for word in words)


def test_a_length_range_chooses_seed_names_of_a_length_in_range():
    # Of the seed names, only Yr-Kithar has 8 letters, so every name has its shape.
    generator = hyphen_generator(min_length=8, max_length=8)
    made = [generator.generate() for _ in range(40)]
    assert all(name.ok and "-" in name.text and letter_count(name.text) == 8 for name in made)
    assert not any(events_of(name, LengthRejected) for name in made)


def test_starting_letters_choose_seed_names_whose_first_word_can_begin_with_them():
    generator = hyphen_generator(markov=Markov.OFF)
    made = [generator.generate(initial="Y") for _ in range(30)]
    assert all(name.ok and name.text.startswith("Y") for name in made)


def test_explain_shows_a_word_of_one_element():
    style = Style(title="Test", name_lists=(NameList("names", ONE_ELEMENT_SEEDS),))
    generator = NameGenerator(analyze(style), rng=random.Random(1))
    assert "structure -1 (V)" in explain_name(generator.generate(structures=[-1], explain=True))


# --- Automatic relaxation (spec 7.4) -----------------------------------------


def test_relax_order():
    options, messages = EVERYTHING_ON, []
    while relaxed := relax(options):
        options, event = relaxed
        messages.append(event.message)
    assert messages == [
        'Changed "Fit_lvl" to 2.',
        'Changed "Fit_lvl" to 1.',
        'Changed "Fit_lvl" to 0.',
        'Dropped "Structural".',
        'Dropped "Prefixes".',
        'Dropped "Suffixes".',
    ]
    assert options.statistical  # never relaxed


def test_relax_skips_rules_already_off():
    options, event = relax(GenerationOptions(fit_level=0, structural=False, prefixes=True))
    assert event.message == 'Dropped "Prefixes".'
    assert relax(GenerationOptions(fit_level=0)) is None


def impossible_generator(options=EVERYTHING_ON, **kwargs):
    """A word that can never be built: every seed ends in a vowel, but the
    word is asked to be structure 5 (VCVC), which ends in a consonant."""
    generator = generator_for("Ana", "Ebo", options=options, **kwargs)
    generator.analysis.structures[5] = 1  # pretend structure 5 occurred, so it can be chosen
    generator.analysis.substructures[5] = {(1, 1, 1, 1): 1}
    return generator


def test_every_fifth_failed_build_loosens_one_rule():
    name = impossible_generator().generate(structures=[5])
    assert not name.ok
    attempts = events_of(name, BuildAttempt)
    assert len(attempts) == MAX_BUILD_ATTEMPTS
    # Rules are loosened after builds 5, 10, 15, 20, 25 and 30.
    relaxed_at = [
        sum(isinstance(e, BuildAttempt) for e in name.events[:i])
        for i, e in enumerate(name.events)
        if isinstance(e, Relaxed)
    ]
    assert relaxed_at == [5, 10, 15, 20, 25, 30]
    assert [attempt.options.fit_level for attempt in attempts[:16]] == [3] * 5 + [2] * 5 + [1] * 5 + [0]
    assert not attempts[-1].options.structural
    assert attempts[-1].options.statistical
    assert isinstance(name.events[-1], GaveUp)


def test_settings_are_restored_after_a_successful_build():
    generator = sample_generator(seed=3)
    for _ in range(100):
        name = generator.generate(initial="B")
        assert name.ok
        # Every shape starts with the user's own settings.
        for index, event in enumerate(name.events):
            if isinstance(event, ShapeChosen):
                assert name.events[index + 1].options == generator.options


def test_initial_letters_no_longer_give_up():
    generator = sample_generator(seed=3)
    names = [generator.generate(initial="B") for _ in range(200)]
    assert all(name.ok and name.text.startswith("B") for name in names)
    assert any(events_of(name, Relaxed) for name in names)


# --- Validation and its relaxation (spec 7.2) -------------------------------


class RejectAboveZero:
    """A check that rejects every word at validation level 1 or more."""

    def applies_at(self, level):
        return level >= 1

    def check(self, letters, text):
        return Rejection("!TEST", "always")


def test_rejected_words_lower_the_validation_level_every_fifth_time():
    generator = generator_for(
        "Ana", "Ebo", options=GenerationOptions(validation_level=2),
        validator=Validator([RejectAboveZero()]),
    )  # fmt: skip
    name = generator.generate()
    assert name.ok
    checks = events_of(name, Checked)
    assert [check.level for check in checks] == [2] * 5 + [1] * 5 + [0]
    assert all(check.rejection.code == "!TEST" for check in checks[:-1])
    assert checks[-1].rejection is None
    assert [event.message for event in events_of(name, ValidationLowered)] == [
        'Changed "Val_lvl" to 1.',
        'Changed "Val_lvl" to 0.',
    ]


def test_a_rejected_word_gets_a_fresh_shape():
    generator = generator_for("Ana", "Ebo", validator=Validator([RejectAboveZero()]))
    name = generator.generate()
    # One shape per built word: each rejection is followed by a new shape.
    assert len(events_of(name, ShapeChosen)) == len(events_of(name, Checked))


def test_validation_level_starts_again_for_each_name():
    generator = generator_for(
        "Ana", "Ebo", "Ita", "Uzo", validator=Validator([RejectAboveZero()]), history_size=0
    )
    generator.generate()
    second = generator.generate()
    assert events_of(second, Checked)[0].level == 2


def test_word_list_that_blocks_everything_gives_up():
    generator = generator_for("Ana", validator=Validator([WordListCheck(["ana"])]))
    name = generator.generate()
    assert not name.ok
    checks = events_of(name, Checked)
    assert len(checks) == MAX_WORD_TRIES
    assert {check.rejection.code for check in checks} == {"!LIST"}
    assert isinstance(name.events[-1], GaveUp)


def test_validation_uses_the_style_letter_limits():
    # Demonic, a small list, has the most spacing and frequency rejections.
    generator = sample_generator(index=3, seed=5)
    rejections = [
        check.rejection.code
        for _ in range(300)
        for check in events_of(generator.generate(), Checked)
        if check.rejection
    ]
    assert any(code.startswith("!DIST/") for code in rejections)
    assert any(code.startswith("!FREQ/") for code in rejections)


# --- Avoiding repeats (spec 7.5) ---------------------------------------------


def test_recent_names_are_not_repeated():
    generator = sample_generator(seed=8)
    names = [generator.generate().text.lower() for _ in range(100)]
    assert len(set(names)) == 100


def test_a_repeat_is_accepted_after_five_failures():
    generator = generator_for("Ana")  # only one name can ever be made
    assert generator.generate().text == "Ana"
    second = generator.generate()
    assert second.text == "Ana"
    conflicts = events_of(second, HistoryConflict)
    assert [conflict.ignored for conflict in conflicts] == [False] * 4 + [True]
    assert len(conflicts) == FAILURES_ALLOWED


def test_history_forgets_older_names():
    generator = generator_for("Ana", history_size=2)
    generator.generate()
    generator.history.extend(["bel", "cor"])  # two newer names push "ana" out
    assert not events_of(generator.generate(), HistoryConflict)


def test_history_can_be_turned_off():
    generator = generator_for("Ana", history_size=0)
    generator.generate()
    assert not events_of(generator.generate(), HistoryConflict)


# --- Refusing copies of seed names (spec 13.1) ---------------------------------


def test_copies_of_seed_names_are_made_again():
    style = load_style(SAMPLE_CHAPTERS[2])  # Angelic, which copies its names now and then
    analysis = analyze(style)
    seeds = {plain(name) for name in style.names}
    options = dataclasses.replace(style.options, refuse_copies=True)
    generator = NameGenerator(analysis, options, random.Random(1))
    names = [generator.generate() for _ in range(300)]
    assert not any(plain(name.text) in seeds for name in names)
    refused = [event for name in names for event in events_of(name, CopyRefused)]
    assert refused and not any(event.ignored for event in refused)
    assert all(plain(event.text) in seeds for event in refused)


def test_copies_are_allowed_by_default():
    generator = sample_generator(seed=1)
    names = [generator.generate() for _ in range(300)]
    seeds = {plain(name) for name in load_style(SAMPLE_CHAPTERS[2]).names}
    assert any(plain(name.text) in seeds for name in names)  # the classic way, as the chapter is written
    assert not any(events_of(name, CopyRefused) for name in names)


def test_a_copy_is_accepted_when_nothing_else_is_made():
    generator = generator_for("Ana", options=GenerationOptions(refuse_copies=True))
    name = generator.generate()
    assert name.text == "Ana"  # the only name that can ever be made
    refused = events_of(name, CopyRefused)
    assert len(refused) == COPIES_REFUSED and [event.ignored for event in refused][-2:] == [False, True]


def test_explain_shows_a_refused_copy():
    generator = generator_for("Ana", options=GenerationOptions(refuse_copies=True))
    text = explain_name(generator.generate(), 1)
    assert f"Fail: {COPY_FAILURE}: Ana is a seed name, so it is made again." in text
    assert "Accepted a copy: Ana is a seed name, but nothing else was made." in text


# --- Templates (spec 7.1) ----------------------------------------------------


def test_a_style_without_templates_uses_any_structure():
    generator = generator_for("Ana", "Banan")
    assert [template.recipe for template, _ in generator.templates] == ["#"]
    assert generator.generate().template == "#"


def test_templates_are_chosen_by_weight():
    templates = [Template("5", 9), Template("6", 1)]
    generator = NameGenerator(sample_generator().analysis, rng=random.Random(4), templates=templates)
    chosen = Counter(generator.generate().template for _ in range(500))
    assert set(chosen) == {"5", "6"}
    assert chosen["5"] > 400  # about 9 in 10


def test_slot_alternatives_and_structures():
    generator = NameGenerator(
        sample_generator().analysis, rng=random.Random(2), templates=[Template("5|6", 1)]
    )
    structures = {generator.generate().words[0].shape.structure for _ in range(50)}
    assert structures == {5, 6}


def test_multi_word_names():
    generator = NameGenerator(
        sample_generator().analysis, rng=random.Random(2), templates=[Template("6 2", 1)]
    )
    name = generator.generate()
    assert len(name.words) == 2
    assert [word.shape.structure for word in name.words] == [6, 2]
    assert name.text == f"{name.words[0].text} {name.words[1].text}"


def test_literal_text_between_words():
    generator = NameGenerator(
        sample_generator().analysis, rng=random.Random(2), templates=[Template("5'7", 1)]
    )
    name = generator.generate()
    first, second = name.words
    assert name.text == f"{first.text}'{second.text}"


def test_sample_chapter_templates_limit_the_structures():
    for path in SAMPLE_CHAPTERS:
        generator = NameGenerator(analyze(load_style(path)), rng=random.Random(6))
        structures = {generator.generate().words[0].shape.structure for _ in range(300)}
        assert max(structures) <= 13, path.name


def test_templates_that_can_never_be_used_are_skipped():
    analysis = sample_generator().analysis  # Angelic: no seed has structure 0 or 1
    generator = NameGenerator(analysis, rng=random.Random(1))
    assert [skipped.template.recipe for skipped in generator.skipped_templates] == ["0", "1"]
    assert generator.skipped_templates[0].reason == "needs structure 0, which no seed name has"
    assert template_problem(analysis, Template("0|5", 1)) is None  # one usable alternative is enough
    assert template_problem(analysis, Template("5", 0)) == "has weight 0"


def test_no_usable_template_is_an_error():
    with pytest.raises(GenerationError, match='None of the templates can ever be used: "0:1"'):
        NameGenerator(sample_generator().analysis, templates=[Template("0", 1)])


def test_an_initial_needs_a_template_that_can_start_with_it():
    analysis = sample_generator().analysis
    generator = NameGenerator(
        analysis, rng=random.Random(3), templates=[Template("Mac6", 5), Template("5", 1)]
    )
    # "Mac6" begins with literal text and 5 (VCVC) begins with a vowel, so no
    # template can start with B.
    with pytest.raises(GenerationError, match="None of the templates can make a name starting with 'B'"):
        generator.generate(initial="B")
    assert all(generator.generate(initial="A").template == "5" for _ in range(10))


def test_a_repeat_is_made_again_with_the_same_template():
    # Only two names can ever be made: Ana (template "3") and Bel ("2"). Once
    # both exist, every name is a repeat and is made again.
    style = Style(
        title="Two",
        name_lists=(NameList("names", ("Ana", "Bel")),),
        templates=(Template("3", 1), Template("2", 1)),
    )
    generator = NameGenerator(analyze(style), rng=random.Random(1))
    names = [generator.generate() for _ in range(6)]
    repeated = [name for name in names if events_of(name, HistoryConflict)]
    assert repeated
    for name in repeated:
        assert len(events_of(name, TemplateChosen)) == 1
        shapes = {event.shape.structure for event in events_of(name, ShapeChosen)}
        assert shapes == {int(name.template)}


def test_punctuation_option():
    analysis = analyze(load_style(SAMPLE_CHAPTERS[0]))  # Ancient Female: Kullaa, Gemekaa...
    plain = NameGenerator(analysis, rng=random.Random(2))
    accented = NameGenerator(
        analysis,
        dataclasses.replace(analysis.style.options, punctuation=Punctuation.ACCENTS),
        random.Random(2),
    )
    plain_names = [plain.generate().text for _ in range(200)]
    accented_names = [accented.generate().text for _ in range(200)]
    assert not any(set(name) & set("áéíóú") for name in plain_names)
    assert any(set(name) & set("áéíóú") for name in accented_names)


def test_built_is_the_name_before_punctuation_and_capitals():
    analysis = analyze(load_style(SAMPLE_CHAPTERS[0]))
    options = dataclasses.replace(analysis.style.options, punctuation=Punctuation.ACCENTS)
    generator = NameGenerator(analysis, options, random.Random(2))
    names = [generator.generate() for _ in range(200)]
    accented = next(name for name in names if set(name.text) & set("áéíóú"))
    assert accented.built.isupper() and not set(accented.built) & set("ÁÉÍÓÚ")
    assert accented.built == [e for e in accented.events if isinstance(e, Finished)][-1].built
    assert GeneratedName(None, (), (), "#").built is None


def test_explain_shows_template_words_and_finished_name():
    generator = NameGenerator(
        sample_generator().analysis, rng=random.Random(2), templates=[Template("5'7", 1)]
    )
    name = generator.generate(explain=True)
    text = explain_name(name)
    assert '  Template: "5\'7"' in text
    assert '  Word 1 of 2 (slot "5"):' in text
    assert '  Word 2 of 2 (slot "7"):' in text
    first, second = name.words
    assert f"  Finished: {first.text.upper()}'{second.text.upper()} → {name.text}" in text


# --- Initial letters ---------------------------------------------------------


def test_initial_vowel_needs_odd_structures():
    generator = sample_generator()
    for _ in range(30):
        name = generator.generate(initial="A")
        assert name.words[0].shape.structure % 2 == 1
        assert name.text.startswith("A")


def test_initial_can_be_several_letters_and_matches_special_letters():
    generator = sample_generator()  # Angelic, with soft consonants
    assert all(generator.generate(initial="Ch").text.startswith("Ch") for _ in range(20))
    # T matches both T and the soft letter TH.
    starts = {generator.generate(initial="T").text[:2] for _ in range(100)}
    assert "Th" in starts and len(starts) > 1


def test_impossible_initial():
    with pytest.raises(GenerationError, match="No seed word starts with 'X'"):
        sample_generator().generate(initial="X")


def test_initial_must_be_letters():
    with pytest.raises(GenerationError, match="must be made of letters"):
        sample_generator().generate(initial="4")


def test_checking_an_initial_draws_nothing_at_random():
    generator, same = sample_generator(seed=4), sample_generator(seed=4)
    assert generator.check_initial("ka") == "KA"
    with pytest.raises(GenerationError, match="No seed word starts with 'X'"):
        generator.check_initial("X")
    assert generator.generate().text == same.generate().text


def test_initial_y_follows_the_y_setting():
    # With y:semi, Yorath starts with a consonant Y and Ysolt with a vowel Y.
    generator = generator_for("Yorath", "Ysolt", y_mode=YMode.SEMI, history_size=0)
    shapes = {generator.generate(initial="Y").words[0].shape.structure % 2 for _ in range(50)}
    assert shapes == {0, 1}


# Starting letters in every way of building (spec 13.11).
LETTERS_SEEDS = ("Kara", "Kelin", "Bran", "Ela", "Mora", "Talen", "Jana", "Joren", "Stella", "Trina")


def fill_generator(*names, seed=1):
    analysis = analyze(Style(title="Test", name_lists=(NameList("names", names),)))
    options = filling(analysis, structural=True, statistical=True)
    return NameGenerator(analysis, options, random.Random(seed))


@pytest.mark.parametrize("markov", [Markov.OFF, Markov.FILL])
def test_starting_letters_can_run_past_the_first_part(markov):
    # No word begins with an element KA or BRE, but K then A, and BR then E, can.
    if markov is Markov.FILL:
        generator = fill_generator(*LETTERS_SEEDS)
    else:
        generator = generator_for(*LETTERS_SEEDS, options=EVERYTHING_ON)
    for initial in ("Ka", "Bre"):
        names = [generator.generate(initial=initial) for _ in range(20)]
        assert all(name.ok and name.text.startswith(initial) for name in names)


def test_a_word_needs_enough_elements_for_its_starting_letters():
    builder = generator_for(*LETTERS_SEEDS).builder
    assert not builder.can_hold(Shape(0), "KAR")  # CV: K, A, and no room for R
    assert builder.can_hold(Shape(2), "KAR")  # CVC


def test_templates_that_cannot_hold_the_starting_letters_are_skipped():
    generator = generator_for(*LETTERS_SEEDS, templates=[Template("0", 5), Template("2", 1)])
    assert all(generator.generate(initial="Kar").template == "2" for _ in range(10))


def real_list(style: str = "Old_English", name_list: str = "male") -> Style:
    """One list of a bundled style, every setting inferred from its names,
    as for a plain list of names, except fill, which these tests are about
    (a plain list may be given weighted choice instead, D96)."""
    path = STYLES_DIR / f"{style}.toml"
    style_list = load_style(path).list_style(name_list)
    return infer(style_list, ignore_pins=True, given={"markov": Markov.FILL}).working


def test_fill_finds_shapes_for_starting_letters_on_a_real_list():
    # Most Old English words begin with one consonant, which left no room for Th.
    generator = NameGenerator(analyze(real_list()), rng=random.Random(1))
    for initial in ("Th", "Bra"):
        names = [generator.generate(initial=initial) for _ in range(20)]
        assert all(name.ok and name.text.startswith(initial) for name in names)


def test_fill_gives_the_starting_letters_parts_as_long_as_the_seeds_do():
    # Some words begin with two consonants, but J is always followed by a
    # vowel, so fill never adds a consonant after it.
    generator = fill_generator(*LETTERS_SEEDS)
    names = [generator.generate(initial="J") for _ in range(30)]
    assert all(name.ok and name.words[0].shape.substructure[0] == 1 for name in names)
    assert all(name.text[1] in "aeiouy" for name in names)


def test_starting_letters_no_name_can_have():
    generator = generator_for(*LETTERS_SEEDS)
    with pytest.raises(GenerationError) as caught:
        generator.generate(initial="Brx")
    assert str(caught.value) == "No name can start with 'BRX'. Names can start with 'BR'."
    with pytest.raises(GenerationError) as caught:
        generator.generate(initial="Ch")
    assert str(caught.value) == "No seed word starts with 'C', so no name can start with 'CH'."


# Every Latin letter (spec 13.8).
TURKISH = ("Işıl", "Yıldız", "Ilgaz", "Aydın", "Altın", "Şükrü", "Gülşen", "Ömer",
           "İlkay", "İsmail", "Emine", "Çağrı", "Doğan", "Kıvanç", "Sıla", "Tülin")  # fmt: skip
POLISH = ("Łucja", "Bożena", "Małgorzata", "Jędrzej", "Włodzimierz", "Zbigniew", "Grażyna",
          "Mieczysław", "Wojciech", "Józef", "Stanisław", "Bolesław", "Jadwiga", "Żaneta")  # fmt: skip


@pytest.mark.parametrize("markov", [Markov.OFF, Markov.WEIGHTED, Markov.FILL])
def test_names_use_every_latin_letter_of_the_seeds(markov):
    analysis = analyze(Style(title="Test", name_lists=(NameList("names", TURKISH + POLISH),)))
    options = dataclasses.replace(EVERYTHING_ON, markov=markov, markov_strength=1.0)
    generator = NameGenerator(analysis, options, random.Random(1))
    names = [generator.generate().text for _ in range(60)]
    seed_letters = set(capitals("".join(TURKISH + POLISH)))
    assert all(name and set(capitals(name)) <= seed_letters for name in names)
    assert all(name[0].isupper() and name[1:] == small(name[1:]) for name in names)
    written = "".join(names)
    assert "ı" in written and "ł" in written
    assert "̇" not in written  # no stray dot from a small İ


def test_starting_letters_can_be_any_latin_letter():
    generator = fill_generator(*TURKISH, *POLISH)
    assert all(generator.generate(initial="Ł").text.startswith("Ł") for _ in range(10))
    assert all(generator.generate(initial="iş").text.startswith("Iş") for _ in range(10))
    with pytest.raises(GenerationError, match="No seed word starts with 'Ğ'"):
        generator.generate(initial="ğ")


# --- Explain mode ------------------------------------------------------------


def test_explain_shows_the_shape_attempts_and_choices():
    name = sample_generator(seed=1).generate(explain=True)
    text = explain_name(name, 1)
    assert text.startswith(f"Name 1: {name.text}")
    assert "  Shape: structure " in text
    assert "  Attempt 1" in text
    assert "candidates" in text
    assert "Built: " in text and f"→ {name.text}" in text
    assert "Checked at validation level 2: passed" in text
    assert text.endswith(f"→ {name.text}")  # the Finished line


def test_explain_shows_relaxation_and_summarizes_long_runs_of_failures():
    name = impossible_generator().generate(structures=[5], explain=True)
    text = explain_name(name)
    assert text.startswith("(no name could be made)")
    assert text.count("    Failed: !") == 3  # the first three in full
    assert "  Attempt 4: failed at position " in text
    assert '  Changed "Fit_lvl" to 2.' in text
    assert '  Dropped "Structural".' in text
    assert text.endswith("Gave up: no word could be built in this shape in 100 tries.")


def test_explain_shows_rejections_and_lowered_levels():
    generator = generator_for("Ana", "Ebo", validator=Validator([RejectAboveZero()]))
    text = explain_name(generator.generate(explain=True))
    assert "Checked at validation level 2: !TEST: always" in text
    assert '  Changed "Val_lvl" to 1.' in text


def test_explain_shows_history_conflicts():
    generator = generator_for("Ana")
    generator.generate()
    text = explain_name(generator.generate(explain=True))
    assert "  Fail: !HIST: Ana was made recently, so it is made again." in text
    assert "  Ignored history conflict: Ana was made recently." in text


# --- Length limits (spec 10.5) ----------------------------------------------


def length_generator(fewest, most, index=2, templates=None, seed=1):
    """A generator for a sample style, with a length range; index 2 is Angelic."""
    analysis = analyze(load_style(SAMPLE_CHAPTERS[index]))
    options = dataclasses.replace(analysis.style.options, min_length=fewest, max_length=most)
    return NameGenerator(analysis, options, random.Random(seed), templates=templates)


@pytest.mark.parametrize(("fewest", "most"), [(5, 6), (9, 0), (0, 5), (7, 7)])
def test_names_have_a_length_in_range(fewest, most):
    generator = length_generator(fewest, most, templates=[Template("#")])
    names = [generator.generate() for _ in range(150)]
    assert all(name.ok for name in names)
    top = most or 100
    assert all(fewest <= letter_count(name.text) <= top for name in names)
    # Shapes are chosen by the seed words of the right length, so few names miss.
    misses = sum(len(events_of(name, LengthRejected)) for name in names)
    assert misses < len(names)


def test_templates_that_rarely_reach_the_range_are_seldom_used():
    generator = length_generator(10, 0)  # Angelic as written, with its 14 one-slot templates
    names = [generator.generate() for _ in range(100)]
    assert all(name.ok and letter_count(name.text) >= 10 for name in names)
    used = Counter(name.template for name in names)
    assert not set(used) & {"2", "3", "4", "5"}  # no seed word of these shapes has 10 letters


def test_a_wrong_length_draws_a_new_template():
    generator = length_generator(9, 0, templates=[Template("#"), Template("# #")])
    names = [generator.generate() for _ in range(60)]
    for name in names:
        for before, after in zip(name.events, name.events[1:]):
            if isinstance(before, LengthRejected):
                assert isinstance(after, TemplateChosen)


def test_several_words_are_checked_as_a_whole_name():
    generator = length_generator(12, 16, templates=[Template("# #")])
    names = [generator.generate() for _ in range(40)]
    assert all(name.ok and 12 <= letter_count(name.text) <= 16 for name in names)
    assert all(" " in name.text for name in names)


def test_a_range_no_seed_word_reaches():
    generator = length_generator(0, 3)
    with pytest.raises(GenerationError) as caught:
        generator.generate()
    assert str(caught.value) == (
        "No seed word has at most 3 letters (the seed words have 4 to 13), so no name can."
    )


def test_a_range_no_seed_word_starting_so_reaches():
    # The only word starting with a vowel (Ana) is short; the long one starts with B.
    generator = generator_for("Ana", "Belinora", "Bel", options=GenerationOptions(min_length=6))
    assert letter_count(generator.generate(initial="B").text) >= 6
    with pytest.raises(GenerationError) as caught:
        generator.generate(initial="A")
    assert str(caught.value) == (
        "No seed word of a shape that can start with 'A' has at least 6 letters "
        "(the seed words have 3 to 8), so no name can."
    )


def test_explain_shows_names_of_the_wrong_length():
    generator = length_generator(12, 14, templates=[Template("# #")], seed=3)
    names = [generator.generate(explain=True) for _ in range(20)]
    missed = next(name for name in names if events_of(name, LengthRejected))
    event = events_of(missed, LengthRejected)[0]
    assert (
        f"Fail: !LEN: {event.text} has {event.letters} letters, not 12 to 14 letters, "
        "so it is made again." in explain_name(missed)
    )


def test_length_options():
    assert GenerationOptions().length_range is None
    assert GenerationOptions(min_length=5).length_range == (5, 100)
    assert GenerationOptions(max_length=8).length_range == (0, 8)
    with pytest.raises(ValueError):
        GenerationOptions(min_length=9, max_length=5)
    with pytest.raises(ValueError):
        GenerationOptions(max_length=101)


# --- Weighted choice (spec 13.3) ---------------------------------------------------


def weighted(analysis, **changes):
    return dataclasses.replace(analysis.style.options, markov=Markov.WEIGHTED, **changes)


def test_strength_0_makes_the_same_names_as_the_classic_way():
    analysis = analyze(load_style(SAMPLE_CHAPTERS[2]))  # Angelic

    def names(options):
        generator = NameGenerator(analysis, options, random.Random(4))
        return [generator.generate().text for _ in range(100)]

    off = names(analysis.style.options)
    assert names(weighted(analysis, markov_strength=0.0)) == off
    assert names(weighted(analysis)) != off


def test_weighted_names_are_more_plausible():
    analysis = analyze(load_style(SAMPLE_CHAPTERS[2]))  # Angelic
    seeds = SeedIndex.for_analysis(analysis)

    def average_score(options):
        generator = NameGenerator(analysis, options, random.Random(1))
        built = [generator.generate().built for _ in range(300)]
        return sum(seeds.chain.score(plain_letters(name)) for name in built) / len(built)

    assert average_score(weighted(analysis)) > average_score(analysis.style.options)


def test_weighted_uses_the_look_back_given():
    analysis = analyze(load_style(SAMPLE_CHAPTERS[2]))
    generator = NameGenerator(analysis, weighted(analysis, markov_order=4), random.Random(1))
    assert generator.builder.strategy.chain.look_back == 4
    assert generator.generate().ok


def test_explain_shows_the_chain_only_when_it_weighs_the_choices():
    analysis = analyze(load_style(SAMPLE_CHAPTERS[2]))
    with_chain = NameGenerator(analysis, weighted(analysis), random.Random(1))
    without = NameGenerator(analysis, rng=random.Random(1))
    assert " chain " in explain_name(with_chain.generate(explain=True))
    assert " chain " not in explain_name(without.generate(explain=True))


# --- Shape-guided fill (spec 13.3) -------------------------------------------------


def filling(analysis, **changes):
    return dataclasses.replace(analysis.style.options, markov=Markov.FILL, markov_strength=1.0, **changes)


def test_fill_needs_structural():
    analysis = analyze(load_style(SAMPLE_CHAPTERS[2]))
    with pytest.raises(GenerationError, match="needs structural on"):
        NameGenerator(analysis, filling(analysis, structural=False))


def test_relaxation_in_fill_only_drops_prefixes_and_suffixes():
    options = GenerationOptions(structural=True, prefixes=True, suffixes=True, markov=Markov.FILL)
    options, event = relax(options)
    assert event == Relaxed("Prefixes") and options.fit_level == 3 and options.structural
    options, event = relax(options)
    assert event == Relaxed("Suffixes")
    assert relax(options) is None


def test_fill_names_follow_the_initial():
    analysis = analyze(load_style(SAMPLE_CHAPTERS[2]))  # Angelic
    generator = NameGenerator(analysis, filling(analysis), random.Random(2))
    names = [generator.generate(initial="Th").text for _ in range(40)]
    assert all(name.startswith("Th") for name in names)


def test_fill_names_are_more_plausible_than_the_classic_way_on_a_real_list():
    analysis = analyze(real_list())
    seeds = SeedIndex.for_analysis(analysis)

    def average_score(options):
        generator = NameGenerator(analysis, options, random.Random(1))
        built = [generator.generate().built for _ in range(200)]
        return sum(seeds.chain.score(plain_letters(name)) for name in built) / len(built)

    classic = dataclasses.replace(analysis.style.options, markov=Markov.OFF)
    assert average_score(analysis.style.options) > average_score(classic)


def test_fill_handles_multi_vowels_with_whole_elements():
    names = ("Hereman", "Ethelbald", "Orolin", "Aramis", "Ebenor", "Ulumar", "Dorogan", "Belena")
    style = Style(
        title="Multi",
        name_lists=(NameList("list", names),),
        special_letters=SpecialLetters(soft_consonants=True, multi_vowels=True),
        options=GenerationOptions(structural=True, statistical=True, markov=Markov.FILL),
    )
    analysis = analyze(style)
    generator = NameGenerator(analysis, rng=random.Random(1))
    made = [generator.generate() for _ in range(60)]
    assert all(name.ok for name in made)
    multi = [word for name in made for word in name.words if 0 in word.shape.substructure]
    assert multi  # some words have a multi vowel, which fill takes whole from the seeds


def test_explain_shows_the_letters_fill_chose():
    analysis = analyze(load_style(SAMPLE_CHAPTERS[2]))
    generator = NameGenerator(analysis, filling(analysis, prefixes=False, suffixes=False), random.Random(1))
    assert " letters: " in explain_name(generator.generate(explain=True))
