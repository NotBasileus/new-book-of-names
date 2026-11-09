"""Helpers shared by the file readers: reading files safely, wording for
problems, and special letters."""

from __future__ import annotations

import difflib
import unicodedata
from collections import Counter
from pathlib import Path

from namebook.letters import LetterClasses, capital
from namebook.style import StyleError

# The largest file namebook reads, so a huge or wrong file cannot freeze it
# (spec 10.2). A list of 50,000 names takes well under 1 MB.
MAX_FILE_SIZE = 5_000_000

# Characters that are invisible or change how text is displayed. They are
# never needed in a style, and could hide or disguise what a file says.
HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})
TEXT_DIRECTION = frozenset("؜‎‏‪‫‬‭‮⁦⁧⁨⁩")

# Characters that look like the apostrophe, hyphen or space, and the plain
# one each is read as in names (spec 13.12). Lists copied from web pages and
# word processors have them: D’Arcy, Jean–Luc, non-breaking spaces.
LOOK_ALIKES = {
    # Single quotation marks, the modifier letter apostrophe, the prime, and
    # the acute and grave accents typed on their own.
    **dict.fromkeys("‘’‛ʼ′´`", "'"),
    # Unicode's own hyphen and non-breaking hyphen, the figure dash, the en
    # and em dashes, the horizontal bar and the minus sign.
    **dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015\u2212", "-"),
    # Every space but the plain one: the no-break spaces, the spaces of other
    # widths, and the Ogham and ideographic spaces.
    **dict.fromkeys("\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
                    "\u2007\u2008\u2009\u200a\u202f\u205f\u3000", " "),
}
_PLAIN = str.maketrans(LOOK_ALIKES)
_PLAIN_NAMES = {"'": ("apostrophe", "apostrophes"), "-": ("hyphen", "hyphens"), " ": ("space", "spaces")}


def read_file(path: Path) -> bytes:
    """Read a file's bytes, refusing a file larger than ``MAX_FILE_SIZE``."""
    try:
        with path.open("rb") as file:
            data = file.read(MAX_FILE_SIZE + 1)
    except OSError as error:
        reason = error.strerror or str(error)
        raise StyleError(str(path), [f"Could not open the file: {reason}."]) from error
    if len(data) > MAX_FILE_SIZE:
        raise StyleError(
            str(path),
            [f"The file is larger than {MAX_FILE_SIZE // 1_000_000} MB, the most namebook reads. "
             "Is it the right file?"],
        )  # fmt: skip
    return data


def hidden_characters(text: str) -> list[str]:
    """The invisible and display-control characters in ``text``, each listed once."""
    return list(dict.fromkeys(char for char in text if unicodedata.category(char) in HIDDEN_CATEGORIES))


def describe_char(char: str) -> str:
    code = f"U+{ord(char):04X}"
    if char == "�":
        return "an unreadable character"
    if char in TEXT_DIRECTION:
        return f"a text-direction character ({code})"
    category = unicodedata.category(char)
    if category == "Cc":
        return f"a control character ({code})"
    if category in HIDDEN_CATEGORIES:
        return f"an invisible character ({code})"
    if char.isprintable() and not char.isspace():
        return f"'{char}'"
    return f"character {code}"


def describe_chars(chars: list[str]) -> str:
    described = [describe_char(char) for char in chars]
    if len(described) == 1:
        return described[0]
    return ", ".join(described[:-1]) + " and " + described[-1]


def shorten(text: str, limit: int = 30) -> str:
    """``text`` for quoting in a message: on one line, cut short if long, and
    with hidden characters shown as ``?`` so they cannot disguise the message."""
    text = " ".join(text.split())
    text = "".join("?" if unicodedata.category(char) in HIDDEN_CATEGORIES else char for char in text)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def suggestion(word: str, choices) -> str:
    matches = difflib.get_close_matches(word, list(choices), n=1)
    return f" Did you mean {matches[0]}?" if matches else ""


def split_names(text: str, made_plain: Counter[str] | None = None) -> list[str]:
    """Split a block of names written one per line or separated by commas.

    Blank lines, and lines starting with ``#`` (comments), are skipped. Each
    name is read by ``plain_name``.
    """
    names: list[str] = []
    for line in text.splitlines():
        if line.strip().startswith("#"):
            continue
        for part in line.split(","):
            name = plain_name(part, made_plain)
            if name:
                names.append(name)
    return names


def plain_name(text: str, made_plain: Counter[str] | None = None) -> str:
    """``text`` as a name: look-alike characters read as the plain apostrophe,
    hyphen or space (spec 13.12), and each run of spaces made one.

    The look-alikes are counted in ``made_plain``, by the plain character.
    """
    text = text.strip()
    if made_plain is not None:
        made_plain.update(LOOK_ALIKES[char] for char in text if char in LOOK_ALIKES)
    return " ".join(text.translate(_PLAIN).split())


def look_alike_warnings(made_plain: Counter[str]) -> list[str]:
    """A warning saying how many look-alike characters the names had, if any."""
    total = made_plain.total()
    if not total:
        return []
    counts = []
    for char, (one, many) in _PLAIN_NAMES.items():
        if made_plain[char]:
            counts.append(f"{made_plain[char]} {one if made_plain[char] == 1 else many}")
    listed = ", ".join(counts[:-1]) + " and " + counts[-1] if len(counts) > 1 else counts[0]
    if total == 1:
        return [f"{listed} in the names was typed as a look-alike character, and is read as a plain one."]
    return [f"{listed} in the names were typed as look-alike characters, and are read as plain ones."]


def soften(cluster: str, letters: LetterClasses) -> str:
    """Rewrite each uppercase consonant + H in ``cluster`` as a lowercase soft letter.

    This is how chapter files write a soft consonant inside a cluster (spec 4.1):
    ``"LTH"`` becomes ``"Lt"``.
    """
    result: list[str] = []
    pos = 0
    while pos < len(cluster):
        char = cluster[pos]
        following = cluster[pos + 1 : pos + 2]
        if following == "H" and char != "H" and char.isalpha() and letters.is_consonant(char):
            result.append(char.lower())
            pos += 2
        else:
            result.append(char)
            pos += 1
    return "".join(result)


def harden(cluster: str) -> str:
    """Write each soft letter in ``cluster`` out in full, undoing ``soften``: ``"Lt"`` is ``"LTH"``."""
    return "".join(capital(char) + "H" if char.islower() else char for char in cluster)
