"""The style being edited in the window (spec 13.9).

A ``Workspace`` holds a style as the window shows it: its title,
description and lists of names, as typed, and every setting of every list,
with where its value comes from. Nothing here touches a file until
``save``, and nothing is inferred again until ``infer`` (D92):

- loading a style infers every setting it does not pin, as the commands
  do;
- changing a setting pins it for every list, unless the list it is changed
  in pins it for itself alone;
- pinning a setting keeps each list's value: one pin for the whole style
  when the lists have the same value, and one for each list when they
  differ, as ``create --pin`` saves them. ``pin_all`` pins every setting
  this way, and ``unpin_all`` removes every pin;
- a setting unpinned keeps its value until the settings are inferred
  again.

Names are made from the values shown, pinned or inferred, even when the
names have changed since they were inferred, so that what the window
shows is what names are made with.
"""

from __future__ import annotations

import random
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from namebook.analysis import StyleAnalysis, analyze
from namebook.combining import CombinedName, Combiner
from namebook.formats import load_style, save_style
from namebook.formats.check import check_style
from namebook.formats.common import harden, look_alike_warnings, soften, split_names
from namebook.generator import NameGenerator, SkippedTemplate
from namebook.inference import Inference, infer_lists
from namebook.style import DEFAULT_LIST, SETTINGS, NameList, Style, StyleError, Template
from namebook.templates import LIST_CLOSE, LIST_OPEN, combination_problem, parse_combination
from namebook.validation import Validator

NEW_TITLE = "New style"
SAVED_TYPE = ".toml"  # the only kind of file the window saves a style as

# The value each setting has before it is inferred, as a style holds it.
_BLANK = Style(NEW_TITLE, (NameList(DEFAULT_LIST, ()),))
DEFAULTS = {key: _BLANK.setting(key) for key in SETTINGS}

NOT_INFERRED = "Not pinned: it is inferred from the names when you press Infer optimum settings."


class Source(Enum):
    """Where a setting's value comes from."""

    PINNED = "pinned"  # pinned for every list
    LIST_PINNED = "pinned for this list"  # pinned for one list alone
    INFERRED = "inferred"  # inferred from the names
    NOT_INFERRED = "not inferred"  # not pinned, and not inferred since


@dataclass(frozen=True)
class Setting:
    """A setting's value in one list, where it comes from, and why it has it."""

    value: object
    source: Source
    reason: str

    @property
    def pinned(self) -> bool:
        return self.source in (Source.PINNED, Source.LIST_PINNED)


@dataclass(frozen=True)
class Batch:
    """Names made in the window, the seed they were made with, and notes to show."""

    names: list[CombinedName]
    seed: int
    notes: list[str]


class Workspace:
    """A style being edited: names as typed, and each setting with its source."""

    def __init__(
        self,
        title: str = NEW_TITLE,
        lists: Mapping[str, str] | None = None,
        description: str = "",
        path: Path | None = None,
    ):
        self.title = title
        self.description = description
        self.path = path
        self._names: dict[str, str] = dict(lists or {DEFAULT_LIST: ""})  # as typed, by list
        self._pinned: dict[str, object] = {}  # for every list
        self._list_pinned: dict[str, dict[str, object]] = {name: {} for name in self._names}
        self._inferred: dict[str, dict[str, Setting]] = {name: {} for name in self._names}
        self._analyses: dict[Style, StyleAnalysis] = {}  # kept between batches
        self.changed = False  # since loaded or saved
        self.names_changed = bool(lists)  # since the settings were inferred
        self.warnings: tuple[str, ...] = ()  # from reading the file

    @classmethod
    def load(cls, path: str | Path) -> Workspace:
        """A style file, chapter or plain list, as read. Raises StyleError.

        Nothing is inferred yet: ``infer`` does that.
        """
        path = Path(path)
        return cls.from_style(load_style(path), path)

    @classmethod
    def from_style(cls, style: Style, path: Path | None = None) -> Workspace:
        lists = {name_list.name: "\n".join(name_list.names) for name_list in style.name_lists}
        workspace = cls(style.title, lists, "\n".join(style.description), path)
        workspace._pinned = {key: _kept(key, style.setting(key)) for key in style.pinned}
        for name_list in style.name_lists:
            own = style.list_style(name_list.name)
            workspace._list_pinned[name_list.name] = {key: _kept(key, own.setting(key)) for key in name_list.settings}
        workspace.warnings = style.source.warnings if style.source else ()
        return workspace

    # Lists and names.

    @property
    def list_names(self) -> list[str]:
        return list(self._names)

    @property
    def default_combination(self) -> Template:
        """The first list alone, as ``Style.default_combination``."""
        return Template(f"{LIST_OPEN}{self.list_names[0]}{LIST_CLOSE}")

    def names_text(self, list_name: str) -> str:
        return self._names[list_name]

    def set_names_text(self, list_name: str, text: str) -> None:
        if text != self._names[list_name]:
            self._names[list_name] = text
            self.changed = self.names_changed = True

    def names(self, list_name: str, made_plain: Counter[str] | None = None) -> tuple[str, ...]:
        """A list's names, read from the text as a style file's are."""
        return tuple(split_names(unicodedata.normalize("NFC", self._names[list_name]), made_plain))

    def list_name_problem(self, name: str) -> str | None:
        """Why ``name`` cannot be a new list's name, or None."""
        if not name.strip():
            return "A list needs a name."
        if LIST_OPEN in name or LIST_CLOSE in name:
            return f"A list's name cannot have {LIST_OPEN} or {LIST_CLOSE}."
        if name in self._names:
            return f'There is already a list called "{name}".'
        return None

    def add_list(self, name: str) -> None:
        if problem := self.list_name_problem(name):
            raise ValueError(problem)
        self._names[name] = ""
        self._list_pinned[name] = {}
        self._inferred[name] = {}
        self.changed = self.names_changed = True

    def rename_list(self, old: str, new: str) -> None:
        if new == old:
            return
        if problem := self.list_name_problem(new):
            raise ValueError(problem)
        for table in (self._names, self._list_pinned, self._inferred):
            renamed = {new if key == old else key: value for key, value in table.items()}
            table.clear()
            table.update(renamed)
        self.changed = True

    def remove_list(self, name: str) -> None:
        if len(self._names) == 1:
            raise ValueError("A style needs at least one list.")
        for table in (self._names, self._list_pinned, self._inferred):
            del table[name]
        self.changed = True

    # Settings.

    def setting(self, list_name: str, key: str) -> Setting:
        """One setting's value in one list, and where it comes from."""
        own = self._list_pinned[list_name]
        if key in own:
            return Setting(own[key], Source.LIST_PINNED, f"Pinned for the list {list_name} alone.")
        if key in self._pinned:
            return Setting(self._pinned[key], Source.PINNED, "Pinned for every list." if len(self._names) > 1 else "Pinned.")
        inferred = self._inferred[list_name].get(key)
        return inferred or Setting(DEFAULTS[key], Source.NOT_INFERRED, NOT_INFERRED)

    def values(self, list_name: str) -> dict[str, object]:
        """Every setting's value in one list, as shown: the values names are made with."""
        return {key: self.setting(list_name, key).value for key in SETTINGS}

    def set_setting(self, list_name: str, key: str, value: object) -> None:
        """Change a setting, pinning it: for the list alone if it pins it already, else for every list."""
        value = _kept(key, value)
        if key in self._list_pinned[list_name]:
            self._list_pinned[list_name][key] = value
        else:
            self._pinned[key] = value
        self.changed = True

    def pin(self, key: str) -> None:
        """Pin a setting at each list's value: for every list if they are the same, else for each."""
        values = [self.setting(name, key).value for name in self._names]
        if all(value == values[0] for value in values):
            self._pinned[key] = values[0]
            for own in self._list_pinned.values():
                own.pop(key, None)
        else:
            self._pinned.pop(key, None)
            for own, value in zip(self._list_pinned.values(), values):
                own[key] = value
        self.changed = True

    def unpin(self, list_name: str, key: str) -> None:
        """Unpin a setting as the list shows it: the list's own pin, or else
        the one for every list. Its value stays until inferred again."""
        if key in self._list_pinned[list_name]:
            value = self._list_pinned[list_name].pop(key)
            if key not in self._pinned:
                self._keep(list_name, key, value)
        elif key in self._pinned:
            value = self._pinned.pop(key)
            for name, own in self._list_pinned.items():
                if key not in own:
                    self._keep(name, key, value)
        else:
            return
        self.changed = True

    def pin_all(self) -> None:
        """Pin every setting at its value, as ``create --pin`` saves them."""
        for key in SETTINGS:
            if not all(self.setting(name, key).pinned for name in self._names):
                self.pin(key)

    def unpin_all(self) -> None:
        """Unpin every setting, keeping its value until inferred again."""
        for name in self._names:
            for key in SETTINGS:
                setting = self.setting(name, key)
                if setting.pinned:
                    self._keep(name, key, setting.value)
        if self._pinned or any(self._list_pinned.values()):
            self.changed = True
        self._pinned.clear()
        for own in self._list_pinned.values():
            own.clear()

    def _keep(self, list_name: str, key: str, value: object) -> None:
        self._inferred[list_name][key] = Setting(value, Source.NOT_INFERRED, NOT_INFERRED)

    # The style as a whole.

    def draft(self) -> Style:
        """The style as it would be saved: its names, title, description and
        pinned settings. Raises StyleError if it could not be."""
        made_plain: Counter[str] = Counter()
        name_lists = tuple(
            NameList(name, self.names(name, made_plain), dict(self._list_pinned[name])) for name in self._names
        )
        description = tuple(line.strip() for line in self.description.splitlines() if line.strip())
        style = Style(
            title=" ".join(self.title.split()) or (self.path.stem if self.path else NEW_TITLE),
            name_lists=name_lists,
            description=description,
            pinned=frozenset(self._pinned),
        ).with_settings(self._pinned)
        check_style(style)
        empty = [name_list.name for name_list in name_lists if not name_list.names]
        if empty:
            problems = (
                ["There are no names. Write them one per line."] if len(name_lists) == 1
                else [f'The list "{name}" has no names.' for name in empty]
            )  # fmt: skip
            raise StyleError(style.title, problems)
        return style

    def look_alike_warnings(self) -> list[str]:
        """How many look-alike characters the names have, read as plain ones (spec 13.12)."""
        made_plain: Counter[str] = Counter()
        for name in self._names:
            self.names(name, made_plain)
        return look_alike_warnings(made_plain)

    def names_texts(self) -> dict[str, str]:
        """Every list's names as typed, to tell later whether they have changed."""
        return dict(self._names)

    def infer(self) -> None:
        """Infer every setting not pinned, from the names as they are now. Raises StyleError."""
        texts = self.names_texts()
        self.apply_inferred(infer_lists(self.draft()), texts)

    def apply_inferred(self, inferences: Mapping[str, Inference], texts: Mapping[str, str]) -> None:
        """Show the settings inferred from ``draft()``, when the names were ``texts``.

        Inferring can take seconds, so the window does it on its own and
        hands the result here. Lists renamed or removed since are skipped.
        """
        for list_name, inference in inferences.items():
            if list_name in self._inferred:
                self._inferred[list_name] = {
                    key: Setting(_kept(key, inference.working.setting(key)), Source.INFERRED, reason)
                    for key, reason in inference.reasons.items()
                }
        self.names_changed = dict(texts) != self._names

    def maker(self, combinations: Sequence[Template]) -> Maker:
        """What makes names by ``combinations``, with each list's settings as
        shown. Raises StyleError for a combination or names that cannot be used."""
        problems = [
            f'The combination "{combination.recipe}" {problem}.'
            for combination in combinations
            if (problem := combination_problem(combination, self.list_names))
        ]
        if problems:
            raise StyleError("The combination", problems)
        draft = self.draft()
        used = dict.fromkeys(name for c in combinations for name in parse_combination(c.recipe).lists)
        styles = {name: self._working(draft, name) for name in used}
        for style in [style for style in self._analyses if style not in styles.values()]:
            del self._analyses[style]
        return Maker(styles, tuple(combinations), self._analyses)

    def _working(self, draft: Style, list_name: str) -> Style:
        """One list as a style of its own, with every setting at its value as shown."""
        values = self.values(list_name)
        if values["soft_consonants"]:
            # Clusters are kept written in full ("LTH"); soft consonants need the soft form ("Lt", spec 4.1).
            clusters = values["consonant_clusters"]
            values["consonant_clusters"] = tuple(dict.fromkeys(soften(c, draft.letter_classes) for c in clusters))
        return draft.list_style(list_name).with_settings(values)

    def save(self, path: str | Path | None = None) -> Path:
        """Write the style file, to ``path`` or the file it came from. Raises StyleError."""
        path = Path(path) if path is not None else self.path
        if path is None or path.suffix.lower() != SAVED_TYPE:
            raise ValueError(f"a style is saved as a {SAVED_TYPE} file, not {path}")
        save_style(self.draft(), path)
        self.path = path
        self.changed = False
        return path

    @property
    def saves_in_place(self) -> bool:
        """Whether Save writes back to the file the style came from: only a style file."""
        return self.path is not None and self.path.suffix.lower() == SAVED_TYPE


class Maker:
    """Makes names from a workspace's lists, as they were when it was made.

    Each list's analysis is kept in ``analyses``, shared with the workspace,
    so a batch made with the same names and settings as the last starts at
    once.
    """

    def __init__(
        self, styles: Mapping[str, Style], combinations: tuple[Template, ...], analyses: dict[Style, StyleAnalysis]
    ):
        self.styles = dict(styles)
        self.combinations = combinations
        self._analyses = analyses

    def make(self, count: int, seed: int | None = None, initial: str | None = None) -> Batch:
        """Make ``count`` full names, each with its explanation. Raises
        StyleError or GenerationError if none can be made."""
        seed = seed if seed is not None else random.SystemRandom().randrange(1_000_000)
        rng = random.Random(seed)
        generators: dict[str, NameGenerator] = {}
        notes: list[str] = []
        for list_name, style in self.styles.items():
            if style not in self._analyses:
                self._analyses[style] = analyze(style)
            analysis = self._analyses[style]
            generator = NameGenerator(analysis, style.options, rng, validator=Validator.classic(analysis))
            if generator.skipped_templates:
                notes.append(_skipped(generator.skipped_templates, list_name if len(self.styles) > 1 else None))
            generators[list_name] = generator
        combiner = Combiner(generators, self.combinations, rng)
        names = [combiner.generate(initial=initial, explain=True) for _ in range(count)]
        return Batch(names, seed, notes)


def _kept(key: str, value: object) -> object:
    """A setting's value as the workspace keeps it: consonant clusters written in full."""
    if key == "consonant_clusters":
        return tuple(dict.fromkeys(harden(cluster) for cluster in value))  # type: ignore[union-attr]
    return value


def _skipped(skipped: list[SkippedTemplate], list_name: str | None) -> str:
    recipes = ", ".join(f'"{item.template.recipe}:{item.template.weight}"' for item in skipped)
    of_list = f" of list {list_name}" if list_name else ""
    return f"Skipping templates{of_list} that can never be used: {recipes}."
