'''Plain name lists (spec 10.1): a ``.txt`` file of names and nothing else.

Names are written one per line, or separated by commas; blank lines and
lines starting with ``#`` are skipped, as in a style file's name block. The
title is the file name. There is no description and nothing is pinned, so
a plain list behaves like a style file holding only ``names = """..."""``.

The file is read as UTF-8, or as Windows-1252 if it is not UTF-8, since
lists may come from older programs.
'''

from __future__ import annotations

import unicodedata
from collections import Counter
from pathlib import Path

from namebook.formats.common import look_alike_warnings, read_file, split_names
from namebook.style import DEFAULT_LIST, NameList, Style, StyleError, StyleSource

FORMAT = "txt"
UTF8 = "utf-8"
WINDOWS_1252 = "windows-1252"


def read_txt(path: str | Path) -> Style:
    """Read a plain name list."""
    path = Path(path)
    data = read_file(path)
    try:
        text, encoding = data.decode("utf-8-sig"), UTF8
    except UnicodeDecodeError:
        text, encoding = data.decode("cp1252", errors="replace"), WINDOWS_1252
    return parse_txt(text, path, encoding)


def parse_txt(text: str, path: str | Path, encoding: str | None = None) -> Style:
    """Turn the text of a plain name list into a ``Style`` named after the file."""
    path = Path(path)
    made_plain: Counter[str] = Counter()
    names = split_names(unicodedata.normalize("NFC", text), made_plain)
    if not names:
        raise StyleError(str(path), ["The file has no names. Write them one per line."])
    return Style(
        title=path.stem,
        name_lists=(NameList(DEFAULT_LIST, tuple(names)),),
        source=StyleSource(path, FORMAT, encoding, tuple(look_alike_warnings(made_plain))),
    )
