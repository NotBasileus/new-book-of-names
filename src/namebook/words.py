"""Turning seed names into words made of letters and elements (spec 4, 5.1, 6).

Each name is converted to uppercase, its special letters are marked, and it is
split into alternating runs of vowel letters and consonant letters: its
elements.

A letter is written as its uppercase spelling. A special letter is one letter
with a longer spelling: with soft consonants on, ``"TH"`` is one letter, and
with the cluster ``{RR}``, ``"RR"`` is one letter. An element is a tuple of
letters, so ``("TH",)`` (one soft letter) and ``("T", "H")`` (two letters) are
different elements.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from namebook.letters import LetterClasses, capital
from namebook.style import HyphenMode, SpecialLetters, Style, UMode, YMode

Element = tuple[str, ...]

SOFT_SECOND_LETTER = "H"
SPACE = " "
HYPHEN = "-"  # a consonant letter, or a joint between words (``letter_hyphen``)

# Characters that are not counted as letters in a name's length (spec 10.5).
NOT_COUNTED = frozenset(" '-")


def plain(name: str) -> str:
    """``name`` as names are compared: lowercase, without accents, spaces, apostrophes or hyphens."""
    decomposed = unicodedata.normalize("NFD", name.casefold())
    return "".join(
        char for char in decomposed if not unicodedata.combining(char) and char not in NOT_COUNTED
    )


def letter_count(text: str) -> int:
    """How many letters a name has, as the Length measurement counts them: spaces,
    apostrophes and hyphens do not count, and TH is two letters even when it is
    one soft letter."""
    return sum(1 for char in text if char not in NOT_COUNTED)


def structure_number(element_count: int, starts_with_vowel: bool) -> int:
    """The structure number of a word shape (spec 6): CV is 0, VC is 1, CVC is 2, ...

    A word of one element follows the same formula: C is -2 and V is -1
    (spec 13.10). The functions below read these numbers too.
    """
    if element_count < 1:
        raise ValueError(f"a word needs at least 1 element, not {element_count}")
    return 2 * (element_count - 2) + (1 if starts_with_vowel else 0)


def element_count(structure: int) -> int:
    """How many elements a word of this structure has."""
    return structure // 2 + 2


def structure_starts_with_vowel(structure: int) -> bool:
    return structure % 2 == 1


def structure_shape(structure: int) -> str:
    """The shape of a structure as vowels and consonants, e.g. ``"CVCVC"`` for 6."""
    first, second = ("V", "C") if structure_starts_with_vowel(structure) else ("C", "V")
    return "".join(first if i % 2 == 0 else second for i in range(element_count(structure)))


def format_element(element: Element) -> str:
    return "".join(element)


def format_substructure(substructure: tuple[int, ...]) -> str:
    """Element lengths as digits, e.g. ``"11121"``. Lengths of 10 or more get dashes."""
    if all(length < 10 for length in substructure):
        return "".join(str(length) for length in substructure)
    return "-".join(str(length) for length in substructure)


@dataclass(frozen=True)
class Word:
    """One word of a seed name, split into elements."""

    text: str  # as written in the seed name
    elements: tuple[Element, ...]
    starts_with_vowel: bool
    # The length of each element in letters. A vowel element holding a multi
    # vowel has length 0 (spec 4.2).
    substructure: tuple[int, ...]

    @property
    def structure(self) -> int:
        return structure_number(len(self.elements), self.starts_with_vowel)

    @property
    def letters(self) -> tuple[str, ...]:
        return tuple(letter for element in self.elements for letter in element)

    def is_vowel_element(self, index: int) -> bool:
        return (index % 2 == 0) == self.starts_with_vowel

    def describe(self) -> str:
        """The elements separated by dots, e.g. ``"M · I · KH · AI · L"``."""
        return " · ".join(format_element(element) for element in self.elements)


class WordSplitError(ValueError):
    """A name could not be split into elements."""


@dataclass(frozen=True)
class _Letter:
    text: str
    vowel: bool
    multi: bool = False


class WordSplitter:
    """Splits names into words and elements, following one style's rules."""

    def __init__(
        self,
        letter_classes: LetterClasses,
        special_letters: SpecialLetters = SpecialLetters(),
        y_mode: YMode = YMode.VOWEL,
        u_mode: UMode = UMode.VOWEL,
        hyphen: HyphenMode = HyphenMode.CONSONANT,
    ):
        self.letter_classes = letter_classes
        self.special_letters = special_letters
        self.y_mode = y_mode
        self.u_mode = u_mode
        self.hyphen = hyphen
        # Each custom cluster as a sequence of letters. A lowercase letter
        # stands for a soft consonant, so "Lt" is L followed by TH. Longer
        # clusters are tried first; clusters of equal length in listed order.
        patterns = [
            tuple(
                capital(char) + SOFT_SECOND_LETTER if char.islower() else char for char in cluster
            )
            for cluster in special_letters.consonant_clusters
        ]
        self._cluster_patterns = sorted(patterns, key=len, reverse=True)

    @classmethod
    def for_style(cls, style: Style) -> WordSplitter:
        return cls(
            style.letter_classes,
            style.special_letters,
            style.options.y_mode,
            style.options.u_mode,
            style.options.hyphen,
        )

    def split_name(self, name: str) -> tuple[Word, ...]:
        """Split a name into words at its joints, and each word into elements."""
        texts, _ = self.words_and_joints(name)
        return tuple(self.split_word(text) for text in texts)

    def words_and_joints(self, name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """The words of a name as written, and the joint between each two: a
        space, or a hyphen when the hyphen is a joint (spec 13.15)."""
        texts: list[str] = []
        joints: list[str] = []
        for token in name.split():
            parts = token.split(HYPHEN) if self.hyphen is HyphenMode.JOINT else [token]
            if "" in parts:
                raise WordSplitError(f"'{token}' has a hyphen that does not join two words")
            if texts:
                joints.append(SPACE)
            texts += parts
            joints += [HYPHEN] * (len(parts) - 1)
        if not texts:
            raise WordSplitError("the name is empty")
        return tuple(texts), tuple(joints)

    def split_word(self, text: str) -> Word:
        """Split a single word (no spaces) into elements."""
        letters = self._letters(text)
        if self.special_letters.soft_consonants:
            letters = self._mark_soft_consonants(letters)
        if self._cluster_patterns:
            letters = self._mark_clusters(letters)
        letters = self._classify_y(letters)
        letters = self._classify_u(letters)
        if self.special_letters.multi_vowels:
            letters = self._mark_multi_vowels(letters)

        # A word of only vowels or only consonants is one element (spec 13.10).
        runs: list[list[_Letter]] = []
        for letter in letters:
            if runs and runs[-1][-1].vowel == letter.vowel:
                runs[-1].append(letter)
            else:
                runs.append([letter])
        return Word(
            text=text,
            elements=tuple(tuple(letter.text for letter in run) for run in runs),
            starts_with_vowel=runs[0][0].vowel,
            substructure=tuple(
                0 if any(letter.multi for letter in run) else len(run) for run in runs
            ),
        )

    def _letters(self, text: str) -> list[_Letter]:
        if not text:
            raise WordSplitError("the word is empty")
        letters = []
        for char in text:
            upper = capital(char)
            if not self.letter_classes.is_letter(upper):
                raise WordSplitError(f"'{char}' is not a vowel or consonant letter")
            letters.append(_Letter(upper, self.letter_classes.is_vowel(upper)))
        return letters

    def _mark_soft_consonants(self, letters: list[_Letter]) -> list[_Letter]:
        """Any consonant followed by H becomes one letter, except HH (spec 4.1)."""
        result: list[_Letter] = []
        pos = 0
        while pos < len(letters):
            letter = letters[pos]
            following = letters[pos + 1] if pos + 1 < len(letters) else None
            if (
                following is not None
                and following.text == SOFT_SECOND_LETTER
                and not following.vowel
                and not letter.vowel
                and letter.text != SOFT_SECOND_LETTER
                and letter.text.isalpha()  # not the hyphen, or the apostrophe as a consonant
            ):
                result.append(_Letter(letter.text + SOFT_SECOND_LETTER, vowel=False))
                pos += 2
            else:
                result.append(letter)
                pos += 1
        return result

    def _mark_clusters(self, letters: list[_Letter]) -> list[_Letter]:
        result: list[_Letter] = []
        pos = 0
        while pos < len(letters):
            for pattern in self._cluster_patterns:
                window = letters[pos : pos + len(pattern)]
                if (
                    len(window) == len(pattern)
                    and not any(letter.vowel for letter in window)
                    and tuple(letter.text for letter in window) == pattern
                ):
                    result.append(_Letter("".join(pattern), vowel=False))
                    pos += len(pattern)
                    break
            else:
                result.append(letters[pos])
                pos += 1
        return result

    def _classify_y(self, letters: list[_Letter]) -> list[_Letter]:
        """Apply the Y setting (spec 4.3).

        ``y:vow`` leaves Y as the letter classes say (a vowel by default), so a
        style with its own letter classes keeps its choice. ``y:con`` makes
        every Y a consonant. With ``y:semi``, Y is a consonant when a vowel
        follows it and it either begins the word or comes after a vowel
        (Yorath, Maya); otherwise it is a vowel (Bryan, Gwyn, Kay).
        """
        if self.y_mode is YMode.VOWEL:
            return letters
        result: list[_Letter] = []
        for pos, letter in enumerate(letters):
            if letter.text == "Y":
                if self.y_mode is YMode.CONSONANT:
                    vowel = False
                else:
                    following = letters[pos + 1] if pos + 1 < len(letters) else None
                    previous = result[-1] if result else None
                    vowel = not (
                        following is not None
                        and following.vowel
                        and (previous is None or previous.vowel)
                    )
                letter = _Letter(letter.text, vowel)
            result.append(letter)
        return result

    def _classify_u(self, letters: list[_Letter]) -> list[_Letter]:
        """Apply the U setting: with ``u:semi``, U after Q is a consonant (spec 4.3).

        ``u:vow`` leaves U as the letter classes say (a vowel by default).
        """
        if self.u_mode is UMode.VOWEL:
            return letters
        result: list[_Letter] = []
        for letter in letters:
            if letter.text == "U" and result and result[-1].text == "Q":
                letter = _Letter(letter.text, vowel=False)
            result.append(letter)
        return result

    def _mark_multi_vowels(self, letters: list[_Letter]) -> list[_Letter]:
        """A vowel, one consonant letter and the same vowel again become one vowel letter."""
        result: list[_Letter] = []
        pos = 0
        while pos < len(letters):
            first, middle, last = (letters[pos : pos + 3] + [None, None])[:3]
            if (
                last is not None
                and first.vowel
                and first.text.isalpha()  # not the apostrophe
                and not middle.vowel
                and last.vowel
                and last.text == first.text
            ):
                text = first.text + middle.text + last.text
                result.append(_Letter(text, vowel=True, multi=True))
                pos += 3
            else:
                result.append(first)
                pos += 1
        return result
