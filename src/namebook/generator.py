"""Generating names (spec 7).

For each name, ``NameGenerator``:

1. chooses a template in proportion to the templates' weights (spec 7.1).
   A style without templates behaves as if its only template were ``#``;
2. for each word slot of the template, chooses a shape and builds a word in
   it (``WordBuilder``), retrying the same shape after a failed build. Every
   fifth failure in a row loosens one rule (automatic relaxation, spec 7.4);
   a successful build restores the user's settings. With starting letters,
   only templates and shapes that can begin with them are used (spec 13.11).
   A template of ``#`` slots joined as some seed names are (``# #``,
   ``#-#``) takes the shapes of all its words from one of those names (D90);
3. validates the word (spec 8). A rejected word counts a failure and a new
   word is built in a fresh shape, or the same one when its shape came from
   a seed name. Every fifth failure lowers the validation level by one (spec
   7.2);
4. puts the words and the template's literal text together, with
   punctuation and capitals (spec 7.6);
5. checks the finished name's length when the options limit it (spec 10.5):
   a name with too few or too many letters is made again. To make that rare,
   a one-word name chooses its shape in proportion to the seed words of that
   shape whose length is in range, so shapes no such seed word has are never
   used (D72);
6. with ``refuse_copies`` on, compares the finished name with the seed
   names, ignoring capitals, accents, spaces, apostrophes and hyphens. A
   copy is made again with the same template, up to 20 times for one name;
   then a copy is accepted (spec 13.1, D81);
7. compares the finished name with the last 100 names. A repeat counts a
   failure too and the name is made again with the same template, until the
   count reaches five; then the repeat is accepted (spec 7.5).

Everything that happens is recorded as a list of events, which explain mode
shows and the quality measurements count.
"""

from __future__ import annotations

import dataclasses
import random
from collections import Counter, deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from namebook.analysis import SeedWord, StyleAnalysis
from namebook.building import (
    BuildAttempt,
    CandidateStrategy,
    LetterFiller,
    Shape,
    WeightedStrategy,
    WordBuilder,
)
from namebook.letters import capitals
from namebook.markov import END, internal_chain, word_letters
from namebook.style import MAX_LENGTH, GenerationOptions, Markov, Template
from namebook.templates import ANY_STRUCTURE, Recipe, Slot, Space, Text, assemble, capitalize, parse_recipe
from namebook.validation import Rejection, Validator
from namebook.words import HYPHEN, Element, format_element, letter_count, plain, structure_starts_with_vowel

# Used when a style has no templates (spec 7.1).
DEFAULT_TEMPLATE = Template("#", 1)

# The shape of a seed name: each word's structure and substructure.
NameShape = tuple[tuple[int, tuple[int, ...]], ...]

RELAX_EVERY = 5  # failed builds in a row before one rule is loosened
FAILURES_ALLOWED = 5  # validation failures and repeats before the level drops or a repeat is accepted
HISTORY_SIZE = 100  # how many recent names are remembered
HISTORY_FAILURE = "!HIST"  # the failure code for a repeat of a recent name
COPY_FAILURE = "!COPY"  # a finished name that is one of the seed names
COPIES_REFUSED = 20  # copies of seed names refused for one name before one is accepted
LENGTH_FAILURE = "!LEN"  # a finished name with too few or too many letters

# Safety limits. Relaxation means a word is normally built long before
# these; they stop impossible requests from running forever.
MAX_BUILD_ATTEMPTS = 100  # builds of one shape
MAX_WORD_TRIES = 500  # built words (rejected or not) for one try at a name
MAX_LENGTH_MISSES = 50  # finished names of the wrong length, for one name


class GenerationError(Exception):
    """A request that can never produce a name, such as an impossible initial letter."""


# --- Events: what happened while making a name ------------------------------


@dataclass(frozen=True)
class TemplateChosen:
    recipe: str


@dataclass(frozen=True)
class WordStarted:
    """Work begins on one word slot of a template with several."""

    number: int
    total: int
    slot: str  # e.g. "9|13" or "#"


@dataclass(frozen=True)
class ShapeChosen:
    shape: Shape


@dataclass(frozen=True)
class Relaxed:
    """One rule was loosened after repeated failed builds (spec 7.4)."""

    setting: str  # "Fit_lvl", "Structural", "Prefixes" or "Suffixes", as the explain mode shows them
    value: int | None = None  # the new fit level; None when a setting is dropped

    @property
    def message(self) -> str:
        if self.value is None:
            return f'Dropped "{self.setting}".'
        return f'Changed "{self.setting}" to {self.value}.'


@dataclass(frozen=True)
class Checked:
    """A built word was validated. ``rejection`` is None if it passed."""

    text: str
    level: int
    rejection: Rejection | None


@dataclass(frozen=True)
class ValidationLowered:
    level: int

    @property
    def message(self) -> str:
        return f'Changed "Val_lvl" to {self.level}.'


@dataclass(frozen=True)
class CopyRefused:
    """The finished name was one of the seed names (spec 13.1)."""

    text: str
    ignored: bool  # True if the copy was accepted anyway


@dataclass(frozen=True)
class HistoryConflict:
    """The finished name was one of the recent names."""

    text: str
    ignored: bool  # True if the repeat was accepted anyway


@dataclass(frozen=True)
class LengthRejected:
    """The finished name had too few or too many letters (spec 10.5)."""

    text: str
    letters: int
    fewest: int
    most: int


@dataclass(frozen=True)
class Finished:
    """The words were put together: ``built`` before punctuation and capitals."""

    built: str
    final: str


@dataclass(frozen=True)
class GaveUp:
    reason: str


Event = (
    TemplateChosen | WordStarted | ShapeChosen | BuildAttempt | Relaxed | Checked
    | ValidationLowered | Finished | LengthRejected | CopyRefused | HistoryConflict | GaveUp
)  # fmt: skip


@dataclass(frozen=True)
class SkippedTemplate:
    """A template that can never be used, and why."""

    template: Template
    reason: str


# --- Results -----------------------------------------------------------------


@dataclass(frozen=True)
class GeneratedWord:
    attempt: BuildAttempt  # the build that made it, with the settings in force
    text: str

    @property
    def shape(self) -> Shape:
        return self.attempt.shape

    @property
    def elements(self) -> tuple[Element, ...]:
        return self.attempt.elements

    @property
    def letters(self) -> tuple[str, ...]:
        return tuple(letter for element in self.elements for letter in element)


@dataclass(frozen=True)
class GeneratedName:
    text: str | None  # None if no name could be made
    words: tuple[GeneratedWord, ...]
    events: tuple[Event, ...]
    template: str

    @property
    def ok(self) -> bool:
        return self.text is not None

    @property
    def built(self) -> str | None:
        """The name as built, before punctuation and capitals (None if no name was made)."""
        if not self.ok:
            return None
        return next(event.built for event in reversed(self.events) if isinstance(event, Finished))


def describe_length(fewest: int, most: int) -> str:
    """A length range in words: "5 to 9 letters", "at least 5 letters", ..."""
    if most >= MAX_LENGTH:
        return f"at least {fewest} letters"
    if fewest <= 0:
        return f"at most {most} letters"
    if fewest == most:
        return f"exactly {fewest} letters"
    return f"{fewest} to {most} letters"


def spell(elements: Iterable[Element]) -> str:
    return capitalize("".join(format_element(element) for element in elements))


def relax(options: GenerationOptions) -> tuple[GenerationOptions, Relaxed] | None:
    """Loosen the first rule still active: fit level, then structural, prefixes, suffixes.

    Returns None when nothing is left to loosen. Statistical is never loosened.
    With ``markov = "fill"``, fit levels and structural do not apply (fill
    needs structural on), so only prefixes and suffixes are loosened.
    """
    fill = options.markov is Markov.FILL
    if options.fit_level > 0 and not fill:
        level = options.fit_level - 1
        return dataclasses.replace(options, fit_level=level), Relaxed("Fit_lvl", level)
    rules = (("structural", "Structural"), ("prefixes", "Prefixes"), ("suffixes", "Suffixes"))
    for field, setting in rules[1:] if fill else rules:
        if getattr(options, field):
            return dataclasses.replace(options, **{field: False}), Relaxed(setting)
    return None


def template_problem(analysis: StyleAnalysis, template: Template) -> str | None:
    """Why a template can never be used with this style, or None if it can."""
    if template.weight <= 0:
        return "has weight 0"
    for slot in parse_recipe(template.recipe).slots:
        if slot.structures is not None and not any(
            analysis.structures[s] > 0 for s in slot.structures
        ):
            numbers = " or ".join(str(s) for s in slot.structures)
            return f"needs structure {numbers}, which no seed name has"
    return None


def _seed_words(shapes: dict[int, Counter[tuple[int, ...]]]) -> int:
    """How many seed words have one of ``shapes`` (as ``NameGenerator._shapes`` gives them)."""
    return sum(sum(counts.values()) for counts in shapes.values())


@dataclass(frozen=True)
class _Pool:
    """The seed words a word's shape is chosen from: how many have each
    structure, each substructure of it, and each length of each shape."""

    structures: Counter[int]
    substructures: dict[int, Counter[tuple[int, ...]]]
    lengths: dict[tuple[int, tuple[int, ...]], Counter[int]]


@dataclass
class _NameState:
    """What one name keeps track of while it is being made."""

    validation_level: int
    failures: int = 0  # shared by validation failures and repeats
    tries: int = 0
    length_misses: int = 0
    copies: int = 0


class NameGenerator:
    """Makes names from one analyzed style.

    All randomness comes from ``rng``, so a generator made with
    ``random.Random(seed)`` always gives the same names for the same seed.
    The generator remembers the last ``history_size`` names it made, so
    names made by one generator avoid repeating each other.

    ``templates`` replaces the style's own templates. Templates that can
    never be used, because a slot asks only for structures no seed word has,
    are set aside in ``skipped_templates``.

    Without a ``strategy``, words are built the classic way; with ``markov =
    "weighted"``, by ``WeightedStrategy``; and with ``markov = "fill"``, by
    ``LetterFiller``, each with a chain learned from the style's names.
    Raises GenerationError for fill with structural off.
    """

    def __init__(
        self,
        analysis: StyleAnalysis,
        options: GenerationOptions | None = None,
        rng: random.Random | None = None,
        strategy: CandidateStrategy | None = None,
        validator: Validator | None = None,
        history_size: int = HISTORY_SIZE,
        templates: Sequence[Template] | None = None,
    ):
        self.analysis = analysis
        self.options = options or analysis.style.options
        self.rng = rng or random.Random()
        self.builder = self._builder(strategy)
        self.validator = validator or Validator.classic(analysis)
        self.history: deque[str] = deque(maxlen=history_size)
        # The seed names as names are compared, when copies of them are refused.
        self.seed_names = frozenset(
            plain(name) for name in analysis.style.name_list(analysis.list_name).names
        ) if self.options.refuse_copies else frozenset()  # fmt: skip

        # How many seed words of each shape (structure, substructure) have each
        # number of letters, for choosing shapes when a length range is asked for.
        self.seed_lengths: dict[tuple[int, tuple[int, ...]], Counter[int]] = {}
        for seed in analysis.words:
            key = (seed.word.structure, seed.word.substructure)
            self.seed_lengths.setdefault(key, Counter())[letter_count(seed.word.text)] += 1
        self._shape_counts: dict[tuple, dict[int, Counter[tuple[int, ...]]]] = {}  # see _shapes

        # The seed names by how their words are joined, written as a recipe
        # ("#", "# #", "#-#"): each name's word shapes and its length. A
        # template joined as some seed names are makes names in the shape of
        # one of them (D90).
        self.name_shapes: dict[str, list[tuple[NameShape, int]]] = {}
        by_name: dict[int, list[SeedWord]] = {}
        for seed in analysis.words:
            by_name.setdefault(seed.name_number, []).append(seed)
        for seeds in by_name.values():
            _, joints = analysis.splitter.words_and_joints(seeds[0].name)
            pattern = ANY_STRUCTURE + "".join(joint + ANY_STRUCTURE for joint in joints)
            shape = tuple((seed.word.structure, seed.word.substructure) for seed in seeds)
            self.name_shapes.setdefault(pattern, []).append((shape, letter_count(seeds[0].name)))
        self._pools: dict[str, _Pool] = {}  # see _pool
        self._name_counts: dict[tuple, Counter[NameShape]] = {}  # see _name_shape_counts

        if templates is None:
            templates = analysis.style.templates
        self.templates: list[tuple[Template, Recipe]] = []
        self.skipped_templates: list[SkippedTemplate] = []
        for template in templates or (DEFAULT_TEMPLATE,):
            recipe = parse_recipe(template.recipe)
            reason = template_problem(analysis, template)
            if reason:
                self.skipped_templates.append(SkippedTemplate(template, reason))
            else:
                self.templates.append((template, recipe))
        if not self.templates:
            reasons = "; ".join(
                f'"{s.template.recipe}:{s.template.weight}" {s.reason}' for s in self.skipped_templates
            )
            raise GenerationError(f"None of the templates can ever be used: {reasons}.")

    def generate(
        self,
        initial: str | None = None,
        explain: bool = False,
        structures: Iterable[int] | None = None,
    ) -> GeneratedName:
        """Make one name.

        ``initial`` asks for names starting with these letters. ``structures``
        makes a one-word name of one of these structures, ignoring the
        templates.
        """
        initial = self._check_initial(initial)
        if structures is not None:
            recipe = Recipe.single_slot(tuple(structures))
        else:
            recipe = self._choose_template(initial)
        events: list[Event] = [TemplateChosen(recipe.text)]
        state = _NameState(self.options.validation_level)
        length = self.options.length_range
        while True:
            slots = recipe.slots
            # A name joined as some seed names are takes the shape of one of
            # them, its words' shapes together (D90); a one-word name takes the
            # shape of a one-word seed name.
            pattern = self._pattern(recipe)
            whole = pattern is not None and len(slots) > 1
            name_shape = self._choose_name_shape(pattern, initial, length) if whole else None
            pool = None if whole else pattern
            # A name that is one word and nothing else chooses its shape by the
            # seed words of the right length; other names are checked when done.
            word_length = length if len(recipe.parts) == 1 else None
            words: list[GeneratedWord] = []
            for number, slot in enumerate(slots, start=1):
                if len(slots) > 1:
                    events.append(WordStarted(number, len(slots), slot.describe()))
                # The initial applies to the first word (templates chosen for
                # an initial always begin with a word slot).
                first_initial = initial if number == 1 else None
                # For the Markov chain, each word is followed by its joint (a
                # space, or a hyphen), and the last by the end of the name.
                joints = recipe.joints
                place = (self._letters_before(words, joints), (*joints, END)[number - 1])
                shape = name_shape[number - 1] if name_shape else None
                word = self._word(
                    slot.structures, first_initial, state, events, explain, word_length, place, shape, pool
                )
                if word is None:
                    return GeneratedName(None, tuple(words), tuple(events), recipe.text)
                words.append(word)
            built, text = assemble(
                recipe, [word.letters for word in words], self.options.punctuation
            )
            events.append(Finished(built, text))
            if length and not length[0] <= (letters := letter_count(text)) <= length[1]:
                events.append(LengthRejected(text, letters, *length))
                state.length_misses += 1
                if state.length_misses >= MAX_LENGTH_MISSES:
                    reason = f"no name with {describe_length(*length)} in {MAX_LENGTH_MISSES} tries"
                    events.append(GaveUp(reason))
                    return GeneratedName(None, tuple(words), tuple(events), recipe.text)
                state.tries = 0  # each new try at the name gets its full count of words
                if structures is None:
                    # Unlike a repeat (spec 7.5), a wrong length draws a new
                    # template: the old one may rarely make names of this length.
                    recipe = self._choose_template(initial)
                    events.append(TemplateChosen(recipe.text))
                continue
            if plain(text) in self.seed_names:
                state.copies += 1
                ignored = state.copies >= COPIES_REFUSED
                events.append(CopyRefused(text, ignored))
                if not ignored:
                    continue
            if text.lower() in self.history:
                state.failures += 1
                ignored = state.failures >= FAILURES_ALLOWED
                events.append(HistoryConflict(text, ignored))
                if not ignored:
                    continue
            if self.history.maxlen:
                self.history.append(text.lower())
            return GeneratedName(text, tuple(words), tuple(events), recipe.text)

    def _choose_template(self, initial: str | None) -> Recipe:
        """Choose a template in proportion to its weight (``_template_choices``)."""
        choices, weights = self._template_choices(initial)
        return self.rng.choices(choices, weights=weights)[0][1]

    def _template_choices(
        self, initial: str | None
    ) -> tuple[list[tuple[Template, Recipe]], list[float]]:
        """The templates a name may be made with, and their weights.

        With an initial, only templates whose first word slot can start with
        it are considered, rather than drawing again until one fits. With a length
        range, a template of just one word slot is only considered when some
        seed word of a length in range has a shape the slot allows. Raises
        GenerationError when none is left.
        """
        choices = self.templates
        if initial:
            choices = [
                (template, recipe)
                for template, recipe in choices
                if recipe.starts_with_slot and self._can_start(recipe, initial)
            ]
            if not choices:
                raise GenerationError(f"None of the templates can make a name starting with '{initial}'.")
        weights = [template.weight for template, _ in choices]
        if length := self.options.length_range:
            # A template counts by the share of the seed names or words it takes
            # its shapes from that are of a length in range: P(template |
            # length) follows P(template) P(length | template).
            weights = [
                weight * self._length_share(recipe, initial, length)
                for weight, (_, recipe) in zip(weights, choices)
            ]
            choices = [choice for choice, weight in zip(choices, weights) if weight > 0]
            weights = [weight for weight in weights if weight > 0]
            if not choices:
                raise GenerationError(self._unreachable(length, initial))
        return choices, weights

    def _word(
        self,
        structures: tuple[int, ...] | None,
        initial: str | None,
        state: _NameState,
        events: list[Event],
        explain: bool,
        length: tuple[int, int] | None = None,
        place: tuple[tuple[str, ...], str] = ((), END),
        shape: Shape | None = None,
        pool: str | None = None,
    ) -> GeneratedWord | None:
        """Build words until one passes validation (spec 7.2).

        ``place`` is where the word sits in its name, as ``WordBuilder.build``
        takes it: the letters before it, and what follows it. A word given
        its ``shape`` (from the shape of a seed name) keeps it; otherwise each
        word gets a fresh one, from the seed words of ``pool`` (see ``_pool``).
        """
        while state.tries < MAX_WORD_TRIES:
            state.tries += 1
            attempt = self._build(structures, initial, events, explain, length, place, shape, pool)
            if attempt is None:
                return None
            word = GeneratedWord(attempt, spell(attempt.elements))
            rejection = self.validator.check(word.letters, word.text, state.validation_level)
            events.append(Checked(word.text, state.validation_level, rejection))
            if rejection is None:
                return word
            state.failures += 1
            if state.failures >= FAILURES_ALLOWED:
                state.failures = 0
                if state.validation_level > 0:
                    state.validation_level -= 1
                    events.append(ValidationLowered(state.validation_level))
        events.append(GaveUp(f"no word passed validation in {MAX_WORD_TRIES} tries"))
        return None

    def _build(
        self,
        structures: tuple[int, ...] | None,
        initial: str | None,
        events: list[Event],
        explain: bool,
        length: tuple[int, int] | None = None,
        place: tuple[tuple[str, ...], str] = ((), END),
        shape: Shape | None = None,
        pool: str | None = None,
    ) -> BuildAttempt | None:
        """Build one word in ``shape``, or a fresh one, loosening rules after
        repeated failures (spec 7.4)."""
        shape = shape or self.choose_shape(structures, initial, length, pool)
        events.append(ShapeChosen(shape))
        options = self.options
        for failures in range(1, MAX_BUILD_ATTEMPTS + 1):
            attempt = self.builder.build(shape, options, initial, explain, *place)
            events.append(attempt)
            if attempt.ok:
                return attempt
            if failures % RELAX_EVERY == 0 and (relaxed := relax(options)):
                options, event = relaxed
                events.append(event)
        events.append(GaveUp(f"no word could be built in this shape in {MAX_BUILD_ATTEMPTS} tries"))
        return None

    def _builder(self, strategy: CandidateStrategy | None) -> WordBuilder:
        markov = self.options.markov
        if strategy is not None or markov is Markov.OFF:
            return WordBuilder(self.analysis, self.rng, strategy)
        if markov is Markov.FILL and not self.options.structural:
            raise GenerationError(
                'markov = "fill" needs structural on: it takes the length of each part of a '
                "word from the seed words' substructures."
            )
        names = self.analysis.style.name_list(self.analysis.list_name).names
        chain = internal_chain(self.analysis.splitter, names, self.options.markov_order or None)
        strength = self.options.markov_strength
        if markov is Markov.FILL:
            return LetterFiller(self.analysis, self.rng, chain, strength)
        return WordBuilder(
            self.analysis, self.rng, WeightedStrategy(chain, strength, self.analysis.splitter.letter_classes)
        )

    def _letters_before(self, words: Sequence[GeneratedWord], joints: Sequence[str]) -> tuple[str, ...]:
        """The letters of the words made so far, each followed by its joint, as the chain reads them."""
        classes = self.analysis.splitter.letter_classes
        letters: list[str] = []
        for word, joint in zip(words, joints):
            first_is_vowel = structure_starts_with_vowel(word.shape.structure)
            letters += [*word_letters(word.elements, first_is_vowel, classes), joint]
        return tuple(letters)

    def choose_shape(
        self,
        structures: Iterable[int] | None,
        initial: str | None,
        length: tuple[int, int] | None = None,
        pool: str | None = None,
    ) -> Shape:
        """Choose a structure in proportion to seed frequency, then maybe a substructure.

        With an ``initial``, only shapes the builder can begin with it are
        used (``_shapes``). With a ``length`` range (fewest, most letters),
        only shapes that some seed word of a length in that range has, each
        as often as it occurs among those seed words. ``pool`` limits the
        seed words to those of the seed names joined that way (``_pool``).
        """
        requested = None if structures is None else tuple(structures)
        shapes = self._shapes(requested, initial, length, pool)
        if not shapes:
            if not self._shapes(requested, pool=pool):
                raise GenerationError("None of the requested structures occur in the seed names.")
            if initial and not self._shapes(requested, initial, pool=pool):
                raise GenerationError(f"None of the requested structures can start with '{initial}'.")
            raise GenerationError(self._unreachable(length, initial))
        allowed = list(shapes)
        weights = [sum(counts.values()) for counts in shapes.values()]
        structure = self.rng.choices(allowed, weights=weights)[0]

        substructure = None
        if self.options.structural:
            counts = shapes[structure]
            kinds = list(counts)
            weights = [counts[kind] for kind in kinds] if self.options.statistical else None
            substructure = self.rng.choices(kinds, weights=weights)[0]
        return Shape(structure, substructure)

    def _shapes(
        self,
        structures: tuple[int, ...] | None,
        initial: str | None = None,
        length: tuple[int, int] | None = None,
        pool: str | None = None,
    ) -> dict[int, Counter[tuple[int, ...]]]:
        """The shapes a word may be built in: for each structure, how many seed
        words have each of its substructures.

        ``structures`` limits the structures (None for any). With an
        ``initial``, only shapes the builder can begin with it count
        (``can_hold``): for the classic way, structures with enough elements for
        it (D25); for fill, shapes whose first positions are as long as
        elements of the seeds that can hold it (spec 13.11). With a
        ``length`` range, only seed words of a length in range count.
        ``pool`` limits the seed words as ``_pool`` does. Structures left
        with none are left out.
        """
        key = (structures, initial, length, pool)
        if key not in self._shape_counts:
            seen = self._pool(pool).structures
            shapes: dict[int, Counter[tuple[int, ...]]] = {}
            for structure in seen if structures is None else structures:
                if seen[structure] <= 0:
                    continue
                counts = self._pool(pool).substructures[structure]
                if length:
                    counts = +Counter({kind: self._in_range(structure, kind, length, pool) for kind in counts})
                if initial:
                    counts = Counter({
                        kind: count for kind, count in counts.items()
                        if self.builder.can_hold(Shape(structure, kind), initial)
                    })  # fmt: skip
                if counts:
                    shapes[structure] = counts
            self._shape_counts[key] = shapes
        return self._shape_counts[key]

    def _pool(self, pattern: str | None) -> _Pool:
        """The seed words a word's shape is chosen from: every seed word, or
        with a ``pattern`` of one word (``"#"``), the seed names of one word,
        so that a part of a longer name, such as Bel in Bel-Abrean, does not
        give its shape to a name of one word (D90)."""
        if pattern is None:
            return _Pool(self.analysis.structures, self.analysis.substructures, self.seed_lengths)
        if pattern not in self._pools:
            structures: Counter[int] = Counter()
            substructures: dict[int, Counter[tuple[int, ...]]] = {}
            lengths: dict[tuple[int, tuple[int, ...]], Counter[int]] = {}
            for ((structure, substructure),), letters in self.name_shapes[pattern]:
                structures[structure] += 1
                substructures.setdefault(structure, Counter())[substructure] += 1
                lengths.setdefault((structure, substructure), Counter())[letters] += 1
            self._pools[pattern] = _Pool(structures, substructures, lengths)
        return self._pools[pattern]

    def _pattern(self, recipe: Recipe) -> str | None:
        """How ``recipe`` joins its words, written as ``name_shapes`` writes it,
        when it is only ``#`` slots joined by spaces or hyphens, as some seed
        names are; otherwise None, and each word takes the shape of any seed
        word, as in the classic rules. When every seed name is one word, so is every seed
        word, and ``"#"`` is None too."""
        pattern = ""
        for part in recipe.parts:
            if isinstance(part, Slot) and part.structures is None:
                pattern += ANY_STRUCTURE
            elif isinstance(part, Space):
                pattern += " "
            elif part == Text(HYPHEN):
                pattern += HYPHEN
            else:
                return None
        if pattern not in self.name_shapes or len(self.name_shapes) == 1 == len(recipe.slots):
            return None
        return pattern

    def _name_shape_counts(
        self, pattern: str, initial: str | None, length: tuple[int, int] | None
    ) -> Counter[NameShape]:
        """How many seed names joined as ``pattern`` have each shape: with an
        ``initial``, those whose first word's shape the builder can begin with
        it; with a ``length`` range, those of a length in range."""
        key = (pattern, initial, length)
        if key not in self._name_counts:
            self._name_counts[key] = Counter(
                shape
                for shape, letters in self.name_shapes[pattern]
                if (not length or length[0] <= letters <= length[1])
                and (not initial or self.builder.can_hold(Shape(*shape[0]), initial))
            )
        return self._name_counts[key]

    def _choose_name_shape(
        self, pattern: str, initial: str | None, length: tuple[int, int] | None
    ) -> tuple[Shape, ...]:
        """The shape of a seed name joined as ``pattern``, one shape for each of
        its words, in proportion to the seed names (or each kind of shape
        alike, with statistical off); without substructures when structural
        is off."""
        counts = self._name_shape_counts(pattern, initial, length)
        if not counts:  # the template was chosen for its share of such names
            raise GenerationError(f'No seed name joined as "{pattern}" can give this name its shape.')
        kinds = list(counts)
        weights = [counts[kind] for kind in kinds] if self.options.statistical else None
        chosen = self.rng.choices(kinds, weights=weights)[0]
        structural = self.options.structural
        return tuple(Shape(structure, substructure if structural else None) for structure, substructure in chosen)

    def _can_start(self, recipe: Recipe, initial: str) -> bool:
        """Whether a name of this template can begin with ``initial``."""
        pattern = self._pattern(recipe)
        if pattern is not None and len(recipe.slots) > 1:
            return bool(self._name_shape_counts(pattern, initial, None))
        return bool(self._shapes(recipe.slots[0].structures, initial, pool=pattern))

    def _length_share(self, recipe: Recipe, initial: str | None, length: tuple[int, int]) -> float:
        """The share of the seed names (or, for a template of one word slot,
        seed words) a template takes its shapes from whose length is in range.
        Other templates count in full: their names are checked when done."""
        pattern = self._pattern(recipe)
        if pattern is not None and len(recipe.slots) > 1:
            total = sum(self._name_shape_counts(pattern, initial, None).values())
            return sum(self._name_shape_counts(pattern, initial, length).values()) / total if total else 0.0
        if len(recipe.parts) == 1:
            return self._slot_share(recipe.slots[0], initial, length, pattern)
        return 1.0

    def _slot_share(
        self, slot: Slot, initial: str | None, length: tuple[int, int], pool: str | None = None
    ) -> float:
        """The share of the seed words of the shapes this slot allows (that can start
        with the initial) whose length is in range."""
        total = _seed_words(self._shapes(slot.structures, initial, pool=pool))
        return _seed_words(self._shapes(slot.structures, initial, length, pool)) / total if total else 0.0

    def _in_range(
        self,
        structure: int,
        substructure: tuple[int, ...] | None,
        length: tuple[int, int],
        pool: str | None = None,
    ) -> int:
        """How many seed words of this shape (any substructure, for None) have a length in range."""
        return sum(
            count
            for (s, sub), counts in self._pool(pool).lengths.items()
            if s == structure and substructure in (None, sub)
            for letters, count in counts.items()
            if length[0] <= letters <= length[1]
        )

    def _unreachable(self, length: tuple[int, int], initial: str | None) -> str:
        fewest = min(min(counts) for counts in self.seed_lengths.values())
        most = max(max(counts) for counts in self.seed_lengths.values())
        which = f"of a shape that can start with '{initial}' " if initial else ""
        return (
            f"No seed word {which}has {describe_length(*length)} "
            f"(the seed words have {fewest} to {most}), so no name can."
        )

    def check_initial(self, initial: str) -> str:
        """``initial`` in capitals, when some name can start with it. Raises
        GenerationError, saying why, when none can."""
        initial = self._check_initial(initial) or ""
        self._template_choices(initial)
        return initial

    def _check_initial(self, initial: str | None) -> str | None:
        if not initial:
            return None
        initial = capitals(initial)
        bad = self.analysis.style.letter_classes.invalid_characters(initial)
        if bad or " " in initial:
            raise GenerationError(f"The initial '{initial}' must be made of letters only.")
        if not self._shapes(None, initial):
            raise GenerationError(self._cannot_start(initial))
        return initial

    def _cannot_start(self, initial: str) -> str:
        """Why no name can start with ``initial``, with the most of it that one can."""
        for size in range(len(initial) - 1, 0, -1):
            if self._shapes(None, initial[:size]):
                return f"No name can start with '{initial}'. Names can start with '{initial[:size]}'."
        which = "it" if len(initial) == 1 else f"'{initial}'"
        return f"No seed word starts with '{initial[0]}', so no name can start with {which}."
