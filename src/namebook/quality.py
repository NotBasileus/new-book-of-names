"""Quality measurements (spec 11).

``measure`` looks at a batch of generated names, and ``measure_seeds`` at a
style's own seed names in the same terms, so the two can be shown side by
side:

- copies: how many names are a seed name, or nearly one, and how different
  the names in the batch are from each other;
- plausibility: how likely a Markov chain learned from the seed names
  finds each letter of a name (spec 13.2);
- variety: how first letters, endings and structures are spread;
- length;
- effort and failures: how hard the generator worked, counted from the
  events each name records.

``report`` turns the measurements of one or more batches into rows of
plain-language text, with the seeds as a reference column. The numbers stay
available on ``Measurements``, for comparing batches in other ways.

Names are compared in a plain form (``plain``) that ignores capitals,
accents, spaces, apostrophes and hyphens.
"""

from __future__ import annotations

import bisect
import math
from collections import Counter
from collections.abc import Iterable, Sequence
from itertools import chain
from dataclasses import dataclass, field
from functools import cached_property

from namebook.analysis import StyleAnalysis
from namebook.building import NO_OPTION, NO_PREFIX, NO_SUFFIX, BuildAttempt
from namebook.generator import (
    COPY_FAILURE,
    HISTORY_FAILURE,
    LENGTH_FAILURE,
    Checked,
    CopyRefused,
    GeneratedName,
    HistoryConflict,
    LengthRejected,
    Relaxed,
    ValidationLowered,
)
from namebook.markov import MEASURED_NAMES, Chain, plain_letters, spread
from namebook.validation import (
    DISTANCE_FAILURE,
    FREQUENCY_FAILURE,
    REPEAT_FAILURE,
    WORD_LIST_FAILURE,
)
from namebook.words import plain

NEAR_COPY_CHANGES = 1  # names this many changes or fewer from a seed are near copies
ENDING_LENGTH = 2  # letters that make up an ending
LOW_PLAUSIBILITY = 0.05  # the least plausible share of seed names sets the bar for "unlikely"

# Failure codes in the order they are reported, with what they mean.
FAILURE_LABELS = {
    NO_OPTION: "no element fits",
    NO_PREFIX: "no prefix fits",
    NO_SUFFIX: "no suffix fits",
    WORD_LIST_FAILURE: "on the word list",
    REPEAT_FAILURE: "letters repeat",
    DISTANCE_FAILURE: "same letter too close",
    FREQUENCY_FAILURE: "one letter too common",
    LENGTH_FAILURE: "too short or too long",
    COPY_FAILURE: "a seed name",
    HISTORY_FAILURE: "made recently",
}
# Loosened settings in the order they are loosened (spec 7.4), then the validation level.
SETTING_ORDER = ("Fit_lvl", "Structural", "Prefixes", "Suffixes", "Val_lvl")

NO_VALUE = "—"


def edit_distance(a: str, b: str, limit: int | None = None) -> int:
    """How many changes turn ``a`` into ``b``; a change adds, removes or replaces one letter.

    With a ``limit``, stops as soon as the answer is known to be larger and
    returns ``limit + 1``.
    """
    if limit is not None and abs(len(a) - len(b)) > limit:
        return limit + 1
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            current.append(min(
                previous[j] + 1,  # remove char_a
                current[j - 1] + 1,  # add char_b
                previous[j - 1] + (char_a != char_b),  # replace, or keep if equal
            ))  # fmt: skip
        if limit is not None and min(current) > limit:
            return limit + 1
        previous = current
    distance = previous[-1]
    return distance if limit is None or distance <= limit else limit + 1


def letter_pairs(name: str) -> list[str]:
    """Each pair of neighbouring letters in ``name``, its start and end counting as letters.

    A pair met again is numbered ("an0", "an1"), so two names share a pair as
    many times as both have it.
    """
    padded = f"^{name}$"
    met: Counter[str] = Counter()
    pairs = []
    for pos in range(len(padded) - 1):
        pair = padded[pos : pos + 2]
        pairs.append(f"{pair}{met[pair]}")
        met[pair] += 1
    return pairs


def trimmed(name: str) -> set[str]:
    """``name``, and ``name`` with any one letter taken out."""
    return {name} | {name[:pos] + name[pos + 1 :] for pos in range(len(name))}


class SeedIndex:
    """A style's seed names in plain form, arranged for finding the nearest one
    quickly, and the chain that scores how plausible a name is."""

    def __init__(self, names: Iterable[str]):
        self._written = tuple(names)
        self.names = frozenset(plain(name) for name in self._written)
        self._seeds = sorted(self.names)
        self._by_length: dict[int, list[str]] = {}
        self._with_trimmed: dict[str, list[str]] = {}  # the seeds that trim to each name
        self._with_pair: dict[str, list[int]] = {}  # the seeds that have each letter pair, by number
        for number, seed in enumerate(self._seeds):
            self._by_length.setdefault(len(seed), []).append(seed)
            for trim in trimmed(seed):
                self._with_trimmed.setdefault(trim, []).append(seed)
            for pair in letter_pairs(seed):
                self._with_pair.setdefault(pair, []).append(number)
        self._longest = max(self._by_length, default=0)

    @classmethod
    def for_analysis(cls, analysis: StyleAnalysis) -> SeedIndex:
        return cls(analysis.style.name_list(analysis.list_name).names)

    def nearest(self, name: str, skip_itself: bool = False) -> int | None:
        """The fewest changes that turn ``name``, in plain form, into a seed name.

        ``skip_itself`` leaves out the seed equal to ``name``, for comparing
        the seeds with each other. None if there is no seed to compare with.
        """
        itself = skip_itself and name in self.names
        if name in self.names and not itself:
            return 0
        if len(self._seeds) == (1 if itself else 0):
            return None
        # A seed 1 change away trims to the same name as ``name`` does, when
        # the letter that differs is taken out of both, or out of the longer.
        for trim in trimmed(name):
            for seed in self._with_trimmed.get(trim, ()):
                if not (itself and seed == name) and edit_distance(name, seed, 1) <= 1:
                    return 1
        pairs = letter_pairs(name)
        shared = Counter(chain.from_iterable(self._with_pair.get(pair, ()) for pair in pairs))
        if itself:
            del shared[bisect.bisect_left(self._seeds, name)]
        # A change spoils at most two of a name's letter pairs, so a seed that
        # shares s of its n pairs is at least (n - s) / 2 changes away. Look
        # for a seed 2 changes away among those that could be, then 3, and so
        # on: the first one found is the nearest.
        for changes in range(2, max(len(name), self._longest) + 1):
            least = len(pairs) - 2 * changes
            if least > 0:
                candidates = [self._seeds[number] for number, count in shared.items() if count >= least]
            else:  # even a seed that shares no pair could be this close
                candidates = [
                    seed
                    for length in range(len(name) - changes, len(name) + changes + 1)
                    for seed in self._by_length.get(length, ())
                ]  # fmt: skip
            for seed in candidates:
                if itself and seed == name:
                    continue
                if abs(len(seed) - len(name)) <= changes and edit_distance(name, seed, changes) <= changes:
                    return changes
        return None

    @cached_property
    def chain(self) -> Chain:
        """The chain that scores how plausible a name is (``plausibility_chain``)."""
        return plausibility_chain(self._written)


def plausibility_chain(names: Iterable[str]) -> Chain:
    """The chain learned from the seed names as written, whatever the style's
    settings, so every batch of a style is scored alike. A long list chooses
    its look-back by hiding only the measured seed names."""
    letters = [plain_letters(name) for name in names]
    return Chain(letters, hiding=spread(letters, MEASURED_NAMES))


@dataclass(frozen=True)
class Measurements:
    """What a batch of names looks like.

    For the seed names themselves, ``requested``, ``seed_copies`` and the
    effort fields are None, and each seed is compared with the other seeds.
    """

    names: tuple[str, ...]  # the names made, or the seed names, in order
    requested: int | None  # how many names were asked for
    near_limit: int
    distinct: int  # how many different names, ignoring capitals
    seed_copies: int | None  # names that are a seed name
    near_copies: int  # names within near_limit changes of a seed name, but not the same
    nearest_distance: float | None  # average changes to the nearest seed name
    first_letters: Counter[str]
    endings: Counter[str]
    structures: Counter[int]  # the structure of each word
    lengths: Counter[int]  # letters in each name's plain form
    build_attempts: int | None = None
    failures: Counter[str] = field(default_factory=Counter)  # failure code -> times
    # (setting, new value) -> times, e.g. ("Fit_lvl", 2) or ("Structural", None)
    relaxations: Counter[tuple[str, int | None]] = field(default_factory=Counter)
    repeats_accepted: int = 0
    # Each name's plausibility (``Chain.score``); seed names are scored by
    # the chain learned without them.
    letter_scores: tuple[float, ...] = ()
    look_back: int | None = None  # how many letters the scoring chain looks back
    compared: int | None = None  # names compared with the seeds, when not all of them

    @property
    def compared_names(self) -> int:
        """How many names were compared with the seeds: all of them, or those of a long list measured."""
        return self.made if self.compared is None else self.compared

    @property
    def is_seeds(self) -> bool:
        return self.requested is None

    @property
    def made(self) -> int:
        return len(self.names)

    def per_100(self, count: int) -> float:
        """``count`` per 100 names asked for."""
        return count * 100 / self.requested if self.requested else 0.0


def measure(
    analysis: StyleAnalysis,
    batch: Sequence[GeneratedName],
    near_limit: int = NEAR_COPY_CHANGES,
    seeds: SeedIndex | None = None,
) -> Measurements:
    """Measure a batch of names generated from ``analysis``."""
    seeds = seeds or SeedIndex.for_analysis(analysis)
    made = [name for name in batch if name.ok]
    plains = [plain(name.text) for name in made]
    nearest = {name: seeds.nearest(name) for name in set(plains)}
    distances = [nearest[name] for name in plains]

    failures: Counter[str] = Counter()
    relaxations: Counter[tuple[str, int | None]] = Counter()
    build_attempts = repeats_accepted = 0
    for name in batch:
        for event in name.events:
            if isinstance(event, BuildAttempt):
                build_attempts += 1
                if event.failure:
                    failures[event.failure] += 1
            elif isinstance(event, Checked) and event.rejection:
                # "!DIST/A" and "!DIST/E" both count as "!DIST".
                failures[event.rejection.code.split("/")[0]] += 1
            elif isinstance(event, LengthRejected):
                failures[LENGTH_FAILURE] += 1
            elif isinstance(event, HistoryConflict):
                if event.ignored:
                    repeats_accepted += 1
                else:
                    failures[HISTORY_FAILURE] += 1
            elif isinstance(event, CopyRefused) and not event.ignored:
                failures[COPY_FAILURE] += 1
            elif isinstance(event, Relaxed):
                relaxations[event.setting, event.value] += 1
            elif isinstance(event, ValidationLowered):
                relaxations["Val_lvl", event.level] += 1

    return Measurements(
        names=tuple(name.text for name in made),
        requested=len(batch),
        near_limit=near_limit,
        distinct=len({name.text.lower() for name in made}),
        seed_copies=sum(distance == 0 for distance in distances),
        near_copies=sum(0 < distance <= near_limit for distance in distances),
        nearest_distance=_mean(distances),
        structures=Counter(word.shape.structure for name in made for word in name.words),
        build_attempts=build_attempts,
        failures=failures,
        relaxations=relaxations,
        repeats_accepted=repeats_accepted,
        letter_scores=tuple(seeds.chain.score(plain_letters(name.built)) for name in made),
        look_back=seeds.chain.look_back,
        **_spreads(plains),
    )


def measure_seeds(
    analysis: StyleAnalysis,
    near_limit: int = NEAR_COPY_CHANGES,
    seeds: SeedIndex | None = None,
) -> Measurements:
    """Measure the style's own seed names, each compared with the other seeds.

    A list of more than MEASURED_NAMES names is compared and scored on that
    many of them, spread evenly through the list, which is quicker and tells
    much the same.
    """
    seeds = seeds or SeedIndex.for_analysis(analysis)
    names = analysis.style.name_list(analysis.list_name).names
    measured = spread(names, MEASURED_NAMES)
    plains = [plain(name) for name in names]
    nearest = {name: seeds.nearest(name, skip_itself=True) for name in {plain(name) for name in measured}}
    distances = [nearest[plain(name)] for name in measured if nearest[plain(name)] is not None]
    return Measurements(
        names=tuple(names),
        requested=None,
        near_limit=near_limit,
        distinct=len({name.lower() for name in names}),
        seed_copies=None,
        near_copies=sum(0 < distance <= near_limit for distance in distances),
        nearest_distance=_mean(distances),
        structures=Counter(analysis.structures),
        letter_scores=tuple(seeds.chain.hidden_score(plain_letters(name)) for name in measured),
        compared=len(measured),
        look_back=seeds.chain.look_back,
        **_spreads(plains),
    )


def _spreads(plains: Sequence[str]) -> dict[str, Counter]:
    return {
        "first_letters": Counter(name[0] for name in plains if name),
        "endings": Counter(name[-ENDING_LENGTH:] for name in plains if name),
        "lengths": Counter(len(name) for name in plains),
    }


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def match(spread: Counter, reference: Counter) -> float:
    """How much two spreads overlap: 0 if they have nothing in common, 1 if they are alike.

    Each spread is turned into shares of its total; the overlap is the sum,
    over every kind, of the smaller of its two shares.
    """
    total, reference_total = sum(spread.values()), sum(reference.values())
    if not total or not reference_total:
        return 0.0
    return sum(
        min(count / total, reference[kind] / reference_total) for kind, count in spread.items()
    )


# --- The report: measurements as rows of text --------------------------------


@dataclass(frozen=True)
class ReportRow:
    label: str
    seeds: str  # the value for the seed names, or NO_VALUE
    values: tuple[str, ...]  # one per batch


@dataclass(frozen=True)
class ReportSection:
    title: str
    rows: tuple[ReportRow, ...]


REPORT_NOTES = (
    "A change adds, removes or replaces one letter. Names are compared ignoring "
    "capitals, accents, spaces, apostrophes and hyphens.",
    f"In the Seeds column, each seed name is compared with the other seed names "
    f"(in a list of more than {MEASURED_NAMES:,}, {MEASURED_NAMES:,} of them, spread through it).",
    "Plausibility: a chain learned from the seed names gives the chance of each "
    "letter, and of the name ending there, after the letters before it. Each seed "
    "name is scored by a chain learned without it. Higher means the letters follow "
    "the seeds' patterns more closely; copies of seed names score high too, so read "
    "it with the copy numbers.",
    "Different names: a bigger batch repeats itself more, so this is best compared "
    "between batches of the same size.",
    "Match with the seeds: how much the two spreads overlap. 100% means the "
    "names are spread exactly like the seeds.",
    "Effort and failures are counted per 100 names asked for. The codes are the "
    "ones explain mode shows.",
)


def report(seeds: Measurements, batches: Sequence[Measurements]) -> list[ReportSection]:
    """The measurements of ``batches`` as rows of text, with the seeds for reference."""

    def row(label: str, seed_value: str, value) -> ReportRow:
        return ReportRow(label, seed_value, tuple(value(batch) for batch in batches))

    def kinds(spread: str) -> list[ReportRow]:
        return [
            row(f"{SPREAD_LABELS[spread]}: kinds", str(len(getattr(seeds, spread))),
                lambda batch: str(len(getattr(batch, spread)))),
            row(f"{SPREAD_LABELS[spread]}: match with the seeds", NO_VALUE,
                lambda batch: _percent(match(getattr(batch, spread), getattr(seeds, spread)), 0)),
        ]  # fmt: skip

    near = f"Within {_changes(seeds.near_limit)} of a seed name"
    sections = [
        ReportSection("Copies of the seed names", (
            row("Names", str(seeds.made), _names_made),
            row("Same as a seed name", NO_VALUE,
                lambda batch: _share(batch.seed_copies, batch.made)),
            row(near, _share(seeds.near_copies, seeds.compared_names),
                lambda batch: _share(batch.near_copies, batch.made)),
            row("Average changes to the nearest seed", _decimal(seeds.nearest_distance),
                lambda batch: _decimal(batch.nearest_distance)),
            row("Different names", _share(seeds.distinct, seeds.made),
                lambda batch: _share(batch.distinct, batch.made)),
        )),
        *([_plausibility(seeds, batches)] if seeds.letter_scores else []),
        ReportSection("Variety", tuple(
            kinds("first_letters") + kinds("endings") + kinds("structures")
        )),
        ReportSection("Length (letters)", (
            row("Average", _decimal(_average_length(seeds)),
                lambda batch: _decimal(_average_length(batch))),
            row("Shortest to longest", _length_range(seeds), _length_range),
            row("Match with the seeds", NO_VALUE,
                lambda batch: _percent(match(batch.lengths, seeds.lengths), 0)),
        )),
        ReportSection("Effort and failures (per 100 names)", tuple(_effort_rows(batches))),
    ]  # fmt: skip
    return sections


def _plausibility(seeds: Measurements, batches: Sequence[Measurements]) -> ReportSection:
    ordered = sorted(seeds.letter_scores)
    bar = ordered[int(len(ordered) * LOW_PLAUSIBILITY)]

    def chance(batch: Measurements) -> str:
        if not batch.letter_scores:
            return NO_VALUE
        return _percent(math.exp(_mean(batch.letter_scores)), 1)

    def unlikely(batch: Measurements) -> str:
        return _share(sum(score < bar for score in batch.letter_scores), len(batch.letter_scores))

    likelier = f"{100 - LOW_PLAUSIBILITY * 100:.0f}%"
    letters = "letter" if seeds.look_back == 1 else "letters"
    return ReportSection(
        f"Plausibility (the chain looks back {seeds.look_back} {letters})",
        (
            ReportRow("Chance of each letter (average)", chance(seeds),
                      tuple(chance(batch) for batch in batches)),
            ReportRow(f"Less likely than {likelier} of the seed names", unlikely(seeds),
                      tuple(unlikely(batch) for batch in batches)),
        ),
    )  # fmt: skip


SPREAD_LABELS = {
    "first_letters": "First letters",
    "endings": f"Endings (last {ENDING_LENGTH} letters)",
    "structures": "Structures",
}


def _effort_rows(batches: Sequence[Measurements]) -> list[ReportRow]:
    def per_100(count_of) -> tuple[str, ...]:
        return tuple(f"{batch.per_100(count_of(batch)):.1f}" for batch in batches)

    rows = [
        ReportRow("Builds per name", NO_VALUE, tuple(
            f"{batch.build_attempts / batch.requested:.2f}" if batch.requested else NO_VALUE
            for batch in batches
        )),
    ]  # fmt: skip
    codes = {code for batch in batches for code in batch.failures}
    for code in sorted(codes, key=_failure_order):
        label = f"Failed: {FAILURE_LABELS.get(code, 'other')} ({code})"
        rows.append(ReportRow(label, NO_VALUE, per_100(lambda batch: batch.failures[code])))
    if any(batch.repeats_accepted for batch in batches):
        rows.append(ReportRow(
            "Recent name made again anyway", NO_VALUE, per_100(lambda batch: batch.repeats_accepted)
        ))  # fmt: skip
    changes = {change for batch in batches for change in batch.relaxations}
    for change in sorted(changes, key=_relaxation_order):
        label = Relaxed(*change).message
        rows.append(ReportRow(label, NO_VALUE, per_100(lambda batch: batch.relaxations[change])))
    return rows


def _failure_order(code: str) -> tuple[int, str]:
    order = list(FAILURE_LABELS)
    return (order.index(code) if code in order else len(order), code)


def _relaxation_order(change: tuple[str, int | None]) -> tuple[int, int]:
    setting, value = change
    position = SETTING_ORDER.index(setting) if setting in SETTING_ORDER else len(SETTING_ORDER)
    return (position, -(value or 0))


def _names_made(batch: Measurements) -> str:
    if batch.requested is not None and batch.made < batch.requested:
        return f"{batch.made} of {batch.requested}"
    return str(batch.made)


def _average_length(batch: Measurements) -> float | None:
    total = sum(batch.lengths.values())
    return sum(length * n for length, n in batch.lengths.items()) / total if total else None


def _length_range(batch: Measurements) -> str:
    if not batch.lengths:
        return NO_VALUE
    return f"{min(batch.lengths)} to {max(batch.lengths)}"


def _changes(count: int) -> str:
    return f"{count} change" + ("" if count == 1 else "s")


def _share(part: int | None, whole: int) -> str:
    if part is None or not whole:
        return NO_VALUE
    return _percent(part / whole, 1)


def _percent(fraction: float, digits: int) -> str:
    return f"{fraction * 100:.{digits}f}%"


def _decimal(value: float | None) -> str:
    return NO_VALUE if value is None else f"{value:.1f}"
