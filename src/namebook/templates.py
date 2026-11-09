"""Templates: recipes for whole names (spec 7.1).

A recipe is written as in chapter files:

- a number (one or two digits) is a word slot of that structure; ``9|13``
  allows either structure, and ``#`` allows any structure;
- a space separates words;
- ``_`` joins two word slots into one word, keeping only one copy of a letter
  that ends the first and begins the second;
- two slots written back to back (``0507``) are joined without that merging;
- any other character is copied into the name as written: ``5'7`` can give
  Renho'Delar.

``parse_recipe`` turns the text into parts; ``assemble`` puts generated
words and the literal text together into the finished name.

A combination says how a style's lists are put together (spec 13.5): a
list's name in braces stands for a name from that list, made with the
list's own templates, and any other text is copied as written, so
``{given} {family}`` can give Aric Thornwood. ``parse_combination`` turns it
into parts.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from namebook.letters import small
from namebook.punctuation import punctuate
from namebook.style import Punctuation, Template
from namebook.words import HYPHEN, SPACE

ANY_STRUCTURE = "#"
ALTERNATIVE = "|"
MERGE_JOIN = "_"
MAX_DIGITS = 2
LIST_OPEN = "{"
LIST_CLOSE = "}"


class TemplateError(ValueError):
    """A recipe or combination that is not written correctly. The message
    completes "Template X ..." or "Combination X ..."."""


@dataclass(frozen=True)
class Slot:
    """A place for one generated word. ``structures`` is None for ``#`` (any structure)."""

    structures: tuple[int, ...] | None

    def describe(self) -> str:
        if self.structures is None:
            return ANY_STRUCTURE
        return ALTERNATIVE.join(str(s) for s in self.structures)


@dataclass(frozen=True)
class Text:
    """Characters copied into the name as written."""

    text: str


@dataclass(frozen=True)
class Space:
    """A space between two words of the name."""


@dataclass(frozen=True)
class Join:
    """Two slots joined into one word. With ``merge``, a shared letter is kept once."""

    merge: bool


Part = Slot | Text | Space | Join


@dataclass(frozen=True)
class Recipe:
    text: str
    parts: tuple[Part, ...]

    @property
    def slots(self) -> tuple[Slot, ...]:
        return tuple(part for part in self.parts if isinstance(part, Slot))

    @property
    def starts_with_slot(self) -> bool:
        return isinstance(self.parts[0], Slot)

    @property
    def joints(self) -> tuple[str, ...]:
        """What follows each slot but the last, as the Markov chain reads it: a
        hyphen when only "-" stands between the slot and the next (spec
        13.15), otherwise a space. Other literal text and joins are not shown
        to the chain."""
        joints: list[str] = []
        between: list[Part] | None = None
        for part in self.parts:
            if isinstance(part, Slot):
                if between is not None:
                    joints.append(HYPHEN if between == [Text(HYPHEN)] else SPACE)
                between = []
            elif between is not None:
                between.append(part)
        return tuple(joints)

    @classmethod
    def single_slot(cls, structures: Sequence[int] | None) -> Recipe:
        slot = Slot(None if structures is None else tuple(structures))
        return cls(slot.describe(), (slot,))


def parse_recipe(text: str) -> Recipe:
    """Turn a recipe such as ``"5'7"`` or ``"9|13 6"`` into its parts."""
    parts: list[Part] = []
    pos = 0
    while pos < len(text):
        char = text[pos]
        if char.isascii() and char.isdigit() or char == ANY_STRUCTURE:
            slot, pos = _read_slot(text, pos)
            if parts and isinstance(parts[-1], Slot):
                parts.append(Join(merge=False))  # back to back, as in 0507
            parts.append(slot)
        elif char == ALTERNATIVE:
            raise TemplateError(f"has a '{ALTERNATIVE}' that is not between two structure numbers")
        elif char == MERGE_JOIN:
            following = text[pos + 1 : pos + 2]
            if not (parts and isinstance(parts[-1], Slot)) or not _starts_slot(following):
                raise TemplateError(
                    f"has a '{MERGE_JOIN}' that is not directly between two word slots"
                )
            parts.append(Join(merge=True))
            pos += 1
        elif char.isspace():
            if parts and not isinstance(parts[-1], Space):
                parts.append(Space())
            pos += 1
        else:
            if parts and isinstance(parts[-1], Text):
                parts[-1] = Text(parts[-1].text + char)
            else:
                parts.append(Text(char))
            pos += 1
    while parts and isinstance(parts[-1], Space):
        parts.pop()
    if not any(isinstance(part, Slot) for part in parts):
        raise TemplateError("has no word slot (a structure number or #)")
    return Recipe(text, tuple(parts))


def _starts_slot(char: str) -> bool:
    return char == ANY_STRUCTURE or (char.isascii() and char.isdigit())


def _read_slot(text: str, pos: int) -> tuple[Slot, int]:
    if text[pos] == ANY_STRUCTURE:
        if text[pos + 1 : pos + 2] == ALTERNATIVE:
            raise TemplateError(f"has '{ANY_STRUCTURE}{ALTERNATIVE}': # already allows any structure")
        return Slot(None), pos + 1
    structures = []
    while True:
        number, pos = _read_number(text, pos)
        structures.append(number)
        if text[pos : pos + 1] != ALTERNATIVE:
            return Slot(tuple(structures)), pos
        pos += 1
        if not (text[pos : pos + 1].isascii() and text[pos : pos + 1].isdigit()):
            raise TemplateError(f"has a '{ALTERNATIVE}' that is not between two structure numbers")


def _read_number(text: str, pos: int) -> tuple[int, int]:
    """Read one structure number of up to two digits: 0507 is 05 then 07."""
    end = pos
    while end < len(text) and end - pos < MAX_DIGITS and text[end].isascii() and text[end].isdigit():
        end += 1
    return int(text[pos:end]), end


@dataclass(frozen=True)
class ListPart:
    """A place for a name made from one of the style's lists."""

    name: str


@dataclass(frozen=True)
class Combination:
    """How a style's lists are put together: names from lists, and text
    copied as written."""

    text: str
    parts: tuple[ListPart | Text, ...]

    @property
    def lists(self) -> tuple[str, ...]:
        """The lists a name is made from, in order, as often as each is used."""
        return tuple(part.name for part in self.parts if isinstance(part, ListPart))

    @property
    def starts_with_list(self) -> bool:
        return isinstance(self.parts[0], ListPart)


def parse_combination(text: str) -> Combination:
    """Turn a combination such as ``"{given} {family}"`` into its parts.

    Each run of spaces in the text between lists is kept as one space, and
    spaces at either end are left out.
    """
    parts: list[ListPart | Text] = []
    literal = ""
    pos = 0
    while pos < len(text):
        char = text[pos]
        if char == LIST_OPEN:
            end = text.find(LIST_CLOSE, pos + 1)
            name = text[pos + 1 : end].strip()
            if end < 0 or LIST_OPEN in name:
                raise TemplateError(f"has a '{LIST_OPEN}' with no '{LIST_CLOSE}' to close it")
            if not name:
                raise TemplateError(f"has '{LIST_OPEN}{LIST_CLOSE}' with no list's name in it")
            if literal:
                parts.append(Text(literal))
                literal = ""
            parts.append(ListPart(name))
            pos = end + 1
        elif char == LIST_CLOSE:
            raise TemplateError(f"has a '{LIST_CLOSE}' with no '{LIST_OPEN}' before it")
        else:
            if not (char.isspace() and literal.endswith(" ")):
                literal += " " if char.isspace() else char
            pos += 1
    if literal:
        parts.append(Text(literal))
    if not any(isinstance(part, ListPart) for part in parts):
        raise TemplateError(
            f"has no list: write a list's name in braces, such as {LIST_OPEN}given{LIST_CLOSE}"
        )
    if isinstance(parts[0], Text):
        parts[0] = Text(parts[0].text.lstrip())
    if isinstance(parts[-1], Text):
        parts[-1] = Text(parts[-1].text.rstrip())
    return Combination(text.strip(), tuple(part for part in parts if part != Text("")))


def combination_problem(combination: Template, lists: Sequence[str]) -> str | None:
    """Why a combination cannot be used by a style with these lists, as words
    that complete "Combination X ..."; None if it can."""
    try:
        parsed = parse_combination(combination.recipe)
    except TemplateError as error:
        return str(error)
    unknown = [name for name in dict.fromkeys(parsed.lists) if name not in lists]
    if unknown:
        return f'has "{unknown[0]}", which is not one of the style\'s lists ({", ".join(lists)})'
    if combination.weight <= 0:
        return "has weight 0, so it is never used"
    return None


def parse_template_text(text: str) -> Template:
    """``"5'7:3"`` → recipe ``5'7`` with weight 3. Without ``:weight`` the weight is 1."""
    match = re.fullmatch(r"(.*):(\d+)", text.strip())
    if match:
        return Template(match.group(1).strip(), int(match.group(2)))
    return Template(text.strip(), 1)


def assemble(
    recipe: Recipe,
    words: Sequence[Sequence[str]],
    punctuation: Punctuation = Punctuation.NONE,
) -> tuple[str, str]:
    """Put a name together from the letters of its words, one word per slot.

    Returns the name as built (words in uppercase, before punctuation) and the
    finished name. Each word, or group of joined words, is punctuated and
    capitalized; literal text is copied as written. A literal character that
    is not a letter is skipped when the name already ends with it.
    """
    built: list[str] = []
    finished: list[str] = []
    unit: list[str] | None = None  # letters of the word being put together
    join: Join | None = None
    remaining = iter(words)

    def finish_unit() -> None:
        nonlocal unit
        if unit:
            text = "".join(unit)
            built.append(text)
            finished.append(capitalize(punctuate(text, punctuation)))
        unit = None

    def ends_with(char: str) -> bool:
        return bool(finished) and finished[-1].endswith(char)

    for part in recipe.parts:
        if isinstance(part, Slot):
            letters = list(next(remaining))
            if join is not None and unit:
                if join.merge and unit[-1] == letters[0]:
                    letters = letters[1:]
                unit.extend(letters)
            else:
                finish_unit()
                unit = letters
            join = None
        elif isinstance(part, Join):
            join = part
        elif isinstance(part, Space):
            finish_unit()
            if finished and not ends_with(" "):
                built.append(" ")
                finished.append(" ")
        else:
            finish_unit()
            for char in part.text:
                if not char.isalpha() and ends_with(char):
                    continue
                built.append(char)
                finished.append(char)
    finish_unit()
    return "".join(built).strip(), "".join(finished).strip()


def capitalize(text: str) -> str:
    """First letter uppercase, the rest lowercase: ``"AALBE'IL"`` becomes ``"Aalbe'il"``.

    A first letter already in capitals stays as it is, so İ keeps its dot;
    the Turkish ı, kept small (``letters.capital``), becomes I. A letter with
    no capital, the ʻokina, is passed over: ``"ʻŌLELO"`` becomes ``"ʻŌlelo"``.
    """
    lower = small(text)
    for index, char in enumerate(text):
        if char.isalpha() and char.upper() != char.lower():
            first = char if char.isupper() else char.upper()
            return lower[:index] + first + lower[index + 1 :]
    return lower
