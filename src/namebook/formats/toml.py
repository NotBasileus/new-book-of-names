'''Style files (spec 10.7): UTF-8 TOML in three parts.

    title = "Angelic"                  # what the style is called (optional)
    description = "Proper Names"       # (optional)

    [settings]                         # pinned settings (optional)
    fit_level = 3

    [names]                            # the names (required)
    list = """
    Aalbe'il
    Aanfe'il
    """

Every setting under ``[settings]`` is pinned; the rest are worked out by
namebook. Names are written one per line, or separated by commas; blank
lines and lines starting with ``#`` are skipped. A style with a single list
may also write it as ``names = """..."""`` before any section. Without a
title, the file name is used.

A style may have several lists under ``[names]``, such as given and family
names, each a key of its own (spec 13.5). Settings under ``[settings]`` are
pinned for every list; a section for one list, such as
``[settings.family]``, pins settings for that list alone.

Python reads TOML with its built-in ``tomllib``. ``write_toml`` writes the
layout above itself, with a short comment beside each setting.
'''

from __future__ import annotations

import json
import re
import tomllib
import unicodedata
from collections import Counter
from pathlib import Path

from namebook.formats.common import (
    describe_chars,
    harden,
    look_alike_warnings,
    read_file,
    soften,
    split_names,
    suggestion,
)
from namebook.letters import LATIN_LETTERS, LetterClasses, capitals
from namebook.style import (
    DEFAULT_LIST,
    MAX_FIT_LEVEL,
    MAX_LENGTH,
    MAX_LOOK_BACK,
    MAX_VALIDATION_LEVEL,
    OPTION_FIELDS,
    SETTINGS,
    GenerationOptions,
    Markov,
    NameList,
    Punctuation,
    SpecialLetters,
    Style,
    StyleError,
    StyleSource,
    Template,
    ApostropheMode,
    HyphenMode,
    UMode,
    YMode,
)
from namebook.templates import TemplateError, parse_recipe

FORMAT = "toml"
UTF8 = "utf-8"

TOP_LEVEL_KEYS = ("title", "description", "settings", "names")

SWITCHES = (
    "soft_consonants",
    "multi_vowels",
    "structural",
    "statistical",
    "prefixes",
    "suffixes",
    "shuffle",
    "refuse_copies",
)
# Settings that are whole numbers, and the largest each may be.
NUMBERS = {
    "fit_level": MAX_FIT_LEVEL,
    "validation_level": MAX_VALIDATION_LEVEL,
    "min_length": MAX_LENGTH,
    "max_length": MAX_LENGTH,
    "markov_order": MAX_LOOK_BACK,
}
Y_MODES = {"vowel": YMode.VOWEL, "consonant": YMode.CONSONANT, "semivowel": YMode.SEMI}
U_MODES = {"vowel": UMode.VOWEL, "semivowel": UMode.SEMI}
APOSTROPHE_MODES = {mode.value: mode for mode in ApostropheMode}
HYPHEN_MODES = {mode.value: mode for mode in HyphenMode}
MARKOV = {mode.value: mode for mode in Markov}
# Settings that are one of a few words, and the value each word stands for.
MODES = {
    "letter_y": Y_MODES,
    "letter_u": U_MODES,
    "letter_apostrophe": APOSTROPHE_MODES,
    "letter_hyphen": HYPHEN_MODES,
    "markov": MARKOV,
}
PUNCTUATION = {
    "accents": Punctuation.ACCENTS,
    "diaeresis": Punctuation.DIAERESIS,
    "vowel_harmony": Punctuation.VOWEL_HARMONY,
}

# The comment written beside each setting.
COMMENTS = {
    "soft_consonants": "CH, KH, TH and the like each count as one letter",
    "consonant_clusters": "letter groups that each count as one consonant",
    "multi_vowels": "ABA, ORO and the like each count as one vowel",
    "letter_y": "vowel, consonant or semivowel",
    "letter_u": "vowel or semivowel",
    "letter_apostrophe": "vowel (the usual choice) or consonant",
    "letter_hyphen": "consonant (the usual choice) or joint: joins two words, as a space does",
    "structural": "element lengths follow the names",
    "statistical": "common elements are chosen more often",
    "fit_level": "0 to 3: how closely neighboring elements must fit",
    "prefixes": "the first two elements come from one name",
    "suffixes": "the last two elements come from one name",
    "shuffle": "middle elements are pooled, whatever their position",
    "validation_level": "0 to 2: how strictly letter limits are checked",
    "refuse_copies": "a name that is one of the names below is made again",
    "markov": "off (building rules alone), weighted or fill (a Markov chain guides the letters)",
    "markov_order": "letters the Markov chain looks back, 1 to 5; 0 to work it out",
    "markov_strength": "0 to 1: how much the Markov chain counts",
    "templates": "recipe:weight, such as \"#:3\" or \"5'7\"; [] for none",
    "min_length": "fewest letters in a name; 0 for no limit",
    "max_length": "most letters in a name; 0 for no limit",
    "punctuation": "any of accents (AA as Á), diaeresis (Eärendil), vowel_harmony (Väinö)",
}

_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
_LINE_WIDTH = 76
_ALIGN_LIMIT = 26


# ---------------------------------------------------------------------------
# Reading


def read_toml(path: str | Path) -> Style:
    """Read a style file."""
    path = Path(path)
    data = read_file(path)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise StyleError(
            str(path),
            [f"The file is not UTF-8 text (byte {error.start + 1} cannot be read). Save it as UTF-8."],
        ) from error
    return parse_toml(text, path=path)


def parse_toml(
    text: str, path: str | Path | None = None, letter_classes: LetterClasses = LATIN_LETTERS
) -> Style:
    """Turn the text of a style file into a ``Style``."""
    source = str(path) if path is not None else "<text>"
    try:
        data = tomllib.loads(unicodedata.normalize("NFC", text))
    except tomllib.TOMLDecodeError as error:
        raise StyleError(source, [f"This is not a valid style file: {error}."]) from error
    path = Path(path) if path is not None else None
    return _StyleReader(source, letter_classes).read(data, path)


def read_title(path: str | Path) -> str:
    """A style file's title, as ``read_toml`` reads it, without checking the
    rest: for listing styles quickly. Empty if it has none or cannot be read."""
    try:
        data = tomllib.loads(unicodedata.normalize("NFC", Path(path).read_text(encoding="utf-8-sig")))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return ""
    title = data.get("title", "")
    return " ".join(title.split()) if isinstance(title, str) else ""


class _StyleReader:
    def __init__(self, source: str, letter_classes: LetterClasses):
        self.source = source
        self.letters = letter_classes
        self.problems: list[str] = []
        self.made_plain: Counter[str] = Counter()

    def read(self, data: dict, path: Path | None) -> Style:
        for key, value in data.items():
            if key in SETTINGS:
                self.problems.append(f"{key} is a setting, so it belongs under [settings].")
            elif key not in TOP_LEVEL_KEYS:
                hint = suggestion(key, TOP_LEVEL_KEYS) or _list_hint(key, value)
                self.problems.append(f"Unknown key '{key}'." + hint)

        title = " ".join(self._text(data, "title").split())
        if not title:
            if path is None:
                self.problems.append("The style needs a title.")
            title = path.stem if path is not None else ""
        lines = (" ".join(line.split()) for line in self._text(data, "description").splitlines())
        description = tuple(line for line in lines if line)
        table = data.get("settings", {})
        if not isinstance(table, dict):
            self.problems.append("settings must be a section: write [settings] on a line of its own.")
            table = {}
        # A section inside [settings], such as [settings.family], is for one list.
        values = self._settings({key: value for key, value in table.items() if not isinstance(value, dict)})
        sections = {key: value for key, value in table.items() if isinstance(value, dict)}
        name_lists = self._name_lists(data.get("names"), sections, values)

        if self.problems:
            raise StyleError(self.source, self.problems)
        style = Style(
            title=title,
            name_lists=name_lists,
            description=description,
            options=GenerationOptions(
                **{OPTION_FIELDS[key]: value for key, value in values.items() if key in OPTION_FIELDS}
            ),
            special_letters=SpecialLetters(
                values.get("soft_consonants", False),
                values.get("consonant_clusters", ()),
                values.get("multi_vowels", False),
            ),
            templates=values.get("templates", ()),
            letter_classes=self.letters,
            pinned=frozenset(values),
            source=StyleSource(path, FORMAT, UTF8, tuple(look_alike_warnings(self.made_plain))),
        )
        if "letter_apostrophe" in values:  # kept in the style's letter classes
            style = style.with_settings({"letter_apostrophe": values["letter_apostrophe"]})
        return style

    def _text(self, data: dict, key: str) -> str:
        value = data.get(key, "")
        if not isinstance(value, str):
            self.problems.append(f"{key} must be text in quotes, not {_show(value)}.")
            return ""
        return value

    # --- Settings ---------------------------------------------------------

    def _settings(
        self, table: dict, where: str = "[settings]", outer: dict[str, object] | None = None
    ) -> dict[str, object]:
        """The valid settings in a section, converted, by name: those under
        ``[settings]``, or those for one list, pinned over ``outer``, those
        under ``[settings]``."""
        values: dict[str, object] = {}
        for key, value in table.items():
            if key not in SETTINGS:
                hint = suggestion(key, SETTINGS) or _list_hint(key, value)
                self.problems.append(f"{where} has an unknown setting '{key}'." + hint)
                continue
            converted = self._setting(key, value)
            if converted is not None:
                values[key] = converted
        both = {**(outer or {}), **values}
        if both.get("soft_consonants") and "consonant_clusters" in values:
            # Written in full ("LTH"); with soft consonants on, kept in the soft form ("Lt", spec 4.1).
            clusters = values["consonant_clusters"]
            values["consonant_clusters"] = tuple(dict.fromkeys(soften(c, self.letters) for c in clusters))
        fewest, most = both.get("min_length", 0), both.get("max_length", 0)
        if fewest and most and fewest > most:
            within = "" if outer is None else f"In {where}, "
            self.problems.append(f"{within}min_length ({fewest}) is more than max_length ({most}).")
            values.pop("min_length", None)
            values.pop("max_length", None)
        return values

    def _setting(self, key: str, value: object) -> object | None:
        """Convert one setting, or record a problem and return None."""
        if key in SWITCHES:
            if isinstance(value, bool):
                return value
            return self._bad(key, value, "true or false")
        if key in NUMBERS:
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= NUMBERS[key]:
                return value
            return self._bad(key, value, f"a whole number from 0 to {NUMBERS[key]}")
        if key in MODES:
            modes = MODES[key]
            if isinstance(value, str) and value in modes:
                return modes[value]
            return self._bad(key, value, _choices(modes))
        if key == "markov_strength":
            if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1:
                return float(value)
            return self._bad(key, value, "a number from 0 to 1, such as 0.5")
        if key == "punctuation":
            return self._punctuation(value)
        if key == "consonant_clusters":
            return self._clusters(value)
        return self._templates(value)

    def _bad(self, key: str, value: object, wanted: str) -> None:
        self.problems.append(f"{key} must be {wanted}, not {_show(value)}.")
        return None

    def _punctuation(self, value: object) -> Punctuation | None:
        items = [value] if isinstance(value, str) else value
        if not isinstance(items, list) or not all(
            isinstance(item, str) and item in PUNCTUATION for item in items
        ):
            return self._bad("punctuation", value, f"a list of any of {_choices(PUNCTUATION)}")
        punctuation = Punctuation.NONE
        for item in items:
            punctuation |= PUNCTUATION[item]
        return punctuation

    def _clusters(self, value: object) -> tuple[str, ...] | None:
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return self._bad("consonant_clusters", value, 'a list of letter groups, such as ["RR", "LTH"]')
        clusters: list[str] = []
        for item in value:
            cluster = capitals("".join(item.split()))
            bad = [char for char in dict.fromkeys(cluster) if not self.letters.is_consonant(char)]
            if not cluster:
                self.problems.append("A consonant cluster is empty.")
            elif bad:
                self.problems.append(
                    f'Consonant cluster "{item}" contains {describe_chars(bad)}, which '
                    f"{'is' if len(bad) == 1 else 'are'} not a consonant letter."
                )
            else:
                clusters.append(cluster)
        return tuple(clusters)

    def _templates(self, value: object) -> tuple[Template, ...] | None:
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return self._bad("templates", value, 'a list of recipes, such as ["5\'7:3", "#:1"]')
        templates: list[Template] = []
        for item in value:
            recipe, colon, weight = item.rpartition(":")
            if not colon:
                recipe, weight = item, "1"
            recipe, weight = recipe.strip(), weight.strip()
            if not recipe or not (weight.isascii() and weight.isdigit()):
                self.problems.append(
                    f"Template \"{item}\" must be a recipe and a weight, such as \"{recipe or '#'}:1\"."
                )
                continue
            try:
                parse_recipe(recipe)
            except TemplateError as error:
                self.problems.append(f"Template \"{item}\" {error}.")
                continue
            templates.append(Template(recipe, int(weight)))
        return tuple(templates)

    # --- Names ------------------------------------------------------------

    def _name_lists(
        self, value: object, sections: dict[str, dict], outer: dict[str, object]
    ) -> tuple[NameList, ...]:
        """The lists under ``[names]``, each with the settings in its section
        of ``[settings]``, pinned over ``outer``, those for every list."""
        if value is None:
            self.problems.append("The style has no names. Add them under [names], one per line.")
            return ()
        if isinstance(value, str):
            lists = [(DEFAULT_LIST, value, "The name list")]
        elif isinstance(value, dict) and value:
            lists = [(key, block, f"[names] {_key(key)}") for key, block in value.items()]
        else:
            self.problems.append(
                'names must be the names in triple quotes (names = """ ... """), '
                "or a [names] section of named lists."
            )
            return ()
        for key in sections:
            if key not in {name for name, _, _ in lists}:
                self.problems.append(
                    f"[settings.{_key(key)}] is for a list called {key}, but there is no such list under [names]."
                )
        name_lists: list[NameList] = []
        for key, block, where in lists:
            if not isinstance(block, str):
                self.problems.append(f'{where} must be names in triple quotes ("""), not {_show(block)}.')
                continue
            settings = self._settings(sections[key], f"[settings.{_key(key)}]", outer) if key in sections else {}
            name_lists.append(NameList(key, self._names(block, where), settings))
        return tuple(name_lists)

    def _names(self, block: str, where: str) -> tuple[str, ...]:
        names = split_names(block, self.made_plain)
        if not names:
            self.problems.append(f"{where} is empty.")
        return tuple(names)


def _list_hint(key: str, value: object) -> str:
    """For names written in the wrong place, where lists go."""
    if not isinstance(value, str):
        return ""
    return f' Lists of names go under [names]: a line [names], then {_key(key)} = """ ... """.'


def _choices(names) -> str:
    quoted = [f'"{name}"' for name in names]
    return ", ".join(quoted[:-1]) + " or " + quoted[-1]


def _show(value: object) -> str:
    """A value as it would be written in a style file, for messages."""
    return json.dumps(value, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Writing


def write_toml(style: Style) -> str:
    """The text of a style file for ``style``, writing only its pinned settings."""
    lines = ["# A namebook style: example names, and any settings pinned for them.", ""]
    lines.append(f"title = {_quote(style.title)}")
    if len(style.description) == 1:
        lines.append(f"description = {_quote(style.description[0])}")
    elif style.description:
        lines.append(f"description = {_block(style.description)}")

    several = len(style.name_lists) > 1
    pinned = [key for key in SETTINGS if key in style.pinned]
    if pinned:
        which = ", for every list" if several else ""
        lines += ["", "[settings]", f"# Pinned settings{which}. Settings left out are worked out by namebook."]
        lines += _setting_lines(style, pinned)
    for name_list in style.name_lists:
        own = [key for key in SETTINGS if key in name_list.settings]
        if own:
            lines += ["", f"[settings.{_key(name_list.name)}]", f"# Pinned for the list {name_list.name} alone."]
            lines += _setting_lines(style.list_style(name_list.name), own)

    lines += ["", "[names]"]
    for number, name_list in enumerate(style.name_lists):
        if number:
            lines.append("")
        lines.append(f"{_key(name_list.name)} = {_block(name_list.names)}")
    return "\n".join(lines) + "\n"


def _setting_lines(style: Style, keys: list[str]) -> list[str]:
    """Each setting as ``key = value``, with its comment beside it."""
    assignments = [_assignment(style, key) for key in keys]
    # Line comments up, except beside the odd long line, which would push them all right.
    width = max((len(first) for first, _ in assignments if len(first) <= _ALIGN_LIMIT), default=0)
    lines: list[str] = []
    for key, (first, rest) in zip(keys, assignments):
        lines.append(f"{first.ljust(width)}  # {COMMENTS[key]}")
        lines += rest
    return lines


def setting_value(style: Style, key: str) -> str:
    """One setting's value as a style file writes it, on one line: ``true``, ``3``, ``["RR"]``."""
    items = _items(style, key)
    return f"[{', '.join(items)}]" if items is not None else _scalar(style, key)


def _assignment(style: Style, key: str) -> tuple[str, list[str]]:
    """``key = value``, as a first line and any further lines (for long lists)."""
    items = _items(style, key)
    if items is not None:
        return _list(key, items)
    return f"{key} = {_scalar(style, key)}", []


def _items(style: Style, key: str) -> list[str] | None:
    """The items of a setting that is a list, written out; None for other settings."""
    if key == "templates":
        return [_quote(f"{template.recipe}:{template.weight}") for template in style.templates]
    if key == "consonant_clusters":
        return [_quote(harden(cluster)) for cluster in style.special_letters.consonant_clusters]
    if key == "punctuation":
        return [_quote(name) for name, flag in PUNCTUATION.items() if flag in style.options.punctuation]
    return None


def _scalar(style: Style, key: str) -> str:
    value = style.setting(key)
    if key in MODES:
        return _quote(next(name for name, mode in MODES[key].items() if mode is value))
    if key in NUMBERS:
        return str(value)
    if key == "markov_strength":
        number = f"{value:g}"
        return number if "." in number else number + ".0"
    return _boolean(value)


def _list(key: str, items: list[str]) -> tuple[str, list[str]]:
    """A list on one line if it fits, otherwise one wrapped line after another."""
    single = f"{key} = [{', '.join(items)}]"
    if len(single) <= _LINE_WIDTH:
        return single, []
    rows: list[str] = []
    row = ""
    for item in items:
        candidate = f"{row} {item}," if row else f"    {item},"
        if row and len(candidate) > _LINE_WIDTH:
            rows.append(row)
            candidate = f"    {item},"
        row = candidate
    rows.append(row)
    return f"{key} = [", rows + ["]"]


def _boolean(value: bool) -> str:
    return "true" if value else "false"


def _key(name: str) -> str:
    return name if _BARE_KEY.fullmatch(name) else _quote(name)


def _escape(text: str) -> str:
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    return "".join(char if char.isprintable() else f"\\u{ord(char):04X}" for char in text)


def _quote(text: str) -> str:
    return f'"{_escape(text)}"'


def _block(lines) -> str:
    """Lines of text in triple quotes, one per line."""
    return '"""\n' + "\n".join(_escape(line) for line in lines) + '\n"""'
