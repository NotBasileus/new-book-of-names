"""What New Book of Names.exe runs: the window, with the styles in the
``styles`` folder beside the program. A style file dropped onto the program
is opened."""

import sys
from pathlib import Path

from namebook.window import run

raise SystemExit(run(sys.argv[1] if len(sys.argv) > 1 else None, Path(sys.executable).parent / "styles"))
