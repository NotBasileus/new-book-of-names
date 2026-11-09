"""Explain mode: a readable account of how a name was made (spec 9).

Like a log, it shows in order: the template, each shape chosen, every
build attempt with its candidates and choices, rules loosened (``Changed
"Fit_lvl" to 2.``), validation results (``!DIST/A``), the validation level
lowered, the name as built and as finished, names of the wrong length
(``Fail: !LEN``), copies of seed names (``Fail: !COPY``) and repeats of
recent names (``Fail: !HIST``).

A full name put together from several lists (spec 13.5) shows its
combination, then each list's name in turn, explained in the same way.
"""

from __future__ import annotations

from namebook.building import ELEMENT, LETTERS, BuildAttempt, PositionChoice
from namebook.combining import CombinedName
from namebook.generator import (
    COPY_FAILURE,
    HISTORY_FAILURE,
    LENGTH_FAILURE,
    Checked,
    CopyRefused,
    Finished,
    GaveUp,
    GeneratedName,
    HistoryConflict,
    LengthRejected,
    Relaxed,
    ShapeChosen,
    TemplateChosen,
    ValidationLowered,
    WordStarted,
    describe_length,
    spell,
)
from namebook.templates import parse_combination
from namebook.words import format_element, format_substructure, structure_shape

SHOWN_CANDIDATES = 6
# Failed attempts of one shape shown in full; later ones get one line each.
SHOWN_FAILED_ATTEMPTS = 3


def explain_combined(name: CombinedName, number: int | None = None) -> str:
    """How a full name was made: a name from one list and nothing else as
    ``explain_name`` shows it; otherwise its combination, then each list's
    name in turn."""
    if len(name.parts) == 1 and name.names:
        return explain_name(name.names[0].name, number)
    title = name.text if name.ok else "(no name could be made)"
    lines = [f"Name {number}: {title}" if number is not None else title, f'  Combination: "{name.combination}"']
    total = len(parse_combination(name.combination).lists)
    for index, part in enumerate(name.names, start=1):
        made = part.name.text if part.name.ok else "(none could be made)"
        lines.append(f'  Part {index} of {total}, from list "{part.list_name}": {made}')
        lines += ["  " + line for line in explain_name(part.name).splitlines()[1:]]
    return "\n".join(lines)


def explain_name(name: GeneratedName, number: int | None = None) -> str:
    title = name.text if name.ok else "(no name could be made)"
    lines = [f"Name {number}: {title}" if number is not None else title]
    attempt_number = failed_in_shape = 0
    for event in name.events:
        if isinstance(event, TemplateChosen):
            lines.append(f'  Template: "{event.recipe}"')
        elif isinstance(event, WordStarted):
            lines.append(f'  Word {event.number} of {event.total} (slot "{event.slot}"):')
        elif isinstance(event, Finished):
            lines.append(f"  Finished: {event.built} → {event.final}")
        elif isinstance(event, ShapeChosen):
            lines.append(_explain_shape(event))
            attempt_number = failed_in_shape = 0
        elif isinstance(event, BuildAttempt):
            attempt_number += 1
            if event.ok or failed_in_shape < SHOWN_FAILED_ATTEMPTS:
                lines.extend(_explain_attempt(attempt_number, event))
            else:
                lines.append(_summarize_attempt(attempt_number, event))
            if not event.ok:
                failed_in_shape += 1
        elif isinstance(event, (Relaxed, ValidationLowered)):
            lines.append(f"  {event.message}")
        elif isinstance(event, Checked):
            lines.append(_explain_check(event))
        elif isinstance(event, LengthRejected):
            wanted = describe_length(event.fewest, event.most)
            lines.append(
                f"  Fail: {LENGTH_FAILURE}: {event.text} has {event.letters} letters, not {wanted}, "
                "so it is made again."
            )
        elif isinstance(event, CopyRefused):
            if event.ignored:
                lines.append(f"  Accepted a copy: {event.text} is a seed name, but nothing else was made.")
            else:
                lines.append(f"  Fail: {COPY_FAILURE}: {event.text} is a seed name, so it is made again.")
        elif isinstance(event, HistoryConflict):
            if event.ignored:
                lines.append(f"  Ignored history conflict: {event.text} was made recently.")
            else:
                lines.append(
                    f"  Fail: {HISTORY_FAILURE}: {event.text} was made recently, so it is made again."
                )
        elif isinstance(event, GaveUp):
            lines.append(f"  Gave up: {event.reason}.")
    return "\n".join(lines)


def _explain_shape(event: ShapeChosen) -> str:
    shape = event.shape
    line = f"  Shape: structure {shape.structure} ({structure_shape(shape.structure)})"
    if shape.substructure is not None:
        line += f", substructure {format_substructure(shape.substructure)}"
    return line


def _explain_attempt(number: int, attempt: BuildAttempt) -> list[str]:
    heading = f"  Attempt {number}"
    if attempt.split is not None and attempt.shape.element_total > 2:
        heading += f" (middle positions up to {attempt.split} count from the start)"
    lines = [heading + ":"]
    for choice in attempt.choices:
        lines.append(_explain_choice(choice, attempt.options.statistical, attempt.options.markov_strength))
    if attempt.failure:
        lines.append(f"    Failed: {attempt.failure}")
    else:
        built = " · ".join(format_element(element) for element in attempt.elements)
        lines.append(f"    Built: {built} → {spell(attempt.elements)}")
    return lines


def _summarize_attempt(number: int, attempt: BuildAttempt) -> str:
    last = attempt.choices[-1]
    where = last.index + 1 if last.kind in (ELEMENT, LETTERS) else f"{last.index + 1}–{last.index + 2}"
    return f"  Attempt {number}: failed at position {where}: {attempt.failure}"


def _explain_check(event: Checked) -> str:
    if event.rejection is None:
        return f"  Checked at validation level {event.level}: passed"
    rejection = event.rejection
    return f"  Checked at validation level {event.level}: {rejection.code}: {rejection.reason}"


def _explain_choice(choice: PositionChoice, statistical: bool, strength: float) -> str:
    """One position: its candidates, best first, and the one chosen.

    Each candidate shows its classic weight (``×12``) when ``statistical`` is on,
    and the Markov chain's fit (``chain 31%``) when a chain weighs it. A
    position filled letter by letter shows each letter with the chance it
    had (``A 31% · I 22%``).
    """
    if choice.kind == LETTERS:
        what = "vowel" if choice.vowel else "consonant"
        line = f"    {choice.index + 1:<5} {what:<10} letters"
        if choice.chosen is None:
            return line + ": none fits"
        (element,) = choice.chosen.elements
        shown = " · ".join(f"{letter} {chance:.0%}" for letter, chance in zip(element, choice.letter_chances))
        return f"{line}: {shown} → {format_element(element)}"
    if choice.kind == ELEMENT:
        where = f"{choice.index + 1}"
        what = "vowel" if choice.vowel else "consonant"
    else:
        where = f"{choice.index + 1}–{choice.index + 2}"
        what = choice.kind
    line = f"    {where:<5} {what:<10}"
    if choice.candidate_count == 0:
        return line + " no candidates"

    line += f" {choice.candidate_count} candidate{'s' if choice.candidate_count != 1 else ''}"
    if choice.candidates:
        fits = choice.chain_fits or (None,) * len(choice.candidates)
        ranked = sorted(zip(choice.candidates, fits), key=lambda item: -item[0][1])
        shown = []
        for (candidate, weight), fit in ranked[:SHOWN_CANDIDATES]:
            text = "·".join(format_element(element) for element in candidate.elements)
            if fit is not None and strength:
                weight /= fit**strength  # the classic part of the weight
            if statistical:
                text += f" ×{round(weight):g}"
            if fit is not None:
                text += f" chain {fit:.0%}"
            shown.append(text)
        if len(ranked) > SHOWN_CANDIDATES:
            shown.append(f"+{len(ranked) - SHOWN_CANDIDATES} more")
        line += ": " + ", ".join(shown)
    chosen = "·".join(format_element(element) for element in choice.chosen.elements)
    return f"{line} → {chosen}"
