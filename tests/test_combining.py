"""Full names from a style's lists (spec 13.5)."""

import dataclasses
import random
import re

import pytest

from namebook.analysis import analyze
from namebook.combining import Combiner, NamePart
from namebook.formats.txt import parse_txt
from namebook.generator import GeneratedName, GenerationError, NameGenerator
from namebook.inference import infer
from namebook.style import Markov, Template

# Given names start with L, M, N, R or S; family names with A to H.
GIVEN = [(c + v + d + w).capitalize() for c in "LMNRS" for v in "AEI" for d in "LNR" for w in "AO"]
FAMILY = [
    first + last
    for first in ("Ash", "Bram", "Cal", "Dun", "El", "Fen", "Gar", "Hol")
    for last in ("wick", "ford", "ton", "by", "more", "dale", "wood", "ley")
]


@pytest.fixture(scope="module")
def analyses():
    # Fill for both, so that these tests do not depend on how inference chooses it.
    fill = {"markov": Markov.FILL}
    return {
        name: analyze(infer(parse_txt("\n".join(names), f"{name}.txt"), given=fill).working)
        for name, names in (("given", GIVEN), ("family", FAMILY))
    }


def combiner(analyses, *combinations, seed=1):
    rng = random.Random(seed)
    generators = {name: NameGenerator(analysis, rng=rng) for name, analysis in analyses.items()}
    return Combiner(generators, [Template(*c) if isinstance(c, tuple) else Template(c) for c in combinations], rng)


def test_a_full_name_takes_a_name_from_each_list_in_order(analyses):
    name = combiner(analyses, "{given} {family}").generate()
    given, space, family = name.parts
    assert (given.list_name, space, family.list_name) == ("given", " ", "family")
    assert name.text == f"{given.name.text} {family.name.text}"
    assert name.combination == "{given} {family}"
    assert name.text[0] in "LMNRS" and family.name.text[0] in "ABCDEFGH"


def test_one_combination_makes_the_same_names_as_its_generator_alone(analyses):
    combined = combiner(analyses, "{given}", seed=3)
    alone = NameGenerator(analyses["given"], rng=random.Random(3))
    assert [combined.generate().text for _ in range(50)] == [alone.generate().text for _ in range(50)]


def test_text_between_lists_is_kept_as_written(analyses):
    made = combiner(analyses, "{family}, {given}", "Lord {given} of {family}")
    for _ in range(20):
        assert re.fullmatch(r"\w+, \w+|Lord \w+ of \w+", made.generate().text)


def test_combinations_are_chosen_in_proportion_to_their_weights(analyses):
    made = combiner(analyses, ("{given}", 1), ("{family}", 3))
    lists = [made.generate().names[0].list_name for _ in range(400)]
    assert 250 < lists.count("family") < 350


def test_a_list_used_twice_gives_two_different_names(analyses):
    made = combiner(analyses, "{given} {given} {family}")
    for _ in range(100):
        first, second, _ = made.generate().names
        assert first.name.text != second.name.text  # the list's recent names are not repeated


def test_an_initial_applies_to_the_first_list(analyses):
    made = combiner(analyses, "{family} {given}")
    for _ in range(20):
        family, given = made.generate(initial="Ash").names
        assert family.name.text.startswith("Ash")
        assert given.name.text[0] in "LMNRS"


def test_with_an_initial_only_combinations_that_can_start_with_it_are_used(analyses):
    made = combiner(analyses, "{given} {family}", "{family} {given}", "Lord {given}")
    assert all(made.generate(initial="Ash").text.startswith("Ash") for _ in range(20))


def test_an_initial_no_list_can_start_with_is_refused(analyses):
    with pytest.raises(GenerationError, match="No seed word starts with 'Q'"):
        combiner(analyses, "{given} {family}", "{family}").generate(initial="Q")


def test_with_an_initial_a_combination_must_begin_with_a_list(analyses):
    with pytest.raises(GenerationError, match="None of the combinations begins with a list"):
        combiner(analyses, "Lord {given}").generate(initial="L")


def test_a_combination_must_name_the_lists_it_has():
    with pytest.raises(GenerationError, match='"surname", which is not one of'):
        Combiner({}, [Template("{surname}")], random.Random(1))


class _NoName:
    """A list that can make no name."""

    def generate(self, initial=None, explain=False):
        return GeneratedName(None, (), (), "#")


def test_a_name_that_cannot_be_made_ends_the_full_name(analyses):
    rng = random.Random(1)
    generators = {"broken": _NoName(), "given": NameGenerator(analyses["given"], rng=rng)}
    name = Combiner(generators, [Template("{broken} {given}")], rng).generate()
    assert not name.ok and name.text is None
    assert [part.list_name for part in name.names] == ["broken"]


def test_one_part_can_be_made_again_without_the_others(analyses):
    made = combiner(analyses, "{given} {family}")
    name = made.generate()
    given, space, _ = name.parts
    family = NamePart("family", made.generators["family"].generate())
    again = dataclasses.replace(name, parts=(given, space, family))
    assert again.text == f"{given.name.text} {family.name.text}"
