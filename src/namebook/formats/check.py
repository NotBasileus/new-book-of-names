"""The input check (spec 10.2): one set of checks for every style, whatever file it came from.

Readers check what only they can (syntax, settings, line numbers) and refuse
files over the size limit. ``check_style`` then checks the neutral ``Style``
they return, before anything else uses it:

- limits on the number of names, the length of a name, the number of lists,
  and the number and length of templates, so a huge or broken file cannot
  freeze the program;
- invisible and display-control characters in any text that gets shown;
- letters outside the style's letter classes;
- list names with braces, which ``--combination`` uses (spec 13.5).

Names that cannot be split into elements are found later, in preprocessing
(decision D17), since that depends on settings such as the Y rule.
"""

from __future__ import annotations

from namebook.formats.common import describe_chars, hidden_characters, shorten
from namebook.style import Style, StyleError, Template
from namebook.templates import LIST_CLOSE, LIST_OPEN

MAX_NAMES = 50_000
MAX_NAME_LENGTH = 60
MAX_LIST_ITEMS = 100  # templates, consonant clusters
MAX_RECIPE_LENGTH = 100
MAX_REPORTED = 20  # problems listed before the rest are only counted


def check_style(style: Style) -> None:
    """Raise ``StyleError`` listing every problem found in ``style``."""
    problems = style_problems(style)
    if len(problems) > MAX_REPORTED:
        extra = len(problems) - MAX_REPORTED
        problems = problems[:MAX_REPORTED] + [f"... and {extra} more {_problems(extra)}."]
    if problems:
        path = style.source.path if style.source else None
        raise StyleError(str(path) if path else shorten(style.title), problems)


def style_problems(style: Style) -> list[str]:
    """Every problem found in ``style``, in plain words."""
    problems = _hidden("The title", style.title)
    for line in style.description:
        problems += _hidden("The description", line)

    total = sum(len(name_list.names) for name_list in style.name_lists)
    if total > MAX_NAMES:
        # Checking each name of a list this long would only bury this message.
        return problems + [f"The style has {total:,} names; the most namebook accepts is {MAX_NAMES:,}."]

    several = len(style.name_lists) > 1
    problems += _too_many("lists", len(style.name_lists))
    for name_list in style.name_lists[:MAX_LIST_ITEMS]:
        in_list = f' in list "{shorten(name_list.name)}"' if several else ""
        if several:
            problems += _hidden(f'The list name "{shorten(name_list.name)}"', name_list.name)
        if not name_list.name.strip():
            problems.append("A list has no name.")
        elif LIST_OPEN in name_list.name or LIST_CLOSE in name_list.name:
            problems.append(
                f'The list name "{shorten(name_list.name)}" has a {LIST_OPEN} or {LIST_CLOSE}, '
                "which list names cannot have."
            )
        if name_list.settings:
            problems += _template_problems(name_list.settings.get("templates", ()), in_list)
            clusters = name_list.settings.get("consonant_clusters", ())
            problems += _too_many(f"consonant clusters{in_list}", len(clusters))
        for number, name in enumerate(name_list.names, start=1):
            where = f'Name {number}{in_list} ("{shorten(name)}")'
            if len(name) > MAX_NAME_LENGTH:
                problems.append(
                    f"{where} is {len(name)} characters long; the most is {MAX_NAME_LENGTH}."
                )
            bad = style.letter_classes.invalid_characters(name)
            if bad:
                problems.append(
                    f"{where} contains {describe_chars(bad)}, which "
                    f"{'is' if len(bad) == 1 else 'are'} not a vowel or consonant letter."
                )

    problems += _template_problems(style.templates)
    problems += _too_many("consonant clusters", len(style.special_letters.consonant_clusters))
    return problems


def _template_problems(templates: tuple[Template, ...], in_list: str = "") -> list[str]:
    problems = _too_many(f"templates{in_list}", len(templates))
    for template in templates[:MAX_LIST_ITEMS]:
        where = f'Template "{shorten(template.recipe)}"{in_list}'
        if len(template.recipe) > MAX_RECIPE_LENGTH:
            problems.append(
                f"{where} is {len(template.recipe)} characters long; the most is {MAX_RECIPE_LENGTH}."
            )
        problems += _hidden(where, template.recipe)
    return problems


def _hidden(what: str, text: str) -> list[str]:
    bad = hidden_characters(text)
    if not bad:
        return []
    return [
        f"{what} contains {describe_chars(bad)}. Invisible and control characters are not "
        "allowed, because they can hide or disguise text."
    ]


def _too_many(what: str, count: int) -> list[str]:
    if count <= MAX_LIST_ITEMS:
        return []
    return [f"The style has {count} {what}; the most is {MAX_LIST_ITEMS}."]


def _problems(count: int) -> str:
    return "problem" if count == 1 else "problems"
