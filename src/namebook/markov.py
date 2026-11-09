"""The Markov chain: how likely each letter is to follow the letters before it (spec 13.2).

A chain learns from names written as sequences of letters. Each name begins
after START markers and is followed by END, so the chain also learns how
names begin and end. A space between words counts as a letter (D75): the
chain learns how a word ends when another follows, and how later words
begin. So does a hyphen that joins two words (spec 13.15).

The chain looks back a few letters at most: its look-back. It blends every
look-back from none up to that one, in the way known as Witten-Bell
smoothing. A run of letters is trusted in proportion to how often the seeds
had it, and less the more different letters followed it; the trust left
over goes to the next shorter look-back, and below the shortest to an even
chance for every letter. So a letter the seeds never had after some run
still gets a small chance, and there is nothing to set by hand.

The look-back is chosen by hiding names (spec 10.6): each name in turn is
taken out, with all its copies, and scored by the chain learned from the
others. The look-back under which the hidden names are likeliest wins.
Taking a name out only subtracts its own counts, so nothing is learned
again.

Two chains are learned from a style's names. The plausibility measurement
reads them as written (``plain_letters``); building words reads them in
their internal form, as the style's rules split them (``internal_chain``).
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from functools import cached_property
from typing import TypeVar

from namebook.letters import LetterClasses, capital, capitals
from namebook.style import MAX_LOOK_BACK
from namebook.words import HYPHEN, SPACE, Element, WordSplitError, WordSplitter

START = "^"  # before a name's first letter
END = "$"  # after its last letter
# Between words: SPACE, or HYPHEN when the hyphen is a joint (spec 13.15).
JOINTS = frozenset((SPACE, HYPHEN))
MEASURED_NAMES = 2000  # hiding names uses at most this many of a long list's names (D80)

Letters = tuple[str, ...]
T = TypeVar("T")


def spread(items: Sequence[T], most: int) -> list[T]:
    """At most ``most`` of ``items``, spread evenly through them: the names a
    long list is measured on, since hiding every one of them would be slow."""
    step = max(1.0, len(items) / most)
    return [items[int(number * step)] for number in range(min(len(items), most))]


def plain_letters(text: str) -> Letters:
    """A name as the plausibility measurement reads it: each character in
    capitals, with one space between words."""
    return tuple(SPACE.join(capitals(text).split()))


def letter_symbol(letter: str, vowel: bool, classes: LetterClasses) -> str:
    """A letter of a word's internal form, as the building chain reads it.

    A special letter such as TH is one letter. A letter used as the other
    class from usual, such as Y as a consonant, is written in small letters,
    so the chain tells the two apart.
    """
    return letter if classes.is_vowel(letter[0]) == vowel else letter.lower()


def symbol_is_vowel(symbol: str, classes: LetterClasses) -> bool:
    """Whether a letter of the building chain (``letter_symbol``) is a vowel."""
    usual = classes.is_vowel(capital(symbol[0]))
    return usual if symbol == capitals(symbol) else not usual


def word_letters(
    elements: Sequence[Element], first_is_vowel: bool, classes: LetterClasses
) -> Letters:
    """The letters of a word made of ``elements``, as the building chain reads them."""
    return tuple(
        letter_symbol(letter, (index % 2 == 0) == first_is_vowel, classes)
        for index, element in enumerate(elements)
        for letter in element
    )


def internal_letters(splitter: WordSplitter, name: str) -> Letters | None:
    """``name`` in its internal form, words separated by their joints (a
    space, or a hyphen as a joint); None if the splitter cannot split it."""
    try:
        words = splitter.split_name(name)
        _, joints = splitter.words_and_joints(name)
    except WordSplitError:
        return None
    letters: list[str] = []
    for word, joint in zip(words, ("", *joints)):
        if joint:
            letters.append(joint)
        letters.extend(word_letters(word.elements, word.starts_with_vowel, splitter.letter_classes))
    return tuple(letters)


def internal_chain(
    splitter: WordSplitter, names: Iterable[str], look_back: int | None = None
) -> Chain:
    """The chain that builds words: ``names`` in their internal form, words
    separated by spaces. Names the splitter cannot split are left out."""
    sequences = (internal_letters(splitter, name) for name in names)
    return Chain([letters for letters in sequences if letters is not None], look_back)


def _steps(name: Letters) -> Iterator[tuple[Letters, str]]:
    """Each letter of ``name`` and then END, with the MAX_LOOK_BACK letters before it."""
    padded = (START,) * MAX_LOOK_BACK + name
    for position, letter in enumerate(name + (END,)):
        yield padded[position : position + MAX_LOOK_BACK], letter


def _runs(before: Letters) -> Iterator[Letters]:
    """The runs of letters just before a letter, from none up to MAX_LOOK_BACK letters."""
    for size in range(MAX_LOOK_BACK + 1):
        yield before[MAX_LOOK_BACK - size :]


class Chain:
    """Which letters followed which runs of letters in the seed names, and how often.

    ``names`` are sequences of letters, such as ``plain_letters`` gives; a
    name listed twice counts twice. A ``look_back`` given here is used as it
    is; otherwise the chain chooses one by hiding names: every name, or only
    those in ``hiding``, a few of a long list's names, which is quicker.
    """

    def __init__(
        self,
        names: Iterable[Sequence[str]],
        look_back: int | None = None,
        hiding: Iterable[Sequence[str]] | None = None,
    ):
        if look_back is not None and not 1 <= look_back <= MAX_LOOK_BACK:
            raise ValueError(f"the look-back must be 1 to {MAX_LOOK_BACK}, not {look_back}")
        self._given_look_back = look_back
        self.copies: Counter[Letters] = Counter(tuple(name) for name in names if name)
        self._hiding = None if hiding is None else {tuple(name) for name in hiding} & self.copies.keys()
        # run of letters -> the letters that followed it, with counts
        self.followers: dict[Letters, Counter[str]] = {}
        for name, copies in self.copies.items():
            for before, letter in _steps(name):
                for run in _runs(before):
                    self.followers.setdefault(run, Counter())[letter] += copies
        self._seen = {run: sum(letters.values()) for run, letters in self.followers.items()}
        # An even chance for every letter the seeds have (END included) and
        # for one they don't.
        self._even = 1 / (len(self.followers.get((), ())) + 1)

    @cached_property
    def look_back(self) -> int:
        """How many letters the chain looks back: as given, or the one under
        which hidden names are likeliest (the shorter one if two tie)."""
        if self._given_look_back is not None:
            return self._given_look_back
        totals = self.hidden_totals
        return max(range(1, MAX_LOOK_BACK + 1), key=lambda size: (totals[size], -size))

    def score(self, name: Sequence[str]) -> float:
        """How plausible ``name`` is: the average log-chance of its letters and its end.

        Log-chances are negative; closer to zero is likelier. ``math.exp`` of
        the score is the chance per letter, on average.
        """
        return _average(self._chances(tuple(name)), self.look_back)

    def hidden_score(self, name: Sequence[str]) -> float:
        """The score of a seed name, by the chain learned without it (or its
        copies). A name that is not a seed name is scored as ``score`` does."""
        scores = self._hidden[0].get(tuple(name))
        return self.score(name) if scores is None else scores[self.look_back]

    def hidden_log_chance(self, name: Sequence[str]) -> float:
        """The log-chance of a seed name, its letters and its end together,
        by the chain learned without it (or its copies)."""
        name = tuple(name)
        return self._hidden[0][name][self.look_back] * (len(name) + 1)

    @property
    def hidden_totals(self) -> list[float]:
        """For each look-back from 0 to MAX_LOOK_BACK, the total log-chance of
        every seed name, each scored with its copies hidden."""
        return self._hidden[1]

    def chance(self, before: Sequence[str], letter: str) -> float:
        """The chance of ``letter`` (or END, or SPACE) following ``before``."""
        run = ((START,) * MAX_LOOK_BACK + tuple(before))[-MAX_LOOK_BACK:]
        return self._chance(run, letter)[self.look_back]

    def chances_after(self, before: Sequence[str]) -> dict[str, float]:
        """The chance of each letter the seeds have (and of END) following ``before``.

        The chances add up to a little under 1: the rest is left for letters
        the seeds never had.
        """
        run = ((START,) * MAX_LOOK_BACK + tuple(before))[-MAX_LOOK_BACK:]
        letters = self.followers.get((), Counter())
        return {letter: self._chance(run, letter)[self.look_back] for letter in letters}

    @cached_property
    def _hidden(self) -> tuple[dict[Letters, list[float]], list[float]]:
        scores: dict[Letters, list[float]] = {}
        totals = [0.0] * (MAX_LOOK_BACK + 1)
        for name, copies in self.copies.items():
            if self._hiding is not None and name not in self._hiding:
                continue
            chances = self._chances(name, hidden=copies)
            scores[name] = [_average(chances, size) for size in range(MAX_LOOK_BACK + 1)]
            for size, score in enumerate(scores[name]):
                totals[size] += copies * score * len(chances)
        return scores, totals

    def _chances(self, name: Letters, hidden: int = 0) -> list[list[float]]:
        """For each letter of ``name`` and its end, its chance after each look-back.

        ``hidden`` copies of ``name`` are taken out of the counts first.
        """
        steps = list(_steps(name))
        if not hidden:
            return [self._chance(before, letter) for before, letter in steps]
        removed: dict[Letters, dict[str, int]] = {}
        for before, letter in steps:
            for run in _runs(before):
                letters = removed.setdefault(run, {})
                letters[letter] = letters.get(letter, 0) + hidden
        # For each run: the letters taken out, how many in all, and how many
        # kinds of letter no longer follow it at all.
        taken: dict[Letters, tuple[dict[str, int], int, int]] = {}
        for run, letters in removed.items():
            followers = self.followers[run]
            gone = sum(1 for other, n in letters.items() if followers[other] == n)
            taken[run] = (letters, sum(letters.values()), gone)
        return [self._chance(before, letter, taken) for before, letter in steps]

    def _chance(
        self,
        before: Letters,
        letter: str,
        taken: dict[Letters, tuple[dict[str, int], int, int]] | None = None,
    ) -> list[float]:
        """The chance of ``letter`` after ``before``, at each look-back from 0
        to MAX_LOOK_BACK, without the counts in ``taken``."""
        chance = self._even
        chances = []
        for run in _runs(before):
            followers = self.followers.get(run)
            if followers is not None:
                seen, kinds, count = self._seen[run], len(followers), followers[letter]
                if taken and run in taken:
                    letters, letters_taken, kinds_gone = taken[run]
                    seen -= letters_taken
                    kinds -= kinds_gone
                    count -= letters.get(letter, 0)
                if seen > 0:
                    chance = (count + kinds * chance) / (seen + kinds)
            chances.append(chance)
        return chances


def _average(chances: list[list[float]], size: int) -> float:
    return sum(math.log(step[size]) for step in chances) / len(chances)
