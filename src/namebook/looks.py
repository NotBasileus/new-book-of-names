"""How namebook's window looks: its colors and fonts, in a light and a dark look.

Every choice about the window's appearance is made here. The window uses
ttk's "clam" theme, which takes any colors (the Windows theme ignores
them), with tick boxes and round buttons drawn from SVG so they match each
look. Widgets that ttk does not style (text boxes, lists) are colored by
walking the window, so the window need not list them.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from tkinter import Misc, PhotoImage, ttk

FAMILY = "Segoe UI"
FONT = (FAMILY, 10)
BOLD = (FAMILY, 10, "bold")
HEADING = (FAMILY, 12, "bold")
NAMES_FONT = (FAMILY, 11)
MARK = (FAMILY, 12)
MONO = ("Consolas", 10)
MONO_BOLD = ("Consolas", 10, "bold")


@dataclass(frozen=True)
class Look:
    """The colors of one look."""

    background: str  # the window
    surface: str  # text boxes, lists and fields
    button: str
    hover: str  # a button under the mouse
    text: str
    muted: str  # labels and notes
    border: str
    accent: str  # the Make names button, ticks, focus
    accent_hover: str
    on_accent: str  # text on the accent
    selection: str
    selected_text: str
    pinned: str  # markers
    inferred: str
    not_inferred: str
    warning: str
    problem: str
    finished: str  # in explanations
    dark: bool = False


LOOKS = {
    "Light": Look(
        background="#f3f3f3", surface="#ffffff", button="#fbfbfb", hover="#e8eef6", text="#1b1b1b",
        muted="#616161", border="#cfcfcf", accent="#0f6cbd", accent_hover="#115ea3", on_accent="#ffffff",
        selection="#cce4f7", selected_text="#1b1b1b", pinned="#0f6cbd", inferred="#8a8a8a",
        not_inferred="#c47f00", warning="#8a5300", problem="#b3261e", finished="#2f6f2f",
    ),
    "Dark": Look(
        background="#202020", surface="#2b2b2b", button="#323232", hover="#3d3d3d", text="#e6e6e6",
        muted="#a3a3a3", border="#454545", accent="#4cc2ff", accent_hover="#7dd3ff", on_accent="#0b1a24",
        selection="#264f78", selected_text="#ffffff", pinned="#4cc2ff", inferred="#8a8a8a",
        not_inferred="#e5a54b", warning="#e5a54b", problem="#ff8a80", finished="#8fd694", dark=True,
    ),
}  # fmt: skip
DEFAULT = "Light"

_current = LOOKS[DEFAULT]
_made: set[str] = set()  # the tick-box and round-button elements made, by look
_images: dict[str, PhotoImage] = {}  # kept, since Tk forgets an image Python no longer holds


def current() -> Look:
    """The look the window has now, for parts made later, such as hover notes."""
    return _current


def apply(root: Misc, name: str) -> Look:
    """Give the window, and every widget in it, the look called ``name``."""
    global _current
    look = _current = LOOKS[name]
    style = ttk.Style(root)
    style.theme_use("clam")
    _style_ttk(style, look)
    _indicators(root, style, name, look)
    _paint(root, look)
    _title_bar(root, look)
    return look


def touch_up(widget: Misc) -> None:
    """Give a widget made after the look was applied, and those inside it, the look's colors."""
    _paint(widget, _current)


def _style_ttk(style: ttk.Style, look: Look) -> None:
    flat = {"lightcolor": look.button, "darkcolor": look.button, "bordercolor": look.border}
    style.configure(
        ".", background=look.background, foreground=look.text, font=FONT, bordercolor=look.border,
        lightcolor=look.background, darkcolor=look.background, troughcolor=look.background,
        focuscolor=look.accent, selectbackground=look.selection, selectforeground=look.selected_text,
        fieldbackground=look.surface, insertcolor=look.text, arrowcolor=look.text,
    )  # fmt: skip
    style.map(".", foreground=[("disabled", look.muted)])
    for kind in ("TButton", "TMenubutton"):
        style.configure(kind, background=look.button, padding=(8, 3), **flat)
        style.map(kind, background=[("pressed", look.selection), ("active", look.hover)])
    style.configure("Make.TButton", background=look.accent, foreground=look.on_accent, font=BOLD, padding=(10, 6),
                    lightcolor=look.accent, darkcolor=look.accent, bordercolor=look.accent)  # fmt: skip
    style.map("Make.TButton", background=[("disabled", look.border), ("active", look.accent_hover)],
              foreground=[("disabled", look.muted)])  # fmt: skip
    for kind in ("TEntry", "TSpinbox", "TCombobox"):
        style.configure(kind, fieldbackground=look.surface, foreground=look.text, background=look.button,
                        lightcolor=look.surface, darkcolor=look.surface, bordercolor=look.border,
                        arrowcolor=look.text, insertcolor=look.text, padding=2)  # fmt: skip
        style.map(kind, bordercolor=[("focus", look.accent)], lightcolor=[("focus", look.surface)],
                  background=[("active", look.hover)])  # fmt: skip
    style.map("TCombobox", fieldbackground=[("readonly", look.surface)], foreground=[("readonly", look.text)],
              selectbackground=[("readonly", look.surface)], selectforeground=[("readonly", look.text)])  # fmt: skip
    style.configure("TLabelframe", background=look.background, bordercolor=look.border,
                    lightcolor=look.background, darkcolor=look.background)  # fmt: skip
    style.configure("TLabelframe.Label", background=look.background, foreground=look.text, font=BOLD)
    style.configure("TScrollbar", background=look.button, troughcolor=look.background, arrowcolor=look.muted,
                    gripcount=0, **flat)  # fmt: skip
    style.map("TScrollbar", background=[("active", look.hover)])
    style.configure("Sash", sashthickness=6, gripcount=0, background=look.background,
                    lightcolor=look.background, darkcolor=look.background, bordercolor=look.background)  # fmt: skip
    for kind in ("TCheckbutton", "TRadiobutton"):
        style.configure(kind, background=look.background, foreground=look.text)
        style.map(kind, background=[("active", look.background)])
    style.configure("Heading.TLabel", font=HEADING)
    style.configure("Muted.TLabel", foreground=look.muted)
    style.configure("Warning.TLabel", foreground=look.warning)
    style.configure("Problem.TLabel", foreground=look.problem)
    style.configure("Pinned.TLabel", foreground=look.pinned, font=MARK)
    style.configure("Inferred.TLabel", foreground=look.inferred, font=MARK)
    style.configure("NotInferred.TLabel", foreground=look.not_inferred, font=MARK)


# Tick boxes and round buttons, as SVG: off, then on. The drawing is 16 pixels
# high and 22 wide, so 6 pixels lie between it and the text beside it.
_BOX = '<rect x="1.5" y="1.5" width="13" height="13" rx="3" fill="{fill}" stroke="{stroke}"/>'
_TICK = '<path d="M4.5 8.2 L7 10.7 L11.6 5.6" fill="none" stroke="{on}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'  # noqa: E501
_RING = '<circle cx="8" cy="8" r="6.5" fill="{fill}" stroke="{stroke}"/>'
_DOT = '<circle cx="8" cy="8" r="3.5" fill="{accent}"/>'
_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="22" height="16">{}</svg>'


def _indicators(root: Misc, style: ttk.Style, name: str, look: Look) -> None:
    """Tick boxes and round buttons in the look's colors."""
    check, radio = f"{name}.Checkbutton.indicator", f"{name}.Radiobutton.indicator"
    if name not in _made:
        scale = root.winfo_fpixels("1i") / 96
        drawings = {
            "check off": _BOX.format(fill=look.surface, stroke=look.muted),
            "check on": _BOX.format(fill=look.accent, stroke=look.accent) + _TICK.format(on=look.on_accent),
            "radio off": _RING.format(fill=look.surface, stroke=look.muted),
            "radio on": _RING.format(fill=look.surface, stroke=look.accent) + _DOT.format(accent=look.accent),
        }
        images = {}
        for key, drawing in drawings.items():
            image = images[key] = _photo(root, _SVG.format(drawing), scale)
            _images[f"{name} {key}"] = image
        style.element_create(check, "image", images["check off"], ("selected", images["check on"]))
        style.element_create(radio, "image", images["radio off"], ("selected", images["radio on"]))
        _made.add(name)
    for kind, element in (("Checkbutton", check), ("Radiobutton", radio)):
        style.layout(f"T{kind}", [(f"{kind}.padding", {"sticky": "nswe", "children": [
            (element, {"side": "left", "sticky": ""}),
            (f"{kind}.focus", {"side": "left", "sticky": "w", "children": [(f"{kind}.label", {"sticky": "nswe"})]}),
        ]})])  # fmt: skip


def _photo(root: Misc, svg: str, scale: float) -> PhotoImage:
    return PhotoImage(master=root, data=svg, format=f"svg -scale {scale:.2f}")


# Options for the widgets ttk does not style, by their Tk class.
def _plain(look: Look) -> dict[str, dict[str, str | int]]:
    field = {
        "background": look.surface, "foreground": look.text, "selectbackground": look.selection,
        "selectforeground": look.selected_text, "highlightthickness": 1, "highlightbackground": look.border,
        "highlightcolor": look.accent, "borderwidth": 0, "relief": "flat",
    }  # fmt: skip
    return {
        "Text": {**field, "insertbackground": look.text, "inactiveselectbackground": look.selection},
        "Listbox": field,
        "Canvas": {"background": look.background, "highlightthickness": 0},
        "Menu": {"background": look.surface, "foreground": look.text, "activebackground": look.selection,
                 "activeforeground": look.selected_text},  # fmt: skip
        "Tk": {"background": look.background},
        "Toplevel": {"background": look.background},
    }


def _paint(root: Misc, look: Look) -> None:
    """Color every widget ttk does not style, and the lists that drop down from choice boxes."""
    plain = _plain(look)
    call, split = root.tk.call, root.tk.splitlist
    waiting = [str(root)]
    while waiting:
        path = waiting.pop()
        waiting.extend(split(call("winfo", "children", path)))
        kind = call("winfo", "class", path)
        if kind in plain:
            call(path, "configure", *_flat(plain[kind]))
        elif kind == "TCombobox":
            popdown = call("ttk::combobox::PopdownWindow", path)
            call(f"{popdown}.f.l", "configure", *_flat(plain["Listbox"]))


def _flat(options: dict[str, str | int]) -> list[str | int]:
    return [item for key, value in options.items() for item in (f"-{key}", value)]


def _title_bar(root: Misc, look: Look) -> None:
    """A dark title bar for a dark look, on Windows 10 and 11."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        root.update_idletasks()
        dark = ctypes.c_int(1 if look.dark else 0)
        hwnd = int(root.tk.call("wm", "frame", str(root)), 16)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))
    except (AttributeError, OSError, ValueError):
        pass
