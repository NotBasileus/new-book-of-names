"""Reading the real chapter files in chapters/."""

import random

import pytest

from conftest import SAMPLE_CHAPTERS
from namebook.analysis import analyze
from namebook.cli import main
from namebook.formats import load_style
from namebook.formats.ebn import WINDOWS_1252
from namebook.generator import NameGenerator
from namebook.style import (
    SETTINGS,
    GenerationOptions,
    Markov,
    Punctuation,
    SpecialLetters,
    Template,
    UMode,
    YMode,
)

# The same 14 templates are used by every sample chapter.
STANDARD_TEMPLATES = tuple(
    Template(str(structure), weight)
    for structure, weight in [
        (0, 1), (1, 1), (2, 2), (3, 2), (4, 3), (5, 3), (6, 4),
        (7, 4), (8, 3), (9, 3), (10, 2), (11, 2), (12, 1), (13, 1),
    ]
)  # fmt: skip

# Expected facts per file, counted independently of the reader.
EXPECTED = {
    "Ancient_Female.ebn": ("Ancient Female", 98, "Bahun", "Zirratbanit"),
    "Ancient_Male.ebn": ("Ancient Male", 426, "Acur", "Zur"),
    "Angelic.ebn": ("Angelic", 266, "Aalbe'il", "Zuphlasah"),
    "Demonic.ebn": ("Demonic", 76, "Adrashyal", "Ersarus"),
    "Fae_female.ebn": ("Fae Female", 208, "Aderyn", "Ysolt"),
    "Fae_male.ebn": ("Fae Male", 334, "Áed", "Yorath"),
}


def test_all_sample_chapters_are_covered():
    assert sorted(path.name for path in SAMPLE_CHAPTERS) == sorted(EXPECTED)


@pytest.mark.parametrize("path", SAMPLE_CHAPTERS, ids=lambda path: path.name)
def test_sample_chapter_loads_cleanly(path):
    chapter = load_style(path)
    title, count, first, last = EXPECTED[path.name]
    assert chapter.title == title
    assert len(chapter.names) == count
    assert chapter.names[0] == first
    assert chapter.names[-1] == last
    assert chapter.templates == STANDARD_TEMPLATES
    assert chapter.source.path == path
    assert chapter.source.format == "ebn"
    assert chapter.source.encoding == WINDOWS_1252
    assert chapter.source.warnings == ()


def test_angelic(chapters_dir):
    chapter = load_style(chapters_dir / "Angelic.ebn")
    assert chapter.description == ("Proper Names", "of the Celestial Watchers")
    assert chapter.options == GenerationOptions(
        structural=True, statistical=True, fit_level=3, prefixes=True, suffixes=True
    )
    assert chapter.special_letters == SpecialLetters(soft_consonants=True)
    assert "Mikhail" in chapter.names
    assert chapter.names.count("Ana'il") == 2  # repeats are kept


def test_ancient_female_uses_shuffle_and_keeps_repeats(chapters_dir):
    chapter = load_style(chapters_dir / "Ancient_Female.ebn")
    assert chapter.options.shuffle
    assert chapter.names.count("Nin") == 5


def test_ancient_male_has_no_special_letters(chapters_dir):
    chapter = load_style(chapters_dir / "Ancient_Male.ebn")
    assert chapter.special_letters == SpecialLetters()
    assert not chapter.options.shuffle
    assert "Kinéladan" in chapter.names
    assert "Kineládanos" in chapter.names


def test_demonic_has_clusters_in_angle_brackets(chapters_dir):
    chapter = load_style(chapters_dir / "Demonic.ebn")
    assert chapter.special_letters == SpecialLetters(
        soft_consonants=True, consonant_clusters=("RR", "JJ", "NN", "ZZ")
    )
    assert chapter.options.validation_level == 2
    # Byte 0x9A in Windows-1252 is š.
    assert "Maškaššaptu" in chapter.names


def test_fae_names_keep_their_accents(chapters_dir):
    female = load_style(chapters_dir / "Fae_female.ebn")
    male = load_style(chapters_dir / "Fae_male.ebn")
    assert {"Bébinn", "Éimhear", "Étaín", "Gráinne", "Siân"} <= set(female.names)
    assert {"Áed", "Óengus", "Cúchulainn", "Lóegaire", "Siôn"} <= set(male.names)
    assert female.options.y_mode is YMode.VOWEL
    assert female.options.u_mode is UMode.VOWEL
    assert female.options.punctuation is Punctuation.NONE


@pytest.mark.parametrize("path", SAMPLE_CHAPTERS, ids=lambda path: path.name)
def test_a_chapter_converted_by_create_makes_the_same_names(path, tmp_path, monkeypatch, capsys):
    # create saves into the styles folder of the current folder: here a temporary one.
    monkeypatch.chdir(tmp_path)
    assert main(["create", str(path.resolve())]) == 0
    capsys.readouterr()
    converted = load_style(tmp_path / "styles" / f"{path.stem}.toml")
    assert converted.pinned == set(SETTINGS) and converted.options.markov is Markov.OFF

    def names(style):
        generator = NameGenerator(analyze(style), rng=random.Random(3))
        return [generator.generate().text for _ in range(200)]

    assert names(converted) == names(load_style(path))
