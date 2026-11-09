"""namebook's window (spec 13.9): make names, and change a style as you go.

Three columns: the style and its names on the left; its settings in the
middle, each with a marker saying whether it is pinned or inferred; and on
the right the names made, how the chosen one was built, and a log. The
window only shows a ``Workspace`` and hands it what is done; nothing is
written to a file until Save (D92). How it looks, light or dark, is up to
``namebook.looks``.

Inferring settings and making names can take seconds, so they run apart
from the window, which stays responsive meanwhile.

Started by ``python -m namebook window``.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from namebook import looks
from namebook.explain import explain_combined
from namebook.formats import NAMED_TYPES, find_style
from namebook.formats.toml import U_MODES, Y_MODES, read_title
from namebook.generator import GenerationError
from namebook.inference import infer_lists
from namebook.style import (
    MAX_FIT_LEVEL,
    MAX_LENGTH,
    MAX_LOOK_BACK,
    MAX_VALIDATION_LEVEL,
    SETTINGS,
    ApostropheMode,
    HyphenMode,
    Markov,
    Punctuation,
    StyleError,
    Template,
)
from namebook.templates import (
    LIST_CLOSE,
    LIST_OPEN,
    ListPart,
    TemplateError,
    Text,
    parse_combination,
    parse_recipe,
    parse_template_text,
)
from namebook.looks import FONT, MONO, MONO_BOLD, NAMES_FONT
from namebook.workspace import SAVED_TYPE, Batch, Setting, Source, Workspace

APP_NAME = "New Book of Names"  # what the window calls the program; namebook is its short name
PAD = 8
MAX_COUNT = 1000  # names in one batch
NAMES_PAUSE = 400  # milliseconds after typing stops before the names are read
NO_PART = "(none)"  # the choice that takes a part out of the full name
OPEN_TYPES = [("Styles and name lists", "*.toml *.txt *.ebn"), ("All files", "*.*")]
# Where the window remembers its look between runs.
PREFERENCES = Path(os.environ.get("APPDATA") or Path.home() / ".config") / "namebook" / "window.json"

# The settings in the middle column, in groups, and what each is called there.
GROUPS = (
    ("Markov chain", ("markov", "markov_order", "markov_strength")),
    ("Building rules", ("structural", "statistical", "fit_level", "prefixes", "suffixes", "shuffle",
                        "validation_level")),
    ("Names", ("refuse_copies", "templates", "min_length", "max_length", "punctuation")),
    ("Letters", ("letter_y", "letter_u", "letter_apostrophe", "letter_hyphen", "soft_consonants",
                 "multi_vowels", "consonant_clusters")),
)  # fmt: skip
LABELS = {
    "markov": "Markov mode",
    "markov_order": "Look-back",
    "markov_strength": "Chain strength",
    "structural": "Structural",
    "statistical": "Statistical",
    "fit_level": "Fit level",
    "prefixes": "Prefixes",
    "suffixes": "Suffixes",
    "shuffle": "Shuffle",
    "validation_level": "Validation level",
    "refuse_copies": "Refuse copies",
    "templates": "Templates",
    "min_length": "Fewest letters",
    "max_length": "Most letters",
    "punctuation": "Punctuation",
    "letter_y": "Y",
    "letter_u": "U",
    "letter_apostrophe": "Apostrophe",
    "letter_hyphen": "Hyphen",
    "soft_consonants": "Soft consonants",
    "multi_vowels": "Multi vowels",
    "consonant_clusters": "Consonant clusters",
}
CHOICES: dict[str, list[tuple[str, object]]] = {
    "markov": [("off", Markov.OFF), ("weighted", Markov.WEIGHTED), ("fill", Markov.FILL)],
    "fit_level": [(str(level), level) for level in range(MAX_FIT_LEVEL, -1, -1)],
    "validation_level": [(str(level), level) for level in range(MAX_VALIDATION_LEVEL, -1, -1)],
    "letter_y": list(Y_MODES.items()),
    "letter_u": list(U_MODES.items()),
    "letter_apostrophe": [(mode.value, mode) for mode in ApostropheMode],
    "letter_hyphen": [(mode.value, mode) for mode in HyphenMode],
}
SWITCHES = ("structural", "statistical", "prefixes", "suffixes", "shuffle", "refuse_copies", "soft_consonants",
            "multi_vowels")  # fmt: skip
NUMBERS: dict[str, tuple[float, float, float]] = {  # lowest, highest, step
    "markov_order": (0, MAX_LOOK_BACK, 1),
    "markov_strength": (0, 1, 0.1),
    "min_length": (0, MAX_LENGTH, 1),
    "max_length": (0, MAX_LENGTH, 1),
}
PUNCTUATION = (
    ("Accents", Punctuation.ACCENTS, "A doubled vowel becomes an accented one: Aa becomes Á, and Aaa becomes "
                                     "Â. For a list that writes long vowels doubled, when you would rather "
                                     "see accents."),
    ("Diaeresis", Punctuation.DIAERESIS, "Two vowels said separately get two dots, as in Eärendil and Fëanor, "
                                         "and so does an E at the end of a word, as in Manwë. For an Elvish or "
                                         "old-fashioned look."),
    ("Vowel harmony", Punctuation.VOWEL_HARMONY, "The vowels of a word are made to agree, as in Finnish: the "
                                                 "first of A, O, U or Ä, Ö, Y decides, so Väino becomes Väinö. "
                                                 "For Finnish-sounding names."),
)  # fmt: skip
# What each setting does, and why someone might change it, shown when the
# mouse rests on its name. A letter group is a run of vowels or of
# consonants, as the names split into them (Ae-thel-wulf: AE, TH, E, LW, U, LF).
HELP = {
    "markov": "How new words are put together. A Markov chain is a record of which letters tend to follow "
              "which in the names.\n- weighted: pieces of real names are joined, and the chain prefers the "
              "pieces that fit best. Names sound closest to the real ones, but vary a little less.\n- fill: "
              "each word takes the shape of a real name, and the chain spells it letter by letter. More "
              "varied and inventive, though now and then a name comes out odd.\n- off: pieces are joined by "
              "the building rules alone. The loosest and most surprising, and the least like the list.\n"
              "Try weighted for names that sound authentic, fill for more variety.",
    "markov_order": "How many letters back the chain looks when it picks the next letter (1 to 5; 0 works it "
                    "out from the names). More copies longer stretches of real spelling, so names sound closer "
                    "to the list but come nearer to copying it; fewer mixes more freely, for more inventive but "
                    "odder names. Raise it if names sound wrong, lower it if they feel like near copies.",
    "markov_strength": "How much the chain counts, from 0 (not at all) to 1 (fully). Lower values let letters "
                       "through that the names seldom put together, for stranger names that sound less like "
                       "the list. Leave it at 1 unless you want that.",
    "structural": "Each word copies the pattern of short and long letter groups from a real name of the same "
                  "shape, so its rhythm sounds right. Turn it off to let letter groups of any length go "
                  "anywhere, for wilder names. Fill needs it on.",
    "statistical": "Letter groups and word shapes common in the names are chosen more often, so new names feel "
                   "typical of the list. Turn it off to give rare ones an equal chance, for more unusual names.",
    "fit_level": "How closely each letter group must match its neighbors in real names, 0 to 3. Higher keeps "
                 "names closer to the list; lower mixes the pieces more freely. It is loosened by itself for a "
                 "name that cannot be built. It does nothing with fill, where the chain does this job.",
    "prefixes": "A word's first two letter groups are taken together from the start of a real name, so new "
                "names begin the way the list's do. Turn it off for more varied beginnings.",
    "suffixes": "A word's last two letter groups are taken together from the end of a real name, so new names "
                "end the way the list's do. Turn it off for more varied endings.",
    "shuffle": "Letter groups from the middle of names may go anywhere in the middle of a new word, not only "
               "where they were. Turn it on for more variety from a short list. It does little with fill.",
    "validation_level": "How strictly finished names are checked for letters repeated more than the list's "
                        "names ever do, such as the same letter too close together, 0 to 2. Lower it to allow "
                        "more doubled and repeated letters. It is loosened by itself when names keep failing.",
    "refuse_copies": "A name that comes out the same as one in the list is made again, so every name is new. "
                     "Capitals and accents don't count: Martin is a copy of Martín. Turn it off if you don't "
                     "mind real names turning up among the new ones.",
    "templates": "The shapes a whole name may have, one per line, each with a weight after a colon: # is a "
                 "word, a space or hyphen separates words, _ joins two words into one, and other characters "
                 "are copied as written. \"# #:1\" makes names of two words. Empty: one word, of any shape the "
                 "list's words have.",
    "min_length": "The fewest letters a name may have; 0 for no limit. Spaces, apostrophes and hyphens don't "
                  "count. Raise it to avoid very short names.",
    "max_length": "The most letters a name may have; 0 for no limit. Spaces, apostrophes and hyphens don't "
                  "count. Lower it for short, punchy names.",
    "punctuation": "Marks added to finished names, such as accents. Most lists need none. Rest the mouse on "
                   "each for what it does and when it suits.",
    "letter_y": "Whether Y counts as a vowel, a consonant, or a semivowel: a consonant before a vowel (Yara, "
                "Maya) and a vowel elsewhere. It changes how names are cut into letter groups. Semivowel suits "
                "lists where Y often starts a syllable.",
    "letter_u": "Whether U counts as a vowel, or a semivowel: a consonant after Q, as in Quinn. Semivowel "
                "keeps QU together like one sound, which suits lists with many QU names.",
    "letter_apostrophe": "Whether the apostrophe counts as a vowel letter, the usual choice, which suits names "
                         "like Aalbe'il, or a consonant letter, which suits names where it stands for a sound, "
                         "like Hawai'i.",
    "letter_hyphen": "Whether the hyphen counts as a consonant letter, the usual choice, or joins two words, as "
                     "a space does: Bel-Abrean is then the words Bel and Abrean, and new names get hyphenated "
                     "words like it.",
    "soft_consonants": "Pairs such as CH, KH, SH and TH each count as one letter, so they stay together like "
                       "the single sounds they are. Turn it on for lists full of them, such as Thorin and "
                       "Khazad.",
    "multi_vowels": "A vowel, one consonant and the same vowel again, such as ABA, ORO or ETHE, count as one "
                    "vowel, so they are copied as a whole. It rarely helps; try it for lists full of names "
                    "like Hereman and Ethelbald.",
    "consonant_clusters": "Groups of consonants that each count as one, with commas between them, such as "
                          "LTH, RR, so they stay together in new names. Add the clusters the list relies on.",
}

# How each setting's marker looks and what it says, by where its value comes from.
MARKERS = {
    Source.PINNED: ("●", "Pinned.TLabel", "Click to unpin it."),
    Source.LIST_PINNED: ("◉", "Pinned.TLabel", "Click to unpin it for this list."),
    Source.INFERRED: ("○", "Inferred.TLabel", "Click to pin it."),
    Source.NOT_INFERRED: ("○", "NotInferred.TLabel", "Click to pin it."),
}


def run(style: str | None, folder: Path) -> int:
    """Open the window, with ``style`` (a style's name or a file), or else the
    first style in ``folder``, and wait until it is closed."""
    _sharpen()
    root = tk.Tk()
    window = Window(root, folder, PREFERENCES)
    path, problem = None, None
    try:
        if style:
            found = find_style(style, folder)
            path = found.path
            if found.note:
                window.log(found.note)
        elif styles := window.styles():
            path = next(iter(styles.values()))
    except StyleError as error:
        problem = error
    root.after_idle(lambda: window.open(path) if path else window.new())
    if problem:
        root.after_idle(lambda: window.report(problem))
    root.mainloop()
    return 0


def _sharpen() -> None:
    """Draw text at the screen's own resolution on Windows, rather than blurred."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


def _read_preferences(path: Path | None) -> dict[str, Any]:
    try:
        preferences = json.loads(path.read_text(encoding="utf-8")) if path else {}
    except (OSError, ValueError):
        return {}
    return preferences if isinstance(preferences, dict) else {}


def _without_part(parts: list[ListPart | Text], place: int) -> list[ListPart | Text]:
    """A combination's ``parts`` without the list at ``place``, and the text
    joining it to the list before, or for the first list, to the list after:
    "{male}, son of {male}" without the second part is "{male}"."""
    first = not any(isinstance(part, ListPart) for part in parts[:place])
    before = not first and isinstance(parts[place - 1], Text)
    after = place + 1 < len(parts) and isinstance(parts[place + 1], Text)
    gone = {place, place - 1} if before else {place, place + 1} if after else {place}
    return [part for at, part in enumerate(parts) if at not in gone]


class Window:
    """The window: three columns showing a ``Workspace``."""

    def __init__(self, root: tk.Tk, folder: Path, preferences: Path | None = None):
        """``preferences`` is the file the window remembers its look in, if any."""
        self.root = root
        self.folder = folder
        self.preferences = preferences
        self.workspace = Workspace()
        self.shown_list = self.workspace.list_names[0]
        self.batch: Batch | None = None
        self.busy = False
        self._showing = False  # True while the window fills in values itself
        self._names_job: str | None = None
        self._action_buttons: list[ttk.Button] = []
        self._listed: dict[str, Path] = {}  # the styles the Style box lists, by the name it shows
        self._titles: dict[Path, tuple[int, str]] = {}  # each style file's time of change and name shown
        scale = root.winfo_fpixels("1i") / 96
        root.geometry(f"{int(1320 * scale)}x{int(800 * scale)}")
        root.minsize(int(900 * scale), int(560 * scale))
        root.protocol("WM_DELETE_WINDOW", self.close)

        body = ttk.Frame(root)  # behind everything, so the whole window takes the look's colors
        body.pack(fill="both", expand=True)
        bar = ttk.Frame(body, padding=(2 * PAD, PAD, 2 * PAD, 0))
        bar.pack(fill="x")
        look = _read_preferences(preferences).get("look")
        self.look = tk.StringVar(value=look if look in looks.LOOKS else looks.DEFAULT)
        for name in reversed(looks.LOOKS):
            ttk.Radiobutton(bar, text=name, value=name, variable=self.look,
                            command=self.change_look).pack(side="right", padx=(PAD, 0))  # fmt: skip
        ttk.Label(bar, text="Look:", style="Muted.TLabel").pack(side="right")

        # Three even columns. With equal weights a resize keeps them even; once
        # a divider is dragged, the widths are the user's.
        self.columns = columns = ttk.PanedWindow(body, orient="horizontal")
        columns.pack(fill="both", expand=True, padx=PAD, pady=(0, PAD))
        left, middle, right = (ttk.Frame(columns, padding=PAD) for _ in range(3))
        for column in (left, middle, right):
            columns.add(column, weight=1)
        self._even_columns = True
        columns.bind("<Configure>", lambda _e: self._even_out())
        columns.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_even_columns", False))
        self._build_style_column(left)
        self._build_settings_column(middle)
        self._build_names_column(right)
        for key, action in (("<Control-s>", self.save), ("<Control-o>", self.choose_file),
                            ("<Control-n>", self.new), ("<F5>", self.make_names)):  # fmt: skip
            root.bind(key, lambda _event, action=action: action())
        self.change_look(remember=False)

    def _even_out(self) -> None:
        """Make the columns even thirds, until a divider is dragged."""
        if not self._even_columns:
            return
        middle = self.columns.nametowidget(self.columns.panes()[1])
        divider = max(0, middle.winfo_x() - self.columns.sashpos(0))  # how wide each divider is
        third = (self.columns.winfo_width() - 2 * divider) / 3
        for _ in range(2):  # a divider cannot pass the other, so twice settles both
            self.columns.sashpos(1, round(2 * third + divider))
            self.columns.sashpos(0, round(third))

    def change_look(self, remember: bool = True) -> None:
        """Give the window the look chosen, and remember it for next time."""
        look = looks.apply(self.root, self.look.get())
        self.explanation.tag_configure("finished", foreground=look.finished)
        self.explanation.tag_configure("failed", foreground=look.problem)
        if remember and self.preferences:
            try:
                self.preferences.parent.mkdir(parents=True, exist_ok=True)
                self.preferences.write_text(json.dumps({"look": self.look.get()}), encoding="utf-8")
            except OSError:
                pass  # the look is still changed; it is only not remembered

    # --- Left: the style and its names -----------------------------------------

    def _build_style_column(self, frame: ttk.Frame) -> None:
        ttk.Label(frame, text="Style", style="Muted.TLabel").pack(anchor="w")
        self.style_box = ttk.Combobox(frame, state="readonly", postcommand=self._list_styles)
        self.style_box.pack(fill="x", pady=(2, PAD))
        self.style_box.bind("<<ComboboxSelected>>", lambda _e: self._style_chosen())

        ttk.Label(frame, text="Title", style="Muted.TLabel").pack(anchor="w")
        self.title_text = tk.StringVar()
        self.title_text.trace_add("write", lambda *_: self._title_typed())
        ttk.Entry(frame, textvariable=self.title_text).pack(fill="x", pady=(2, PAD))
        ttk.Label(frame, text="Description", style="Muted.TLabel").pack(anchor="w")
        self.description = tk.Text(frame, height=2, width=24, wrap="word", font=FONT, undo=True)
        self.description.pack(fill="x", pady=(2, PAD))
        self.description.bind("<<Modified>>", lambda _e: self._description_typed())

        ttk.Label(frame, text="List", style="Muted.TLabel").pack(anchor="w")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(2, PAD))
        self.list_box = ttk.Combobox(row, state="readonly", width=12)
        self.list_box.pack(side="left", fill="x", expand=True)
        self.list_box.bind("<<ComboboxSelected>>", lambda _e: self._list_chosen())
        add = ttk.Button(row, text="+", width=3, command=self.add_list)
        add.pack(side="left", padx=(4, 0))
        Tooltip(add, "Add a list, such as family names.")
        more = ttk.Menubutton(row, text="⋯", width=3)
        menu = tk.Menu(more, tearoff=False)
        menu.add_command(label="Rename this list…", command=self.rename_list)
        menu.add_command(label="Remove this list", command=self.remove_list)
        more["menu"] = menu
        more.pack(side="left", padx=(4, 0))

        ttk.Label(frame, text="Names, one per line", style="Muted.TLabel").pack(anchor="w")
        box = ttk.Frame(frame)
        box.pack(fill="both", expand=True, pady=(2, 2))
        self.names = tk.Text(box, width=24, wrap="none", font=FONT, undo=True)
        bar = ttk.Scrollbar(box, orient="vertical", command=self.names.yview)
        self.names.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.names.pack(side="left", fill="both", expand=True)
        self.names.bind("<<Modified>>", lambda _e: self._names_typed())
        self.names_count = ttk.Label(frame, style="Muted.TLabel")
        self.names_count.pack(anchor="w", pady=(0, PAD))
        self.names_tip = Tooltip(self.names_count, "")

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        for column, (text, command, tip) in enumerate((
            ("New", self.new, "Start a new style (Ctrl+N)."),
            ("Open…", self.choose_file, "Open a style or list of names from any folder (Ctrl+O)."),
            ("Save", self.save, "Save the style file (Ctrl+S)."),
            ("Save as…", self.save_as, "Save as a new style file."),
        )):  # fmt: skip
            button = ttk.Button(buttons, text=text, command=command)
            button.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 4, 0), pady=2)
            buttons.columnconfigure(column, weight=1, uniform="file buttons")
            Tooltip(button, tip)
            self._action_buttons.append(button)

    def styles(self) -> dict[str, Path]:
        """The styles in the styles folder, in order, by the name the Style box
        shows for each: its title, or else its file's name with spaces for
        underscores. Where a style file and a plain list have the same file
        name, the style file is the one shown, as ``find_style`` chooses it."""
        if not self.folder.is_dir():
            return {}
        files: dict[str, Path] = {}
        for entry in sorted(self.folder.iterdir()):
            kind = entry.suffix.lower()
            if entry.is_file() and kind in NAMED_TYPES:
                known = files.get(entry.stem.casefold())
                if known is None or NAMED_TYPES.index(kind) < NAMED_TYPES.index(known.suffix.lower()):
                    files[entry.stem.casefold()] = entry
        titles = {path: self._title(path) for path in files.values()}
        times = Counter(title.casefold() for title in titles.values())
        # Styles of the same title are told apart by their files.
        shown = {title if times[title.casefold()] == 1 else f"{title} ({path.name})": path
                 for path, title in titles.items()}  # fmt: skip
        return dict(sorted(shown.items(), key=lambda item: item[0].casefold()))

    def _title(self, path: Path) -> str:
        """The name the Style box shows for a file, read again only when the file changes."""
        stamp = path.stat().st_mtime_ns
        if path not in self._titles or self._titles[path][0] != stamp:
            title = read_title(path) if path.suffix.lower() == SAVED_TYPE else ""
            self._titles[path] = (stamp, title or path.stem.replace("_", " "))
        return self._titles[path][1]

    def _list_styles(self) -> None:
        self._listed = self.styles()
        self.style_box["values"] = list(self._listed)

    def _style_chosen(self) -> None:
        path = self._listed.get(self.style_box.get())
        if path is None:
            return
        try:
            found = find_style(path.stem, self.folder)  # for its note when a plain list has the same name
        except StyleError as error:
            self.report(error)
            return
        if found.note:
            self.log(found.note)
        if not self.open(found.path):
            self._show_style_name()

    def _show_style_name(self) -> None:
        path = self.workspace.path
        if path is None:
            self.style_box.set("")
            return
        self._listed = self.styles()
        shown = [name for name, listed in self._listed.items() if listed.resolve() == path.resolve()]
        self.style_box.set(shown[0] if shown else path.name)

    def _title_typed(self) -> None:
        if not self._showing and self.title_text.get() != self.workspace.title:
            self.workspace.title = self.title_text.get()
            self.workspace.changed = True
            self._show_changed()

    def _description_typed(self) -> None:
        if not self.description.edit_modified():
            return
        self.description.edit_modified(False)
        text = self.description.get("1.0", "end-1c")
        if not self._showing and text != self.workspace.description:
            self.workspace.description = text
            self.workspace.changed = True
            self._show_changed()

    def _names_typed(self) -> None:
        if not self.names.edit_modified():
            return
        self.names.edit_modified(False)
        if self._showing:
            return
        if self._names_job:
            self.root.after_cancel(self._names_job)
        self._names_job = self.root.after(NAMES_PAUSE, self._read_names)

    def _read_names(self) -> None:
        """Hand the names typed to the workspace, and count them."""
        self._names_job = None
        self.workspace.set_names_text(self.current_list, self.names.get("1.0", "end-1c"))
        self._count_names()
        self._show_changed()
        self._show_stale()

    def _count_names(self) -> None:
        count = len(self.workspace.names(self.current_list))
        text = f"{count:,} name{'s' if count != 1 else ''}"
        try:
            self.workspace.draft()
            problems: tuple[str, ...] = ()
        except StyleError as error:
            problems = error.problems
        if problems:
            text += f" · {len(problems)} problem{'s' if len(problems) != 1 else ''}"
        self.names_count.configure(text=text, style="Problem.TLabel" if problems else "Muted.TLabel")
        self.names_tip.text = "\n".join(problems)

    @property
    def current_list(self) -> str:
        """The list whose names and settings are shown."""
        return self.shown_list

    def _list_chosen(self) -> None:
        self._commit()  # into the list shown until now
        self._show_names(self.list_box.get())
        self.show_settings()

    def add_list(self) -> None:
        name = simpledialog.askstring("Add a list", "What is the new list called? For example: family",
                                      parent=self.root)  # fmt: skip
        if name is None:
            return
        self._commit()
        try:
            self.workspace.add_list(name.strip())
        except ValueError as error:
            messagebox.showerror("Add a list", str(error), parent=self.root)
            return
        self._show_lists(name.strip())
        self.log(f'Added the list "{name.strip()}". Write its names, then press Infer optimum settings.')

    def rename_list(self) -> None:
        old = self.current_list
        name = simpledialog.askstring("Rename the list", f'What should the list "{old}" be called?',
                                      initialvalue=old, parent=self.root)  # fmt: skip
        if name is None:
            return
        self._commit()
        try:
            self.workspace.rename_list(old, name.strip())
        except ValueError as error:
            messagebox.showerror("Rename the list", str(error), parent=self.root)
            return
        self._rename_in_full_name(old, name.strip())
        self._show_lists(name.strip())

    def remove_list(self) -> None:
        name = self.current_list
        if len(self.workspace.list_names) == 1:
            messagebox.showinfo("Remove the list", "A style needs at least one list.", parent=self.root)
            return
        if not messagebox.askyesno("Remove the list", f'Remove the list "{name}" and its names?', parent=self.root):
            return
        self._commit()
        self.workspace.remove_list(name)
        self._rename_in_full_name(name, None)
        self._show_lists(self.workspace.list_names[0])
        self.log(f'Removed the list "{name}".')

    # --- Middle: the settings ---------------------------------------------------

    def _build_settings_column(self, frame: ttk.Frame) -> None:
        top = ttk.Frame(frame)
        top.pack(fill="x")
        self.settings_heading = ttk.Label(top, text="Settings", style="Heading.TLabel")
        self.settings_heading.pack(side="left")
        legend = ttk.Label(top, text="● pinned   ○ inferred", style="Muted.TLabel")
        legend.pack(side="right")
        Tooltip(legend, "A pinned setting is saved in the style file. The others are inferred from the names "
                        "each time the style is opened, or when you press Infer optimum settings.")  # fmt: skip

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(PAD, 0))
        for text, command, tip in (
            ("Infer optimum settings", self.infer, "Work out the best value for every setting that is not "
                                                   "pinned, by testing them on the names as they are now. "
                                                   "Press it after changing the names."),
            ("Pin all", self.pin_all, "Pin every setting at its value, so that Save writes them all and the "
                                      "style keeps them, even if a later version would infer others."),
            ("Unpin all", self.unpin_all, "Unpin every setting. Each keeps its value until the settings are "
                                          "inferred again."),
        ):
            button = ttk.Button(buttons, text=text, command=command)
            button.pack(side="left", padx=(0, 4))
            Tooltip(button, tip)
            self._action_buttons.append(button)
        self.lists_note = ttk.Label(frame, style="Muted.TLabel", wraplength=360)
        self.lists_note.pack(anchor="w", pady=(4, 0))
        self.stale_note = ttk.Label(frame, style="Warning.TLabel", wraplength=360)
        self.stale_note.pack(anchor="w")

        scrolled = Scrolled(frame)
        scrolled.pack(fill="both", expand=True, pady=(PAD, 0))
        self.rows: dict[str, Row] = {}
        # The names column is as wide in every group, so the controls line up.
        names_width = max(tkfont.Font(font=FONT).measure(label) for label in LABELS.values()) + 2 * PAD
        for title, keys in GROUPS:
            group = ttk.Labelframe(scrolled.inner, text=title, padding=(PAD, 2, PAD, PAD))
            group.pack(fill="x", pady=(0, PAD))
            group.columnconfigure(0, minsize=names_width)
            group.columnconfigure(1, weight=1)
            for number, key in enumerate(keys):
                self.rows[key] = _make_row(self, group, number, key)
        assert set(self.rows) == set(SETTINGS)

    def show_settings(self) -> None:
        """Show every setting of the list chosen."""
        list_name = self.current_list
        several = len(self.workspace.list_names) > 1
        self.settings_heading.configure(text=f"Settings of {list_name}" if several else "Settings")
        self.lists_note.configure(
            text="A change applies to every list, unless the list pins the setting for itself alone (◉)."
            if several else ""
        )  # fmt: skip
        for key, row in self.rows.items():
            row.show(self.workspace.setting(list_name, key))
        self._show_stale()

    def _show_stale(self) -> None:
        stale = self.workspace.names_changed and any(
            not self.workspace.setting(name, key).pinned for name in self.workspace.list_names for key in SETTINGS
        )
        self.stale_note.configure(
            text="The names have changed since the settings were inferred." if stale else ""
        )

    def set_setting(self, key: str, value: object) -> None:
        self.workspace.set_setting(self.current_list, key, value)
        self.show_settings()
        self._show_changed()

    def toggle_pin(self, key: str) -> None:
        self._commit()
        if self.workspace.setting(self.current_list, key).pinned:
            self.workspace.unpin(self.current_list, key)
        else:
            self.workspace.pin(key)
        self.show_settings()
        self._show_changed()

    def pin_all(self) -> None:
        self._commit()
        self.workspace.pin_all()
        self.show_settings()
        self._show_changed()
        self.log("Pinned every setting. Save writes them into the style file.")

    def unpin_all(self) -> None:
        self._commit()
        self.workspace.unpin_all()
        self.show_settings()
        self._show_changed()
        self.log("Unpinned every setting. Each keeps its value until the settings are inferred again.")

    def infer(self) -> None:
        self._commit()
        try:
            draft = self.workspace.draft()
        except StyleError as error:
            self.report(error, "The settings could not be inferred")
            return
        texts = self.workspace.names_texts()
        warnings = self.workspace.look_alike_warnings()
        self.run_apart("Inferring the settings", lambda: infer_lists(draft),
                       lambda inferences, seconds: self._inferred(inferences, texts, warnings, seconds))  # fmt: skip

    def _inferred(self, inferences, texts, warnings, seconds: float) -> None:
        self.workspace.apply_inferred(inferences, texts)
        self.show_settings()
        count = len(next(iter(inferences.values())).reasons)
        each = " for each list" if len(inferences) > 1 else ""
        self.log(f"Inferred {count} setting{'s' if count != 1 else ''}{each} in {seconds:.1f} s."
                 if count else "Every setting is pinned, so there is nothing to infer.")  # fmt: skip
        for warning in warnings:
            self.log(warning)

    # --- Right: making names ----------------------------------------------------

    def _build_names_column(self, frame: ttk.Frame) -> None:
        options = ttk.Frame(frame)
        options.pack(fill="x")
        options.columnconfigure(1, weight=1)
        self.count = tk.StringVar(value="20")
        self.initial = tk.StringVar()
        self.seed = tk.StringVar()
        self.combination = tk.StringVar()
        rows = (
            ("Names", ttk.Spinbox(options, from_=1, to=MAX_COUNT, width=6, textvariable=self.count),
             "How many names to make."),
            ("Starts with", ttk.Entry(options, textvariable=self.initial, width=12),
             "Only make names starting with these letters, such as A, Th or Kar."),
            ("Seed", ttk.Entry(options, textvariable=self.seed, width=12),
             "Leave empty for new names each time. The same seed, with the same settings, always makes the "
             "same names, so write down a seed to get a batch back later."),
        )  # fmt: skip
        for number, (label, entry, tip) in enumerate(rows):
            name = ttk.Label(options, text=label)
            name.grid(row=number, column=0, sticky="w", pady=2)
            entry.grid(row=number, column=1, sticky="w", padx=PAD, pady=2)
            entry.bind("<Return>", lambda _e: self.make_names())
            Tooltip(name, tip)
        # For a style of several lists: a choice box for each part of the full
        # name, and the combination as written; changing either changes the other.
        self.full_name_label = ttk.Label(options, text="Full name")
        Tooltip(self.full_name_label, "Which lists a full name takes its parts from, in order. Choose a list "
                                      f"for each part, + for one more part, or {NO_PART} to take one out.")
        self.parts = ttk.Frame(options)
        self.parts.bind("<Configure>", lambda _e: self._flow_parts())
        self.part_boxes: list[ttk.Combobox] = []
        self.add_part_button = ttk.Button(self.parts, text="+", width=3, command=self.add_part)
        Tooltip(self.add_part_button, "Add a part to the full name.")
        self.combination_entry = ttk.Entry(options, textvariable=self.combination)
        self.combination_entry.bind("<Return>", lambda _e: self.make_names())
        Tooltip(self.combination_entry, "The full name as written, which you can change: a list's name in "
                                        "braces stands for a name from it, and other text is copied as "
                                        "written, as in {male}, son of {male}.")
        self.combination.trace_add("write", lambda *_: self._show_parts())  # fmt: skip

        make = ttk.Button(frame, text="Make names", style="Make.TButton", command=self.make_names)
        make.pack(fill="x", pady=PAD)
        Tooltip(make, "Make names with the settings as shown (F5).")
        self._action_buttons.append(make)

        parts = ttk.PanedWindow(frame, orient="vertical")
        parts.pack(fill="both", expand=True)
        made = ttk.Frame(parts)
        self.output = tk.Listbox(made, selectmode="extended", activestyle="none", exportselection=False,
                                 font=NAMES_FONT, height=10)  # fmt: skip
        bar = ttk.Scrollbar(made, orient="vertical", command=self.output.yview)
        self.output.configure(yscrollcommand=bar.set)
        buttons = ttk.Frame(made)
        buttons.pack(side="bottom", fill="x", pady=(4, PAD))
        bar.pack(side="right", fill="y")
        self.output.pack(side="left", fill="both", expand=True)
        self.output.bind("<<ListboxSelect>>", lambda _e: self._show_explanation())
        self.output.bind("<Control-c>", lambda _e: self.copy_names())
        self.output.bind("<Control-a>", lambda _e: self.output.selection_set(0, "end"))
        copy = ttk.Button(buttons, text="Copy", command=self.copy_names)
        copy.pack(side="left")
        Tooltip(copy, "Copy the names chosen, or all of them if none is (Ctrl+C). Ctrl+click or Shift+click "
                      "chooses several.")  # fmt: skip
        ttk.Button(buttons, text="Save names…", command=self.save_names).pack(side="left", padx=4)
        parts.add(made, weight=3)

        explained = ttk.Frame(parts)
        ttk.Label(explained, text="How it was built", style="Muted.TLabel").pack(anchor="w")
        self.explanation = _read_only_text(explained, height=12, font=MONO, wrap="none")
        self.explanation.tag_configure("heading", font=MONO_BOLD)
        parts.add(explained, weight=4)

        logged = ttk.Frame(parts)
        ttk.Label(logged, text="Log", style="Muted.TLabel").pack(anchor="w", pady=(PAD, 0))
        self.log_text = _read_only_text(logged, height=5, font=FONT, wrap="word")
        parts.add(logged, weight=1)

    def make_names(self) -> None:
        self._commit()
        try:
            count = int(self.count.get())
            if not 1 <= count <= MAX_COUNT:
                raise ValueError
        except ValueError:
            self.log(f"Names: give a number from 1 to {MAX_COUNT:,}.")
            return
        seed_text = self.seed.get().strip()
        if seed_text and not seed_text.isdigit():
            self.log("Seed: give a whole number, or leave it empty for new names each time.")
            return
        combination = self.workspace.default_combination
        if len(self.workspace.list_names) > 1 and self.combination.get().strip():
            combination = parse_template_text(self.combination.get().strip())
        try:
            maker = self.workspace.maker([combination])
        except StyleError as error:
            self.report(error, "No names could be made")
            return
        initial = self.initial.get().strip() or None
        seed = int(seed_text) if seed_text else None
        self.run_apart("Making names", lambda: maker.make(count, seed, initial),
                       lambda batch, _seconds: self._show_batch(batch))  # fmt: skip

    def _show_batch(self, batch: Batch) -> None:
        self.batch = batch
        self.output.delete(0, "end")
        for name in batch.names:
            self.output.insert("end", name.text if name.ok else "(no name could be made)")
        if batch.names:
            self.output.selection_set(0)
            self.output.selection_anchor(0)
        self._show_explanation()
        made = sum(name.ok for name in batch.names)
        self.log(f"Made {made} name{'s' if made != 1 else ''} with seed {batch.seed}.")
        for note in batch.notes:
            self.log(note)

    def _show_explanation(self) -> None:
        chosen = self.output.curselection()
        if not self.batch or not chosen:
            return
        anchor = self.output.index("anchor")
        number = anchor if anchor in chosen else chosen[0]
        text = explain_combined(self.batch.names[number], number + 1)
        self.explanation.configure(state="normal")
        self.explanation.delete("1.0", "end")
        for line in text.splitlines():
            stripped = line.strip()
            tag = (
                "heading" if stripped.startswith(("Name ", "Part "))
                else "failed" if stripped.startswith("Fail")
                else "finished" if stripped.startswith("Finished")
                else ()
            )  # fmt: skip
            self.explanation.insert("end", line + "\n", tag)
        self.explanation.configure(state="disabled")

    def chosen_names(self) -> list[str]:
        """The names chosen in the list, or all of them if none is; never a failed one."""
        if not self.batch:
            return []
        numbers = self.output.curselection() or range(len(self.batch.names))
        return [text for number in numbers if (text := self.batch.names[number].text)]

    def copy_names(self) -> str:
        names = self.chosen_names()
        if names:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(names))
            self.log(f"Copied {len(names)} name{'s' if len(names) != 1 else ''}.")
        return "break"

    def save_names(self) -> None:
        if not self.batch:
            self.log("Make some names first.")
            return
        path = filedialog.asksaveasfilename(parent=self.root, title="Save names", defaultextension=".txt",
                                            filetypes=[("Text files", "*.txt")],
                                            initialfile=f"{self.workspace.title} names.txt")  # fmt: skip
        if not path:
            return
        names = [name.text for name in self.batch.names if name.text]
        Path(path).write_text("\n".join(names) + "\n", encoding="utf-8")
        self.log(f"Saved {len(names)} names to {path}.")

    # --- The full name, for a style of several lists ---------------------------

    def _show_full_name(self) -> None:
        """Show the full name's parts for a style of several lists; one list needs none."""
        if len(self.workspace.list_names) == 1:
            for widget in (self.full_name_label, self.parts, self.combination_entry):
                widget.grid_remove()
            return
        self.full_name_label.grid(row=3, column=0, sticky="nw", pady=(6, 2))
        self.parts.grid(row=3, column=1, sticky="ew", padx=PAD, pady=2)
        self.combination_entry.grid(row=4, column=1, sticky="ew", padx=PAD, pady=2)
        self._show_parts()

    def combination_parts(self) -> list[ListPart | Text] | None:
        """The parts of the combination as written; None while it cannot be read."""
        try:
            return list(parse_combination(parse_template_text(self.combination.get()).recipe).parts)
        except TemplateError:
            return None

    def _write_combination(self, parts: list[ListPart | Text]) -> None:
        self.combination.set("".join(
            f"{LIST_OPEN}{part.name}{LIST_CLOSE}" if isinstance(part, ListPart) else part.text for part in parts
        ))  # fmt: skip

    def _show_parts(self) -> None:
        """A choice box for each list the combination takes a name from, in order.
        While the combination cannot be read, they are left as they were."""
        parts = self.combination_parts()
        if parts is None:
            return
        names = [part.name for part in parts if isinstance(part, ListPart)]
        lists = self.workspace.list_names
        while len(self.part_boxes) > len(names):
            self.part_boxes.pop().destroy()
        while len(self.part_boxes) < len(names):
            box = ttk.Combobox(self.parts, state="readonly")
            box.bind("<<ComboboxSelected>>", lambda _e, number=len(self.part_boxes): self._part_chosen(number))
            looks.touch_up(box)
            self.part_boxes.append(box)
        width = max(len(name) for name in [*lists, NO_PART]) + 1
        for box, name in zip(self.part_boxes, names):
            box.configure(values=[*lists, NO_PART] if len(names) > 1 else lists, width=width)
            box.set(name)
        self._flow_parts()

    def _flow_parts(self) -> None:
        """Lay the choice boxes and + out in rows, as many to a row as fit."""
        room = self.parts.winfo_width()
        row = column = used = 0
        for widget in (*self.part_boxes, self.add_part_button):
            width = widget.winfo_reqwidth() + 4
            if column and used + width > room:
                row, column, used = row + 1, 0, 0
            widget.grid(row=row, column=column, padx=(0, 4), pady=(0, 4) if row else 0, sticky="w")
            column, used = column + 1, used + width

    def _part_chosen(self, number: int) -> None:
        """Put the list chosen in part ``number`` of the combination, or take the part out."""
        parts = self.combination_parts()
        if parts is None:
            return
        place = [place for place, part in enumerate(parts) if isinstance(part, ListPart)][number]
        chosen = self.part_boxes[number].get()
        if chosen == NO_PART:
            parts = _without_part(parts, place)
        else:
            parts[place] = ListPart(chosen)
        self._write_combination(parts)

    def add_part(self) -> None:
        """Add a part to the full name: the first list it does not use yet, or else the first."""
        parts = self.combination_parts() or []
        used = {part.name for part in parts if isinstance(part, ListPart)}
        lists = self.workspace.list_names
        chosen = next((name for name in lists if name not in used), lists[0])
        self._write_combination([*parts, *([Text(" ")] if parts else []), ListPart(chosen)])

    def _rename_in_full_name(self, old: str, new: str | None) -> None:
        """Follow a list renamed (or removed, with ``new`` None) in the combination."""
        parts = self.combination_parts()
        if parts is None:
            return
        if new is not None:
            parts = [ListPart(new) if part == ListPart(old) else part for part in parts]
        while ListPart(old) in parts:
            parts = _without_part(parts, parts.index(ListPart(old)))
        if any(isinstance(part, ListPart) for part in parts):
            self._write_combination(parts)
        else:
            self.combination.set(self.workspace.default_combination.recipe)

    # --- Files --------------------------------------------------------------------

    def open(self, path: Path) -> bool:
        """Open a style, chapter or list of names, and infer its settings.
        False if it was not opened."""
        if self.busy:
            self.log("Still working; try again in a moment.")
            return False
        if not self._may_discard():
            return False
        try:
            workspace = Workspace.load(path)
        except StyleError as error:
            self.report(error, f"{path} could not be opened")
            return False
        self.workspace = workspace
        self.batch = None
        self._show_workspace()
        names = sum(len(workspace.names(name)) for name in workspace.list_names)
        lists = workspace.list_names
        what = f"{len(lists)} lists, " if len(lists) > 1 else ""
        self.log(f"Opened {path}: {what}{names:,} names.")
        for warning in workspace.warnings:
            self.log(warning)
        self.infer()
        return True

    def new(self) -> None:
        if self.busy:
            self.log("Still working; try again in a moment.")
            return
        if not self._may_discard():
            return
        self.workspace = Workspace()
        self.batch = None
        self._show_workspace()
        self.log("New style: write its names, one per line, then press Infer optimum settings.")

    def choose_file(self) -> None:
        path = filedialog.askopenfilename(parent=self.root, title="Open a style", filetypes=OPEN_TYPES,
                                          initialdir=self.folder if self.folder.is_dir() else None)  # fmt: skip
        if path:
            self.open(Path(path))

    def save(self) -> bool:
        """Save the style file; a chapter or plain list is saved as a new style file."""
        if not self.workspace.saves_in_place:
            return self.save_as()
        return self._save_to(self.workspace.path)

    def save_as(self) -> bool:
        self.folder.mkdir(exist_ok=True)
        stem = self.workspace.path.stem if self.workspace.path else self.workspace.title
        path = filedialog.asksaveasfilename(parent=self.root, title="Save the style", initialdir=self.folder,
                                            initialfile=stem + SAVED_TYPE, defaultextension=SAVED_TYPE,
                                            filetypes=[("Style files", "*" + SAVED_TYPE)])  # fmt: skip
        if not path:
            return False
        path = Path(path)
        if path.suffix.lower() != SAVED_TYPE:
            messagebox.showerror("Save the style", f"A style is saved as a {SAVED_TYPE} file.", parent=self.root)
            return False
        return self._save_to(path)

    def _save_to(self, path: Path) -> bool:
        self._commit()
        try:
            self.workspace.save(path)
        except StyleError as error:
            self.report(error, "The style could not be saved")
            return False
        except OSError as error:
            self.log(f"The style could not be saved: {error.strerror or error}.")
            return False
        self.log(f"Saved {path}.")
        plain = path.with_suffix(".txt")
        if plain.exists() and path.parent.resolve() == self.folder.resolve():
            self.log(f'Note: {plain.name} is still there. The name "{path.stem}" now finds {path.name}.')
        self._show_changed()
        self._show_style_name()
        return True

    def _may_discard(self) -> bool:
        """Whether the style shown may be closed: saved, or the changes given up."""
        self._commit()
        if not self.workspace.changed:
            return True
        answer = messagebox.askyesnocancel(APP_NAME, f"Save the changes to {self.workspace.title}?",
                                           parent=self.root)  # fmt: skip
        if answer is None:
            return False
        return self.save() if answer else True

    def close(self) -> None:
        if self._may_discard():
            self.root.destroy()

    # --- Showing the workspace ----------------------------------------------------

    def _show_workspace(self) -> None:
        self._showing = True
        self.title_text.set(self.workspace.title)
        self.description.delete("1.0", "end")
        self.description.insert("1.0", self.workspace.description)
        self.description.edit_modified(False)
        self._showing = False
        self.output.delete(0, "end")
        self._clear_explanation()
        self._show_style_name()
        self.combination.set(self.workspace.default_combination.recipe)
        self._show_lists(self.workspace.list_names[0])
        self._show_changed()

    def _show_lists(self, chosen: str) -> None:
        self.list_box["values"] = self.workspace.list_names
        self.list_box.set(chosen)
        self._show_names(chosen)
        self._show_full_name()
        self.show_settings()

    def _show_names(self, list_name: str) -> None:
        self.shown_list = list_name
        self._showing = True
        self.names.delete("1.0", "end")
        self.names.insert("1.0", self.workspace.names_text(list_name))
        self.names.edit_reset()
        self.names.edit_modified(False)
        self._showing = False
        self._count_names()

    def _show_changed(self) -> None:
        changed = " (changed, not saved)" if self.workspace.changed else ""
        self.root.title(f"{APP_NAME} — {self.workspace.title or 'untitled'}{changed}")

    def _clear_explanation(self) -> None:
        self.explanation.configure(state="normal")
        self.explanation.delete("1.0", "end")
        self.explanation.configure(state="disabled")

    def _commit(self) -> None:
        """Hand the workspace anything typed but not yet read: names, and settings being edited."""
        if self._names_job:
            self.root.after_cancel(self._names_job)
            self._names_job = None
        if self.names.get("1.0", "end-1c") != self.workspace.names_text(self.current_list):
            self._read_names()
        for row in self.rows.values():
            row.commit()

    # --- Log, and work done apart -------------------------------------------------

    def log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        if self.log_text.get("1.0", "end-1c"):
            self.log_text.insert("end", "\n")
        self.log_text.insert("end", text)
        self.log_text.configure(state="disabled")
        self.log_text.see("end")

    def report(self, error: Exception, what: str | None = None) -> None:
        """Log a problem in plain words."""
        if isinstance(error, StyleError):
            self.log(f"{what or 'Could not read ' + error.source}:")
            for problem in error.problems:
                self.log(f"  - {problem}")
        elif isinstance(error, GenerationError):
            self.log(str(error))
        else:
            self.log(f"Something went wrong: {error!r}")

    def run_apart(self, what: str, work: Callable[[], Any], done: Callable[[Any, float], None]) -> None:
        """Run ``work`` apart from the window, then ``done`` with its result and the seconds it took."""
        if self.busy:
            self.log("Still working; try again in a moment.")
            return
        self.busy = True
        self.root.configure(cursor="watch")
        for button in self._action_buttons:
            button.state(["disabled"])
        self.log(what + "…")
        results: queue.Queue = queue.Queue(maxsize=1)
        start = time.perf_counter()

        def target() -> None:
            try:
                results.put((True, work()))
            except Exception as error:  # reported in the window, whatever it is
                results.put((False, error))

        def check() -> None:
            try:
                ok, value = results.get_nowait()
            except queue.Empty:
                self.root.after(50, check)
                return
            self.busy = False
            self.root.configure(cursor="")
            for button in self._action_buttons:
                button.state(["!disabled"])
            if ok:
                done(value, time.perf_counter() - start)
            else:
                self.report(value, f"{what} failed")

        threading.Thread(target=target, daemon=True).start()
        self.root.after(50, check)


# --- Rows of settings --------------------------------------------------------------

_UNSHOWN = object()  # a row's value before any setting is shown in it


class Row:
    """One setting in the middle column: its name, a control and a marker.

    Changing the control changes the setting, which pins it; clicking the
    marker pins or unpins it. Hovering over the name says what the setting
    does and where its value comes from.
    """

    def __init__(self, window: Window, parent: ttk.Frame, number: int, key: str):
        self.window = window
        self.key = key
        self.value: object = _UNSHOWN
        label = ttk.Label(parent, text=LABELS[key])
        label.grid(row=number, column=0, sticky="w", padx=(0, 12), pady=2)
        self.tip = Tooltip(label, "")
        self.control = self.build(parent)
        self.control.grid(row=number, column=1, sticky="w", pady=2)
        self.marker = ttk.Label(parent, width=2, anchor="center", cursor="hand2")
        self.marker.grid(row=number, column=2, sticky="e", padx=(PAD, 0))
        self.marker.bind("<Button-1>", lambda _e: window.toggle_pin(key))
        self.marker_tip = Tooltip(self.marker, "")

    def build(self, parent: ttk.Frame) -> tk.Widget:
        raise NotImplementedError

    def display(self, value: object) -> None:
        """Put ``value`` in the control."""
        raise NotImplementedError

    def read(self) -> object:
        """The value in the control. Raises ValueError, in plain words, if it cannot be used."""
        return self.value

    def show(self, setting: Setting) -> None:
        self.value = setting.value
        self.display(setting.value)
        mark, style, action = MARKERS[setting.source]
        self.marker.configure(text=mark, style=style)
        reason = setting.reason if setting.source is not Source.INFERRED else f"Inferred: {setting.reason}"
        self.marker_tip.text = f"{reason}\n{action}"
        self.tip.text = f"{HELP[self.key]}\n\n{reason}"

    def commit(self) -> None:
        """Change the setting if the control holds another value."""
        if self.value is _UNSHOWN:
            return
        try:
            value = self.read()
        except ValueError as error:
            self.window.log(f"{LABELS[self.key]}: {error}")
            self.display(self.value)
            return
        if value != self.value:
            self.window.set_setting(self.key, value)


class SwitchRow(Row):
    def build(self, parent):
        self.on = tk.BooleanVar()
        return ttk.Checkbutton(parent, variable=self.on, command=self.commit)

    def display(self, value):
        self.on.set(bool(value))

    def read(self):
        return self.on.get()


class ChoiceRow(Row):
    def build(self, parent):
        self.choices = CHOICES[self.key]
        width = max(len(text) for text, _ in self.choices) + 2
        box = ttk.Combobox(parent, state="readonly", width=width, values=[text for text, _ in self.choices])
        box.bind("<<ComboboxSelected>>", lambda _e: self.commit())
        return box

    def display(self, value):
        self.control.set(next((text for text, choice in self.choices if choice == value), str(value)))

    def read(self):
        text = self.control.get()
        return next((choice for choice_text, choice in self.choices if choice_text == text), self.value)


class NumberRow(Row):
    def build(self, parent):
        self.lowest, self.highest, step = NUMBERS[self.key]
        self.whole = step == 1
        box = ttk.Spinbox(parent, from_=self.lowest, to=self.highest, increment=step, width=6, command=self.commit,
                          format="" if self.whole else "%.1f")  # fmt: skip
        box.bind("<Return>", lambda _e: self.commit())
        box.bind("<FocusOut>", lambda _e: self.commit())
        return box

    def display(self, value):
        self.control.set(f"{value:g}")

    def read(self):
        text = self.control.get().strip()
        try:
            value = int(text) if self.whole else float(text)
        except ValueError:
            raise ValueError(f"give a {'whole ' if self.whole else ''}number from {self.lowest:g} to "
                             f"{self.highest:g}.") from None  # fmt: skip
        if not self.lowest <= value <= self.highest:
            raise ValueError(f"give a number from {self.lowest:g} to {self.highest:g}.")
        return value


class ClustersRow(Row):
    def build(self, parent):
        entry = ttk.Entry(parent, width=18)
        entry.bind("<Return>", lambda _e: self.commit())
        entry.bind("<FocusOut>", lambda _e: self.commit())
        return entry

    def display(self, value):
        self.control.delete(0, "end")
        self.control.insert(0, ", ".join(value))  # type: ignore[arg-type]

    def read(self):
        clusters = [cluster.strip().upper() for cluster in self.control.get().replace(" ", ",").split(",")]
        clusters = [cluster for cluster in clusters if cluster]
        if any(not cluster.isalpha() for cluster in clusters):
            raise ValueError("write letters only, with commas between the clusters, such as LTH, RR.")
        return tuple(dict.fromkeys(clusters))


class TemplatesRow(Row):
    def build(self, parent):
        text = tk.Text(parent, width=18, height=3, wrap="none", font=FONT, undo=True)
        text.bind("<FocusOut>", lambda _e: self.commit())
        return text

    def display(self, value):
        self.control.delete("1.0", "end")
        self.control.insert("1.0", "\n".join(f"{t.recipe}:{t.weight}" for t in value))  # type: ignore[attr-defined]

    def read(self):
        templates: list[Template] = []
        for line in self.control.get("1.0", "end-1c").splitlines():
            if not line.strip():
                continue
            template = parse_template_text(line.strip())
            try:
                parse_recipe(template.recipe)
            except TemplateError as error:
                raise ValueError(f'"{template.recipe}" {error}.') from None
            templates.append(template)
        return tuple(templates)


class PunctuationRow(Row):
    def build(self, parent):
        frame = ttk.Frame(parent)
        self.modes = []
        for text, mode, help_text in PUNCTUATION:
            on = tk.BooleanVar()
            box = ttk.Checkbutton(frame, text=text, variable=on, command=self.commit)
            box.pack(anchor="w")
            Tooltip(box, help_text)
            self.modes.append((on, mode))
        return frame

    def display(self, value):
        for on, mode in self.modes:
            on.set(mode in value)  # type: ignore[operator]

    def read(self):
        value = Punctuation.NONE
        for on, mode in self.modes:
            if on.get():
                value |= mode
        return value


def _make_row(window: Window, parent: ttk.Frame, number: int, key: str) -> Row:
    if key in SWITCHES:
        kind: type[Row] = SwitchRow
    elif key in CHOICES:
        kind = ChoiceRow
    elif key in NUMBERS:
        kind = NumberRow
    else:
        kind = {"consonant_clusters": ClustersRow, "templates": TemplatesRow, "punctuation": PunctuationRow}[key]
    return kind(window, parent, number, key)


# --- Small parts ---------------------------------------------------------------------


class Tooltip:
    """A note that appears when the mouse rests on a widget; ``text`` may change."""

    def __init__(self, widget: tk.Widget, text: str):
        self.widget = widget
        self.text = text
        self.tip: tk.Toplevel | None = None
        self.job: str | None = None
        widget.bind("<Enter>", lambda _e: self._schedule(), add="+")
        widget.bind("<Leave>", lambda _e: self.hide(), add="+")
        widget.bind("<ButtonPress>", lambda _e: self.hide(), add="+")

    def _schedule(self) -> None:
        self.job = self.widget.after(500, self.show)

    def show(self) -> None:
        if self.tip or not self.text:
            return
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip.wm_geometry(f"+{x}+{y}")
        look = looks.current()
        tk.Label(self.tip, text=self.text, justify="left", wraplength=360, padx=8, pady=5, font=FONT,
                 background=look.surface, foreground=look.text, highlightthickness=1,
                 highlightbackground=look.border).pack()  # fmt: skip
        self.tip.attributes("-topmost", True)

    def hide(self) -> None:
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


class Scrolled(ttk.Frame):
    """A frame whose contents, ``inner``, scroll up and down."""

    def __init__(self, parent: tk.Widget):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        self.inner = ttk.Frame(self.canvas, padding=(0, 0, PAD, 0))
        item = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda _e: self._fit())
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(item, width=e.width))
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bind_all("<MouseWheel>", self._wheel, add="+")

    def _fit(self) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"), width=self.inner.winfo_reqwidth())

    def _wheel(self, event: tk.Event) -> None:
        """Scroll when the mouse wheel turns anywhere over the frame."""
        under = self.winfo_containing(event.x_root, event.y_root)
        if under is None or not str(under).startswith(str(self)):
            return
        if self.inner.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")


def _read_only_text(parent: tk.Widget, **options: Any) -> tk.Text:
    """Text that can be read, chosen and copied, but not typed into."""
    box = ttk.Frame(parent)
    box.pack(fill="both", expand=True)
    text = tk.Text(box, state="disabled", **options)
    bar = ttk.Scrollbar(box, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=bar.set)
    if options.get("wrap") == "none":
        across = ttk.Scrollbar(box, orient="horizontal", command=text.xview)
        text.configure(xscrollcommand=across.set)
        across.pack(side="bottom", fill="x")
    bar.pack(side="right", fill="y")
    text.pack(side="left", fill="both", expand=True)
    return text
