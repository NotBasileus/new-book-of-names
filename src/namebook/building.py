"""Building one word from a style's elements (spec 7.3).

A word is built position by position, from first to last, in a chosen shape
(a structure, and a substructure when ``structural`` is on). At each position
choosing happens in two separate steps, both done by a candidate strategy:

1. **filter**: keep only the candidates that are allowed here;
2. **weigh**: give each allowed candidate a weight, then pick one at random
   in proportion to the weights.

``ClassicStrategy`` is the classic rule set. ``WeightedStrategy`` keeps its
filter and lets a Markov chain weigh the allowed candidates (spec 13.3).
Other strategies can be added as new classes without changing
``WordBuilder``.

``LetterFiller`` is a second way of filling a shape: letter by letter from
the Markov chain (spec 13.3, ``fill``).
"""

from __future__ import annotations

import dataclasses
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import NamedTuple, Protocol

from namebook.analysis import ElementPair, ElementStats, StyleAnalysis
from namebook.letters import LetterClasses, capitals
from namebook.markov import END, JOINTS, SPACE, Chain, symbol_is_vowel, word_letters
from namebook.style import GenerationOptions, HyphenMode
from namebook.words import Element, element_count, format_element, structure_starts_with_vowel

# What is being chosen at a point of the build.
ELEMENT = "element"
PREFIX = "prefix"
SUFFIX = "suffix"
LETTERS = "letters"  # a position filled letter by letter (fill)

# Failure codes, as the explain mode shows them (spec 9).
NO_OPTION = "!NOOPT"
NO_PREFIX = "!PRE/NOOPT"
NO_SUFFIX = "!SUF/NOOPT"
FAILURE_CODES = {ELEMENT: NO_OPTION, LETTERS: NO_OPTION, PREFIX: NO_PREFIX, SUFFIX: NO_SUFFIX}

# Prefixes are used from structure 6 (five elements), suffixes from structure
# 4 (four elements).
PREFIX_MIN_STRUCTURE = 6
SUFFIX_MIN_STRUCTURE = 4


class PositionKind(Enum):
    FIRST = "first"
    LAST = "last"
    FROM_START = "from start"
    FROM_END = "from end"
    MIDDLE = "middle"  # any middle position, used by shuffle
    ALONE = "alone"  # the one element of a one-element word: first and last at once


class Position(NamedTuple):
    """Which of an element's position counters applies at one place in a word."""

    kind: PositionKind
    rank: int = 0  # for FROM_START and FROM_END: the rank among elements of its class


def position_count(stats: ElementStats, position: Position) -> int:
    """How often an element occurred in the seeds at this position.

    An element alone must have begun a seed word and ended one (spec 13.10).
    Its weight is as if a first and a last element were chosen apart and
    came out the same: the two counts multiplied.
    """
    if position.kind is PositionKind.FIRST:
        return stats.first
    if position.kind is PositionKind.LAST:
        return stats.last
    if position.kind is PositionKind.ALONE:
        return stats.first * stats.last
    if position.kind is PositionKind.FROM_START:
        return stats.from_start[position.rank]
    if position.kind is PositionKind.FROM_END:
        return stats.from_end[position.rank]
    return stats.middle


def word_positions(element_total: int, split: int | None) -> tuple[Position, ...]:
    """The position of every element in a word of ``element_total`` elements.

    Middle elements before index ``split`` are counted from the start of the
    word, the others from the end (spec 5.2). With ``split`` None (shuffle),
    all middle elements share one pooled position.
    """
    if element_total == 1:
        return (Position(PositionKind.ALONE),)
    positions = []
    for index in range(element_total):
        if index == 0:
            positions.append(Position(PositionKind.FIRST))
        elif index == element_total - 1:
            positions.append(Position(PositionKind.LAST))
        elif split is None:
            positions.append(Position(PositionKind.MIDDLE))
        elif index < split:
            positions.append(Position(PositionKind.FROM_START, index // 2 + 1))
        else:
            positions.append(Position(PositionKind.FROM_END, (element_total - 1 - index) // 2 + 1))
    return tuple(positions)


def fits_neighbors(
    analysis: StyleAnalysis,
    fit_level: int,
    placed: Sequence[Element],
    index: int,
    element: Element,
    vowel: bool,
) -> bool:
    """Whether ``element`` at ``index`` fits the elements already placed before it (spec 5.3).

    Level 1 checks the element just before; level 2 (consonants) and level 3
    (vowels) also check the element of the same class two positions back.
    Each level includes the levels below it.
    """
    fitting = analysis.fitting
    if fit_level >= 1 and index >= 1:
        previous = placed[index - 1]
        if vowel:
            if (previous[-1], element) not in fitting.consonant_to_vowel:
                return False
        elif (previous, element[0]) not in fitting.vowel_to_consonant:
            return False
    if index >= 2:
        before = placed[index - 2]
        if vowel and fit_level >= 3:
            if (before, element) not in fitting.vowel_to_vowel:
                return False
        elif not vowel and fit_level >= 2:
            if (before[-1], element[0]) not in fitting.consonant_to_consonant:
                return False
    return True


@dataclass(frozen=True)
class Shape:
    """The shape a word is built in: its structure and, optionally, its substructure."""

    structure: int
    substructure: tuple[int, ...] | None = None

    @property
    def element_total(self) -> int:
        return element_count(self.structure)

    def is_vowel(self, index: int) -> bool:
        return (index % 2 == 0) == structure_starts_with_vowel(self.structure)


@dataclass(frozen=True)
class Candidate:
    """Something that can fill the next position: one element, or a prefix or suffix pair."""

    elements: tuple[Element, ...]
    pair: ElementPair | None = None


@dataclass(frozen=True)
class Placement:
    """Everything a strategy may look at when judging candidates for the next position."""

    analysis: StyleAnalysis
    options: GenerationOptions
    shape: Shape
    kind: str  # ELEMENT, PREFIX or SUFFIX
    index: int  # the first position the candidate fills, counting from 0
    positions: tuple[Position, ...]
    placed: tuple[Element, ...]
    # The starting letters the word must still spell from this position on,
    # in capitals: all of them at the first position, and None once spelled.
    initial: str | None
    # Where the word sits in its name, for the Markov chain: the letters of
    # the words before it (each followed by a space), and what follows it
    # (END, or a space when another word does).
    before: tuple[str, ...] = ()
    then: str = END


class CandidateStrategy(Protocol):
    def allows(self, placement: Placement, candidate: Candidate) -> bool: ...

    def weight(self, placement: Placement, candidate: Candidate) -> float: ...

    def chain_fit(self, placement: Placement, candidate: Candidate) -> float | None:
        """The Markov chain's part in the weight, for explain mode; None without a chain."""
        ...


class ClassicStrategy:
    """The classic rules for choosing candidates (spec 7.3)."""

    def allows(self, placement: Placement, candidate: Candidate) -> bool:
        if candidate.pair and candidate.pair.first_is_vowel != placement.shape.is_vowel(placement.index):
            return False
        options = placement.options
        substructure = placement.shape.substructure if options.structural else None
        placed = placement.placed
        initial = placement.initial
        for offset, element in enumerate(candidate.elements):
            index = placement.index + offset
            vowel = placement.shape.is_vowel(index)
            stats = placement.analysis.elements(vowel).get(element)
            if stats is None:
                return False
            # Prefixes and suffixes are whole pairs from the seeds; only single
            # elements must have occurred at this position.
            if placement.kind == ELEMENT and position_count(stats, placement.positions[index]) == 0:
                return False
            if initial:
                spelled = format_element(element)
                if not _still_begins(placement.analysis, placement.shape, index, spelled, initial):
                    return False
                initial = rest_of_initial(initial, spelled)
            if substructure is not None and stats.length != substructure[index]:
                return False
            if not fits_neighbors(placement.analysis, options.fit_level, placed, index, element, vowel):
                return False
            placed = placed + (element,)
        return True

    def weight(self, placement: Placement, candidate: Candidate) -> float:
        if not placement.options.statistical:
            return 1.0
        if placement.kind == PREFIX:
            return placement.analysis.prefixes[candidate.pair]
        if placement.kind == SUFFIX:
            return placement.analysis.suffixes[candidate.pair]
        vowel = placement.shape.is_vowel(placement.index)
        stats = placement.analysis.elements(vowel)[candidate.elements[0]]
        return position_count(stats, placement.positions[placement.index])

    def chain_fit(self, placement: Placement, candidate: Candidate) -> float | None:
        return None


class WeightedStrategy(ClassicStrategy):
    """The classic filter, with a Markov chain weighing the allowed candidates (spec 13.3).

    Each candidate's usual weight is multiplied by its fit raised to the
    ``strength``. The fit is the chain's chance of the candidate's letters
    after the letters already placed, and of the word ending after it when
    it is last, taken per letter (D75), so a longer element is not marked
    down for its length. Strength 0 is the classic way; 1 gives the fit its full
    weight.
    """

    def __init__(self, chain: Chain, strength: float, classes: LetterClasses):
        self.chain = chain
        self.strength = strength
        self.classes = classes
        self._chances: dict[tuple[tuple[str, ...], str], float] = {}
        self._placement: Placement | None = None
        self._before: tuple[str, ...] = ()

    def weight(self, placement: Placement, candidate: Candidate) -> float:
        weight = super().weight(placement, candidate)
        if not weight or not self.strength:
            return weight
        return weight * self.chain_fit(placement, candidate) ** self.strength

    def chain_fit(self, placement: Placement, candidate: Candidate) -> float:
        before = self._letters_before(placement)
        first_is_vowel = placement.shape.is_vowel(placement.index)
        letters = word_letters(candidate.elements, first_is_vowel, self.classes)
        if placement.index + len(candidate.elements) == placement.shape.element_total:
            letters += (placement.then,)
        log_chance = 0.0
        for letter in letters:
            log_chance += math.log(self._chance(before, letter))
            before = before[1:] + (letter,)
        return math.exp(log_chance / len(letters))

    def _letters_before(self, placement: Placement) -> tuple[str, ...]:
        """The letters before the next position, as many as the chain looks back.

        Missing letters at the start of a name are "", which ``_chance``
        leaves out, so the chain sees the start of the name there.
        """
        if placement is not self._placement:
            placed = word_letters(placement.placed, placement.shape.is_vowel(0), self.classes)
            run = ("",) * self.chain.look_back + placement.before + placed
            self._placement, self._before = placement, run[-self.chain.look_back :]
        return self._before

    def _chance(self, before: tuple[str, ...], letter: str) -> float:
        key = (before, letter)
        if key not in self._chances:
            self._chances[key] = self.chain.chance([known for known in before if known], letter)
        return self._chances[key]


@dataclass(frozen=True)
class PositionChoice:
    """The record of one choice during a build, for explain mode and measurements."""

    kind: str
    index: int
    vowel: bool
    candidate_count: int
    # Allowed candidates with their weights; only kept when explaining.
    candidates: tuple[tuple[Candidate, float], ...]
    chosen: Candidate | None
    # The Markov chain's fit of each of ``candidates``, when a chain weighs them.
    chain_fits: tuple[float, ...] = ()
    # For a position filled letter by letter: the chance each chosen letter had.
    letter_chances: tuple[float, ...] = ()


@dataclass(frozen=True)
class BuildAttempt:
    """One try at building a word. ``failure`` is a failure code if it did not succeed."""

    shape: Shape
    options: GenerationOptions
    split: int | None
    choices: tuple[PositionChoice, ...]
    failure: str | None = None

    @property
    def ok(self) -> bool:
        return self.failure is None

    @property
    def elements(self) -> tuple[Element, ...]:
        return tuple(
            element for choice in self.choices if choice.chosen for element in choice.chosen.elements
        )


class WordBuilder:
    """Builds words for one analyzed style (spec 7.3)."""

    def __init__(
        self,
        analysis: StyleAnalysis,
        rng: random.Random,
        strategy: CandidateStrategy | None = None,
    ):
        self.analysis = analysis
        self.rng = rng
        self.strategy = strategy or ClassicStrategy()
        self._element_pools = {
            vowel: tuple(Candidate((element,)) for element in analysis.elements(vowel))
            for vowel in (True, False)
        }
        self._pair_pools = {
            PREFIX: tuple(Candidate((pair.first, pair.second), pair) for pair in analysis.prefixes),
            SUFFIX: tuple(Candidate((pair.first, pair.second), pair) for pair in analysis.suffixes),
        }

    def build(
        self,
        shape: Shape,
        options: GenerationOptions,
        initial: str | None = None,
        explain: bool = False,
        before: tuple[str, ...] = (),
        then: str = END,
    ) -> BuildAttempt:
        """Try once to build a word in ``shape``. ``initial`` is in uppercase, e.g. ``"TH"``.

        ``before`` and ``then`` say where the word sits in its name, for a
        strategy that uses the Markov chain (see ``Placement``).
        """
        total = shape.element_total
        split = None
        if not options.shuffle and total > 2:
            split = self.rng.randint(1, total - 1)
        positions = word_positions(total, split)

        choices: list[PositionChoice] = []
        placed: tuple[Element, ...] = ()
        index = 0
        while index < total:
            kind = self._kind_at(index, shape, options)
            placement = Placement(
                self.analysis, options, shape, kind, index, positions, placed, initial, before, then
            )
            choice = self._choose(placement, explain)
            choices.append(choice)
            if choice.chosen is None:
                return BuildAttempt(shape, options, split, tuple(choices), FAILURE_CODES[choice.kind])
            placed += choice.chosen.elements
            index += len(choice.chosen.elements)
            initial = rest_of_initial(initial, "".join(map(format_element, choice.chosen.elements)))
        return BuildAttempt(shape, options, split, tuple(choices))

    def can_hold(self, shape: Shape, initial: str) -> bool:
        """Whether a word in ``shape`` can begin with ``initial`` (in capitals).

        The elements that hold the letters come from the seeds, the first one
        an element that began a seed word, and the shape must have enough
        elements for them (spec 13.11). Only the structure counts, as in
        the classic way: a substructure whose lengths do not suit them fails to build
        until automatic relaxation drops ``structural`` (D25).
        """
        ways = self.analysis.start_lengths(initial, shape.is_vowel(0), first=True)
        return any(len(lengths) <= shape.element_total for lengths in ways)

    def _choose(self, placement: Placement, explain: bool) -> PositionChoice:
        """Filter the candidates for the next position, weigh them, and pick one."""
        kind, shape, index = placement.kind, placement.shape, placement.index
        pool = self._pair_pools[kind] if kind != ELEMENT else self._element_pools[shape.is_vowel(index)]
        allowed = [candidate for candidate in pool if self.strategy.allows(placement, candidate)]
        weights = [self.strategy.weight(placement, candidate) for candidate in allowed]
        chosen = None
        if allowed and sum(weights) > 0:
            chosen = self.rng.choices(allowed, weights=weights)[0]
        fits: tuple[float, ...] = ()
        if explain and allowed and self.strategy.chain_fit(placement, allowed[0]) is not None:
            fits = tuple(self.strategy.chain_fit(placement, candidate) for candidate in allowed)
        return PositionChoice(
            kind=kind,
            index=index,
            vowel=shape.is_vowel(index),
            candidate_count=len(allowed),
            candidates=tuple(zip(allowed, weights)) if explain else (),
            chosen=chosen,
            chain_fits=fits,
        )

    @staticmethod
    def _kind_at(index: int, shape: Shape, options: GenerationOptions) -> str:
        if index == 0 and options.prefixes and shape.structure >= PREFIX_MIN_STRUCTURE:
            return PREFIX
        if (
            index == shape.element_total - 2
            and options.suffixes
            and shape.structure >= SUFFIX_MIN_STRUCTURE
        ):
            return SUFFIX
        return ELEMENT


class LetterFiller(WordBuilder):
    """Fills a word's shape letter by letter from a Markov chain (spec 13.3, ``fill``).

    The shape is chosen as for the classic way. Each position is filled with as
    many letters as the substructure gives, vowels in vowel positions and
    consonants in consonant positions; a special letter such as TH counts as
    one, as it does in the substructure. Each letter is weighed by the
    chain's chance of it after the letters before it, times the chance that
    what the shape needs next follows it: a letter of the same class inside
    a position, of the other class at its end, and the end of the word (or a
    space, when another word follows) after the last letter (D78). Both
    chances are raised to the strength: 1 follows the chain as learned, and
    lower values make the allowed letters more equally likely.

    Prefixes and suffixes are still pairs of elements from the seeds, chosen
    as ``WeightedStrategy`` chooses them, and so is a position of length 0,
    which holds a multi vowel (spec 4.2). Fit levels do not apply: the chain
    does their job.
    """

    def __init__(self, analysis: StyleAnalysis, rng: random.Random, chain: Chain, strength: float):
        classes = analysis.splitter.letter_classes
        super().__init__(analysis, rng, WeightedStrategy(chain, strength, classes))
        self.chain = chain
        self.strength = strength
        self.classes = classes
        # The joints between words are not letters, nor is the end. Unless it
        # is a joint, the hyphen is a letter: a consonant.
        joints = JOINTS if analysis.splitter.hyphen is HyphenMode.JOINT else {SPACE}
        known = [letter for letter in chain.followers.get((), ()) if letter != END and letter not in joints]
        # A vowel letter of more than one character is a multi vowel, which
        # only whole elements hold.
        self._letters = {
            vowel: tuple(
                letter for letter in known
                if symbol_is_vowel(letter, classes) == vowel and (len(letter) == 1 or not vowel)
            )
            for vowel in (True, False)
        }  # fmt: skip
        self._spellings = {
            vowel: frozenset(map(capitals, letters)) for vowel, letters in self._letters.items()
        }
        self._after: dict[tuple[str, ...], dict[str, float]] = {}
        self._class_chances: dict[tuple[tuple[str, ...], bool], float] = {}
        self._held: dict[tuple[Shape, int, int, str], bool] = {}

    def build(
        self,
        shape: Shape,
        options: GenerationOptions,
        initial: str | None = None,
        explain: bool = False,
        before: tuple[str, ...] = (),
        then: str = END,
    ) -> BuildAttempt:
        if shape.substructure is None:
            raise ValueError("fill takes each position's length from the substructure")
        no_fitting = dataclasses.replace(options, fit_level=0)
        return super().build(shape, no_fitting, initial, explain, before, then)

    def can_hold(self, shape: Shape, initial: str) -> bool:
        """Whether a word in ``shape`` can begin with ``initial``: the positions
        that hold the letters must be as long as elements of the seeds that
        can hold them, as in the classic way, the first one an element that began
        a seed word (spec 13.11). Fill cannot drop ``structural``, so a shape
        that does not suit the letters is never chosen.
        """
        ways = self.analysis.start_lengths(initial, shape.is_vowel(0), first=True)
        return any(
            shape.substructure[: len(lengths)] == lengths for lengths in ways
        ) and self._holds(shape, 0, 0, initial)

    def _holds(self, shape: Shape, index: int, used: int, start: str) -> bool:
        """Whether the positions of ``shape`` from ``index`` on, after the ``used``
        letters already in position ``index``, can begin with ``start``."""
        key = (shape, index, used, start)
        if key not in self._held:
            if index == shape.element_total:
                held = False
            elif shape.substructure[index] == 0:  # a whole element, a multi vowel
                vowel = shape.is_vowel(index)
                held = any(
                    self._goes_on(shape, index + 1, 0, start, format_element(element))
                    for element, stats in self.analysis.elements(vowel).items()
                    if stats.length == 0
                )
            elif used == shape.substructure[index]:
                held = self._holds(shape, index + 1, 0, start)
            else:
                held = any(
                    self._goes_on(shape, index, used + 1, start, letter)
                    for letter in self._spellings[shape.is_vowel(index)]
                )
            self._held[key] = held
        return self._held[key]

    def _goes_on(self, shape: Shape, index: int, used: int, start: str, spelled: str) -> bool:
        """Whether letters spelled ``spelled``, ending with the ``used``-th letter of
        position ``index``, can begin ``start`` or all of it."""
        if spelled.startswith(start):
            return True
        return start.startswith(spelled) and self._holds(shape, index, used, start[len(spelled) :])

    def _choose(self, placement: Placement, explain: bool) -> PositionChoice:
        length = placement.shape.substructure[placement.index]
        if placement.kind != ELEMENT or length == 0:
            return super()._choose(placement, explain)
        return self._fill(placement, length)

    def _fill(self, placement: Placement, length: int) -> PositionChoice:
        """Fill one position with ``length`` letters."""
        shape, index = placement.shape, placement.index
        vowel = shape.is_vowel(index)
        placed = word_letters(placement.placed, shape.is_vowel(0), self.classes)
        run = self._run(placement.before + placed)
        initial = placement.initial
        letters: list[str] = []
        chances: list[float] = []
        first_count = 0
        for step in range(length):
            last = step == length - 1
            allowed = self._letters[vowel]
            if initial:
                spelled = capitals("".join(letters))
                allowed = tuple(
                    letter for letter in allowed
                    if self._goes_on(shape, index, step + 1, initial, spelled + capitals(letter))
                )  # fmt: skip
            if step == 0:
                first_count = len(allowed)
            if not last:
                then: str | bool = vowel  # another letter of this class
            elif index == shape.element_total - 1:
                then = placement.then  # the end of the word, or a space
            else:
                then = not vowel  # the next position's class
            weights = [self._weight(run, letter, then) for letter in allowed]
            total = sum(weights)
            if not allowed or total <= 0:
                return PositionChoice(LETTERS, index, vowel, first_count, (), None)
            position = self.rng.choices(range(len(allowed)), weights=weights)[0]
            letters.append(allowed[position])
            chances.append(weights[position] / total)
            run = self._run(run + (allowed[position],))
        element = tuple(map(capitals, letters))
        return PositionChoice(
            LETTERS, index, vowel, first_count, (), Candidate((element,)), letter_chances=tuple(chances)
        )

    def _weight(self, run: tuple[str, ...], letter: str, then: str | bool) -> float:
        """The chance of ``letter`` after ``run``, times the chance that ``then``
        follows it (a letter, or any letter of a class), to the strength."""
        after = self._run(run + (letter,))
        if isinstance(then, bool):
            key = (after, then)
            if key not in self._class_chances:
                chances = self._chances_after(after)
                self._class_chances[key] = sum(chances[other] for other in self._letters[then])
            following = self._class_chances[key]
        else:
            following = self._chance(after, then)
        return (self._chance(run, letter) * following) ** self.strength

    def _chance(self, run: tuple[str, ...], letter: str) -> float:
        chances = self._chances_after(run)
        return chances[letter] if letter in chances else self.chain.chance(run, letter)

    def _chances_after(self, run: tuple[str, ...]) -> dict[str, float]:
        if run not in self._after:
            self._after[run] = self.chain.chances_after(run)
        return self._after[run]

    def _run(self, letters: Sequence[str]) -> tuple[str, ...]:
        """The letters the chain looks back at. Fewer only at the start of a name."""
        return tuple(letters)[-self.chain.look_back :]


def rest_of_initial(initial: str | None, spelled: str) -> str | None:
    """What is left of the starting letters ``initial`` after ``spelled``, which
    begins them or all of them; None when nothing is left."""
    if not initial or spelled.startswith(initial):
        return None
    return initial[len(spelled) :]


def _still_begins(
    analysis: StyleAnalysis, shape: Shape, index: int, spelled: str, initial: str
) -> bool:
    """Whether a word whose element at ``index`` is spelled ``spelled`` can begin
    with ``initial``, what is left of the starting letters: the element begins
    with all of it, or with a start of it that enough elements after it can
    carry on (spec 13.11), as K, then A, carry on KA."""
    if spelled.startswith(initial):
        return True
    if not initial.startswith(spelled):
        return False
    ways = analysis.start_lengths(initial[len(spelled) :], not shape.is_vowel(index))
    return any(index + len(lengths) < shape.element_total for lengths in ways)
