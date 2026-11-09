"""Building words: shapes, positions, fitting, prefixes and suffixes."""

import math
import random
from collections import Counter

import pytest

from conftest import SAMPLE_CHAPTERS
from namebook.analysis import ElementPair, analyze
from namebook.building import (
    ELEMENT,
    LETTERS,
    NO_OPTION,
    NO_PREFIX,
    NO_SUFFIX,
    PREFIX,
    SUFFIX,
    Candidate,
    ClassicStrategy,
    LetterFiller,
    Placement,
    Position,
    PositionKind,
    Shape,
    WeightedStrategy,
    WordBuilder,
    fits_neighbors,
    position_count,
    word_positions,
)
from namebook.formats import load_style
from namebook.markov import END, SPACE, internal_chain
from namebook.style import GenerationOptions, NameList, SpecialLetters, Style
from namebook.words import structure_number

ALL_OFF = GenerationOptions(fit_level=0)


def analysis_of(*names, soft=False):
    return analyze(
        Style(
            title="Test",
            name_lists=(NameList("names", names),),
            special_letters=SpecialLetters(soft_consonants=soft),
        )
    )


def builder_for(analysis, seed=1):
    return WordBuilder(analysis, random.Random(seed))


# --- Positions ---------------------------------------------------------------


def test_word_positions_with_a_split():
    # Seven elements, C V C V C V C; middle positions before index 3 count from the start.
    assert word_positions(7, 3) == (
        Position(PositionKind.FIRST),
        Position(PositionKind.FROM_START, 1),
        Position(PositionKind.FROM_START, 2),
        Position(PositionKind.FROM_END, 2),
        Position(PositionKind.FROM_END, 2),
        Position(PositionKind.FROM_END, 1),
        Position(PositionKind.LAST),
    )


def test_word_positions_with_shuffle():
    assert word_positions(4, None) == (
        Position(PositionKind.FIRST),
        Position(PositionKind.MIDDLE),
        Position(PositionKind.MIDDLE),
        Position(PositionKind.LAST),
    )


def test_two_element_words_have_only_first_and_last():
    assert word_positions(2, 1) == (Position(PositionKind.FIRST), Position(PositionKind.LAST))


def test_a_one_element_word_has_one_position_first_and_last_at_once():
    assert word_positions(1, None) == (Position(PositionKind.ALONE),)


def test_an_element_alone_must_have_begun_a_seed_word_and_ended_one():
    # AI began Ai and ended Ai and Lai; AO began Aola and ended Bao; A only ended Aola.
    analysis = analysis_of("Ai", "Lai", "Aola", "Bao")
    alone = Position(PositionKind.ALONE)
    vowels = analysis.vowel_elements
    assert position_count(vowels[("A", "I")], alone) == 2  # first once, last twice
    assert position_count(vowels[("A", "O")], alone) == 1
    assert position_count(vowels[("A",)], alone) == 0
    builder = builder_for(analysis)
    options = GenerationOptions(structural=True, statistical=True)
    built = Counter(builder.build(Shape(-1, (2,)), options).elements for _ in range(300))
    assert set(built) == {(("A", "I"),), (("A", "O"),)}
    assert built[(("A", "I"),)] > built[(("A", "O"),)]


def test_fill_spells_a_word_of_one_element_letter_by_letter():
    analysis = analysis_of("Ai", "Lai", "Aola", "Bao", "Ou", "Lou")
    chain = internal_chain(analysis.splitter, analysis.style.names)
    filler = LetterFiller(analysis, random.Random(1), chain, 1.0)
    options = GenerationOptions(structural=True)
    attempts = [filler.build(Shape(-1, (2,)), options) for _ in range(200)]
    assert all(attempt.ok and len(attempt.choices) == 1 for attempt in attempts)
    words = {"".join(attempt.elements[0]) for attempt in attempts}
    assert all(len(word) == 2 and set(word) <= set("AEIOU") for word in words)
    assert len(words) > 2  # new ones as well as AI, AO and OU


def test_position_count():
    stats = analysis_of("Banana").vowel_elements[("A",)]
    assert position_count(stats, Position(PositionKind.LAST)) == 1
    assert position_count(stats, Position(PositionKind.FROM_START, 1)) == 1
    assert position_count(stats, Position(PositionKind.FROM_END, 3)) == 1
    assert position_count(stats, Position(PositionKind.FROM_END, 1)) == 0
    assert position_count(stats, Position(PositionKind.MIDDLE)) == 2


# --- Fitting -----------------------------------------------------------------


def test_fit_level_one_checks_the_previous_element():
    # Seed: M · I · KH · AI · L. Placed so far: M · I · KH.
    analysis = analysis_of("Mikhail", soft=True)
    placed = (("M",), ("I",), ("KH",))
    assert fits_neighbors(analysis, 3, placed, 3, ("A", "I"), vowel=True)
    assert not fits_neighbors(analysis, 1, placed, 3, ("E",), vowel=True)  # KH→E never seen
    assert fits_neighbors(analysis, 0, placed, 3, ("E",), vowel=True)  # level 0: no fitting


def test_fit_level_three_checks_the_vowel_two_back():
    # Akhi gives KH→I, so I passes level 1 after KH. But I…I across a
    # consonant never occurred, so level 3 rejects it.
    analysis = analysis_of("Mikhail", "Akhi", soft=True)
    placed = (("M",), ("I",), ("KH",))
    assert fits_neighbors(analysis, 2, placed, 3, ("I",), vowel=True)
    assert not fits_neighbors(analysis, 3, placed, 3, ("I",), vowel=True)


def test_fit_level_two_checks_consonants_by_first_and_last_letter():
    # Seeds give A→R and T…R (Tara), and A→L (Ala, Alra). Placed so far: T · A.
    analysis = analysis_of("Tara", "Ala", "Alra")
    placed = (("T",), ("A",))
    assert fits_neighbors(analysis, 2, placed, 2, ("R",), vowel=False)
    assert fits_neighbors(analysis, 1, placed, 2, ("L",), vowel=False)
    assert not fits_neighbors(analysis, 2, placed, 2, ("L",), vowel=False)  # T…L never seen
    # RL was never seen anywhere, but it fits because its first letter R does.
    assert fits_neighbors(analysis, 2, placed, 2, ("R", "L"), vowel=False)


# --- Building ----------------------------------------------------------------


def test_building_a_seed_shape_succeeds():
    analysis = analysis_of("Ana", "Ebo")
    attempt = builder_for(analysis).build(Shape(3, (1, 1, 1)), GenerationOptions())
    assert attempt.ok
    assert attempt.failure is None
    assert len(attempt.elements) == 3


def test_no_candidates_fails_with_noopt():
    # The seeds are VCV words, so no vowel element ever occurred in the middle
    # of a word: a VCVC word can never be built. (Where it fails depends on
    # the random split point.)
    analysis = analysis_of("Ana", "Ebo")
    attempt = builder_for(analysis).build(Shape(5), ALL_OFF)
    assert not attempt.ok
    assert attempt.failure == NO_OPTION
    assert attempt.choices[-1].candidate_count == 0
    assert attempt.choices[-1].chosen is None


def test_no_prefix_fits_the_substructure():
    analysis = analysis_of("Banana", "Tobelo")
    options = GenerationOptions(structural=True, fit_level=0, prefixes=True)
    attempt = builder_for(analysis).build(Shape(8, (2, 1, 1, 1, 1, 1)), options)
    assert attempt.failure == NO_PREFIX
    assert attempt.choices[0].kind == PREFIX


def test_no_suffix_fits():
    analysis = analysis_of("Banana", "Tobelo")
    options = GenerationOptions(structural=True, fit_level=0, suffixes=True)
    attempt = builder_for(analysis).build(Shape(8, (1, 1, 1, 1, 1, 3)), options)
    assert attempt.failure == NO_SUFFIX
    assert attempt.choices[-1].kind == SUFFIX


def test_prefix_and_suffix_need_long_enough_words():
    analysis = analysis_of("Banana", "Tobelo", "Ana")
    options = GenerationOptions(prefixes=True, suffixes=True, fit_level=0)
    builder = builder_for(analysis)
    assert [c.kind for c in builder.build(Shape(8), options).choices] == [
        PREFIX, ELEMENT, ELEMENT, SUFFIX,
    ]  # fmt: skip
    # Structure 3 (three elements) is too short for either.
    assert [c.kind for c in builder.build(Shape(3), options).choices] == [ELEMENT] * 3


def test_explain_keeps_candidates_only_when_asked():
    analysis = analysis_of("Ana", "Ebo")
    builder = builder_for(analysis)
    quiet = builder.build(Shape(3), GenerationOptions())
    assert all(choice.candidates == () for choice in quiet.choices)
    explained = builder.build(Shape(3), GenerationOptions(), explain=True)
    first = explained.choices[0]
    assert {candidate.elements for candidate, _ in first.candidates} == {(("A",),), (("E",),)}


def test_without_statistical_every_candidate_weighs_the_same():
    analysis = analysis_of("Ana", "Ana", "Ebo")
    placement = Placement(
        analysis, GenerationOptions(), Shape(3), ELEMENT, 0, word_positions(3, 1), (), None
    )
    strategy = ClassicStrategy()
    assert strategy.weight(placement, Candidate((("A",),))) == 1.0
    statistical = Placement(
        analysis, GenerationOptions(statistical=True), Shape(3), ELEMENT, 0,
        word_positions(3, 1), (), None,
    )  # fmt: skip
    assert strategy.weight(statistical, Candidate((("A",),))) == 2
    assert strategy.weight(statistical, Candidate((("E",),))) == 1


def test_same_seed_same_word():
    analysis = analyze(load_style(SAMPLE_CHAPTERS[0]))
    options = analysis.style.options
    first = builder_for(analysis, seed=7).build(Shape(6), options)
    again = builder_for(analysis, seed=7).build(Shape(6), options)
    assert first == again


# --- Properties that must hold for every word built from the sample chapters --


def built_words(path, options=None, count=300, seed=3):
    """Successful builds from a sample style, in shapes drawn like the generator does."""
    from namebook.generator import NameGenerator

    analysis = analyze(load_style(path))
    generator = NameGenerator(analysis, options, random.Random(seed))
    words = []
    for _ in range(count):
        name = generator.generate()
        for word in name.words:
            words.append((word.shape, word.elements, word.attempt))
    return analysis, words


@pytest.mark.parametrize("path", SAMPLE_CHAPTERS, ids=lambda path: path.name)
def test_every_combination_occurred_in_the_seeds(path):
    # Words are checked at the fit level in force when they were built, which
    # automatic relaxation may have lowered.
    analysis, words = built_words(path)
    fitting = analysis.fitting
    assert words
    assert sum(attempt.options.fit_level == 3 for _, _, attempt in words) > len(words) * 0.8
    for shape, elements, attempt in words:
        level = attempt.options.fit_level
        for index, element in enumerate(elements):
            vowel = shape.is_vowel(index)
            if index >= 1 and level >= 1:
                previous = elements[index - 1]
                if vowel:
                    assert (previous[-1], element) in fitting.consonant_to_vowel
                else:
                    assert (previous, element[0]) in fitting.vowel_to_consonant
            if index >= 2:
                before = elements[index - 2]
                if vowel and level >= 3:
                    assert (before, element) in fitting.vowel_to_vowel
                elif not vowel and level >= 2:
                    assert (before[-1], element[0]) in fitting.consonant_to_consonant


@pytest.mark.parametrize("path", SAMPLE_CHAPTERS, ids=lambda path: path.name)
def test_words_follow_seed_shapes_positions_prefixes_and_suffixes(path):
    analysis, words = built_words(path)
    for shape, elements, attempt in words:
        options = attempt.options
        assert len(elements) == shape.element_total
        # The substructure is one the seeds had for this structure, and is
        # followed unless relaxation dropped "structural".
        assert shape.substructure in analysis.substructures[shape.structure]
        lengths = tuple(
            analysis.elements(shape.is_vowel(i))[element].length for i, element in enumerate(elements)
        )
        if options.structural:
            assert lengths == shape.substructure
        # First and last elements occurred first and last in the seeds.
        assert analysis.elements(shape.is_vowel(0))[elements[0]].first > 0
        assert analysis.elements(shape.is_vowel(len(elements) - 1))[elements[-1]].last > 0
        if options.prefixes and shape.structure >= 6:
            assert ElementPair(shape.is_vowel(0), elements[0], elements[1]) in analysis.prefixes
        if options.suffixes and shape.structure >= 4:
            pair = ElementPair(shape.is_vowel(len(elements) - 2), elements[-2], elements[-1])
            assert pair in analysis.suffixes


def test_middle_elements_occurred_at_their_position():
    analysis, words = built_words(
        SAMPLE_CHAPTERS[1], GenerationOptions(fit_level=0, statistical=True)
    )
    for shape, elements, attempt in words:
        positions = word_positions(len(elements), attempt.split)
        for index in range(1, len(elements) - 1):
            stats = analysis.elements(shape.is_vowel(index))[elements[index]]
            assert position_count(stats, positions[index]) > 0


def test_structures_follow_seed_frequency():
    analysis = analysis_of(*(["Ana"] * 9 + ["Banan"]))
    from namebook.generator import NameGenerator

    generator = NameGenerator(analysis, GenerationOptions(fit_level=0), random.Random(5))
    counts = Counter(generator.choose_shape(None, None).structure for _ in range(1000))
    assert set(counts) == {3, 6}
    assert counts[3] > 800  # about 9 in 10


def test_structure_numbers_used_here():
    assert structure_number(5, False) == 6
    assert structure_number(4, False) == 4


# --- Weighted choice (spec 13.3) ---------------------------------------------------


def weighted_setup(*names, look_back=1):
    analysis = analysis_of(*names)
    chain = internal_chain(analysis.splitter, names, look_back)
    return analysis, chain, WeightedStrategy(chain, 1.0, analysis.splitter.letter_classes)


def placement_at(analysis, index, placed, then=END):
    shape = Shape(4)  # C V C V
    return Placement(analysis, ALL_OFF, shape, ELEMENT, index, word_positions(4, 2), placed, None,
                     then=then)  # fmt: skip


def test_the_chain_fit_is_the_chance_of_the_letters_after_those_placed():
    analysis, chain, strategy = weighted_setup("Nana", "Nono", "Nina", "Mona")
    fit = strategy.chain_fit(placement_at(analysis, 1, (("N",),)), Candidate((("A",),)))
    assert math.isclose(fit, chain.chance(["N"], "A"))


def test_the_last_element_is_also_fitted_to_the_end_of_the_word():
    analysis, chain, strategy = weighted_setup("Nana", "Nono", "Nina", "Mona")
    placed = (("N",), ("A",), ("N",))
    fit = strategy.chain_fit(placement_at(analysis, 3, placed), Candidate((("A",),)))
    expected = math.sqrt(chain.chance(["N"], "A") * chain.chance(["A"], END))  # per letter
    assert math.isclose(fit, expected)
    # When another word follows, the chain judges a space instead, which these names never have.
    other = WeightedStrategy(chain, 1.0, analysis.splitter.letter_classes)
    followed = other.chain_fit(placement_at(analysis, 3, placed, then=SPACE), Candidate((("A",),)))
    assert followed < fit


def test_weighted_weights_are_classic_weights_times_the_fit_to_the_strength():
    analysis, chain, _ = weighted_setup("Nana", "Nono", "Nina", "Mona")
    options = GenerationOptions(fit_level=0, statistical=True)
    placement = Placement(analysis, options, Shape(4), ELEMENT, 1, word_positions(4, 2), (("N",),), None)
    candidate = Candidate((("O",),))
    classic = ClassicStrategy().weight(placement, candidate)
    for strength in (0.0, 0.5, 1.0):
        strategy = WeightedStrategy(chain, strength, analysis.splitter.letter_classes)
        fit = strategy.chain_fit(placement, candidate)
        assert math.isclose(strategy.weight(placement, candidate), classic * fit**strength)
    assert ClassicStrategy().chain_fit(placement, candidate) is None


# --- Shape-guided fill (spec 13.3) -------------------------------------------------


@pytest.fixture(scope="module")
def angelic_filler():
    analysis = analyze(load_style(SAMPLE_CHAPTERS[2]))  # Angelic
    chain = internal_chain(analysis.splitter, analysis.style.names)
    return analysis, LetterFiller(analysis, random.Random(1), chain, 1.0)


def filled_words(analysis, filler, count=200):
    options = analysis.style.options
    shapes = [Shape(structure, substructure) for structure, subs in analysis.substructures.items()
              for substructure in subs]  # fmt: skip
    return [filler.build(shapes[n % len(shapes)], options) for n in range(count)]


def test_fill_puts_each_class_in_its_positions_with_the_substructure_lengths(angelic_filler):
    analysis, filler = angelic_filler
    classes = analysis.splitter.letter_classes
    filled = 0
    for attempt in filled_words(analysis, filler):
        for choice in attempt.choices:
            if choice.kind == LETTERS and choice.chosen:
                (element,) = choice.chosen.elements
                assert len(element) == attempt.shape.substructure[choice.index]
                assert all(classes.is_vowel(letter[0]) == choice.vowel for letter in element)
                filled += 1
    assert filled > 100


def test_fill_can_make_letter_groups_the_seeds_never_had(angelic_filler):
    analysis, filler = angelic_filler
    new = [
        choice.chosen.elements[0]
        for attempt in filled_words(analysis, filler)
        for choice in attempt.choices
        if choice.kind == LETTERS and choice.chosen
        and choice.chosen.elements[0] not in analysis.elements(choice.vowel)
    ]  # fmt: skip
    assert new


def test_fill_keeps_prefixes_and_suffixes_from_the_seeds(angelic_filler):
    analysis, filler = angelic_filler
    pairs = [choice for attempt in filled_words(analysis, filler) for choice in attempt.choices
             if choice.kind in (PREFIX, SUFFIX) and choice.chosen]  # fmt: skip
    assert {choice.kind for choice in pairs} == {PREFIX, SUFFIX}
    assert all(choice.chosen.pair in analysis.prefixes or choice.chosen.pair in analysis.suffixes
               for choice in pairs)  # fmt: skip


def test_each_letter_is_weighed_by_what_the_shape_needs_next(angelic_filler):
    analysis, filler = angelic_filler
    chain = filler.chain
    run = ("A",)
    then_end = (chain.chance(run, "L") * chain.chance(("A", "L"), END)) ** filler.strength
    assert math.isclose(filler._weight(run, "L", END), then_end)
    vowels = filler._letters[True]
    after = chain.chances_after(("A", "L"))
    then_vowel = chain.chance(run, "L") * sum(after[vowel] for vowel in vowels)
    assert math.isclose(filler._weight(run, "L", True), then_vowel)


def test_fill_at_strength_0_gives_every_allowed_letter_the_same_chance():
    analysis = analyze(load_style(SAMPLE_CHAPTERS[2]))
    chain = internal_chain(analysis.splitter, analysis.style.names)
    filler = LetterFiller(analysis, random.Random(1), chain, 0.0)
    options = GenerationOptions(structural=True)
    attempt = filler.build(Shape(1, (1, 1)), options)
    first = attempt.choices[0]
    assert first.kind == LETTERS
    assert math.isclose(first.letter_chances[0], 1 / first.candidate_count)


def test_fill_needs_a_substructure(angelic_filler):
    analysis, filler = angelic_filler
    with pytest.raises(ValueError, match="substructure"):
        filler.build(Shape(1), GenerationOptions())
