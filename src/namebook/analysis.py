"""Learning from a style's seed names (spec 5.2 to 5.5).

``analyze`` splits every seed name into words and elements, then gathers what
the generator and the validator need:

- which elements exist, and how often each appears at each position (5.2);
- which neighboring combinations occur, for fitting (5.3);
- how often each structure and substructure occurs, and the prefixes and
  suffixes (5.4);
- how closely each letter repeats and how much of a word it can fill (5.5).

The counts are kept, not just yes/no facts, so later strategies can use
them.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import cached_property
from typing import NamedTuple

from namebook.style import Style, StyleError
from namebook.words import Element, Word, WordSplitError, WordSplitter, format_element

# Used when no seed letter ever repeats: a gap of 2 or more is always allowed,
# and a letter may make up the whole word.
NO_DISTANCE_LIMIT = 2
NO_SHARE_LIMIT = 1.0


@dataclass(frozen=True)
class SeedWord:
    """A word of a seed name, with the name it came from."""

    name_number: int  # position of the seed name in its list, counting from 1
    name: str
    word: Word


class ElementPair(NamedTuple):
    """Two neighboring elements kept together: a prefix or a suffix (spec 5.4)."""

    first_is_vowel: bool
    first: Element
    second: Element


@dataclass
class ElementStats:
    """How one element occurs in the seed words (spec 5.2).

    Positions: ``first`` and ``last`` count the element as a word's first or
    last element; the one element of a one-element word counts as both.
    Every other occurrence is a middle one, counted twice: by
    its rank among elements of its class from the start of the word
    (``from_start``) and from the end (``from_end``). In M·I·KH·AI·L, the
    vowel element AI is the 2nd vowel element from the start and the 1st from
    the end.
    """

    element: Element
    length: int  # its length in a substructure
    count: int = 0
    first: int = 0
    last: int = 0
    from_start: Counter[int] = field(default_factory=Counter)
    from_end: Counter[int] = field(default_factory=Counter)

    @property
    def middle(self) -> int:
        """How often the element occurs anywhere but first or last (used by ``shuffle``)."""
        return sum(self.from_start.values())


@dataclass
class FittingData:
    """Neighboring combinations seen in the seed words, with counts (spec 5.3).

    Vowel elements are fitted as whole elements; consonant elements by their
    first and last letters.
    """

    # Level 1: last letter of a consonant element -> the vowel element after it.
    consonant_to_vowel: Counter[tuple[str, Element]] = field(default_factory=Counter)
    # Level 1: vowel element -> first letter of the consonant element after it.
    vowel_to_consonant: Counter[tuple[Element, str]] = field(default_factory=Counter)
    # Level 2: last letter of a consonant element -> first letter of the next
    # consonant element, across one vowel element.
    consonant_to_consonant: Counter[tuple[str, str]] = field(default_factory=Counter)
    # Level 3: vowel element -> the next vowel element, across one consonant element.
    vowel_to_vowel: Counter[tuple[Element, Element]] = field(default_factory=Counter)


@dataclass(frozen=True)
class LetterLimits:
    """How letters may repeat in a word, learned from the seeds (spec 5.5).

    ``min_distance`` holds, for each letter that repeats in some seed word,
    the smallest gap between two of its occurrences (doubled letters, with a
    gap of 1, are ignored). ``max_share`` holds, for each letter that appears
    at least twice in some seed word, the largest share of a word it fills.
    Letters that never repeat get the fallback values: the loosest limits
    seen for any letter.
    """

    min_distance: dict[str, int]
    max_share: dict[str, float]
    fallback_distance: int
    fallback_share: float

    def distance_for(self, letter: str) -> int:
        return self.min_distance.get(letter, self.fallback_distance)

    def share_for(self, letter: str) -> float:
        return self.max_share.get(letter, self.fallback_share)


@dataclass
class StyleAnalysis:
    """Everything learned from one name list of a style."""

    style: Style
    list_name: str
    splitter: WordSplitter
    words: tuple[SeedWord, ...]
    structures: Counter[int]
    substructures: dict[int, Counter[tuple[int, ...]]]
    vowel_elements: dict[Element, ElementStats]
    consonant_elements: dict[Element, ElementStats]
    fitting: FittingData
    prefixes: Counter[ElementPair]
    suffixes: Counter[ElementPair]
    letter_limits: LetterLimits

    def elements(self, vowel: bool) -> dict[Element, ElementStats]:
        return self.vowel_elements if vowel else self.consonant_elements

    def start_lengths(self, start: str, vowel: bool, first: bool = False) -> frozenset[tuple[int, ...]]:
        """How elements of the seeds can begin a word with ``start``: for each
        way, the lengths of its elements in order, as a substructure gives
        them. Empty if they cannot.

        ``start`` is in capitals, e.g. ``"KA"`` (K, then A). The first element
        is a vowel element if ``vowel``, then the classes alternate, and the
        last element may go on past ``start``. With ``first``, the first
        element must be one that began a seed word (spec 13.11).
        """
        key = (start, vowel, first)
        if key not in self._start_lengths:
            ways: set[tuple[int, ...]] = set()
            for spelled, length in self._spellings[vowel, first]:
                if spelled.startswith(start):
                    ways.add((length,))
                elif start.startswith(spelled):
                    rest = self.start_lengths(start[len(spelled) :], not vowel)
                    ways.update((length, *lengths) for lengths in rest)
            self._start_lengths[key] = frozenset(ways)
        return self._start_lengths[key]

    @cached_property
    def _spellings(self) -> dict[tuple[bool, bool], frozenset[tuple[str, int]]]:
        """For each class, and for first elements only or all: each element's
        spelling and length."""
        return {
            (vowel, first): frozenset(
                (format_element(element), stats.length)
                for element, stats in self.elements(vowel).items()
                if stats.first or not first
            )
            for vowel in (True, False)
            for first in (True, False)
        }

    @cached_property
    def _start_lengths(self) -> dict[tuple[str, bool, bool], frozenset[tuple[int, ...]]]:
        return {}  # what start_lengths has worked out


def analyze(style: Style, list_name: str | None = None) -> StyleAnalysis:
    """Split one name list of a style (the first, by default) and learn from it.

    Raises ``StyleError`` listing every seed name that cannot be split.
    """
    name_list = style.name_list(list_name) if list_name else style.name_lists[0]
    splitter = WordSplitter.for_style(style)

    words: list[SeedWord] = []
    problems: list[str] = []
    for number, name in enumerate(name_list.names, start=1):
        try:
            words.extend(SeedWord(number, name, word) for word in splitter.split_name(name))
        except WordSplitError as error:
            problems.append(f'Name {number} ("{name}"): {error}.')
    if problems:
        raise StyleError(_source(style), problems)

    analysis = StyleAnalysis(
        style=style,
        list_name=name_list.name,
        splitter=splitter,
        words=tuple(words),
        structures=Counter(),
        substructures={},
        vowel_elements={},
        consonant_elements={},
        fitting=FittingData(),
        prefixes=Counter(),
        suffixes=Counter(),
        letter_limits=_letter_limits(seed.word.letters for seed in words),
    )
    for seed in words:
        _learn_from(analysis, seed.word)
    return analysis


def _learn_from(analysis: StyleAnalysis, word: Word) -> None:
    elements = word.elements
    count = len(elements)
    analysis.structures[word.structure] += 1
    analysis.substructures.setdefault(word.structure, Counter())[word.substructure] += 1

    fitting = analysis.fitting
    for index, element in enumerate(elements):
        vowel = word.is_vowel_element(index)
        inventory = analysis.elements(vowel)
        stats = inventory.get(element)
        if stats is None:
            stats = inventory[element] = ElementStats(element, word.substructure[index])
        stats.count += 1
        # The one element of a one-element word is both its first and its last.
        if index == 0:
            stats.first += 1
        if index == count - 1:
            stats.last += 1
        if 0 < index < count - 1:
            # Elements alternate classes, so index // 2 elements of this
            # class come before this one, and (count - 1 - index) // 2 after.
            stats.from_start[index // 2 + 1] += 1
            stats.from_end[(count - 1 - index) // 2 + 1] += 1

        if index + 1 < count:
            following = elements[index + 1]
            if vowel:
                fitting.vowel_to_consonant[(element, following[0])] += 1
            else:
                fitting.consonant_to_vowel[(element[-1], following)] += 1
        if index + 2 < count:
            after_next = elements[index + 2]
            if vowel:
                fitting.vowel_to_vowel[(element, after_next)] += 1
            else:
                fitting.consonant_to_consonant[(element[-1], after_next[0])] += 1

    if count >= 2:
        analysis.prefixes[ElementPair(word.starts_with_vowel, elements[0], elements[1])] += 1
        analysis.suffixes[ElementPair(word.is_vowel_element(count - 2), elements[-2], elements[-1])] += 1


def smallest_gaps(letters: Sequence[str]) -> dict[str, int]:
    """For each letter that repeats, the smallest gap between two of its occurrences.

    The gap is the difference in position, so in ANA the gap for A is 2.
    Doubled letters (a gap of 1, as in ANNA) are ignored.
    """
    last_seen: dict[str, int] = {}
    gaps: dict[str, int] = {}
    for position, letter in enumerate(letters):
        if letter in last_seen:
            gap = position - last_seen[letter]
            if gap >= 2:
                gaps[letter] = min(gap, gaps.get(letter, gap))
        last_seen[letter] = position
    return gaps


def repeated_letter_shares(letters: Sequence[str]) -> dict[str, float]:
    """For each letter appearing at least twice, its share of the word (count / length)."""
    counts = Counter(letters)
    return {letter: n / len(letters) for letter, n in counts.items() if n >= 2}


def _letter_limits(words_letters) -> LetterLimits:
    min_distance: dict[str, int] = {}
    max_share: dict[str, float] = {}
    for letters in words_letters:
        for letter, gap in smallest_gaps(letters).items():
            min_distance[letter] = min(gap, min_distance.get(letter, gap))
        for letter, share in repeated_letter_shares(letters).items():
            max_share[letter] = max(share, max_share.get(letter, share))
    return LetterLimits(
        min_distance=min_distance,
        max_share=max_share,
        fallback_distance=min(min_distance.values(), default=NO_DISTANCE_LIMIT),
        fallback_share=max(max_share.values(), default=NO_SHARE_LIMIT),
    )


def _source(style: Style) -> str:
    if style.source and style.source.path:
        return str(style.source.path)
    return style.title
