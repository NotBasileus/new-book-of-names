"""Inference (spec 10.4): working out every setting a style does not pin, from its names.

``infer`` returns the style's *working settings*: its pinned settings as
written, and every other setting worked out from the names, each with a
plain-language reason. Inference never changes the style file; saving is a
separate step (``create --pin`` writes the working settings, D52).

Generation settings start at the fullest, the ones that follow the names
most closely, and back off only where the names call for it (D68):

- the fit level, prefixes, suffixes and validation level start at their
  strictest. namebook already loosens them by itself, name by name, when a
  name cannot be built (spec 7.4);
- a name that copies one of the list's names is made again (D81);
- when words are built the classic way (``markov`` off or weighted), a list with
  fewer than ``SMALL_LIST`` names backs off further, since the classic way at the
  fullest settings makes few different names from a small list. Filling
  words from the chain needs no such back-off (D81).

Spelling settings (soft consonants, the Y and U rules) are chosen by
hiding names (spec 10.6, D80): each value is tried on the names, and one
wins only if the hidden names are clearly likelier with it. Otherwise the
plain value stays (TH as two letters, Y and U as vowels). The apostrophe's
class is chosen by test runs instead (D87): names are made with it a vowel
and with it a consonant, and it becomes a consonant only if those names are
clearly likelier. Hiding names cannot choose it, since the chain reads the
same letters either way. A hyphen in the names joins two words, as a space
does, unless some hyphen joins nothing (spec 13.15).

Word shapes follow the names (D53, D72): each word may have any shape the
names' words have, and when some names are several words, templates of
``#`` slots make names of one, two or more words in the same proportions,
joined by a space or a hyphen as the names are.
Lengths follow the names too, so no length range is inferred.

Words are built with a Markov chain at its full strength (spec 13.3): filled
letter by letter from it (``markov = "fill"``), or built the classic way with the
chain weighing the choices (``"weighted"``). A test run on hidden halves
chooses between them (D96): names are made from half the names each way and
judged by a chain learned from the other half. Weighted choice wins only if
its names are clearly likelier; otherwise, and for a small list, fill stays.
A style that pins structural off gets weighted choice, since fill needs
structural on. The chain's look-back is chosen by hiding names (spec 10.6).
"""

from __future__ import annotations

import dataclasses
import math
import random
import statistics
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property

from namebook.analysis import analyze
from namebook.formats.common import soften
from namebook.generator import GenerationError, NameGenerator
from namebook.letters import APOSTROPHE, LetterClasses, capitals
from namebook.markov import MEASURED_NAMES, Chain, Letters, internal_letters, plain_letters, spread
from namebook.quality import plausibility_chain
from namebook.style import (
    DEFAULT_MARKOV_STRENGTH,
    MAX_FIT_LEVEL,
    MAX_VALIDATION_LEVEL,
    ApostropheMode,
    HyphenMode,
    Markov,
    NameList,
    Punctuation,
    Style,
    Template,
    UMode,
    YMode,
)
from namebook.templates import ANY_STRUCTURE
from namebook.words import HYPHEN, WordSplitError, WordSplitter

SMALL_LIST = 40  # fewer different names than this: the classic way backs off (D68, D81)
SMALL_LIST_FIT_LEVEL = 1
SHOWN_PAIRS = 3
CLEAR_MARGIN = 2.0  # a spelling must win by this many standard errors (D80)
TEST_RUN_NAMES = 600  # names a test run makes with each value it tries (D87)
TEST_RUN_SEED = 1  # so a style's settings come out the same each time it is loaded

# Spelling settings in the order they are measured: each one's plain value,
# and the value tried against it.
SPELLINGS = {
    "letter_y": (YMode.VOWEL, YMode.SEMI),
    "letter_u": (UMode.VOWEL, UMode.SEMI),
    "soft_consonants": (False, True),
}


@dataclass(frozen=True)
class Inference:
    """A style's working settings, and why each inferred setting has its value."""

    written: Style  # the style as read
    working: Style  # pinned settings as written; every other setting inferred
    reasons: dict[str, str]  # why, for each inferred setting, by name


def infer_lists(
    style: Style, ignore_pins: bool = False, given: Mapping[str, object] | None = None
) -> dict[str, Inference]:
    """Work out each list's settings on its own, by its name (spec 13.5).

    Each list is inferred as a style of its own (``Style.list_style``): its
    names, with the settings it pins over those the style pins. ``given``
    settings apply to every list.
    """
    return {name: infer(style.list_style(name), ignore_pins, given) for name in style.list_names}


def infer(
    style: Style, ignore_pins: bool = False, given: Mapping[str, object] | None = None
) -> Inference:
    """Work out every setting ``style`` does not pin (or every setting, with ``ignore_pins``).

    ``given`` settings, from the command line, are used as if pinned, so the
    others are worked out around them.
    """
    given = dict(given or {})
    pinned = (frozenset() if ignore_pins else style.pinned) | frozenset(given)
    inferred = {key: choice for key, choice in _choices(style).items() if key not in pinned}
    working = style.with_settings(given).with_settings({key: value for key, (value, _) in inferred.items()})
    reasons = {key: reason for key, (_, reason) in inferred.items()}
    if "templates" in reasons:
        templates, reasons["templates"] = _word_shapes(working)
        working = working.with_settings({"templates": templates})
    readings = _Readings(soften_clusters="consonant_clusters" in pinned)
    for key, values in SPELLINGS.items():
        if key in reasons:
            plain, other = (readings.of(working.with_settings({key: value})) for value in values)
            choice, reasons[key] = _spelling(key, plain, other)
            working = working.with_settings({key: choice})
    working = readings.spelled(working)
    if "markov" in reasons and working.options.markov is Markov.FILL and not working.options.structural:
        working = working.with_settings({"markov": Markov.WEIGHTED})
        reasons["markov"] = "weighted: fill needs structural on, and the style pins it off."
    if "markov_order" in reasons and working.options.markov is not Markov.OFF:
        # The chain learns from the names as the working settings split them,
        # which fill and weighted choice do alike.
        look_back = readings.of(working).look_back
        working = working.with_settings({"markov_order": look_back})
        reasons["markov_order"] = (
            f"{look_back}: names held back as a test read most naturally when the chain looks back "
            f"{_letters(look_back)}."
        )
    if "markov" in reasons and working.options.markov is Markov.FILL:
        choice, reasons["markov"] = _markov(working)
        working = working.with_settings({"markov": choice})
    if working.options.markov is not Markov.FILL:
        for key, (value, reason) in _small_list(style.names).items():
            if key in reasons:
                working = working.with_settings({key: value})
                reasons[key] = reason
    if "letter_apostrophe" in reasons:
        # Last, so the test runs make names with every other working setting.
        choice, reasons["letter_apostrophe"] = _apostrophe(working, readings)
        working = working.with_settings({"letter_apostrophe": choice})
    working = dataclasses.replace(working, pinned=pinned)
    return Inference(style, working, reasons)


class _Reading:
    """A style's names as one set of spelling settings reads them, and the
    chain learned from them, for hiding names.

    Every name is read, so that a name that cannot be split is caught; the
    chain learns from at most MEASURED_NAMES of them, spread evenly through
    the list, and hides each of those in turn.
    """

    def __init__(self, style: Style):
        splitter = WordSplitter.for_style(style)
        self.names = style.names
        self.letter_classes = style.letter_classes
        self.letters: list[Letters | None] = [internal_letters(splitter, name) for name in style.names]

    def unsplit(self) -> list[str]:
        """The names this reading cannot split into elements."""
        return [name for name, letters in zip(self.names, self.letters) if letters is None]

    @cached_property
    def _measured(self) -> list[Letters | None]:
        return spread(self.letters, MEASURED_NAMES)

    @cached_property
    def chain(self) -> Chain:
        """The chain learned from the measured names."""
        return Chain([letters for letters in self._measured if letters is not None])

    @cached_property
    def look_back(self) -> int:
        """The look-back of the chain learned from every name, hiding the measured ones."""
        if len(self.letters) <= MEASURED_NAMES:
            return self.chain.look_back
        every = [letters for letters in self.letters if letters is not None]
        return Chain(every, hiding=[letters for letters in self._measured if letters is not None]).look_back

    @cached_property
    def hidden(self) -> list[float | None]:
        """The log-chance of each measured name, by the chain learned without it."""
        chain = self.chain
        return [None if letters is None else chain.hidden_log_chance(letters) for letters in self._measured]


class _Readings:
    """A style's readings under each spelling tried, kept so that none is made twice."""

    def __init__(self, soften_clusters: bool):
        self.soften_clusters = soften_clusters
        self._made: dict[object, _Reading] = {}

    def spelled(self, style: Style) -> Style:
        """``style``, with pinned clusters in the form its soft consonants need."""
        if style.special_letters.soft_consonants and self.soften_clusters:
            # Pinned clusters are written in full ("LTH"); with soft consonants
            # on, they must use the soft form ("Lt") to match (spec 4.1).
            clusters = style.special_letters.consonant_clusters
            softened = tuple(dict.fromkeys(soften(cluster, style.letter_classes) for cluster in clusters))
            return style.with_settings({"consonant_clusters": softened})
        return style

    def of(self, style: Style) -> _Reading:
        style = self.spelled(style)
        options = style.options
        key = (style.special_letters, options.y_mode, options.u_mode, options.hyphen, style.letter_classes)
        if key not in self._made:
            self._made[key] = _Reading(style)
        return self._made[key]


def _spelling(key: str, plain: _Reading, other: _Reading) -> tuple[object, str]:
    """Choose a spelling setting's plain value or the other one, by hiding names."""
    plain_value, other_value = SPELLINGS[key]
    words = _SPELLING_REASONS[key]
    pairs = _soft_pairs(plain.names, plain.letter_classes)
    if plain.letters == other.letters:
        return plain_value, words["same"]
    # Every reading splits the same names, since a word of only vowels or only
    # consonants is one element (spec 13.10); hidden names decide.
    differences = [
        after - before
        for before, after in zip(plain.hidden, other.hidden)
        if before is not None and after is not None
    ]  # fmt: skip
    if len(differences) < 2:
        return plain_value, words["even"].format(pairs=pairs)
    mean = statistics.fmean(differences)
    margin = CLEAR_MARGIN * statistics.stdev(differences) / math.sqrt(len(differences))
    if mean > margin:
        return other_value, words["other"].format(likelier=_likelier(mean), pairs=pairs)
    if -mean > margin:
        return plain_value, words["plain"].format(likelier=_likelier(-mean), pairs=pairs)
    return plain_value, words["even"].format(pairs=pairs)


def _apostrophe(working: Style, readings: _Readings) -> tuple[ApostropheMode, str]:
    """Choose the apostrophe's class by test runs: names are made with it a
    vowel and with it a consonant, and scored by how plausible they are."""
    words = _SPELLING_REASONS["letter_apostrophe"]
    if not any(APOSTROPHE in name for name in working.names):
        return ApostropheMode.VOWEL, words["same"]
    vowel, consonant = (working.with_settings({"letter_apostrophe": mode}) for mode in ApostropheMode)
    unsplit = readings.of(vowel).unsplit()  # the same names either way
    if unsplit:
        return ApostropheMode.VOWEL, words["unsplit"].format(name=unsplit[0])
    chain = plausibility_chain(working.names)
    try:
        plain = _test_run(vowel, chain)
    except GenerationError:
        return ApostropheMode.CONSONANT, words["plain fails"]
    try:
        other = _test_run(consonant, chain)
    except GenerationError:
        return ApostropheMode.VOWEL, words["other fails"]
    if min(len(plain), len(other)) < 2:
        return ApostropheMode.VOWEL, words["even"]
    mean = statistics.fmean(other) - statistics.fmean(plain)
    error = math.sqrt(statistics.variance(plain) / len(plain) + statistics.variance(other) / len(other))
    if mean > CLEAR_MARGIN * error:
        return ApostropheMode.CONSONANT, words["other"].format(likelier=_likelier(mean))
    if -mean > CLEAR_MARGIN * error:
        return ApostropheMode.VOWEL, words["plain"].format(likelier=_likelier(-mean))
    return ApostropheMode.VOWEL, words["even"]


def _hyphen(style: Style) -> tuple[HyphenMode, str]:
    """The hyphen is a joint between words when the names have one, and every
    hyphen in them joins two words (spec 13.15)."""
    hyphenated = [name for name in style.names if HYPHEN in name]
    if not hyphenated:
        return HyphenMode.CONSONANT, "consonant, the usual choice: there is no hyphen in the names."
    splitter = WordSplitter(style.letter_classes, hyphen=HyphenMode.JOINT)
    for name in hyphenated:
        try:
            splitter.words_and_joints(name)
        except WordSplitError:
            return HyphenMode.CONSONANT, f"consonant, the usual choice: the hyphen in {name} does not join two words."
    return HyphenMode.JOINT, (
        f"joint: it joins two words of a name, as a space does, in {len(hyphenated)} of "
        f"{len(style.names)} names (such as {hyphenated[0]})."
    )


def _markov(working: Style) -> tuple[Markov, str]:
    """Choose fill or weighted choice by a test run on hidden halves (D96).

    The list is split in two, every other name. Names are made from one half
    in each way and scored by a chain learned from the other half alone, then
    the halves swap. Hiding half the names keeps the score from favoring the
    way that follows the names it learned from most closely: fill follows a
    chain learned from them. Weighted choice wins only if its names are
    clearly likelier; otherwise fill stays.
    """
    words = _MARKOV_REASONS
    count = len({capitals(name) for name in working.names})
    if count < SMALL_LIST:
        return Markov.FILL, words["small"].format(count=count, small=SMALL_LIST)
    names = spread(working.names, MEASURED_NAMES)
    halves = (names[0::2], names[1::2])
    fill, weighted = (working.with_settings({"markov": mode}) for mode in (Markov.FILL, Markov.WEIGHTED))
    try:
        plain = _halves_run(fill, halves)
    except GenerationError:
        return Markov.WEIGHTED, words["fill fails"]
    try:
        other = _halves_run(weighted, halves)
    except GenerationError:
        return Markov.FILL, words["weighted fails"]
    if min(len(plain), len(other)) < 2:
        return Markov.FILL, words["even"]
    mean = statistics.fmean(other) - statistics.fmean(plain)
    error = math.sqrt(statistics.variance(plain) / len(plain) + statistics.variance(other) / len(other))
    if mean > CLEAR_MARGIN * error:
        return Markov.WEIGHTED, words["other"].format(likelier=_likelier(mean))
    if -mean > CLEAR_MARGIN * error:
        return Markov.FILL, words["plain"].format(likelier=_likelier(-mean))
    return Markov.FILL, words["even"]


def _halves_run(style: Style, halves: tuple[list[str], list[str]]) -> list[float]:
    """How plausible each name of a test run on hidden halves is: TEST_RUN_NAMES
    names, half of them made from each half of the names alone, and each scored
    by the chain learned from the other half."""
    scores = []
    name_list = style.name_lists[0]
    for kept, hidden in (halves, halves[::-1]):
        half = dataclasses.replace(style, name_lists=(NameList(name_list.name, tuple(kept)),))
        chain = plausibility_chain(hidden)
        generator = NameGenerator(analyze(half), rng=random.Random(TEST_RUN_SEED))
        made = (generator.generate() for _ in range(TEST_RUN_NAMES // 2))
        scores += [chain.score(plain_letters(name.built)) for name in made if name.ok]
    return scores


_MARKOV_REASONS = {
    "small": "fill: with only {count} different names (fewer than {small}), there are too few to "
    "compare the two ways, and filling words from the chain makes more different names.",
    "other": "weighted: in a test, names made from half the list with weighted choice read {likelier} "
    "more naturally, judged against the other half, than names made by fill.",
    "plain": "fill: in a test, names made by fill from half the list read {likelier} more naturally, "
    "judged against the other half, than names made with weighted choice.",
    "even": "fill, the usual choice: in a test, names made from half the list with weighted choice "
    "read about as naturally, judged against the other half, as names made by fill.",
    "fill fails": "weighted: filling words from the chain can make no names with the pinned settings.",
    "weighted fails": "fill: weighted choice can make no names with the pinned settings.",
}


def _test_run(style: Style, chain: Chain) -> list[float]:
    """How plausible each name of a test run with ``style`` is: TEST_RUN_NAMES
    names, each scored by ``chain`` (as the Plausibility measurement scores them)."""
    generator = NameGenerator(analyze(style), rng=random.Random(TEST_RUN_SEED))
    made = (generator.generate() for _ in range(TEST_RUN_NAMES))
    return [chain.score(plain_letters(name.built)) for name in made if name.ok]


# Each spelling setting's reasons: when both values read the names alike,
# when one is clearly likelier, and when neither is.
_SPELLING_REASONS = {
    "letter_y": {
        "same": "vowel: no Y in the names comes before a vowel at the start of a word "
        "or after a vowel (as in Yara or Maya).",
        "other": "semivowel: names held back as a test read {likelier} more naturally with Y a "
        "consonant before a vowel (as in Yara or Maya).",
        "plain": "vowel: names held back as a test read {likelier} more naturally with Y always a vowel.",
        "even": "vowel: names held back as a test read about as naturally with Y a consonant "
        "before a vowel (as in Yara or Maya).",
    },
    "letter_u": {
        "same": "vowel: there is no QU in the names.",
        "other": "semivowel: names held back as a test read {likelier} more naturally with U after Q a "
        "consonant.",
        "plain": "vowel: names held back as a test read {likelier} more naturally with U after Q a vowel.",
        "even": "vowel: names held back as a test read about as naturally with U after Q a consonant.",
    },
    "soft_consonants": {
        "same": "off: there are no consonant + H pairs (such as TH) in the names.",
        "other": "on: names held back as a test read {likelier} more naturally with pairs like {pairs} "
        "as one letter.",
        "plain": "off: names held back as a test read {likelier} more naturally with pairs like {pairs} "
        "as two letters.",
        "even": "off: names held back as a test read about as naturally with pairs like {pairs} as "
        "one letter.",
    },
    "letter_apostrophe": {
        "same": "vowel, the usual choice: there is no apostrophe in the names.",
        "unsplit": "vowel, the usual choice: {name} cannot be split, so no test names were made.",
        "plain fails": "consonant: with the apostrophe a vowel, the pinned settings can make no names.",
        "other fails": "vowel: with the apostrophe a consonant, the pinned settings can make no names.",
        "other": "consonant: in a test, names made with it a consonant read {likelier} more naturally.",
        "plain": "vowel: in a test, names made with it a vowel read {likelier} more naturally.",
        "even": "vowel, the usual choice: in a test, names made with it a consonant read about as naturally.",
    },
}


def _letters(count: int) -> str:
    return "1 letter" if count == 1 else f"{count} letters"


def _likelier(mean: float) -> str:
    """How much likelier a hidden name is on average, for a mean gain in log-chance."""
    gain = math.exp(mean) - 1
    return f"{gain:.0%}" if gain >= 0.01 else f"{gain:.1%}"


def _choices(style: Style) -> dict[str, tuple[object, str]]:
    """Every setting's inferred value and the reason for it, by name."""
    choices: dict[str, tuple[object, str]] = {
        # Spelling settings start plain; ``infer`` chooses them by hiding names.
        "soft_consonants": (False, ""),
        "consonant_clusters": ((), "none: namebook does not work these out; pin them to use them."),
        "multi_vowels": (False, "off: it rarely helps; pin it for lists full of names like Hereman."),
        "letter_y": (YMode.VOWEL, ""),
        "letter_u": (UMode.VOWEL, ""),
        "letter_apostrophe": (ApostropheMode.VOWEL, ""),  # chosen by test runs
        "letter_hyphen": _hyphen(style),
        "structural": (True, "on: letter groups have the lengths they have in the names."),
        "statistical": (True, "on: letter groups are chosen as often as they occur in the names."),
    }
    loosened = "namebook loosens it by itself for a name it cannot build"
    choices["fit_level"] = (MAX_FIT_LEVEL, f"{MAX_FIT_LEVEL}, the closest fit; {loosened}.")
    choices["prefixes"] = (True, f"on, the closest to the names; {loosened}.")
    choices["suffixes"] = (True, f"on, the closest to the names; {loosened}.")
    choices["shuffle"] = (False, "off: letter groups keep the places they have in the names.")
    choices["validation_level"] = (
        MAX_VALIDATION_LEVEL,
        f"{MAX_VALIDATION_LEVEL}, the strictest; namebook loosens it by itself when names keep failing.",
    )
    choices["refuse_copies"] = (
        True,
        "on: a name that is one of the list's names is made again, so names are new.",
    )
    choices["markov"] = (Markov.FILL, "")  # fill or weighted, chosen by a test run in ``infer``
    choices["markov_order"] = (0, "0: worked out from the names when the Markov chain is used.")
    choices["markov_strength"] = (
        DEFAULT_MARKOV_STRENGTH,
        f"{DEFAULT_MARKOV_STRENGTH:g}, the fullest: in the measurements, lowering it only made "
        "names less like the list.",
    )
    choices["templates"] = ((), "")  # worked out in ``infer``, once the hyphen is known
    choices["min_length"] = (0, "0, no limit: lengths follow the names.")
    choices["max_length"] = (0, "0, no limit: lengths follow the names.")
    choices["punctuation"] = (Punctuation.NONE, "none: names keep the spelling of the list.")
    return choices


def _small_list(names: tuple[str, ...]) -> dict[str, tuple[object, str]]:
    """The settings the classic way backs off to for a list of fewer than SMALL_LIST names (D68)."""
    count = len({capitals(name) for name in names})
    if count >= SMALL_LIST:
        return {}
    few = f"with only {count} different names (fewer than {SMALL_LIST}), building without the chain"
    return {
        "fit_level": (
            SMALL_LIST_FIT_LEVEL,
            f"{SMALL_LIST_FIT_LEVEL}: {few} makes few new names at closer fits.",
        ),
        "prefixes": (False, f"off: {few} makes few new names with it on."),
        "suffixes": (False, f"off: {few} makes few new names with it on."),
        "shuffle": (True, f"on: {few} needs letter groups from the middle of names mixed for variety."),
    }


def _word_shapes(style: Style) -> tuple[tuple[Template, ...], str]:
    """Templates that make names of as many words as the names have, joined
    as they are (by spaces, or hyphens as joints), in proportion."""
    splitter = WordSplitter.for_style(style)
    recipes: Counter[str] = Counter()
    for name in style.names:
        try:
            _, joints = splitter.words_and_joints(name)
        except WordSplitError:
            continue  # reported when the names are split
        recipes[ANY_STRUCTURE + "".join(joint + ANY_STRUCTURE for joint in joints)] += 1
    counts = sorted(recipes.items(), key=lambda item: (item[0].count(ANY_STRUCTURE), item[0]))
    if [recipe for recipe, _ in counts] == [ANY_STRUCTURE]:
        return (), "none: every name is one word, of any shape the names' words have."
    templates = tuple(Template(recipe, weight) for recipe, weight in counts)
    if not any(HYPHEN in recipe for recipe in recipes):
        shares = [f"{weight} of {_number(recipe.count(ANY_STRUCTURE))}" for recipe, weight in counts]
        return templates, f"names of one word or more, as in the list ({_join(shares)} words); {_SHAPED}"
    shares = [f"{weight} of {_words(recipe)}" for recipe, weight in counts]
    return templates, f"names of one word or more, as in the list ({_join(shares)}); {_SHAPED}"


# How a name of an inferred template gets its shape (D90).
_SHAPED = "each in the shape of a name in the list with as many words, joined the same way."


def _words(recipe: str) -> str:
    """How many words a recipe makes and how they are joined, as in "two joined by a hyphen"."""
    words, hyphens = recipe.count(ANY_STRUCTURE), recipe.count(HYPHEN)
    text = f"{_number(words)} {'word' if words == 1 else 'words'}"
    if hyphens == words - 1 == 1:
        return text + " joined by a hyphen"
    if hyphens and hyphens == words - 1:
        return text + " joined by hyphens"
    if hyphens:
        return text + f", with {'a hyphen' if hyphens == 1 else _number(hyphens) + ' hyphens'}"
    return text


def _number(count: int) -> str:
    return _NUMBERS.get(count, str(count))


_NUMBERS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


def _soft_pairs(names: tuple[str, ...], letters: LetterClasses) -> str:
    """The commonest consonant + H pairs in ``names`` (TH, CH, ...), to show in a reason."""
    pairs: Counter[str] = Counter()
    for name in names:
        name = capitals(name)
        pairs.update(
            name[pos : pos + 2]
            for pos in range(len(name) - 1)
            if name[pos + 1] == "H" and name[pos] != "H"
            and name[pos].isalpha() and letters.is_consonant(name[pos])
        )  # fmt: skip
    return _join([pair for pair, _ in pairs.most_common(SHOWN_PAIRS)]) if pairs else ""


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
