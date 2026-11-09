"""The window, driven as a user would, without showing it."""

import time

import pytest

from namebook.style import Markov
from namebook.workspace import Source

tk = pytest.importorskip("tkinter")

from namebook import looks  # noqa: E402  (needs tkinter)
from namebook.window import NO_PART, Window  # noqa: E402

GIVEN = [(c + v + d + w).capitalize() for c in "LMNRS" for v in "AEI" for d in "LNR" for w in "AO"]
FAMILY = [a + b for a in ("Ash", "Bram", "Cal", "Dun", "El", "Fen") for b in ("wick", "ford", "ton", "by", "wood")]


@pytest.fixture
def root(capfd):
    # Tcl, under tkinter, sometimes cannot find its own files while pytest
    # holds the program's output, so the window is made without it.
    try:
        with capfd.disabled():
            root = tk.Tk()
    except tk.TclError as error:
        pytest.skip(f"no display to open a window on: {error}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def folder(tmp_path):
    folder = tmp_path / "styles"
    folder.mkdir()
    text = (
        'title = "Folk"\n\n[settings]\nmarkov = "fill"\n\n[names]\n'
        'given = """\n' + "\n".join(GIVEN) + '\n"""\n\nfamily = """\n' + "\n".join(FAMILY) + '\n"""\n'
    )
    (folder / "Folk.toml").write_text(text, encoding="utf-8")
    (folder / "Fae.txt").write_text("\n".join(GIVEN[:30]), encoding="utf-8")
    return folder


def settle(window):
    """Wait for work done apart from the window to finish."""
    deadline = time.monotonic() + 60
    while True:
        window.root.update()
        if not window.busy:
            return
        assert time.monotonic() < deadline, "the window kept working"
        time.sleep(0.01)


@pytest.fixture
def window(root, folder):
    window = Window(root, folder)
    assert window.open(folder / "Folk.toml")
    settle(window)
    return window


def test_opening_a_style_shows_it_and_infers_its_settings(window, folder):
    assert list(window.styles()) == ["Fae", "Folk"]
    assert window.style_box.get() == "Folk"
    assert window.title_text.get() == "Folk"
    assert window.list_box.get() == "given"
    assert window.names.get("1.0", "2.0").strip() == GIVEN[0]
    assert window.names_count.cget("text") == f"{len(GIVEN)} names"
    assert window.rows["markov"].marker.cget("text") == "●"
    assert window.rows["prefixes"].marker.cget("text") == "○"
    assert window.workspace.setting("given", "prefixes").source is Source.INFERRED
    assert "Inferred" in window.log_text.get("1.0", "end")


def test_the_style_box_shows_each_style_by_its_title(root, folder):
    (folder / "Wood_Elves.txt").write_text("\n".join(GIVEN[:30]), encoding="utf-8")
    (folder / "old_folk.toml").write_text('title = "The  Old Folk"\nnames = "Ana, Bel"\n', encoding="utf-8")
    (folder / "Folk2.toml").write_text('title = "Folk"\nnames = "Ana, Bel"\n', encoding="utf-8")
    window = Window(root, folder)
    assert window.styles() == {
        "Fae": folder / "Fae.txt",
        "Folk (Folk.toml)": folder / "Folk.toml",
        "Folk (Folk2.toml)": folder / "Folk2.toml",
        "The Old Folk": folder / "old_folk.toml",
        "Wood Elves": folder / "Wood_Elves.txt",
    }
    window._list_styles()
    window.style_box.set("Wood Elves")
    window._style_chosen()
    settle(window)
    assert window.workspace.path == folder / "Wood_Elves.txt"
    assert window.style_box.get() == "Wood Elves"


def test_the_columns_are_even_thirds(root, folder):
    window = Window(root, folder)
    root.attributes("-alpha", 0.0)  # shown, so it is laid out, but not seen
    root.deiconify()
    for size in ("1200x700", "1500x800"):
        root.geometry(size)
        root.update()
        widths = [root.nametowidget(pane).winfo_width() for pane in window.columns.panes()]
        assert min(widths) > 300 and max(widths) - min(widths) <= 1, widths


def test_making_names_shows_them_and_how_each_was_built(window):
    window.seed.set("4")
    window.count.set("6")
    window.make_names()
    settle(window)
    shown = list(window.output.get(0, "end"))
    expected = window.workspace.maker([window.workspace.default_combination]).make(6, seed=4).names
    assert shown == [name.text for name in expected]
    assert window.explanation.get("1.0", "2.0").startswith("Name 1: ")
    window.output.selection_clear(0, "end")
    window.output.selection_set(2)
    window.output.selection_anchor(2)
    window._show_explanation()
    assert window.explanation.get("1.0", "2.0").startswith(f"Name 3: {shown[2]}")
    assert window.chosen_names() == [shown[2]]


def test_names_come_from_the_first_list_unless_a_full_name_is_chosen(window):
    assert window.combination.get() == "{given}"
    assert [box.get() for box in window.part_boxes] == ["given"]
    window.seed.set("1")
    window.make_names()
    settle(window)
    assert all([part.list_name for part in name.names] == ["given"] for name in window.batch.names)
    window.add_part()
    window.make_names()
    settle(window)
    assert all([part.list_name for part in name.names] == ["given", "family"] for name in window.batch.names)
    assert list(window.output.get(0, "end")) == [name.text for name in window.batch.names]


def choose_part(window, number, value):
    window.part_boxes[number].set(value)
    window._part_chosen(number)


def test_the_full_name_parts_and_the_combination_follow_each_other(window):
    window.add_part()
    assert window.combination.get() == "{given} {family}"
    assert window.part_boxes[0]["values"] == ("given", "family", NO_PART)
    choose_part(window, 0, "family")
    assert window.combination.get() == "{family} {family}"
    choose_part(window, 1, NO_PART)
    assert window.combination.get() == "{family}"
    assert window.part_boxes[0]["values"] == ("given", "family")  # the last part cannot be taken out
    window.combination.set("{family}, {given}")  # typed
    assert [box.get() for box in window.part_boxes] == ["family", "given"]
    window.combination.set("{fam")  # half typed: the parts stay as they were
    assert [box.get() for box in window.part_boxes] == ["family", "given"]
    window.combination.set("Lord {given} of {family}")
    choose_part(window, 0, NO_PART)
    assert window.combination.get() == "Lord {family}"
    window.combination.set("{given}, son of {given}")
    choose_part(window, 1, NO_PART)
    assert window.combination.get() == "{given}"


def test_the_full_name_follows_lists_renamed_and_removed(window):
    window.combination.set("{given} {family}")
    window.workspace.rename_list("family", "surname")
    window._rename_in_full_name("family", "surname")
    assert window.combination.get() == "{given} {surname}"
    window.workspace.remove_list("given")
    window._rename_in_full_name("given", None)
    assert window.combination.get() == "{surname}"
    window.workspace.add_list("given")
    window.combination.set("{surname}")
    window.workspace.remove_list("surname")
    window._rename_in_full_name("surname", None)
    assert window.combination.get() == "{given}"  # back to the first list


def test_opening_a_style_starts_from_its_first_list(window, folder):
    window.combination.set("{family}, {given}")
    assert window.open(folder / "Fae.txt")
    settle(window)
    assert window.combination.get() == "{list}"
    assert not window.parts.winfo_manager()  # one list: no full name to choose
    assert window.open(folder / "Folk.toml")
    settle(window)
    assert window.combination.get() == "{given}"


def test_part_boxes_made_later_take_the_look(root, folder):
    window = Window(root, folder)
    window.look.set("Dark")
    window.change_look(remember=False)
    assert window.open(folder / "Folk.toml")
    settle(window)
    window.add_part()
    popdown = root.tk.call("ttk::combobox::PopdownWindow", window.part_boxes[1])
    assert str(root.tk.call(f"{popdown}.f.l", "cget", "-background")) == looks.LOOKS["Dark"].surface


def test_changing_a_setting_pins_it_and_marks_the_style_changed(window):
    row = window.rows["prefixes"]
    row.on.set(False)
    row.commit()
    assert window.workspace.setting("family", "prefixes").value is False
    assert row.marker.cget("text") == "●"
    assert window.root.title().endswith("(changed, not saved)")


def test_clicking_a_marker_pins_and_unpins(window):
    window.toggle_pin("markov")
    assert window.workspace.setting("given", "markov").source is Source.NOT_INFERRED
    window.toggle_pin("markov")
    assert window.workspace.setting("given", "markov").source is Source.PINNED
    assert window.workspace.setting("given", "markov").value is Markov.FILL


def test_editing_the_names_leaves_the_settings_alone(window):
    before = window.workspace.values("given")
    window.names.delete("1.0", "end")
    window.names.insert("1.0", "Ana\nBel\nCora")
    window._commit()
    assert window.workspace.names("given") == ("Ana", "Bel", "Cora")
    assert window.workspace.values("given") == before
    assert "names have changed" in window.stale_note.cget("text")
    window.infer()
    settle(window)
    assert window.stale_note.cget("text") == ""


def test_choosing_another_list_shows_its_names_and_settings(window):
    window.names.insert("end", "\nZelda")  # typed, and not read yet
    window.list_box.set("family")
    window._list_chosen()
    assert window.names.get("1.0", "2.0").strip() == FAMILY[0]
    assert window.settings_heading.cget("text") == "Settings of family"
    assert window.workspace.names("given")[-1] == "Zelda"
    assert window.workspace.names("family") == tuple(FAMILY)


def test_saving_writes_the_style_file(window, folder):
    window.title_text.set("Folk names")
    assert window._save_to(folder / "Folk.toml")
    assert 'title = "Folk names"' in (folder / "Folk.toml").read_text(encoding="utf-8")
    assert not window.workspace.changed
    assert window.root.title() == "New Book of Names — Folk names"


def test_the_look_changes_and_is_remembered(root, folder, tmp_path):
    preferences = tmp_path / "prefs" / "window.json"
    window = Window(root, folder, preferences)
    assert window.look.get() == "Light"
    window.look.set("Dark")
    window.change_look()
    assert window.names.cget("background") == looks.LOOKS["Dark"].surface
    assert window.explanation.tag_cget("failed", "foreground") == looks.LOOKS["Dark"].problem
    assert Window(root, folder, preferences).look.get() == "Dark"


def test_a_style_with_problems_is_reported_in_the_log(window):
    window.names.delete("1.0", "end")
    window.names.insert("1.0", "Ana\nB4l")
    window._commit()
    assert window.names_count.cget("text") == "2 names · 1 problem"
    window.make_names()
    assert "contains '4'" in window.log_text.get("1.0", "end")
