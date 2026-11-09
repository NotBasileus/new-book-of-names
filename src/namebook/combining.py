"""Full names from a style's lists (spec 13.5).

A style with several lists, such as given and family names, puts them
together by a combination: ``{given} {family}`` takes a name from each
list, in order, and joins them with a space, as written. Unless another is
asked for, names come from the first list alone
(``Style.default_combination``). Each list makes its names as a style of
its own (``Style.list_style``), with its own working settings, templates
and generator, so its names keep their own shapes and its recent names are
not repeated, even when a combination uses it twice.

``Combiner`` chooses a combination in proportion to its weight, when it is
given more than one, and makes each of its names in turn. With
starting letters, only combinations that begin with a list whose names can
start with them are used, and the letters apply to that first name, as a
template's apply to its first word (spec 7.1).

A full name keeps its parts, the name made from each list and the text
between them, so that a part can be made again, or the parts put in
another order, without making the others again.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from namebook.generator import GeneratedName, GenerationError, NameGenerator
from namebook.style import Template
from namebook.templates import Combination, ListPart, Text, combination_problem, parse_combination


@dataclass(frozen=True)
class NamePart:
    """A name made from one list, as part of a full name."""

    list_name: str
    name: GeneratedName


@dataclass(frozen=True)
class CombinedName:
    """A full name: the names made from lists, and the text between them, as written.

    If a name could not be made, it is the last part.
    """

    combination: str
    parts: tuple[NamePart | str, ...]

    @property
    def names(self) -> tuple[NamePart, ...]:
        return tuple(part for part in self.parts if isinstance(part, NamePart))

    @property
    def ok(self) -> bool:
        return all(part.name.ok for part in self.names)

    @property
    def text(self) -> str | None:
        """The full name (None if a name could not be made)."""
        if not self.ok:
            return None
        return "".join(part if isinstance(part, str) else part.name.text for part in self.parts)


class Combiner:
    """Makes full names from a style's lists, by one or more combinations.

    ``generators`` holds a generator for each list the combinations name,
    by the list's name. They and the combiner should share one ``rng``, so a
    random seed always gives the same names. A single combination is used
    without drawing on ``rng``, so a style of one list makes the same names
    as its generator alone.
    """

    def __init__(
        self,
        generators: Mapping[str, NameGenerator],
        combinations: Sequence[Template],
        rng: random.Random,
    ):
        self.generators = dict(generators)
        self.rng = rng
        self.combinations: list[tuple[Template, Combination]] = []
        for template in combinations:
            if problem := combination_problem(template, list(self.generators)):
                raise GenerationError(f'Combination "{template.recipe}" {problem}.')
            self.combinations.append((template, parse_combination(template.recipe)))
        if not self.combinations:
            raise GenerationError("There is no combination to make names with.")

    def generate(self, initial: str | None = None, explain: bool = False) -> CombinedName:
        """Make one full name; ``initial`` asks for one starting with these letters."""
        combination = self._choose(initial)
        parts: list[NamePart | str] = []
        for part in combination.parts:
            if isinstance(part, Text):
                parts.append(part.text)
                continue
            first = not any(isinstance(made, NamePart) for made in parts)
            name = self.generators[part.name].generate(initial=initial if first else None, explain=explain)
            parts.append(NamePart(part.name, name))
            if not name.ok:
                break
        return CombinedName(combination.text, tuple(parts))

    def _choose(self, initial: str | None) -> Combination:
        """Choose a combination in proportion to its weight; with an initial,
        one that begins with a list whose names can start with it."""
        choices = self.combinations
        if initial:
            choices = [(template, combination) for template, combination in choices if combination.starts_with_list]
            if not choices:
                raise GenerationError(
                    f"None of the combinations begins with a list, so none can make a name starting with "
                    f"'{initial}'."
                )
            if len(self.combinations) > 1:
                possible = [choice for choice in choices if self._can_start(choice[1], initial)]
                # With none, the first list's generator says why when it is asked.
                choices = possible or choices[:1]
        if len(choices) == 1:
            return choices[0][1]
        return self.rng.choices(choices, weights=[template.weight for template, _ in choices])[0][1]

    def _can_start(self, combination: Combination, initial: str) -> bool:
        try:
            self.generators[combination.lists[0]].check_initial(initial)
        except GenerationError:
            return False
        return True
