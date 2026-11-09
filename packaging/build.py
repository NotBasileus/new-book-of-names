r"""Build New Book of Names for Windows, as a folder and a zip to attach to a release.

Run it from the project folder, with PyInstaller installed:

    .venv\Scripts\python -m pip install -e .[build]
    .venv\Scripts\python packaging\build.py

It makes ``dist\New Book of Names\``: the program, ``New Book of Names.exe``,
with the Python it needs in ``_internal\``, the bundled styles in
``styles\``, and the license and README; and the same folder zipped, as
``dist\New-Book-of-Names-<version>-windows.zip``. The styles are plain files
beside the program, so styles saved in the window go there too.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import PyInstaller.__main__

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT / "src"))

from namebook import __version__  # noqa: E402  (found through the line above)
from namebook.window import APP_NAME  # noqa: E402

DIST = PROJECT / "dist"
STYLE_TYPES = (".toml", ".txt")


def build() -> Path:
    """Build the program's folder and zip it. Returns the zip."""
    PyInstaller.__main__.run([
        str(PROJECT / "packaging" / "new_book_of_names.py"),
        "--name", APP_NAME,
        "--windowed",  # no console window behind the program's own
        "--noconfirm",
        "--clean",
        "--paths", str(PROJECT / "src"),
        "--distpath", str(DIST),
        "--workpath", str(PROJECT / "build"),
        "--specpath", str(PROJECT / "build"),
    ])  # fmt: skip
    folder = DIST / APP_NAME
    styles = folder / "styles"
    styles.mkdir()
    for path in sorted((PROJECT / "styles").iterdir()):
        if path.suffix.lower() in STYLE_TYPES or path.name == "README.md":
            shutil.copy2(path, styles / path.name)
    for name in ("LICENSE", "README.md"):
        shutil.copy2(PROJECT / name, folder / name)
    archive = DIST / f"{APP_NAME.replace(' ', '-')}-{__version__}-windows"
    return Path(shutil.make_archive(str(archive), "zip", DIST, APP_NAME))


if __name__ == "__main__":
    print(f"Built {build()}")
