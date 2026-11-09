"""Style file formats.

Each reader turns one kind of file into a neutral ``Style``. Nothing outside
this package needs to know how style files are written.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from namebook.formats.check import check_style
from namebook.formats.ebn import read_ebn
from namebook.formats.toml import read_toml, write_toml
from namebook.formats.txt import read_txt
from namebook.style import Style, StyleError

READERS = {".ebn": read_ebn, ".toml": read_toml, ".txt": read_txt}
WRITERS = {".toml": write_toml}

# The kinds of file a style's name finds in the styles folder, in order of
# preference: a style file wins over a plain list of the same name.
NAMED_TYPES = (".toml", ".txt")


@dataclass(frozen=True)
class FoundStyle:
    """The file a style's name or path stands for, and a note to show, if any."""

    path: Path
    note: str | None = None


def find_style(text: str, folder: Path) -> FoundStyle:
    """Find the style ``text`` stands for: a file, or the name of a style in ``folder``.

    ``text`` is a path if it names an existing file or includes a folder.
    Otherwise it is a style's name, matched in ``folder`` regardless of
    capitals: ``angelic`` finds ``Angelic.toml``, or ``Angelic.txt`` if there
    is no style file. When both exist, the style file is used and the note
    says so. A name with an extension (``Angelic.txt``) finds that file.
    Raises ``StyleError`` when nothing matches.
    """
    path = Path(text)
    if path.is_file() or path.parent != Path(".") or path.is_absolute():
        return FoundStyle(path)

    entries = sorted(entry for entry in folder.iterdir() if entry.is_file()) if folder.is_dir() else []
    wanted = text.casefold()
    if path.suffix.lower() in READERS:
        matches = [entry for entry in entries if entry.name.casefold() == wanted]
    else:
        by_type = {
            entry.suffix.lower(): entry
            for entry in entries
            if entry.stem.casefold() == wanted and entry.suffix.lower() in NAMED_TYPES
        }
        matches = [by_type[kind] for kind in NAMED_TYPES if kind in by_type]

    if not matches:
        styles = sorted({entry.stem for entry in entries if entry.suffix.lower() in NAMED_TYPES})
        known = f" Styles there: {', '.join(styles)}." if styles else ""
        raise StyleError(text, [f'There is no file "{text}", and no style called "{text}" in {folder}.{known}'])
    note = None
    if len(matches) > 1:
        others = " and ".join(str(match) for match in matches[1:])
        note = f"Note: {others} is also there; using {matches[0]}."
    return FoundStyle(matches[0], note)


def load_style(path: str | Path) -> Style:
    """Read a style file, choosing the reader by the file's extension.

    Every style read passes the input check (``check_style``) before it is
    returned. Problems raise ``StyleError``.
    """
    path = Path(path)
    reader = READERS.get(path.suffix.lower())
    if reader is None:
        known = ", ".join(sorted(READERS))
        raise StyleError(
            str(path), [f"Unknown style file type '{path.suffix}'. Supported types: {known}."]
        )
    style = reader(path)
    check_style(style)
    return style


def save_style(style: Style, path: str | Path) -> None:
    """Write a style file, choosing the format by the file's extension."""
    path = Path(path)
    writer = WRITERS.get(path.suffix.lower())
    if writer is None:
        known = ", ".join(sorted(WRITERS))
        raise ValueError(f"cannot save a style as '{path.suffix}'; supported types: {known}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(writer(style), encoding="utf-8")
