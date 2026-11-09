"""Reader for ``.ebn`` chapter files (spec section 2).

A chapter file is plain text made of statements such as ``TAG = "value";`` or
``TAG = "a","b","c";``, with ``//`` comments. Reading happens in three stages:

1. decode the bytes (UTF-8 first, then Windows-1252);
2. split the text into tokens: tag names, quoted values, ``=``, ``,`` and ``;``;
3. turn each tag's values into parts of a neutral ``Style``.

Problems that make a chapter unusable raise ``StyleError``, listing every
problem found. Problems that can be safely worked around become warnings,
kept in ``style.source.warnings``.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from namebook.formats.common import (
    describe_char,
    describe_chars,
    look_alike_warnings,
    plain_name,
    read_file,
    shorten,
    soften,
    suggestion,
)
from namebook.letters import LATIN_LETTERS, LetterClasses, capital
from namebook.style import (
    DEFAULT_LIST,
    MAX_FIT_LEVEL,
    MAX_VALIDATION_LEVEL,
    SETTINGS,
    GenerationOptions,
    NameList,
    Punctuation,
    SpecialLetters,
    Style,
    StyleError,
    StyleSource,
    Template,
    UMode,
    YMode,
)
from namebook.templates import TemplateError, parse_recipe

FORMAT = "ebn"

UTF8 = "utf-8"
WINDOWS_1252 = "windows-1252"

SINGLE_VALUE_TAGS = ("TITLE", "LINE1", "LINE2", "AUTHOR", "DATE", "TOPIC")
LIST_TAGS = ("SPCCON", "SPCVOW", "GENOPT", "TEMPLATES", "NAMES")
KNOWN_TAGS = SINGLE_VALUE_TAGS + LIST_TAGS
REQUIRED_TAGS = ("TITLE", "NAMES")

# GENOPT switches that take no value, and the option each one turns on.
SWITCH_OPTIONS = {
    "structgen": "structural",
    "statgen": "statistical",
    "prefix": "prefixes",
    "suffix": "suffixes",
    "shuffle": "shuffle",
}
VALUE_OPTIONS = ("fit", "val", "punct", "y", "u")
Y_MODES = {mode.value: mode for mode in YMode}
U_MODES = {mode.value: mode for mode in UMode}
ALL_PUNCTUATION = Punctuation.ACCENTS | Punctuation.DIAERESIS | Punctuation.VOWEL_HARMONY

CLUSTER_BRACKETS = (("{", "}"), ("<", ">"))

_TAG_NAME = re.compile(r"[A-Za-z0-9_]+")


def read_ebn(path: str | Path) -> Style:
    """Read an ``.ebn`` chapter file."""
    path = Path(path)
    data = read_file(path)
    text, encoding = decode_ebn(data)
    return parse_ebn(text, path=path, encoding=encoding)


def decode_ebn(data: bytes) -> tuple[str, str]:
    """Decode a chapter file's bytes. Returns the text and the encoding used.

    UTF-8 (with or without a byte order mark) is tried first. If the bytes are
    not valid UTF-8, they are read as Windows-1252, which old chapters use.
    The text is normalized so that accented letters always have one form.
    """
    try:
        text, encoding = data.decode("utf-8-sig"), UTF8
    except UnicodeDecodeError:
        text, encoding = data.decode("cp1252", errors="replace"), WINDOWS_1252
    return unicodedata.normalize("NFC", text), encoding


def parse_ebn(
    text: str,
    path: str | Path | None = None,
    encoding: str | None = None,
    letter_classes: LetterClasses = LATIN_LETTERS,
) -> Style:
    """Turn the text of an ``.ebn`` file into a ``Style``."""
    source = str(path) if path is not None else "<text>"
    warnings: list[str] = []
    statements = _parse_statements(_tokenize(text, source), source, warnings)
    builder = _StyleBuilder(source, letter_classes, warnings)
    return builder.build(statements, Path(path) if path is not None else None, encoding)


# ---------------------------------------------------------------------------
# Stage 2: tokens and statements


@dataclass(frozen=True)
class _Token:
    kind: str  # "tag", "value", "=", ",", ";" or "end"
    text: str
    line: int


@dataclass(frozen=True)
class _Value:
    text: str
    line: int


@dataclass(frozen=True)
class _Statement:
    tag: str
    line: int
    values: tuple[_Value, ...]


def _tokenize(text: str, source: str) -> Iterator[_Token]:
    """Yield tokens one at a time.

    Tokens are made only as the parser asks for them, so the first thing that
    looks wrong is reported. (With a missing quote, every later quote pairs up
    wrongly, and only the first oddity points at the real mistake.)
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    line = 1
    pos = 0
    while pos < len(text):
        char = text[pos]
        if char == "\n":
            line += 1
            pos += 1
        elif char.isspace():
            pos += 1
        elif text.startswith("//", pos):
            end = text.find("\n", pos)
            pos = len(text) if end == -1 else end
        elif char == '"':
            end = text.find('"', pos + 1)
            if end == -1:
                raise StyleError(source, [f"Line {line}: this quote is never closed."])
            value = text[pos + 1 : end]
            yield _Token("value", value, line)
            line += value.count("\n")
            pos = end + 1
        elif char in "=,;":
            yield _Token(char, char, line)
            pos += 1
        elif match := _TAG_NAME.match(text, pos):
            yield _Token("tag", match.group(), line)
            pos = match.end()
        else:
            raise StyleError(
                source, [f"Line {line}: unexpected {describe_char(char)} outside quotes."]
            )
    yield _Token("end", "", line)


class _TokenStream:
    """Tokens with one token of look-ahead: ``current`` is the next one to use."""

    def __init__(self, tokens: Iterator[_Token]):
        self._tokens = tokens
        self.current = next(tokens)

    def advance(self) -> _Token:
        token = self.current
        if token.kind != "end":
            self.current = next(self._tokens)
        return token


def _parse_statements(
    tokens: Iterator[_Token], source: str, warnings: list[str]
) -> list[_Statement]:
    """Group tokens into statements of the form ``TAG = "a", "b", ...;``."""

    def fail(token: _Token, message: str) -> None:
        raise StyleError(source, [f"Line {token.line}: {message}"])

    stream = _TokenStream(tokens)
    statements: list[_Statement] = []
    while stream.current.kind != "end":
        start = stream.advance()
        if start.kind != "tag":
            fail(start, f"expected a tag such as TITLE or NAMES, but found {_describe(start)}.")
        tag = start.text.upper()
        if stream.current.kind != "=":
            fail(stream.current, f"expected '=' after {tag}, but found {_describe(stream.current)}.")
        stream.advance()
        if stream.current.kind != "value":
            fail(
                stream.current,
                f"expected a quoted value after '{tag} =', but found {_describe(stream.current)}.",
            )
        first = stream.advance()
        values = [_Value(first.text, first.line)]
        while True:
            token = stream.current
            if token.kind == ";":
                stream.advance()
                break
            if token.kind == "end":
                warnings.append(
                    f"Line {values[-1].line}: the {tag} statement has no ';' at the end."
                )
                break
            if token.kind == ",":
                stream.advance()
                following = stream.current
                if following.kind == "value":
                    values.append(_Value(following.text, following.line))
                    stream.advance()
                elif following.kind in (";", "end"):
                    warnings.append(f"Line {token.line}: an extra ',' at the end of {tag} was ignored.")
                else:
                    fail(
                        following,
                        f"expected a quoted value after ',' in {tag}, "
                        f"but found {_describe(following)}.",
                    )
            elif token.kind == "value":
                fail(token, f"a ',' is missing before {_describe(token)} in {tag}.")
            else:
                fail(
                    token,
                    f"expected ',' or ';' after the last value of {tag} (line {start.line}), "
                    f"but found {_describe(token)}. Is a ';' or a quote missing?",
                )
        statements.append(_Statement(tag, start.line, tuple(values)))
    return statements


# ---------------------------------------------------------------------------
# Stage 3: from statements to a style


class _StyleBuilder:
    def __init__(self, source: str, letter_classes: LetterClasses, warnings: list[str]):
        self.source = source
        self.letters = letter_classes
        self.warnings = warnings
        self.problems: list[str] = []

    def build(
        self, statements: list[_Statement], path: Path | None, encoding: str | None
    ) -> Style:
        tags = self._collect_tags(statements)
        title = self._text(tags.get("TITLE"))
        if "TITLE" in tags and not title:
            self.problems.append(f"Line {tags['TITLE'].line}: TITLE is empty.")
        description = tuple(
            line for line in (self._text(tags.get("LINE1")), self._text(tags.get("LINE2"))) if line
        )
        names = self._names(tags.get("NAMES"))
        options = self._options(tags.get("GENOPT"))
        special_letters = self._special_letters(tags.get("SPCCON"), tags.get("SPCVOW"))
        templates = self._templates(tags.get("TEMPLATES"))

        if self.problems:
            raise StyleError(self.source, self.problems)
        return Style(
            title=title,
            name_lists=(NameList(DEFAULT_LIST, names),),
            description=description,
            options=options,
            special_letters=special_letters,
            templates=templates,
            letter_classes=self.letters,
            pinned=frozenset(SETTINGS),  # a chapter pins everything (spec 10.1)
            source=StyleSource(path, FORMAT, encoding, tuple(self.warnings)),
        )

    def _collect_tags(self, statements: list[_Statement]) -> dict[str, _Statement]:
        tags: dict[str, _Statement] = {}
        for statement in statements:
            tag = statement.tag
            if tag not in KNOWN_TAGS:
                self.warnings.append(
                    f"Line {statement.line}: unknown tag {tag} was ignored."
                    + suggestion(tag, KNOWN_TAGS)
                )
            elif tag in tags:
                self.problems.append(
                    f"Line {statement.line}: {tag} appears a second time "
                    f"(first on line {tags[tag].line}). Each tag may appear only once."
                )
            elif tag in SINGLE_VALUE_TAGS and len(statement.values) > 1:
                self.problems.append(
                    f"Line {statement.line}: {tag} should have one quoted value, "
                    f"but has {len(statement.values)}."
                )
            else:
                tags[tag] = statement
        for tag in REQUIRED_TAGS:
            if tag not in tags and not any(s.tag == tag for s in statements):
                self.problems.append(f"The chapter has no {tag} tag, which every chapter needs.")
        return tags

    @staticmethod
    def _text(statement: _Statement | None) -> str:
        if statement is None:
            return ""
        return " ".join(statement.values[0].text.split())

    def _names(self, statement: _Statement | None) -> tuple[str, ...]:
        if statement is None:
            return ()
        names: list[str] = []
        made_plain: Counter[str] = Counter()
        for number, value in enumerate(statement.values, start=1):
            where = f"Line {value.line}: name {number}"
            if "\n" in value.text.strip():
                self.problems.append(
                    f'{where} ("{shorten(value.text)}") runs onto the next line. '
                    "Is a closing quote missing?"
                )
                continue
            name = plain_name(value.text, made_plain)
            if not name:
                self.problems.append(f"{where} is empty.")
                continue
            bad = self.letters.invalid_characters(name)
            if bad:
                hint = ' Each name needs its own quotes, like "Ana","Bel".' if "," in bad else ""
                self.problems.append(
                    f'{where} ("{shorten(name, 60)}") contains {describe_chars(bad)}, '
                    f"which {'is' if len(bad) == 1 else 'are'} not a vowel or consonant letter."
                    + hint
                )
                continue
            names.append(name)
        self.warnings += look_alike_warnings(made_plain)
        return tuple(names)

    def _options(self, statement: _Statement | None) -> GenerationOptions:
        settings: dict[str, object] = {}
        for item, line in _items(statement):
            item = item.lower()
            key, colon, value = item.partition(":")
            if not colon and key in SWITCH_OPTIONS:
                settings[SWITCH_OPTIONS[key]] = True
            elif colon and key == "fit":
                level = self._level(item, value, MAX_FIT_LEVEL, line)
                if level is not None:
                    settings["fit_level"] = level
            elif colon and key == "val":
                # Levels above 2 behave exactly like 2 (spec section 8), so no warning.
                level = self._level(item, value, None, line)
                if level is not None:
                    settings["validation_level"] = min(level, MAX_VALIDATION_LEVEL)
            elif colon and key == "punct":
                if value.isascii() and value.isdigit() and int(value) <= ALL_PUNCTUATION:
                    settings["punctuation"] = Punctuation(int(value))
                else:
                    self._ignored_option(item, line, "the number must be 0 to 7")
            elif colon and key == "y":
                if value in Y_MODES:
                    settings["y_mode"] = Y_MODES[value]
                else:
                    self._ignored_option(item, line, "use y:vow, y:con or y:semi")
            elif colon and key == "u":
                if value in U_MODES:
                    settings["u_mode"] = U_MODES[value]
                else:
                    self._ignored_option(item, line, "use u:vow or u:semi")
            else:
                known = [*SWITCH_OPTIONS, *(f"{option}:" for option in VALUE_OPTIONS)]
                self.warnings.append(
                    f"Line {line}: unknown GENOPT option '{item}' was ignored."
                    + suggestion(item, known)
                )
        return GenerationOptions(**settings)

    def _level(self, item: str, value: str, maximum: int | None, line: int) -> int | None:
        if not (value.isascii() and value.isdigit()):
            self._ignored_option(item, line, "the level must be a whole number")
            return None
        level = int(value)
        if maximum is not None and level > maximum:
            self.warnings.append(
                f"Line {line}: GENOPT option '{item}' is above the highest level, "
                f"so {maximum} is used."
            )
            return maximum
        return level

    def _ignored_option(self, item: str, line: int, advice: str) -> None:
        self.warnings.append(f"Line {line}: GENOPT option '{item}' was ignored; {advice}.")

    def _special_letters(
        self, spccon: _Statement | None, spcvow: _Statement | None
    ) -> SpecialLetters:
        soft = False
        written: list[tuple[str, int]] = []
        for item, line in _items(spccon):
            if item.lower() == "soft":
                soft = True
            elif len(item) >= 2 and (item[0], item[-1]) in CLUSTER_BRACKETS:
                written.append((item[1:-1], line))
            else:
                self.warnings.append(
                    f"Line {line}: SPCCON item '{item}' was ignored. "
                    "Write 'soft', or a consonant cluster in braces such as {LF}."
                )

        clusters: list[str] = []
        for number, (cluster, line) in enumerate(written, start=1):
            if self._check_cluster(number, cluster, line, soft) and cluster not in clusters:
                clusters.append(cluster)

        multi = False
        for item, line in _items(spcvow):
            if item.lower() == "multi":
                multi = True
            else:
                self.warnings.append(
                    f"Line {line}: SPCVOW item '{item}' was ignored. The only option is 'multi'."
                )
        return SpecialLetters(soft, tuple(clusters), multi)

    def _check_cluster(self, number: int, cluster: str, line: int, soft: bool) -> bool:
        """Check one special consonant. Returns False if it cannot be used."""
        where = f"Line {line}: special consonant {number}"
        if not cluster:
            self.warnings.append(f"{where} is empty and was ignored.")
            return False
        bad = [char for char in dict.fromkeys(cluster) if not self._is_cluster_letter(char)]
        if bad:
            self.problems.append(
                f"{where} ('{cluster}') contains {describe_chars(bad)}, "
                f"which {'is' if len(bad) == 1 else 'are'} not a consonant letter."
            )
            return False
        if len(cluster) == 1:
            self.warnings.append(
                f"{where} ('{cluster}') is a single letter already, so it has no effect."
            )
        elif soft and (better := soften(cluster, self.letters)) != cluster:
            self.warnings.append(
                f"{where} ('{cluster}') can never match, because 'soft' first turns "
                f"each consonant followed by H into one letter. Write {{{better}}} instead."
            )
        elif not soft and any(char.islower() for char in cluster):
            self.warnings.append(
                f"{where} ('{cluster}') can never match: lowercase letters stand for soft "
                "consonants (t for TH), but SPCCON does not include 'soft'."
            )
        return True

    def _is_cluster_letter(self, char: str) -> bool:
        """Uppercase consonants, or lowercase ones standing for a soft consonant."""
        if char.islower():
            upper = capital(char)
            return upper != "H" and self.letters.is_consonant(upper)
        return self.letters.is_consonant(char)

    def _templates(self, statement: _Statement | None) -> tuple[Template, ...]:
        if statement is None:
            return ()
        templates: list[Template] = []
        for value in statement.values:
            text = value.text.strip()
            where = f"Line {value.line}: template '{shorten(text)}'"
            if not text:
                continue
            if "\n" in text:
                self.problems.append(f"{where} runs onto the next line. Is a closing quote missing?")
                continue
            recipe, colon, weight_text = text.rpartition(":")
            if not colon:
                self.warnings.append(f"{where} has no weight, so 1 is used. Write it as '{text}:1'.")
                recipe, weight_text = text, "1"
            recipe, weight_text = recipe.strip(), weight_text.strip()
            if not recipe:
                self.problems.append(f"{where} has nothing before the ':'.")
                continue
            if not (weight_text.isascii() and weight_text.isdigit()):
                self.problems.append(
                    f"{where} needs a whole number after the last ':', such as '{recipe}:1'."
                )
                continue
            try:
                parse_recipe(recipe)
            except TemplateError as error:
                self.problems.append(f"{where} {error}.")
                continue
            weight = int(weight_text)
            if weight == 0:
                self.warnings.append(f"{where} has weight 0, so it will never be chosen.")
            templates.append(Template(recipe, weight))
        return tuple(templates)


def _items(statement: _Statement | None):
    """Yield the comma-separated items of an option tag, with their line numbers.

    Items may be split over several quoted values or written in one, and all
    whitespace inside them is ignored (the manual shows ``GENOPT`` split
    across lines and ``"soft, <LTH>"`` with a space).
    """
    if statement is None:
        return
    for value in statement.values:
        for item in value.text.split(","):
            item = "".join(item.split())
            if item:
                yield item, value.line


# ---------------------------------------------------------------------------
# Message helpers


def _describe(token: _Token) -> str:
    if token.kind == "end":
        return "the end of the file"
    if token.kind == "value":
        return f'the value "{shorten(token.text)}"'
    return f"'{token.text}'"
