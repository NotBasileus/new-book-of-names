from pathlib import Path
from types import CodeType, FunctionType

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
CHAPTERS_DIR = PROJECT_DIR / "chapters"
STYLES_DIR = PROJECT_DIR / "styles"
SAMPLE_CHAPTERS = sorted(CHAPTERS_DIR.glob("*.ebn"))

# Tests that read files not in every copy of the project are skipped where
# those files are missing.
CHAPTER_NAMES = {"CHAPTERS_DIR", "SAMPLE_CHAPTERS", "chapters_dir"}
NO_CHAPTERS = "the files this test reads are not present"


@pytest.fixture
def chapters_dir() -> Path:
    if not SAMPLE_CHAPTERS:
        pytest.skip(NO_CHAPTERS)
    return CHAPTERS_DIR


def pytest_collection_modifyitems(items):
    if SAMPLE_CHAPTERS:
        return
    for item in items:
        functions = [getattr(item, "function", None)]
        fixtures = getattr(item, "_fixtureinfo", None)
        if fixtures:
            functions += [definition.func for definitions in fixtures.name2fixturedefs.values()
                          for definition in definitions]  # fmt: skip
        if any(_reads_chapters(function) for function in functions if isinstance(function, FunctionType)):
            item.add_marker(pytest.mark.skip(reason=NO_CHAPTERS))


def _reads_chapters(function: FunctionType, seen: set[CodeType] | None = None) -> bool:
    """Whether ``function``, or a function of its module it calls, names the chapters."""
    seen = set() if seen is None else seen
    waiting = [function.__code__]
    while waiting:
        code = waiting.pop()
        if code in seen:
            continue
        seen.add(code)
        if CHAPTER_NAMES & {*code.co_names, *code.co_varnames}:
            return True
        waiting += [const for const in code.co_consts if isinstance(const, CodeType)]  # lambdas, comprehensions
        waiting += [called.__code__ for name in code.co_names
                    if isinstance(called := function.__globals__.get(name), FunctionType)]  # fmt: skip
    return False
