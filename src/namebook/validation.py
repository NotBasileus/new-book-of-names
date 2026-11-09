"""Checking finished words (spec 8).

A ``Validator`` runs a chain of independent checks over a built word. Each
check says at which validation levels it applies and, if the word fails it,
returns a ``Rejection`` with its failure code and a plain-language reason.
The checks:

| Level | Checks |
| ----- | ------ |
| 0     | word list |
| 1     | word list, repeated sequences (``!REP``), letter spacing (``!DIST/x``) |
| 2+    | all of level 1, plus letter frequency (``!FREQ/x``) |

Later checks, such as a filter against copies of seed names (spec 13.1), can
be added to the chain without changing the others.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Protocol

from namebook.analysis import LetterLimits, StyleAnalysis, repeated_letter_shares, smallest_gaps
from namebook.formats.common import plain_name, read_file
from namebook.letters import small

WORD_LIST_FAILURE = "!LIST"
REPEAT_FAILURE = "!REP"
DISTANCE_FAILURE = "!DIST"
FREQUENCY_FAILURE = "!FREQ"

REPEAT_LENGTH = 4  # a sequence of this many letters may not occur twice


@dataclass(frozen=True)
class Rejection:
    code: str  # e.g. "!DIST/A"
    reason: str  # in plain language, for explain mode


class WordCheck(Protocol):
    def applies_at(self, level: int) -> bool: ...

    def check(self, letters: Sequence[str], text: str) -> Rejection | None:
        """``letters`` is the word's internal form (special letters as one
        letter); ``text`` is its spelling."""
        ...


class WordListCheck:
    """Rejects words found in a list (all levels).

    Entries ignore case and may use ``*`` for any letters: ``mog`` blocks only
    that word, ``*mog*`` blocks every word containing it.
    """

    def __init__(self, entries: Iterable[str]):
        self.entries = tuple(map(small, entries))

    def applies_at(self, level: int) -> bool:
        return True

    def check(self, letters: Sequence[str], text: str) -> Rejection | None:
        lower = small(text)
        for entry in self.entries:
            if fnmatchcase(lower, entry):
                return Rejection(WORD_LIST_FAILURE, f"it matches '{entry}' in the word list")
        return None


class RepeatedSequenceCheck:
    """Rejects a word in which any four-letter sequence occurs twice (level 1+).

    Overlapping occurrences count, so AAAAA fails. The whole word is checked, to its
    last letter.
    """

    def applies_at(self, level: int) -> bool:
        return level >= 1

    def check(self, letters: Sequence[str], text: str) -> Rejection | None:
        seen: set[tuple[str, ...]] = set()
        for start in range(len(letters) - REPEAT_LENGTH + 1):
            sequence = tuple(letters[start : start + REPEAT_LENGTH])
            if sequence in seen:
                return Rejection(REPEAT_FAILURE, f"the letters {''.join(sequence)} appear twice")
            seen.add(sequence)
        return None


class LetterSpacingCheck:
    """Rejects a word where a letter repeats closer than the seeds allow (level 1+).

    Doubled letters, such as NN, are ignored.
    """

    def __init__(self, limits: LetterLimits):
        self.limits = limits

    def applies_at(self, level: int) -> bool:
        return level >= 1

    def check(self, letters: Sequence[str], text: str) -> Rejection | None:
        for letter, gap in smallest_gaps(letters).items():
            limit = self.limits.distance_for(letter)
            if gap < limit:
                return Rejection(
                    f"{DISTANCE_FAILURE}/{letter}",
                    f"two {letter}s are {gap} apart; in the seeds they are at least {limit} apart",
                )
        return None


class LetterFrequencyCheck:
    """Rejects a word where a repeated letter fills more of it than in any seed (level 2+).

    Letters that appear only once are never flagged.
    """

    def __init__(self, limits: LetterLimits):
        self.limits = limits

    def applies_at(self, level: int) -> bool:
        return level >= 2

    def check(self, letters: Sequence[str], text: str) -> Rejection | None:
        for letter, share in repeated_letter_shares(letters).items():
            limit = self.limits.share_for(letter)
            if share > limit:
                return Rejection(
                    f"{FREQUENCY_FAILURE}/{letter}",
                    f"{letter} makes up {share:.0%} of the word; in the seeds at most {limit:.0%}",
                )
        return None


class Validator:
    """Runs a chain of checks; the first one a word fails decides the rejection."""

    def __init__(self, checks: Sequence[WordCheck]):
        self.checks = tuple(checks)

    @classmethod
    def classic(cls, analysis: StyleAnalysis, word_list: Iterable[str] = ()) -> Validator:
        """The checks, in the order the spec lists them."""
        limits = analysis.letter_limits
        return cls([
            WordListCheck(word_list),
            RepeatedSequenceCheck(),
            LetterSpacingCheck(limits),
            LetterFrequencyCheck(limits),
        ])  # fmt: skip

    def check(self, letters: Sequence[str], text: str, level: int) -> Rejection | None:
        for word_check in self.checks:
            if word_check.applies_at(level):
                rejection = word_check.check(letters, text)
                if rejection:
                    return rejection
        return None


def load_word_list(path: str | Path) -> tuple[str, ...]:
    """Read a word list file: one entry per line; blank lines and lines starting with # are skipped.

    The file is read as UTF-8, or as Windows-1252 if it is not UTF-8.
    Look-alike apostrophes, hyphens and spaces are read as plain ones, as in
    names (spec 13.12). Raises ``StyleError`` if it cannot be read or is too
    large.
    """
    data = read_file(Path(path))
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    entries = []
    for line in unicodedata.normalize("NFC", text).splitlines():
        line = plain_name(line)
        if line and not line.startswith("#"):
            entries.append(line)
    return tuple(entries)
