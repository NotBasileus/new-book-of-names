"""The neutral style object.

A style is everything needed to make names of one kind: a title and
description, generation options, special letters, templates and one or more
lists of seed names. File readers (``namebook.formats``) turn a file into a
``Style``; everything else works from the ``Style`` and never from the file.

A style with several lists, such as given and family names, makes names
from the first unless a combination puts them together another way (spec
13.5).
Each list is made from as a style of its own (``Style.list_style``): its
names, with the settings pinned for it alone over the style's.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, IntFlag
from pathlib import Path

from namebook.letters import APOSTROPHE, LATIN_LETTERS, LetterClasses

MAX_FIT_LEVEL = 3
MAX_VALIDATION_LEVEL = 2
MAX_LENGTH = 100  # the largest length limit a style may set, in letters
MAX_LOOK_BACK = 5  # the most letters the Markov chain looks back (spec 13.2)
DEFAULT_MARKOV_STRENGTH = 1.0  # the fullest; lowering it did not make better names (D81)

# What a style's name list is called when there is only one.
DEFAULT_LIST = "list"

# Every setting a style can pin, by its name in style files, in the order
# style files list them (spec 10.3).
SETTINGS = (
    "soft_consonants",
    "consonant_clusters",
    "multi_vowels",
    "letter_y",
    "letter_u",
    "letter_apostrophe",
    "letter_hyphen",
    "structural",
    "statistical",
    "fit_level",
    "prefixes",
    "suffixes",
    "shuffle",
    "validation_level",
    "refuse_copies",
    "markov",
    "markov_order",
    "markov_strength",
    "templates",
    "min_length",
    "max_length",
    "punctuation",
)

# Where each setting is kept: settings that are GenerationOptions fields (by
# field name), and those that are SpecialLetters fields. "templates" is the
# style's own field.
OPTION_FIELDS = {
    "structural": "structural",
    "statistical": "statistical",
    "fit_level": "fit_level",
    "validation_level": "validation_level",
    "refuse_copies": "refuse_copies",
    "prefixes": "prefixes",
    "suffixes": "suffixes",
    "shuffle": "shuffle",
    "punctuation": "punctuation",
    "letter_y": "y_mode",
    "letter_u": "u_mode",
    "letter_hyphen": "hyphen",
    "min_length": "min_length",
    "max_length": "max_length",
    "markov": "markov",
    "markov_order": "markov_order",
    "markov_strength": "markov_strength",
}
LETTER_FIELDS = ("soft_consonants", "consonant_clusters", "multi_vowels")
# "letter_apostrophe" is kept in the style's letter classes: the apostrophe's class.


class StyleError(Exception):
    """A style could not be read. ``problems`` lists everything found wrong."""

    def __init__(self, source: str, problems: list[str]):
        self.source = source
        self.problems = tuple(problems)
        super().__init__(f"{source}: " + "; ".join(self.problems))


class YMode(Enum):
    """How the letter Y is classified (spec 4.3)."""

    VOWEL = "vow"
    CONSONANT = "con"
    SEMI = "semi"


class UMode(Enum):
    """How the letter U is classified (spec 4.3)."""

    VOWEL = "vow"
    SEMI = "semi"


class ApostropheMode(Enum):
    """How the apostrophe is classified: a vowel letter, the usual choice (spec 2.3), or a consonant letter."""

    VOWEL = "vowel"
    CONSONANT = "consonant"


class HyphenMode(Enum):
    """What the hyphen is: a consonant letter, the usual choice (spec 2.3), or a
    joint between two words of a name, as a space is (spec 13.15)."""

    CONSONANT = "consonant"
    JOINT = "joint"


class Markov(Enum):
    """How a word is built (spec 13.3): the classic way, or guided by a Markov chain."""

    OFF = "off"
    WEIGHTED = "weighted"  # the classic filter, with the chain weighing the allowed elements
    FILL = "fill"  # the classic word shape, filled letter by letter from the chain


class Punctuation(IntFlag):
    """Punctuation modes (spec 7.6). Several can be combined."""

    NONE = 0
    ACCENTS = 1
    DIAERESIS = 2  # once called the Quenya mode
    VOWEL_HARMONY = 4  # once called the Finnish mode


@dataclass(frozen=True)
class GenerationOptions:
    """Generation options (spec section 3). The defaults are the classic ones.

    ``min_length`` and ``max_length`` limit how many letters a name has
    (spec 10.5); 0 means no limit.

    ``markov`` chooses how words are built (spec 13.3). ``markov_order`` is
    how many letters the chain looks back, 0 to work it out from the names;
    ``markov_strength``, from 0 to 1, how much the chain counts.
    """

    structural: bool = False
    statistical: bool = False
    fit_level: int = MAX_FIT_LEVEL
    validation_level: int = MAX_VALIDATION_LEVEL
    prefixes: bool = False
    suffixes: bool = False
    shuffle: bool = False
    punctuation: Punctuation = Punctuation.NONE
    refuse_copies: bool = False
    y_mode: YMode = YMode.VOWEL
    u_mode: UMode = UMode.VOWEL
    hyphen: HyphenMode = HyphenMode.CONSONANT
    min_length: int = 0
    max_length: int = 0
    markov: Markov = Markov.OFF
    markov_order: int = 0
    markov_strength: float = DEFAULT_MARKOV_STRENGTH

    def __post_init__(self) -> None:
        if not 0 <= self.fit_level <= MAX_FIT_LEVEL:
            raise ValueError(f"fit level must be 0 to {MAX_FIT_LEVEL}, not {self.fit_level}")
        if not 0 <= self.validation_level <= MAX_VALIDATION_LEVEL:
            raise ValueError(
                f"validation level must be 0 to {MAX_VALIDATION_LEVEL}, "
                f"not {self.validation_level}"
            )
        for name in ("min_length", "max_length"):
            if not 0 <= getattr(self, name) <= MAX_LENGTH:
                raise ValueError(f"{name} must be 0 to {MAX_LENGTH}, not {getattr(self, name)}")
        if self.min_length and self.max_length and self.min_length > self.max_length:
            raise ValueError(f"min_length {self.min_length} is more than max_length {self.max_length}")
        if not 0 <= self.markov_order <= MAX_LOOK_BACK:
            raise ValueError(f"markov_order must be 0 to {MAX_LOOK_BACK}, not {self.markov_order}")
        if not 0 <= self.markov_strength <= 1:
            raise ValueError(f"markov_strength must be 0 to 1, not {self.markov_strength}")

    @property
    def length_range(self) -> tuple[int, int] | None:
        """The letters a name may have, as (fewest, most), or None without a limit."""
        if not (self.min_length or self.max_length):
            return None
        return self.min_length, self.max_length or MAX_LENGTH


@dataclass(frozen=True)
class SpecialLetters:
    """Multi-character sequences treated as one letter (spec section 4).

    ``consonant_clusters`` are written as in chapter files: uppercase letters, with a
    soft consonant written as its lowercase first letter (``"Lt"`` is L
    followed by TH).
    """

    soft_consonants: bool = False
    consonant_clusters: tuple[str, ...] = ()
    multi_vowels: bool = False


@dataclass(frozen=True)
class Template:
    """A recipe for a full name and its weight (spec 7.1), or for how a
    style's lists are put together, a combination (spec 13.5).

    The recipe is kept as written, e.g. ``"9|13"``, ``"5'7"`` or ``"{given}
    {family}"``; it is interpreted when names are generated.
    """

    recipe: str
    weight: int = 1


@dataclass(frozen=True)
class NameList:
    """A named list of seed names, kept exactly as written in the style, and
    the settings pinned for this list alone, by their names in ``SETTINGS``."""

    name: str
    names: tuple[str, ...]
    settings: Mapping[str, object] = field(default_factory=dict, hash=False)


@dataclass(frozen=True)
class StyleSource:
    """Where a style came from, and any warnings raised while reading it."""

    path: Path | None
    format: str
    encoding: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Style:
    """A style: seed names plus everything that controls how names are made.

    ``pinned`` names the settings (from ``SETTINGS``) that the style sets
    itself, for every list. A style as read from a file holds defaults for
    the others; ``namebook.inference`` works them out from the names (spec
    10.3). Each list may pin settings of its own as well.
    """

    title: str
    name_lists: tuple[NameList, ...]
    description: tuple[str, ...] = ()
    options: GenerationOptions = field(default_factory=GenerationOptions)
    special_letters: SpecialLetters = field(default_factory=SpecialLetters)
    templates: tuple[Template, ...] = ()
    letter_classes: LetterClasses = LATIN_LETTERS
    pinned: frozenset[str] = frozenset()
    source: StyleSource | None = None

    def __post_init__(self) -> None:
        if not self.name_lists:
            raise ValueError("a style needs at least one name list")
        list_names = [name_list.name for name_list in self.name_lists]
        if len(set(list_names)) != len(list_names):
            raise ValueError(f"name lists must have different names: {list_names}")
        unknown = (self.pinned | {key for name_list in self.name_lists for key in name_list.settings}) - set(
            SETTINGS
        )
        if unknown:
            raise ValueError(f"unknown settings cannot be pinned: {sorted(unknown)}")

    @property
    def names(self) -> tuple[str, ...]:
        """The seed names of the first (default) name list."""
        return self.name_lists[0].names

    @property
    def list_names(self) -> tuple[str, ...]:
        return tuple(name_list.name for name_list in self.name_lists)

    @property
    def default_combination(self) -> Template:
        """How names are made unless another way is asked for: from the first
        list alone (spec 13.5), since a style's lists may be alternatives,
        such as male and female names, rather than parts of one name (D97)."""
        return Template(f"{{{self.list_names[0]}}}")

    def list_style(self, name: str) -> Style:
        """One list as a style of its own, as it is analyzed and made from
        (spec 13.5): its names, with the settings it pins over the style's."""
        name_list = self.name_list(name)
        style = self.with_settings(name_list.settings)
        return dataclasses.replace(
            style,
            name_lists=(NameList(name_list.name, name_list.names),),
            pinned=self.pinned | frozenset(name_list.settings),
        )

    def setting(self, key: str) -> object:
        """The value of one setting, by its name in ``SETTINGS``."""
        if key in OPTION_FIELDS:
            return getattr(self.options, OPTION_FIELDS[key])
        if key in LETTER_FIELDS:
            return getattr(self.special_letters, key)
        if key == "templates":
            return self.templates
        if key == "letter_apostrophe":
            consonant = self.letter_classes.is_consonant(APOSTROPHE)
            return ApostropheMode.CONSONANT if consonant else ApostropheMode.VOWEL
        raise KeyError(key)

    def with_settings(self, values: Mapping[str, object]) -> Style:
        """This style with some settings changed, given by their names in ``SETTINGS``."""
        unknown = set(values) - set(SETTINGS)
        if unknown:
            raise KeyError(f"unknown settings: {sorted(unknown)}")
        options = {OPTION_FIELDS[key]: value for key, value in values.items() if key in OPTION_FIELDS}
        letters = {key: value for key, value in values.items() if key in LETTER_FIELDS}
        letter_classes = self.letter_classes
        if "letter_apostrophe" in values:
            vowel = values["letter_apostrophe"] is ApostropheMode.VOWEL
            letter_classes = letter_classes.moved(APOSTROPHE, vowel)
        return dataclasses.replace(
            self,
            options=dataclasses.replace(self.options, **options),
            special_letters=dataclasses.replace(self.special_letters, **letters),
            templates=values.get("templates", self.templates),
            letter_classes=letter_classes,
        )

    def name_list(self, name: str) -> NameList:
        """Return the name list called ``name``."""
        for name_list in self.name_lists:
            if name_list.name == name:
                return name_list
        raise KeyError(name)


def pin_working_settings(style: Style, working: Mapping[str, Style]) -> Style:
    """``style`` with every setting pinned at its working value, as ``create
    --pin`` saves it (D52): the settings every list has alike are pinned for
    the whole style, and the rest for each list. ``working`` holds each
    list's working settings, as a style of one list, by the list's name."""
    workings = [working[name] for name in style.list_names]
    first = workings[0]
    shared = frozenset(key for key in SETTINGS if all(w.setting(key) == first.setting(key) for w in workings))
    name_lists = tuple(
        NameList(name_list.name, name_list.names, {key: w.setting(key) for key in SETTINGS if key not in shared})
        for name_list, w in zip(style.name_lists, workings)
    )
    return dataclasses.replace(first, name_lists=name_lists, pinned=shared)
