"""Letter classes: which characters count as vowel letters and consonant letters.

The classic letter lists (spec section 2.3) are kept here as default data, with a
few more letters, and any other Latin letter has the class of the letter
inside it: Č is a consonant like C, Ā a vowel like A (spec 13.8). The
generator never hard-codes them; it asks a style's ``LetterClasses``, so a
style or settings file can supply different lists later.

Letters are kept in capitals, one character each (``capital``).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import cache

# The classic allowed letters, in uppercase. The apostrophe counts as a vowel letter
# and the hyphen as a consonant letter.
CLASSIC_VOWELS = "AEIOUYÄËÏÖÜŸÁÉÍÓÚÝÂÊÎÔÛÀÈÌÒÙÃÕÅÆØŒ'"
CLASSIC_CONSONANTS = "BCDFGHJKLMNPQRSTVWXZÇÐÑÞŠ-"
APOSTROPHE = "'"  # a style may make it a consonant letter (``letter_apostrophe``)

# Letters of today's Latin alphabets with no plain letter inside them (spec
# 13.8). The Turkish dotless ı is kept small (see ``capital``). The ʻokina
# of Hawaiian, Samoan and Tongan, ʻ, is a consonant, unlike the apostrophe
# it looks like (spec 13.12); it has no capital.
OTHER_VOWELS = "ıƏƎƐƆƖƱɄɅⱭĲ"
OTHER_CONSONANTS = "ẞŊƉƔƩƷǄǇǊǱȜǷꞋɁʻ"

# Capitals that ``str.upper`` does not give. ß has the capital ẞ (``str.upper``
# makes it SS, two letters). The Turkish dotless ı keeps its small form: its
# capital, I, is also the capital of i, and the two are different letters.
OWN_CAPITALS = {"ß": "ẞ", "ı": "ı"}


@dataclass(frozen=True)
class LetterClasses:
    """The vowel letters and consonant letters a style may use, in uppercase.

    A letter in neither list has the class of the letter inside it.
    """

    vowels: frozenset[str]
    consonants: frozenset[str]

    def __post_init__(self) -> None:
        both = self.vowels & self.consonants
        if both:
            raise ValueError(
                "letters cannot be both vowels and consonants: " + "".join(sorted(both))
            )

    @classmethod
    def from_strings(cls, vowels: str, consonants: str) -> LetterClasses:
        """Build letter classes from two strings of letters, e.g. ``"AEIOU"``."""
        return cls(frozenset(vowels), frozenset(consonants))

    def moved(self, letter: str, vowel: bool) -> LetterClasses:
        """These classes with ``letter`` a vowel letter if ``vowel``, else a consonant letter."""
        vowels, consonants = self.vowels - {letter}, self.consonants - {letter}
        if vowel:
            return LetterClasses(vowels | {letter}, consonants)
        return LetterClasses(vowels, consonants | {letter})

    def class_of(self, letter: str) -> bool | None:
        """True for a vowel letter, False for a consonant letter, None for anything else."""
        while letter not in self.vowels and letter not in self.consonants:
            letter = letter_inside(letter)
            if letter is None:
                return None
        return letter in self.vowels

    def is_vowel(self, letter: str) -> bool:
        return self.class_of(letter) is True

    def is_consonant(self, letter: str) -> bool:
        return self.class_of(letter) is False

    def is_letter(self, letter: str) -> bool:
        return self.class_of(letter) is not None

    def invalid_characters(self, name: str) -> list[str]:
        """Return the characters in ``name`` that are not allowed, in order of appearance.

        A character is allowed if its capital is a letter of one of the
        classes. Spaces are allowed too (for names of several words).
        Each bad character is listed once.
        """
        bad: list[str] = []
        for char in name:
            if char == " " or self.is_letter(capital(char)):
                continue
            if char not in bad:
                bad.append(char)
        return bad


@cache
def letter_inside(letter: str) -> str | None:
    """The letter inside a marked one, such as C in Č, or None if there is none.

    The mark is an accent (Č is C and a caron), or else the letter's Unicode
    name says it: Ł is LATIN CAPITAL LETTER L WITH STROKE.
    """
    if len(letter) != 1:
        return None
    base = unicodedata.normalize("NFD", letter)[0]
    if base != letter:
        return base
    found = re.fullmatch(r"LATIN CAPITAL LETTER ([A-Z]) WITH .+", unicodedata.name(letter, ""))
    return found[1] if found else None


def capital(char: str) -> str:
    """The form a letter is kept in: its capital, one character (ß is ẞ)."""
    if char in OWN_CAPITALS:
        return OWN_CAPITALS[char]
    upper = char.upper()
    return upper if len(upper) == 1 else char


def capitals(text: str) -> str:
    """Each letter of ``text`` in the form letters are kept in."""
    return "".join(map(capital, text))


def small(text: str) -> str:
    """``text`` in small letters, one for one. İ, the Turkish dotted capital
    I, becomes i (``str.lower`` would add a second dot)."""
    return text.replace("İ", "i").lower()


LATIN_LETTERS = LetterClasses.from_strings(
    CLASSIC_VOWELS + OTHER_VOWELS, CLASSIC_CONSONANTS + OTHER_CONSONANTS
)
