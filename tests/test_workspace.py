"""The style being edited in the window."""

from pathlib import Path

import pytest

from namebook.cli import main
from namebook.formats import load_style
from namebook.style import SETTINGS, HyphenMode, Markov, StyleError, Template
from namebook.workspace import NOT_INFERRED, Source, Workspace

GIVEN = [(c + v + d + w).capitalize() for c in "LMNRS" for v in "AEI" for d in "LNR" for w in "AO"]
FAMILY = [a + b for a in ("Ash", "Bram", "Cal", "Dun", "El", "Fen") for b in ("wick", "ford", "ton", "by", "wood")]


def block(names):
    return '"""\n' + "\n".join(names) + '\n"""'


def write(folder: Path, file_name: str, text: str) -> Path:
    path = folder / file_name
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def folk(tmp_path):
    """Two lists, with a setting pinned for every list and one for the family list alone."""
    text = (
        'title = "Folk"\n\n[settings]\nmarkov = "fill"\nmarkov_order = 2\n\n'
        "[settings.family]\nmarkov_order = 1\n\n"
        f"[names]\ngiven = {block(GIVEN)}\n\nfamily = {block(FAMILY)}\n"
    )
    return write(tmp_path, "Folk.toml", text)


def loaded(path) -> Workspace:
    workspace = Workspace.load(path)
    workspace.infer()
    return workspace


def made(workspace, count, seed, combination=None):
    maker = workspace.maker([combination or workspace.default_combination])
    return [name.text for name in maker.make(count, seed).names]


# --- Names as the commands make them ------------------------------------------


@pytest.mark.parametrize("kind", ["chapter", "plain list", "two lists", "soft clusters"])
def test_names_are_the_ones_the_generate_command_makes(kind, chapters_dir, folk, tmp_path, capsys):
    """The window makes names from the values it shows, not from inference:
    with the settings as loaded and inferred, they are the command's names."""
    path = {
        "chapter": chapters_dir / "Angelic.ebn",
        "plain list": write(tmp_path, "Fae.txt", "\n".join(GIVEN[:40])),
        "two lists": folk,
        "soft clusters": write(
            tmp_path,
            "Soft.toml",
            'names = "Althar, Belthis, Corran, Dalthe, Ethorn, Farrin, Galthor, Harreth"\n'
            '[settings]\nsoft_consonants = true\nconsonant_clusters = ["LTH", "RR"]\n',
        ),
    }[kind]
    assert main(["generate", str(path), "-n", "12", "--seed", "3"]) == 0
    expected = capsys.readouterr().out.splitlines()
    assert made(loaded(path), 12, seed=3) == expected


def test_full_names_are_the_ones_generate_makes(folk, capsys):
    assert main(["generate", str(folk), "-n", "12", "--seed", "3", "--combination", "{given} {family}"]) == 0
    expected = capsys.readouterr().out.splitlines()
    assert made(loaded(folk), 12, seed=3, combination=Template("{given} {family}")) == expected


def test_a_combination_puts_the_lists_together_another_way(folk):
    maker = loaded(folk).maker([Template("{family}, {given}")])
    for name in maker.make(5, seed=1).names:
        family, comma, given = name.parts
        assert (family.list_name, comma, given.list_name) == ("family", ", ", "given")
        assert name.text == f"{family.name.text}, {given.name.text}"


def test_a_combination_naming_no_list_is_refused(folk):
    with pytest.raises(StyleError, match='"{surname}" has "surname", which is not one of'):
        loaded(folk).maker([Template("{surname}")])


def test_each_name_keeps_its_explanation(folk):
    (name,) = loaded(folk).maker([Template("{given} {family}")]).make(1, seed=2).names
    assert all(part.name.events for part in name.names)


# --- Where each setting comes from --------------------------------------------


def test_a_loaded_style_keeps_its_pins_and_infers_the_rest(folk):
    workspace = Workspace.load(folk)
    assert workspace.setting("given", "markov_order").source is Source.PINNED
    assert workspace.setting("given", "markov_order").value == 2
    family = workspace.setting("family", "markov_order")
    assert (family.source, family.value) == (Source.LIST_PINNED, 1)
    assert workspace.setting("given", "prefixes").source is Source.NOT_INFERRED
    workspace.infer()
    prefixes = workspace.setting("given", "prefixes")
    assert prefixes.source is Source.INFERRED and prefixes.value is True
    assert prefixes.reason.startswith("on")
    assert not workspace.changed and not workspace.names_changed


def test_a_chapter_pins_everything(chapters_dir):
    workspace = Workspace.load(chapters_dir / "Demonic.ebn")
    assert all(workspace.setting("list", key).pinned for key in SETTINGS)
    assert workspace.setting("list", "consonant_clusters").value == ("RR", "JJ", "NN", "ZZ")


def test_changing_a_setting_pins_it_for_every_list(folk):
    workspace = loaded(folk)
    workspace.set_setting("family", "fit_level", 1)
    for list_name in ("given", "family"):
        assert workspace.setting(list_name, "fit_level").source is Source.PINNED
        assert workspace.setting(list_name, "fit_level").value == 1
    assert workspace.changed


def test_changing_a_setting_a_list_pins_alone_changes_it_for_that_list(folk):
    workspace = loaded(folk)
    workspace.set_setting("family", "markov_order", 3)
    assert workspace.setting("family", "markov_order").value == 3
    assert workspace.setting("given", "markov_order").value == 2


def test_pinning_keeps_each_lists_value(folk):
    workspace = loaded(folk)
    workspace.unpin_all()
    assert workspace.setting("given", "markov_order").source is Source.NOT_INFERRED
    assert (workspace.setting("given", "markov_order").value, workspace.setting("family", "markov_order").value) == (2, 1)
    workspace.pin("markov_order")  # the lists differ: a pin for each
    assert workspace.setting("given", "markov_order").source is Source.LIST_PINNED
    assert (workspace.setting("given", "markov_order").value, workspace.setting("family", "markov_order").value) == (2, 1)
    workspace.pin("fit_level")  # the same in both: one pin for every list
    assert workspace.setting("family", "fit_level").source is Source.PINNED


def test_unpinning_keeps_the_value_until_inferred_again(folk):
    workspace = loaded(folk)
    workspace.set_setting("given", "prefixes", False)
    workspace.unpin("given", "prefixes")
    unpinned = workspace.setting("family", "prefixes")
    assert (unpinned.value, unpinned.source, unpinned.reason) == (False, Source.NOT_INFERRED, NOT_INFERRED)
    workspace.infer()
    assert workspace.setting("family", "prefixes").value is True


def test_unpinning_a_lists_own_pin_shows_the_pin_for_every_list(folk):
    workspace = loaded(folk)
    workspace.unpin("family", "markov_order")
    assert workspace.setting("family", "markov_order").source is Source.PINNED
    assert workspace.setting("family", "markov_order").value == 2


def test_settings_stay_as_they_are_when_the_names_change(folk):
    workspace = loaded(folk)
    before = workspace.values("given")
    workspace.set_names_text("given", "\n".join(GIVEN[:10]))
    assert workspace.names_changed
    assert workspace.values("given") == before


def test_settings_inferred_from_older_names_leave_them_marked_changed(folk):
    workspace = loaded(folk)
    texts = workspace.names_texts()
    workspace.set_names_text("given", "Ana\nBel")
    workspace.apply_inferred({}, texts)
    assert workspace.names_changed


# --- Saving -------------------------------------------------------------------


def test_pin_all_saves_what_create_pin_saves(folk, tmp_path, monkeypatch):
    workspace = loaded(folk)
    workspace.unpin_all()
    workspace.infer()
    workspace.pin_all()
    saved = workspace.save(tmp_path / "Saved.toml")
    monkeypatch.chdir(tmp_path)
    plain = write(tmp_path, "Plain.toml", folk.read_text(encoding="utf-8").split("[settings]")[0]
                  + f"[names]\ngiven = {block(GIVEN)}\n\nfamily = {block(FAMILY)}\n")  # fmt: skip
    assert main(["create", str(plain), "--pin"]) == 0
    created = (tmp_path / "styles" / "Plain.toml").read_text(encoding="utf-8")
    assert saved.read_text(encoding="utf-8") == created
    assert "[settings.family]" in created  # the lists' look-backs differ


def test_unpin_all_saves_no_settings(folk, tmp_path):
    workspace = loaded(folk)
    workspace.unpin_all()
    path = workspace.save(tmp_path / "Bare.toml")
    style = load_style(path)
    assert style.pinned == frozenset() and all(not name_list.settings for name_list in style.name_lists)
    assert style.title == "Folk" and style.list_names == ("given", "family")
    assert not workspace.changed and workspace.path == path


def test_saving_keeps_names_title_description_and_pins(folk, tmp_path):
    workspace = loaded(folk)
    workspace.title = "Folk names"
    workspace.description = "Given names\nand family names"
    workspace.set_names_text("given", "Ana, Bel\n# a comment\nCora")
    workspace.set_setting("given", "letter_hyphen", HyphenMode.JOINT)
    style = load_style(workspace.save(tmp_path / "Folk2.toml"))
    assert style.title == "Folk names"
    assert style.description == ("Given names", "and family names")
    assert style.name_list("given").names == ("Ana", "Bel", "Cora")
    assert style.pinned == {"markov", "markov_order", "letter_hyphen"}
    assert style.options.markov is Markov.FILL
    assert style.name_list("family").settings == {"markov_order": 1}


def test_only_a_style_file_is_saved_in_place(chapters_dir, folk, tmp_path):
    assert Workspace.load(folk).saves_in_place
    chapter = Workspace.load(chapters_dir / "Angelic.ebn")
    assert not chapter.saves_in_place
    with pytest.raises(ValueError):
        chapter.save()
    assert not Workspace.load(write(tmp_path, "Fae.txt", "Ana\nBel")).saves_in_place


def test_a_style_with_problems_is_neither_saved_nor_used(folk, tmp_path):
    workspace = loaded(folk)
    workspace.set_names_text("family", "")
    with pytest.raises(StyleError, match='The list "family" has no names'):
        workspace.save(tmp_path / "Empty.toml")
    workspace.set_names_text("family", "Ash4")
    with pytest.raises(StyleError, match="contains '4'"):
        workspace.maker([Template("{given} {family}")])
    assert not (tmp_path / "Empty.toml").exists()


def test_a_new_workspace_starts_with_one_empty_list():
    workspace = Workspace()
    assert workspace.list_names == ["list"]
    with pytest.raises(StyleError, match="There are no names"):
        workspace.infer()
    workspace.set_names_text("list", "Ana\nBel\nCora\nDana")
    workspace.infer()
    assert workspace.setting("list", "markov").value is Markov.FILL


# --- Lists ---------------------------------------------------------------------


def test_lists_can_be_added_renamed_and_removed(folk):
    workspace = loaded(folk)
    workspace.add_list("title")
    assert workspace.list_names == ["given", "family", "title"]
    assert workspace.default_combination == Template("{given}")
    workspace.rename_list("family", "surname")
    assert workspace.list_names == ["given", "surname", "title"]
    assert workspace.setting("surname", "markov_order").source is Source.LIST_PINNED
    workspace.remove_list("title")
    assert workspace.list_names == ["given", "surname"]


@pytest.mark.parametrize(
    ("name", "problem"),
    [("", "needs a name"), ("gi{ven", "cannot have"), ("given", "already a list")],
)
def test_a_list_name_that_cannot_be_used_is_refused(folk, name, problem):
    with pytest.raises(ValueError, match=problem):
        Workspace.load(folk).add_list(name)


def test_the_last_list_cannot_be_removed():
    with pytest.raises(ValueError, match="at least one list"):
        Workspace().remove_list("list")
