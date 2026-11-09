"""Quality measurements (spec 11)."""

import random
from collections import Counter

import pytest

from conftest import CHAPTERS_DIR
from namebook.analysis import analyze
from namebook.building import NO_OPTION, BuildAttempt, Shape
from namebook.formats import load_style
from namebook.formats.ebn import parse_ebn
from namebook.generator import (
    COPY_FAILURE,
    HISTORY_FAILURE,
    Checked,
    CopyRefused,
    Finished,
    GaveUp,
    GeneratedName,
    GeneratedWord,
    HistoryConflict,
    NameGenerator,
    Relaxed,
    ValidationLowered,
)
from namebook.quality import (
    NO_VALUE,
    SeedIndex,
    edit_distance,
    letter_pairs,
    match,
    measure,
    measure_seeds,
    plain,
    report,
)
from namebook.style import GenerationOptions
from namebook.validation import FREQUENCY_FAILURE, Rejection


@pytest.fixture(scope="module")
def angelic():
    return analyze(load_style(CHAPTERS_DIR / "Angelic.ebn"))


def made_name(text, structure=1, events=()):
    """A finished one-word name, as the generator would return it."""
    attempt = BuildAttempt(Shape(structure, None), GenerationOptions(), None, ())
    finished = Finished(text.upper(), text)
    return GeneratedName(text, (GeneratedWord(attempt, text),), (attempt, finished, *events), "#")


def test_plain_ignores_capitals_accents_and_punctuation():
    assert plain("Aalbe'il") == "aalbeil"
    assert plain("Eärendil") == "earendil"
    assert plain("Van Helsing") == "vanhelsing"
    assert plain("Ar-Pharazôn") == "arpharazon"
    assert plain("Æthelstan") == "æthelstan"  # Æ is a letter of its own, not an accented one


@pytest.mark.parametrize(
    ("a", "b", "distance"),
    [("kitten", "sitting", 3), ("", "abc", 3), ("abc", "abc", 0), ("ana", "anna", 1), ("ab", "ba", 2)],
)
def test_edit_distance(a, b, distance):
    assert edit_distance(a, b) == distance
    assert edit_distance(b, a) == distance


def test_edit_distance_with_a_limit_agrees_with_the_full_answer():
    rng = random.Random(5)
    for _ in range(300):
        a = "".join(rng.choices("abcd", k=rng.randint(0, 7)))
        b = "".join(rng.choices("abcd", k=rng.randint(0, 7)))
        full = edit_distance(a, b)
        for limit in range(5):
            assert edit_distance(a, b, limit) == min(full, limit + 1)


def test_nearest_seed_matches_a_full_search(angelic):
    seeds = SeedIndex.for_analysis(angelic)
    generator = NameGenerator(angelic, rng=random.Random(2))
    for _ in range(40):
        name = plain(generator.generate().text)
        assert seeds.nearest(name) == min(edit_distance(name, seed) for seed in seeds.names)
        others = [seed for seed in seeds.names if seed != name]
        assert seeds.nearest(name, skip_itself=True) == min(edit_distance(name, s) for s in others)


def test_nearest_seed_matches_a_full_search_for_made_up_names():
    rng = random.Random(7)
    names = ["".join(rng.choices("abcde", k=rng.randint(1, 8))) for _ in range(150)]
    seeds = SeedIndex(names)
    queries = ["".join(rng.choices("abcdef", k=rng.randint(0, 9))) for _ in range(150)]
    for query in queries:
        assert seeds.nearest(query) == min(edit_distance(query, seed) for seed in seeds.names)
    for name in seeds.names:
        others = [seed for seed in seeds.names if seed != name]
        assert seeds.nearest(name, skip_itself=True) == min(edit_distance(name, s) for s in others)


def test_letter_pairs_count_a_repeated_pair_each_time():
    assert letter_pairs("anan") == ["^a0", "an0", "na0", "an1", "n$0"]


def test_nearest_seed_without_seeds_to_compare():
    assert SeedIndex([]).nearest("ana") is None
    assert SeedIndex(["Ana"]).nearest("ana", skip_itself=True) is None
    assert SeedIndex(["Ana"]).nearest("ana") == 0


def test_match():
    assert match(Counter(a=1, b=3), Counter(a=1, b=3)) == pytest.approx(1)
    assert match(Counter(a=2, b=2), Counter(a=1, b=1)) == pytest.approx(1)  # only shares count
    assert match(Counter(a=1, b=1), Counter(a=1)) == pytest.approx(0.5)
    assert match(Counter(a=1), Counter(b=1)) == 0
    assert match(Counter(), Counter(a=1)) == 0


def test_measure_counts_copies(angelic):
    batch = [
        made_name("Aalbe'il"),  # a seed name
        made_name("Aalbeil"),  # the same seed name, without the apostrophe
        made_name("Aalbe'ix"),  # one change from it
        made_name("Zzzzzzzz"),
        GeneratedName(None, (), (GaveUp("no word passed validation in 500 tries"),), "#"),
    ]
    measured = measure(angelic, batch)
    assert (measured.requested, measured.made) == (5, 4)
    assert measured.seed_copies == 2
    assert measured.near_copies == 1
    assert measured.distinct == 4
    assert not measured.is_seeds


def test_measure_counts_spreads(angelic):
    batch = [made_name("Anael", 1), made_name("Ber'iel", 2), made_name("Anor", 1)]
    measured = measure(angelic, batch)
    assert measured.first_letters == Counter(a=2, b=1)
    assert measured.endings == Counter({"el": 2, "or": 1})
    assert measured.lengths == Counter({5: 1, 6: 1, 4: 1})
    assert measured.structures == Counter({1: 2, 2: 1})


def test_measure_counts_failures_and_loosened_rules(angelic):
    failed = BuildAttempt(Shape(3, None), GenerationOptions(), None, (), NO_OPTION)
    events = (
        failed,
        failed,
        Relaxed("Fit_lvl", 2),
        Checked("Ananas", 2, Rejection("!DIST/A", "")),
        Checked("Enebe", 2, Rejection("!DIST/E", "")),
        Checked("Ilili", 2, Rejection("!FREQ/I", "")),
        ValidationLowered(1),
        HistoryConflict("Anael", ignored=False),
        HistoryConflict("Anael", ignored=True),
        CopyRefused("Ariel", ignored=False),
        CopyRefused("Ariel", ignored=True),
    )
    measured = measure(angelic, [made_name("Anael", events=events), made_name("Bel")])
    assert measured.build_attempts == 4  # two failed, plus one for each name made
    assert measured.failures == Counter(
        {NO_OPTION: 2, "!DIST": 2, FREQUENCY_FAILURE: 1, HISTORY_FAILURE: 1, COPY_FAILURE: 1}
    )
    assert measured.relaxations == Counter({("Fit_lvl", 2): 1, ("Val_lvl", 1): 1})
    assert measured.repeats_accepted == 1
    assert measured.per_100(1) == 50


def test_measure_a_real_batch(angelic):
    generator = NameGenerator(angelic, rng=random.Random(3))
    batch = [generator.generate() for _ in range(300)]
    measured = measure(angelic, batch)
    assert measured.made == 300
    assert sum(measured.first_letters.values()) == 300
    assert sum(measured.structures.values()) == sum(len(name.words) for name in batch)
    assert measured.build_attempts >= 300
    failed_builds = [
        event for name in batch for event in name.events
        if isinstance(event, BuildAttempt) and event.failure == NO_OPTION
    ]  # fmt: skip
    assert measured.failures[NO_OPTION] == len(failed_builds)
    assert measured.seed_copies + measured.near_copies <= measured.made
    assert measured.nearest_distance > 0

    again = NameGenerator(angelic, rng=random.Random(3))
    assert measure(angelic, [again.generate() for _ in range(300)]) == measured


def test_measure_seeds_compares_each_seed_with_the_others():
    style = parse_ebn('TITLE = "T"; NAMES = "Ana", "Ana", "Anna", "Belor";')
    measured = measure_seeds(analyze(style))
    assert measured.is_seeds
    assert measured.seed_copies is None and measured.build_attempts is None
    assert measured.made == 4
    assert measured.distinct == 3
    # Ana (twice) and Anna are one change from each other; Belor is far from both.
    assert measured.near_copies == 3


def test_a_long_list_of_seeds_is_measured_on_some_of_them(monkeypatch):
    import namebook.quality

    monkeypatch.setattr(namebook.quality, "MEASURED_NAMES", 2)
    style = parse_ebn('TITLE = "T"; NAMES = "Ana", "Anna", "Belor", "Belar";')
    measured = measure_seeds(analyze(style))
    assert measured.made == 4 and measured.compared_names == 2  # Ana and Belor
    assert measured.near_copies == 2 and len(measured.letter_scores) == 2
    near = report(measured, [])[0].rows[2]
    assert near.seeds == "100.0%"  # of the two compared, not of all four


def test_report_sections_and_columns(angelic):
    seeds = measure_seeds(angelic)
    first = measure(angelic, [made_name("Anael")])
    second = measure(angelic, [made_name("Bel"), made_name("Cor")])
    sections = report(seeds, [first, second])
    assert [section.title for section in sections] == [
        "Copies of the seed names",
        f"Plausibility (the chain looks back {seeds.look_back} letters)",
        "Variety",
        "Length (letters)",
        "Effort and failures (per 100 names)",
    ]
    rows = {row.label: row for section in sections for row in section.rows}
    assert all(len(row.values) == 2 for row in rows.values())
    assert rows["Names"].seeds == "266" and rows["Names"].values == ("1", "2")
    assert rows["Same as a seed name"].seeds == NO_VALUE
    assert "Within 1 change of a seed name" in rows
    assert rows["Builds per name"].values == ("1.00", "1.00")


def test_report_rows_for_every_failure_and_loosened_rule(angelic):
    failed = BuildAttempt(Shape(3, None), GenerationOptions(), None, (), NO_OPTION)
    first = measure(angelic, [made_name("Anael", events=(failed, Relaxed("Fit_lvl", 1)))])
    second = measure(angelic, [made_name("Bel", events=(
        Checked("Ilili", 2, Rejection("!FREQ/I", "")),
        Relaxed("Fit_lvl", 2),
        Relaxed("Structural"),
        ValidationLowered(1),
    ))])  # fmt: skip
    effort = report(measure_seeds(angelic), [first, second])[-1]
    labels = [row.label for row in effort.rows]
    assert labels == [
        "Builds per name",
        "Failed: no element fits (!NOOPT)",
        "Failed: one letter too common (!FREQ)",
        'Changed "Fit_lvl" to 2.',
        'Changed "Fit_lvl" to 1.',
        'Dropped "Structural".',
        'Changed "Val_lvl" to 1.',
    ]
    assert effort.rows[1].values == ("100.0", "0.0")


def test_plausibility_of_seeds_and_names(angelic):
    seeds = measure_seeds(angelic)
    assert len(seeds.letter_scores) == 266 and seeds.look_back >= 1
    measured = measure(angelic, [made_name("Aalbe'il"), made_name("Xqzrtkp")])
    copy, jumble = measured.letter_scores
    assert copy > jumble
    # A copy of a seed name scores higher than the seed name hidden from the chain.
    assert copy > seeds.letter_scores[angelic.style.name_lists[0].names.index("Aalbe'il")]


def test_plausibility_rows(angelic):
    seeds = measure_seeds(angelic)
    measured = measure(angelic, [made_name("Raphael"), made_name("Xqzrtkp")])
    section = report(seeds, [measured])[1]
    rows = {row.label: row for row in section.rows}
    unlikely = rows["Less likely than 95% of the seed names"]
    assert 4 <= float(unlikely.seeds.rstrip("%")) <= 5  # the seeds set the bar
    assert unlikely.values == ("50.0%",)  # the jumbled name is below it
    assert rows["Chance of each letter (average)"].seeds.endswith("%")


def test_report_near_label_follows_the_limit(angelic):
    seeds = measure_seeds(angelic, near_limit=2)
    measured = measure(angelic, [made_name("Anael")], near_limit=2)
    labels = [row.label for row in report(seeds, [measured])[0].rows]
    assert "Within 2 changes of a seed name" in labels
