"""Command-line interface: ``python -m namebook <command> ...``."""

from __future__ import annotations

import argparse
import io
import random
import sys
import textwrap
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from namebook.analysis import ElementPair, ElementStats, StyleAnalysis, analyze
from namebook.combining import CombinedName, Combiner
from namebook.explain import explain_combined
from namebook.formats import find_style, load_style, save_style
from namebook.formats.toml import setting_value
from namebook.generator import (
    GeneratedName,
    GenerationError,
    NameGenerator,
    SkippedTemplate,
    describe_length,
    template_problem,
)
from namebook.inference import Inference, infer, infer_lists
from namebook.quality import (
    NEAR_COPY_CHANGES,
    REPORT_NOTES,
    Measurements,
    SeedIndex,
    measure,
    measure_seeds,
    report,
)
from namebook.style import (
    MAX_FIT_LEVEL,
    MAX_LENGTH,
    MAX_LOOK_BACK,
    MAX_VALIDATION_LEVEL,
    SETTINGS,
    ApostropheMode,
    GenerationOptions,
    HyphenMode,
    Markov,
    Punctuation,
    Style,
    StyleError,
    Template,
    pin_working_settings,
)
from namebook.templates import (
    TemplateError,
    combination_problem,
    parse_combination,
    parse_recipe,
    parse_template_text,
)
from namebook.validation import Validator, load_word_list
from namebook.words import (
    Word,
    WordSplitError,
    WordSplitter,
    format_element,
    format_substructure,
    structure_shape,
)

LABEL_WIDTH = 18
LINE_WIDTH = 79
SAMPLE_SIZE = 8
SPLIT_SAMPLE_SIZE = 12
MOST_COMMON = 8


def main(argv: list[str] | None = None) -> int:
    _make_output_safe()
    args = _build_parser().parse_args(argv)
    return args.handler(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="namebook",
        description="Make new names that sound like a list of example names (a style).",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    show = commands.add_parser("show", help="read a style file and describe what is in it")
    show.add_argument("style", help=STYLE_HELP)
    show.add_argument("--names", action="store_true", help="also list every seed name")
    show.set_defaults(handler=_show)

    analyze_command = commands.add_parser(
        "analyze", help="show how a style's names split into elements, and what is learned"
    )
    analyze_command.add_argument("style", help=STYLE_HELP)
    analyze_command.add_argument(
        "--all", action="store_true", help="show how every seed name splits, not just the first few"
    )
    analyze_command.add_argument(
        "--name",
        action="append",
        metavar="NAME",
        help="only show how NAME splits with this style's rules (may be given several times)",
    )
    analyze_command.set_defaults(handler=_analyze)

    generate = commands.add_parser("generate", help="make new names in a style")
    _add_batch_options(
        generate,
        count=10,
        seed_help="random seed; the same seed always gives the same names",
    )
    generate.add_argument("--explain", action="store_true", help="show how each name was built")
    _add_setting_options(generate)
    generate.set_defaults(handler=_generate)

    evaluate = commands.add_parser(
        "evaluate", help="make a batch of names and measure how they compare with the seeds"
    )
    _add_batch_options(
        evaluate,
        count=EVALUATE_COUNT,
        seed=EVALUATE_SEED,
        seed_help=f"random seed (default {EVALUATE_SEED}, so the same command gives the same report)",
    )
    _add_near_option(evaluate)
    _add_setting_options(evaluate)
    evaluate.set_defaults(handler=_evaluate)

    compare = commands.add_parser(
        "compare",
        help="measure the style's own settings and your changes to them, side by side",
        description="Make the same batch of names twice, with the same random seed: once with "
        "the style's own settings (A) and once with the changes you give (B), and show the "
        "measurements side by side.",
    )
    _add_batch_options(
        compare,
        count=EVALUATE_COUNT,
        seed=EVALUATE_SEED,
        seed_help=f"random seed for both batches (default {EVALUATE_SEED})",
    )
    _add_near_option(compare)
    _add_setting_options(compare, " for batch B")
    compare.set_defaults(handler=_compare)

    create = commands.add_parser(
        "create",
        help=f"save a style or list of names as a style file in the {STYLES_FOLDER} folder",
        description=f"Read a style or list of names and save it as a style file in the {STYLES_FOLDER} "
        "folder, named after the original. The settings it pins are written; with --pin, so are "
        "the settings worked out from the names. The original file is not changed.",
    )
    create.add_argument("style", help=f"the style or list of names to save: {STYLE_HELP}")
    create.add_argument(
        "--pin", action="store_true", help="also write the settings worked out from the names"
    )
    create.add_argument("--replace", action="store_true", help="replace a style file that already exists")
    create.set_defaults(handler=_create)

    window = commands.add_parser(
        "window", help="open the interface, to make names and change a style as you go"
    )
    window.add_argument(
        "style",
        nargs="?",
        help=f"the style to open: {STYLE_HELP} (by default the first style in the {STYLES_FOLDER} folder)",
    )
    window.set_defaults(handler=_window)
    return parser


# namebook's own folder of styles, in the folder the command is run from (D57).
STYLES_FOLDER = Path("styles")
STYLE_FILE_TYPE = ".toml"
STYLE_HELP = (
    f"a style's name, found in the {STYLES_FOLDER} folder, or a file: "
    "e.g. Quenya, or C:\\names\\Elves.txt"
)


EVALUATE_COUNT = 1000
EVALUATE_SEED = 1
COMPARE_SAMPLE_SIZE = 8

# Generation options that can be switched on (--prefixes) or off (--no-prefixes).
GENERATE_SWITCHES = {
    "structural": "element lengths follow the seeds' substructures",
    "statistical": "choose in proportion to how often things occur in the seeds",
    "prefixes": "begin long words with a prefix pair from the seeds",
    "suffixes": "end words with a suffix pair from the seeds",
    "shuffle": "pool middle positions together",
    "refuse_copies": "make a name again when it is one of the seed names",
}


def _add_batch_options(
    parser: argparse.ArgumentParser, count: int, seed_help: str, seed: int | None = None
) -> None:
    """Options for what to make: the style, how many names, the random seed and an initial."""
    parser.add_argument("style", help=STYLE_HELP)
    parser.add_argument(
        "-n", "--count", type=_positive_int, default=count, help=f"how many names (default {count})"
    )
    parser.add_argument("--seed", type=int, default=seed, help=seed_help)
    parser.add_argument("--initial", help="only make names starting with these letters")


def _add_near_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--near",
        type=_positive_int,
        default=NEAR_COPY_CHANGES,
        metavar="CHANGES",
        help="names this many letter changes or fewer from a seed name count as near copies "
        f"(default {NEAR_COPY_CHANGES})",
    )


def _add_setting_options(parser: argparse.ArgumentParser, which: str = "") -> None:
    """Options for how names are made, each overriding the style's own setting."""
    parser.add_argument(
        "--infer",
        action="store_true",
        help=f"ignore the settings the style pins, and work them all out from the names{which}",
    )
    parser.add_argument(
        "--fit",
        type=int,
        choices=range(MAX_FIT_LEVEL + 1),
        help=f"fit level{which}, overriding the style's",
    )
    parser.add_argument(
        "--validation",
        type=int,
        choices=range(MAX_VALIDATION_LEVEL + 1),
        help=f"validation level{which}, overriding the style's",
    )
    parser.add_argument(
        "--punctuation",
        type=int,
        choices=range(8),
        help=f"punctuation modes{which}, overriding the style's: 1 accents (AA becomes Á), "
        "2 diaeresis (Eärendil, Manwë), 4 vowel harmony (Väinö), added together",
    )
    parser.add_argument(
        "--apostrophe",
        choices=[mode.value for mode in ApostropheMode],
        help=f"whether the apostrophe is a vowel letter, the usual choice, or a consonant letter{which}, "
        "overriding the style's",
    )
    parser.add_argument(
        "--hyphen",
        choices=[mode.value for mode in HyphenMode],
        help=f"whether the hyphen is a consonant letter, the usual choice, or a joint between two words, "
        f"as a space is{which}, overriding the style's",
    )
    parser.add_argument(
        "--markov",
        choices=[mode.value for mode in Markov],
        help=f"how words are built{which}, overriding the style's: off (by the building rules alone), "
        "weighted (a Markov chain learned from the names weighs the rules' choices) or "
        "fill (the chain spells each word letter by letter, in a shape from the names)",
    )
    parser.add_argument(
        "--markov-order",
        type=int,
        choices=range(MAX_LOOK_BACK + 1),
        metavar="LETTERS",
        help=f"how many letters the Markov chain looks back{which}, 1 to {MAX_LOOK_BACK} "
        "(0 to work it out from the names)",
    )
    parser.add_argument(
        "--markov-strength",
        type=_strength,
        metavar="STRENGTH",
        help=f"how much the Markov chain counts{which}, from 0 (not at all) to 1",
    )
    for option, what in (("min", "fewest"), ("max", "most")):
        parser.add_argument(
            f"--{option}-length",
            type=_length,
            metavar="LETTERS",
            help=f"the {what} letters a name may have{which}, overriding the style's (0 for no limit)",
        )
    parser.add_argument(
        "--template",
        action="append",
        metavar="RECIPE",
        help=f"use this template{which} instead of the style's, e.g. \"5'7\" or \"9|13 6:2\" "
        "(may be given several times)",
    )
    parser.add_argument(
        "--combination",
        action="append",
        metavar="LISTS",
        help=f"put the style's lists together this way{which}, instead of names from the first "
        'list alone, e.g. "{given} {family}" or "{family}, {given}" (may be given several times, '
        'with weights: "{given}:2")',
    )
    parser.add_argument(
        "--word-list",
        metavar="FILE",
        help=f"a text file of words never to make{which}, one per line (* matches any letters)",
    )
    for option, help_text in GENERATE_SWITCHES.items():
        parser.add_argument(
            f"--{option.replace('_', '-')}",
            action=argparse.BooleanOptionalAction,
            default=None,
            help=help_text + (f" ({which.strip()})" if which else ""),
        )


def _length(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if not 0 <= value <= MAX_LENGTH:
        raise argparse.ArgumentTypeError(f"{value} must be 0 to {MAX_LENGTH}")
    return value


def _strength(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None
    if not 0 <= value <= 1:
        raise argparse.ArgumentTypeError(f"{value:g} must be 0 to 1")
    return value


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"{value} is too small; it must be 1 or more")
    return value


def _show(args: argparse.Namespace) -> int:
    try:
        style = _load(args.style)
        inferences = infer_lists(style)
    except StyleError as error:
        _print_problems(error)
        return 1
    print(describe_style(style, inferences, list_names=args.names))
    return 0


def _analyze(args: argparse.Namespace) -> int:
    """Each list in turn, under its name when the style has several."""
    blocks = []
    try:
        inferences = infer_lists(_load(args.style))
        for list_name, inference in inferences.items():
            heading = [_list_heading(list_name), ""] if len(inferences) > 1 else []
            if args.name:
                text = describe_splits(WordSplitter.for_style(inference.working), args.name)
            else:
                text = describe_analysis(analyze(inference.working), show_all=args.all)
            blocks.append("\n".join([*heading, text]))
    except StyleError as error:
        _print_problems(error)
        return 1
    print("\n\n".join(blocks))
    return 0


def _list_heading(list_name: str) -> str:
    return f'List "{list_name}":'


def _create(args: argparse.Namespace) -> int:
    try:
        source = _find(args.style)
        style = load_style(source)
    except StyleError as error:
        _print_problems(error)
        return 1
    target = STYLES_FOLDER / (source.stem + STYLE_FILE_TYPE)
    if target.exists() and not args.replace:
        print(f"{target} already exists. Add --replace to replace it.", file=sys.stderr)
        return 1
    inferences = infer_lists(style)
    if args.pin:
        # Saving the working settings pins them (D52); inference itself is unchanged.
        style = pin_working_settings(style, {name: inference.working for name, inference in inferences.items()})
    save_style(style, target)
    names = sum(len(name_list.names) for name_list in style.name_lists)
    print(f"Saved {target}: {_plural(names, 'name')}; pinned settings: {_describe_pinned(style)}.")
    for list_name, inference in inferences.items():
        if not inference.reasons:
            continue
        names_of = f" of list {list_name}" if len(inferences) > 1 else ""
        heading = (
            f"Worked out from the names{names_of}, and pinned:" if args.pin
            else f"Worked out from the names{names_of} each time the style is used (add --pin to save them):"
        )  # fmt: skip
        print("\n" + "\n".join(_describe_inferred(inference, heading)))
    if source.suffix.lower() != STYLE_FILE_TYPE and source.parent.resolve() == STYLES_FOLDER.resolve():
        note = f'Note: {source} is still there. The name "{source.stem}" now finds {target}.'
        print(_wrap(note, "", "  "), file=sys.stderr)
    return 0


def _window(args: argparse.Namespace) -> int:
    # Imported here, so the other commands start without loading the window toolkit.
    from namebook.window import run

    return run(args.style, STYLES_FOLDER)


def _find(text: str) -> Path:
    """The file a style's name or path stands for, noting any choice made. Raises StyleError."""
    found = find_style(text, STYLES_FOLDER)
    if found.note:
        print(_wrap(found.note, "", "  "), file=sys.stderr)
    return found.path


def _load(text: str) -> Style:
    """Find and read the style a name or path stands for. Raises StyleError."""
    return load_style(_find(text))


@dataclass(frozen=True)
class _Settings:
    """How to make names: the style's own settings, with any changes from the command line."""

    analyses: dict[str, StyleAnalysis]  # each list the combinations use, by its name
    combinations: tuple[Template, ...]
    templates: list[Template] | None = None  # None means each list's own
    word_list: tuple[str, ...] = ()

    def generator(self, seed: int) -> Combiner:
        """A generator for these settings. Raises GenerationError if no name can ever be made."""
        rng = random.Random(seed)
        generators: dict[str, NameGenerator] = {}
        for list_name, analysis in self.analyses.items():
            generator = NameGenerator(
                analysis,
                analysis.style.options,
                rng,
                validator=Validator.classic(analysis, self.word_list),
                templates=self.templates,
            )
            if generator.skipped_templates:
                in_list = list_name if len(self.analyses) > 1 else None
                print(_describe_skipped(generator.skipped_templates, in_list), file=sys.stderr)
            generators[list_name] = generator
        return Combiner(generators, self.combinations, rng)


def _own_settings(written: Style) -> _Settings:
    """The style's own working settings, for every list. Raises StyleError."""
    combinations = (written.default_combination,)
    analyses = {name: analyze(infer(written.list_style(name)).working) for name in _lists_used(combinations)}
    return _Settings(analyses, combinations)


def _lists_used(combinations: tuple[Template, ...]) -> list[str]:
    return list(dict.fromkeys(name for c in combinations for name in parse_combination(c.recipe).lists))


def _load_settings(args: argparse.Namespace, written: Style | None = None) -> _Settings | None:
    """The settings names are made with: each list's working settings, with
    the setting options given used as if pinned, so that the others are
    worked out around them (or every other setting inferred, with --infer).

    ``written`` is the style as read, when it has been read already. None
    after an error, which has been printed.
    """
    try:
        written = written or _load(args.style)
    except StyleError as error:
        _print_problems(error)
        return None
    given = _given(args, written)
    if given is None:
        return None
    combinations = (written.default_combination,)
    if args.combination:
        combinations = tuple(parse_template_text(text) for text in args.combination)
        for combination in combinations:
            if problem := combination_problem(combination, written.list_names):
                print(f'Combination "{combination.recipe}" {problem}.', file=sys.stderr)
                return None
    word_list: tuple[str, ...] = ()
    if args.word_list:
        try:
            word_list = load_word_list(args.word_list)
        except StyleError as error:
            _print_problems(error, "the word list ")
            return None

    templates = None
    if args.template:
        templates = [parse_template_text(text) for text in args.template]
        for template in templates:
            try:
                parse_recipe(template.recipe)
            except TemplateError as error:
                print(f'Template "{template.recipe}" {error}.', file=sys.stderr)
                return None

    try:
        analyses = {
            name: analyze(infer(written.list_style(name), ignore_pins=args.infer, given=given).working)
            for name in _lists_used(combinations)
        }
    except StyleError as error:
        _print_problems(error)
        return None
    return _Settings(analyses, combinations, templates, word_list)


def _given(args: argparse.Namespace, written: Style) -> dict[str, object] | None:
    """The settings given as options, by their names in style files, for
    every list; None if they ask for a length no name can have."""
    given = {name: getattr(args, name) for name in GENERATE_SWITCHES}
    given["fit_level"] = args.fit
    given["validation_level"] = args.validation
    given["min_length"] = args.min_length
    given["max_length"] = args.max_length
    given["markov_order"] = args.markov_order
    given["markov_strength"] = args.markov_strength
    if args.markov is not None:
        given["markov"] = Markov(args.markov)
    if args.punctuation is not None:
        given["punctuation"] = Punctuation(args.punctuation)
    if args.apostrophe is not None:
        given["letter_apostrophe"] = ApostropheMode(args.apostrophe)
    if args.hyphen is not None:
        given["letter_hyphen"] = HyphenMode(args.hyphen)
    given = {name: value for name, value in given.items() if value is not None}
    # Pinned lengths still apply unless --infer drops them.
    for list_name in written.list_names:
        style = written.list_style(list_name)
        pinned = {} if args.infer else {key: style.setting(key) for key in style.pinned}
        fewest = given.get("min_length", pinned.get("min_length", 0))
        most = given.get("max_length", pinned.get("max_length", 0))
        if fewest and most and fewest > most:
            print(f"A name cannot have at least {fewest} letters and at most {most}.", file=sys.stderr)
            return None
    return given


def _describe_changes(args: argparse.Namespace) -> list[str]:
    """The setting options given, as they would be typed."""
    changes = ["--infer"] if args.infer else []
    for option, value in (("fit", args.fit), ("validation", args.validation),
                          ("markov", args.markov), ("markov-order", args.markov_order),
                          ("markov-strength", args.markov_strength),
                          ("min-length", args.min_length), ("max-length", args.max_length),
                          ("punctuation", args.punctuation), ("apostrophe", args.apostrophe),
                          ("hyphen", args.hyphen)):  # fmt: skip
        if value is not None:
            changes.append(f"--{option} {value}")
    for option in GENERATE_SWITCHES:
        value = getattr(args, option)
        if value is not None:
            option = option.replace("_", "-")
            changes.append(f"--{option}" if value else f"--no-{option}")
    changes.extend(f'--template "{recipe}"' for recipe in args.template or ())
    changes.extend(f'--combination "{text}"' for text in args.combination or ())
    if args.word_list:
        changes.append(f"--word-list {args.word_list}")
    return changes


def _generate(args: argparse.Namespace) -> int:
    settings = _load_settings(args)
    if not settings:
        return 1
    # The seed itself is picked at random when none is given, and printed, so
    # any batch of names can be made again.
    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(1_000_000)
    try:
        generator = settings.generator(seed)
        for number in range(1, args.count + 1):
            name = generator.generate(initial=args.initial, explain=args.explain)
            if args.explain:
                print(explain_combined(name, number) + "\n")
            else:
                print(name.text if name.ok else "(no name could be made; see --explain)")
    except GenerationError as error:
        print(error, file=sys.stderr)
        return 1
    if args.seed is None:
        print(f"\n(Random seed {seed}. Add --seed {seed} to get these names again.)")
    return 0


def _evaluate(args: argparse.Namespace) -> int:
    settings = _load_settings(args)
    if not settings:
        return 1
    try:
        batch = _make_batch(settings, args)
    except GenerationError as error:
        print(error, file=sys.stderr)
        return 1
    title = next(iter(settings.analyses.values())).style.title
    lines = [f"{title}: {_plural(args.count, 'name')} made with random seed {args.seed}."]
    if changes := _describe_changes(args):
        lines.append(_wrap("Settings changed: " + " ".join(changes), "", "  "))
    # Each list's names are measured against that list's own.
    lines.append("")
    for list_name, analysis in settings.analyses.items():
        seeds = SeedIndex.for_analysis(analysis)
        measurements = measure(analysis, _list_names(batch, list_name), args.near, seeds)
        if len(settings.analyses) > 1:
            lines.append(_list_heading(list_name))
        lines += _describe_report(measure_seeds(analysis, args.near, seeds), [measurements], ["Made"])
    lines += [
        _wrap("Sample: " + ", ".join(_sample(batch)), "", "  "),
        "",
        *_report_notes(),
    ]
    print("\n".join(lines))
    return 0


def _compare(args: argparse.Namespace) -> int:
    changes = _describe_changes(args)
    if not changes:
        print(
            'Give at least one setting to change for batch B, e.g. --fit 2 or --template "#".',
            file=sys.stderr,
        )
        return 1
    try:
        written = _load(args.style)
        own = _own_settings(written)
    except StyleError as error:
        _print_problems(error)
        return 1
    # B's settings are worked out again around the changes, so B has its own analyses.
    changed = _load_settings(args, written)
    if not changed:
        return 1
    batches = []
    for label, settings in (("A", own), ("B", changed)):
        try:
            batches.append(_make_batch(settings, args))
        except GenerationError as error:
            print(f"Batch {label}: {error}", file=sys.stderr)
            return 1
    lines = [
        f"{written.title}: {_plural(args.count, 'name')} made twice with random seed "
        f"{args.seed}:",
        "  A: the style's own settings",
        _wrap("B: " + " ".join(changes), "  ", "     "),
    ]
    # Each list's names are measured against that list's own, in each batch that uses the list.
    list_names = list(dict.fromkeys([*own.analyses, *changed.analyses]))
    lines.append("")
    for list_name in list_names:
        used = [
            (label, settings.analyses[list_name], batch)
            for label, settings, batch in zip(("A", "B"), (own, changed), batches)
            if list_name in settings.analyses
        ]
        seeds = SeedIndex.for_analysis(used[0][1])
        measured = [measure(analysis, _list_names(batch, list_name), args.near, seeds) for _, analysis, batch in used]
        if len(list_names) > 1:
            lines.append(_list_heading(list_name))
        lines += _describe_report(
            measure_seeds(used[0][1], args.near, seeds), measured, [label for label, _, _ in used]
        )
        if len(used) == 2 and used[0][1].structures != used[1][1].structures:
            lines += [
                _wrap(
                    "Note: B splits the seed names into elements differently from A, so its "
                    "structures are numbered differently: read its structure match with care.",
                    "",
                    "  ",
                ),
                "",
            ]
    for label, batch in zip(("A", "B"), batches):
        lines.append(_wrap(f"Sample {label}: {', '.join(_sample(batch))}", "", "  "))
    lines += ["", *_report_notes()]
    print("\n".join(lines))
    return 0


def _make_batch(settings: _Settings, args: argparse.Namespace) -> list[CombinedName]:
    generator = settings.generator(args.seed)
    return [generator.generate(initial=args.initial) for _ in range(args.count)]


def _list_names(batch: list[CombinedName], list_name: str) -> list[GeneratedName]:
    """The names made from one list, for all the full names of a batch."""
    return [part.name for name in batch for part in name.names if part.list_name == list_name]


def _sample(batch: list[CombinedName]) -> list[str]:
    return [name.text for name in batch if name.ok][:COMPARE_SAMPLE_SIZE]


def _describe_report(
    seeds: Measurements, batches: list[Measurements], headings: list[str]
) -> list[str]:
    """The quality report as tables, one per section, each followed by a blank line.

    The columns line up across all the tables.
    """
    tables = [
        ((section.title, "Seeds", *headings),
         [(row.label, row.seeds, *row.values) for row in section.rows])
        for section in report(seeds, batches)
    ]  # fmt: skip
    every_row = [row for heading, rows in tables for row in (heading, *rows)]
    widths = [max(len(row[column]) for row in every_row) for column in range(len(every_row[0]))]
    lines: list[str] = []
    for heading, rows in tables:
        lines.extend(_table(heading, rows, align_right=True, widths=widths))
        lines.append("")
    return lines


def _report_notes() -> list[str]:
    return ["Notes:", *(_wrap(note, "  - ", "    ") for note in REPORT_NOTES)]


def _describe_skipped(skipped: list[SkippedTemplate], list_name: str | None = None) -> str:
    recipes = ", ".join(f'"{item.template.recipe}:{item.template.weight}"' for item in skipped)
    of_list = f" of list {list_name}" if list_name else ""
    return _wrap(
        f"Note: skipping {_plural(len(skipped), 'template')}{of_list} that can never be used: {recipes}. "
        '"analyze" shows why.',
        "",
        "  ",
    )


def _print_problems(error: StyleError, what: str = "") -> None:
    print(f"Could not read {what}{error.source}:", file=sys.stderr)
    for problem in error.problems:
        print(_wrap(problem, "  - ", "    "), file=sys.stderr)


def describe_style(style: Style, inferences: dict[str, Inference], list_names: bool = False) -> str:
    """A plain-language description of a style and each list's working
    settings (``infer_lists``), as shown by ``show``."""
    rows: list[tuple[str, str]] = [("Title", style.title)]
    if style.description:
        rows.append(("Description", " / ".join(style.description)))
    if style.source and style.source.path:
        encoding = f" (read as {style.source.encoding})" if style.source.encoding else ""
        rows.append(("File", f"{style.source.path}{encoding}"))
    several = len(style.name_lists) > 1
    if several:
        rows.append(("Lists", f"{', '.join(style.list_names)}: names are made from {style.list_names[0]} "
                              "unless --combination puts the lists together"))  # fmt: skip
    lines = [_format_row(label, value) for label, value in rows]

    for list_name, inference in inferences.items():
        if several:
            lines += ["", _list_heading(list_name)]
        lines += _describe_list(inference)

    if style.source and style.source.warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(_wrap(warning, "  - ", "    ") for warning in style.source.warnings)
    if list_names:
        for name_list in style.name_lists:
            lines.append("")
            lines.append(f"Seed names ({name_list.name}):")
            width = len(str(len(name_list.names)))
            for number, name in enumerate(name_list.names, start=1):
                lines.append(f"  {number:>{width}}  {name}")
    return "\n".join(lines)


def _describe_list(inference: Inference) -> list[str]:
    """One list's names and working settings, and why each inferred setting has its value."""
    style = inference.working
    rows: list[tuple[str, str]] = [("Seed names", _describe_names(style.names))]
    rows.append(("Pinned settings", _describe_pinned(style)))
    rows.append(("Options", _describe_switches(style.options)))
    rows.append(("", f"fit level {style.options.fit_level}, "
                     f"validation level {style.options.validation_level}"))
    rows.append(("Markov chain", _describe_markov(style.options)))
    rows.append(("Letters", f"Y: {style.options.y_mode.name.lower()}, "
                            f"U: {style.options.u_mode.name.lower()}, "
                            f"apostrophe: {style.setting('letter_apostrophe').value}, "
                            f"hyphen: {style.options.hyphen.value}"))
    rows.append(("Punctuation", _describe_punctuation(style.options.punctuation)))
    rows.append(("Special letters", _describe_special_letters(style)))
    rows.append(("Templates", _describe_templates(style)))
    length = style.options.length_range
    rows.append(("Length", describe_length(*length) if length else "any"))

    lines = [_format_row(label, value) for label, value in rows]
    if inference.reasons:
        lines += ["", *_describe_inferred(inference, "Worked out from the names:")]
    return lines


def _describe_pinned(style: Style) -> str:
    """The settings a style pins: for every list, then any a list pins for itself alone."""
    if all(style.list_style(name).pinned == set(SETTINGS) for name in style.list_names):
        return "all"
    described = [", ".join(key for key in SETTINGS if key in style.pinned) or "none"]
    for name_list in style.name_lists:
        if name_list.settings:
            own = ", ".join(key for key in SETTINGS if key in name_list.settings)
            described.append(f"list {name_list.name} also {own}")
    return "; ".join(described)


def _describe_inferred(inference: Inference, heading: str) -> list[str]:
    """Each inferred setting, as a style file would write it, and why it has that value."""
    assignments = {
        key: f"  {key} = {setting_value(inference.working, key)}"
        for key in SETTINGS
        if key in inference.reasons
    }
    width = max(len(assignment) for assignment in assignments.values()) + 2
    lines = [heading]
    for key, assignment in assignments.items():
        lines.append(_wrap(inference.reasons[key], assignment.ljust(width), " " * width))
    return lines


def _describe_names(names: tuple[str, ...]) -> str:
    counts = Counter(names)
    repeats = sum(count - 1 for count in counts.values())
    summary = f"{len(names)}"
    if repeats:
        summary += f" ({repeats} {'is a repeat' if repeats == 1 else 'are repeats'})"
    sample = ", ".join(names[:SAMPLE_SIZE])
    if len(names) > SAMPLE_SIZE:
        sample += ", ..."
    return f"{summary}: {sample}"


def _describe_switches(options: GenerationOptions) -> str:
    switches = [
        ("structural", options.structural),
        ("statistical", options.statistical),
        ("prefixes", options.prefixes),
        ("suffixes", options.suffixes),
        ("shuffle", options.shuffle),
        ("refuse copies", options.refuse_copies),
    ]
    return ", ".join(f"{name} {'on' if value else 'off'}" for name, value in switches)


def _describe_markov(options: GenerationOptions) -> str:
    if options.markov is Markov.OFF:
        return "off (building rules alone)"
    look_back = (
        f"looks back {options.markov_order} letter{'s' if options.markov_order != 1 else ''}"
        if options.markov_order else "look-back worked out from the names"
    )  # fmt: skip
    return f"{options.markov.value}: {look_back}, strength {options.markov_strength:g}"


def _describe_punctuation(punctuation: Punctuation) -> str:
    modes = [mode.name.lower().replace("_", " ") for mode in Punctuation if mode and mode in punctuation]
    return ", ".join(modes) if modes else "none"


def _describe_special_letters(style: Style) -> str:
    special = style.special_letters
    parts = []
    if special.soft_consonants:
        parts.append("soft consonants (TH, CH, KH, ... count as one letter)")
    if special.consonant_clusters:
        parts.append("consonant clusters " + ", ".join(special.consonant_clusters))
    if special.multi_vowels:
        parts.append("multi vowels")
    return "; ".join(parts) if parts else "none"


def _describe_templates(style: Style) -> str:
    if not style.templates:
        return "none (any structure)"
    recipes = ", ".join(f'"{t.recipe}:{t.weight}"' for t in style.templates)
    return f"{len(style.templates)}: {recipes}"


def describe_splits(splitter: WordSplitter, names: list[str]) -> str:
    """How each of ``names`` splits into elements, as shown by ``analyze --name``."""
    rows: list[tuple[str, ...]] = []
    failures: list[str] = []
    for name in names:
        try:
            words = splitter.split_name(name)
        except WordSplitError as error:
            failures.append(_wrap(f"{name} cannot be split: {error}.", "  ", "    "))
            continue
        rows.extend(_split_row(word) for word in words)
    lines = _table(SPLIT_HEADINGS, rows) if rows else []
    return "\n".join(lines + failures)


def describe_analysis(analysis: StyleAnalysis, show_all: bool = False) -> str:
    """A plain-language summary of what was learned from a style, as shown by ``analyze``."""
    style = analysis.style
    names = style.name_list(analysis.list_name).names
    lines = [f"{style.title}: {len(names)} seed names, split into {len(analysis.words)} words.", ""]

    heading = "How the names split into elements"
    if not show_all and len(names) > SPLIT_SAMPLE_SIZE:
        heading += f" (first {SPLIT_SAMPLE_SIZE} names; add --all to see every name)"
    shown = [
        seed.word
        for seed in analysis.words
        if show_all or seed.name_number <= SPLIT_SAMPLE_SIZE
    ]
    lines.append(heading + ":")
    lines.extend(_table(SPLIT_HEADINGS, [_split_row(word) for word in shown]))

    lines += ["", "Structures (word shapes) and their substructures (element lengths):"]
    rows = []
    for structure in sorted(analysis.structures):
        count = analysis.structures[structure]
        substructures = analysis.substructures[structure]
        common = ", ".join(
            _times(format_substructure(sub), n) for sub, n in substructures.most_common(4)
        )
        if len(substructures) > 4:
            common += f", ... ({len(substructures)} kinds)"
        rows.append((str(structure), structure_shape(structure), _plural(count, "word"), common))
    lines.extend(_table(("Structure", "Shape", "Seeds", "Most common substructures"), rows))

    lines += ["", "Elements (runs of vowel letters or consonant letters):"]
    for label, inventory in (("Vowel", analysis.vowel_elements),
                             ("Consonant", analysis.consonant_elements)):  # fmt: skip
        lines.append(_wrap(
            f"{label} elements: {len(inventory)} kinds. Most common: {_common_elements(inventory)}",
            "  ", "    ",
        ))  # fmt: skip

    lines += ["", "Prefixes and suffixes (the first two and last two elements of a word):"]
    for label, pairs in (("Prefixes", analysis.prefixes), ("Suffixes", analysis.suffixes)):
        common = ", ".join(_times(_format_pair(pair), n) for pair, n in pairs.most_common(MOST_COMMON))
        lines.append(_wrap(f"{label}: {len(pairs)} kinds. Most common: {common}", "  ", "    "))

    fitting = analysis.fitting
    lines += [
        "",
        "Fitting: neighboring combinations found in the seeds:",
        f"  Level 1: {len(fitting.consonant_to_vowel)} consonant → vowel and "
        f"{len(fitting.vowel_to_consonant)} vowel → consonant",
        f"  Level 2: {len(fitting.consonant_to_consonant)} consonant … consonant "
        "(across one vowel element)",
        f"  Level 3: {len(fitting.vowel_to_vowel)} vowel … vowel (across one consonant element)",
    ]

    lines += ["", *_describe_template_use(analysis)]
    lines += ["", *_describe_letter_limits(analysis)]
    return "\n".join(lines)


def _describe_template_use(analysis: StyleAnalysis) -> list[str]:
    templates = analysis.style.templates
    if not templates:
        return ['Templates: none, so each name is one word of any structure the seeds have ("#").']
    unusable = [(t, problem) for t in templates if (problem := template_problem(analysis, t))]
    lines = [
        f"Templates: {len(templates)}, of which {len(templates) - len(unusable)} can be used."
    ]
    for template, problem in unusable:
        lines.append(f'  "{template.recipe}:{template.weight}" {problem}.')

    allowed: set[int] = set()
    for template in templates:
        if template_problem(analysis, template):
            continue
        for slot in parse_recipe(template.recipe).slots:
            allowed |= set(analysis.structures) if slot.structures is None else set(slot.structures)
    outside = sum(n for structure, n in analysis.structures.items() if structure not in allowed)
    if outside:
        lines.append(_wrap(
            f"{outside} of {len(analysis.words)} seed words have a structure that no template "
            "allows, so words shaped like them are never made.",
            "  ", "  ",
        ))  # fmt: skip
    return lines


SPLIT_HEADINGS = ("Word", "Elements", "Structure", "Substructure")


def _split_row(word: Word) -> tuple[str, ...]:
    return (
        word.text,
        word.describe(),
        f"{word.structure} ({structure_shape(word.structure)})",
        format_substructure(word.substructure),
    )


def _common_elements(inventory: dict[tuple[str, ...], ElementStats]) -> str:
    ranked = sorted(inventory.values(), key=lambda stats: -stats.count)[:MOST_COMMON]
    return ", ".join(_times(format_element(stats.element), stats.count) for stats in ranked)


def _format_pair(pair: ElementPair) -> str:
    return f"{format_element(pair.first)}·{format_element(pair.second)}"


def _times(text: str, count: int) -> str:
    """``"A ×249"``, joined by a no-break space so line wrapping keeps it together."""
    return f"{text} ×{count}"


def _describe_letter_limits(analysis: StyleAnalysis) -> list[str]:
    limits = analysis.letter_limits
    lines = [
        "Letter limits used by validation:",
        "  Closest repeat: the smallest gap between two of the same letter in a seed word",
        "  (a gap of 2 is ANA; doubled letters like NN don't count).",
        "  Largest share: the most of one seed word a repeated letter fills.",
    ]
    rows = []
    for letter in sorted(set(limits.min_distance) | set(limits.max_share)):
        distance = str(limits.distance_for(letter))
        share = f"{limits.share_for(letter):.0%}"
        if letter not in limits.min_distance:
            distance += "*"
        if letter not in limits.max_share:
            share += "*"
        rows.append((letter, distance, share))
    rows.append(("others*", str(limits.fallback_distance), f"{limits.fallback_share:.0%}"))
    lines.extend(_table(("Letter", "Closest repeat", "Largest share"), rows))
    lines.append(_wrap(
        "* No seed word shows this limit for the letter, so the loosest limit seen for "
        "any letter is used.",
        "  ", "    ",
    ))  # fmt: skip
    return lines


def _table(
    headings: tuple[str, ...],
    rows: list[tuple[str, ...]],
    align_right: bool = False,
    widths: list[int] | None = None,
) -> list[str]:
    """Rows of text in aligned columns, indented by two spaces.

    The last column is not padded, unless ``align_right`` lines up every
    column but the first on the right, as numbers are. ``widths`` sets the
    column widths instead of fitting them to these rows.
    """
    widths = widths or [
        max(len(row[column]) for row in [headings, *rows] if column < len(row))
        for column in range(len(headings))
    ]
    lines = []
    for row in [headings, *rows]:
        if align_right:
            cells = [row[0].ljust(widths[0])] + [
                cell.rjust(widths[i]) for i, cell in enumerate(row) if i > 0
            ]
        else:
            cells = [cell.ljust(widths[i]) for i, cell in enumerate(row[:-1])] + [row[-1]]
        lines.append("  " + "  ".join(cells))
    return lines


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _format_row(label: str, value: str) -> str:
    first = f"{label + ':' if label else '':<{LABEL_WIDTH}}"
    return _wrap(value, first, " " * LABEL_WIDTH)


def _wrap(text: str, first_indent: str, later_indent: str) -> str:
    return textwrap.fill(
        text,
        width=LINE_WIDTH,
        initial_indent=first_indent,
        subsequent_indent=later_indent,
        break_long_words=False,
        break_on_hyphens=False,
    )


def _make_output_safe() -> None:
    """Never crash on a letter the console cannot show; print a '?' instead."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="replace")
