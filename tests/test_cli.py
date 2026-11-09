"""The command-line interface."""

import re
from pathlib import Path

import pytest

from namebook.cli import _build_parser, _load_settings, main
from namebook.formats import load_style
from namebook.style import SETTINGS, ApostropheMode, HyphenMode, Markov


def test_show_describes_a_chapter(chapters_dir, capsys):
    assert main(["show", str(chapters_dir / "Demonic.ebn")]) == 0
    output = capsys.readouterr().out
    assert "Title:            Demonic" in output
    assert "Proper Names / of Denizens of Hell" in output
    assert "Seed names:       76: Adrashyal, Iblimael," in output
    assert "structural on, statistical on, prefixes on, suffixes on" in output
    assert "consonant clusters RR, JJ, NN, ZZ" in output
    assert "read as windows-1252" in output
    assert "Warnings" not in output
    assert "Pinned settings:  all" in output
    assert "Letters:          Y: vowel, U: vowel, apostrophe: vowel, hyphen: consonant" in output


def test_show_lists_the_pinned_settings_of_a_style_file(tmp_path, capsys):
    path = tmp_path / "Mini.toml"
    text = 'names = "Ana, Bel"\n[settings]\nprefixes = true\nfit_level = 2\nmarkov = "off"\n'
    path.write_text(text, encoding="utf-8")
    assert main(["show", str(path)]) == 0
    output = capsys.readouterr().out
    assert "Title:            Mini" in output
    assert "Pinned settings:  fit_level, prefixes, markov" in output
    inferred = output.split("Worked out from the names:", 1)[1]
    assert "  shuffle = true " in inferred  # only 2 names and the classic way, so it backs off
    assert "  refuse_copies = true " in inferred
    assert "fit_level" not in inferred and "prefixes" not in inferred


def test_show_a_chapter_has_nothing_to_work_out(chapters_dir, capsys):
    main(["show", str(chapters_dir / "Angelic.ebn")])
    assert "Worked out from the names" not in capsys.readouterr().out


def test_show_counts_repeats(chapters_dir, capsys):
    main(["show", str(chapters_dir / "Ancient_Female.ebn")])
    assert "98 (10 are repeats)" in capsys.readouterr().out


def test_show_names_lists_every_name(chapters_dir, capsys):
    main(["show", str(chapters_dir / "Angelic.ebn"), "--names"])
    output = capsys.readouterr().out
    assert "    1  Aalbe'il" in output
    assert "  266  Zuphlasah" in output


def test_show_reports_warnings(tmp_path, capsys):
    path = tmp_path / "warn.ebn"
    path.write_text('TITLE = "T"; NAMES = "Ana"; GENOPT = "fit:9";', encoding="utf-8")
    assert main(["show", str(path)]) == 0
    output = capsys.readouterr().out
    assert "Warnings:" in output
    assert "fit:9" in output


def test_show_reports_problems(tmp_path, capsys):
    path = tmp_path / "bad.ebn"
    path.write_text('TITLE = "T"; NAMES = "An4";', encoding="utf-8")
    assert main(["show", str(path)]) == 1
    error = capsys.readouterr().err
    assert f"Could not read {path}:" in error
    assert "name 1" in error


def test_analyze_summarizes_a_chapter(chapters_dir, capsys):
    assert main(["analyze", str(chapters_dir / "Angelic.ebn")]) == 0
    output = capsys.readouterr().out
    assert "Angelic: 266 seed names, split into 266 words." in output
    assert "first 12 names; add --all to see every name" in output
    assert "Aalbe'il   AA · LB · E'I · L" in output
    assert "Mikhail" not in output  # only the first 12 names are shown
    assert "Structure  Shape" in output
    assert "Suffixes: 51 kinds" in output
    assert "Letter limits used by validation" in output


def test_analyze_all_shows_every_name(chapters_dir, capsys):
    main(["analyze", str(chapters_dir / "Angelic.ebn"), "--all"])
    output = capsys.readouterr().out
    assert "M · I · KH · AI · L" in output
    assert "Zuphlasah" in output


def test_analyze_name_splits_any_name(chapters_dir, capsys):
    path = str(chapters_dir / "Demonic.ebn")
    assert main(["analyze", path, "--name", "Chemorrah", "--name", "Van Helsing"]) == 0
    output = capsys.readouterr().out
    assert "CH · E · M · O · RR · A · H" in output
    assert "H · E · LS · I · NG" in output
    assert "Letter limits" not in output


def test_analyze_name_reports_names_that_cannot_be_split(chapters_dir, capsys):
    main(["analyze", str(chapters_dir / "Demonic.ebn"), "--name", "R2D2"])
    assert "R2D2 cannot be split: '2' is not a vowel or consonant letter" in capsys.readouterr().out


def test_analyze_shows_words_of_one_element(tmp_path, capsys):
    path = tmp_path / "Short.txt"
    path.write_text("Ana\nAei\nBel\n", encoding="utf-8")
    assert main(["analyze", str(path)]) == 0
    out = capsys.readouterr().out
    assert "AEI" in out
    assert "-1 (V)" in out


def test_generate_with_a_seed(chapters_dir, capsys):
    path = str(chapters_dir / "Angelic.ebn")
    assert main(["generate", path, "-n", "5", "--seed", "1"]) == 0
    first = capsys.readouterr().out
    main(["generate", path, "-n", "5", "--seed", "1"])
    assert capsys.readouterr().out == first
    names = first.splitlines()
    assert len(names) == 5
    assert "Random seed" not in first


def test_generate_without_a_seed_prints_the_seed(chapters_dir, capsys):
    main(["generate", str(chapters_dir / "Angelic.ebn"), "-n", "2"])
    output = capsys.readouterr().out
    assert "(Random seed " in output and "Add --seed " in output


def test_generate_explain(chapters_dir, capsys):
    main(["generate", str(chapters_dir / "Angelic.ebn"), "-n", "2", "--seed", "1", "--explain"])
    output = capsys.readouterr().out
    assert "Name 1: " in output and "Name 2: " in output
    assert "Shape: structure" in output
    assert "Built: " in output


def test_generate_initial_and_option_switches(chapters_dir, capsys):
    path = str(chapters_dir / "Angelic.ebn")
    args = ["generate", path, "-n", "10", "--seed", "2", "--initial", "Z", "--fit", "1"]
    assert main(args + ["--no-prefixes", "--no-suffixes", "--no-structural", "--explain"]) == 0
    output = capsys.readouterr().out
    assert "prefix " not in output and "suffix " not in output
    names = [line.split(": ", 1)[1] for line in output.splitlines() if line.startswith("Name ")]
    assert len(names) == 10 and all(name.startswith("Z") for name in names)


def test_generate_with_a_word_list(chapters_dir, tmp_path, capsys):
    path = str(chapters_dir / "Angelic.ebn")
    main(["generate", path, "-n", "30", "--seed", "4"])
    names = capsys.readouterr().out.splitlines()
    blocked = tmp_path / "blocked.txt"
    blocked.write_text(f"# test list\n{names[0]}\n*e'il\n", encoding="utf-8")
    assert main(["generate", path, "-n", "30", "--seed", "4", "--word-list", str(blocked)]) == 0
    filtered = capsys.readouterr().out.splitlines()
    assert names[0] not in filtered
    assert not any(name.endswith("e'il") for name in filtered)


def test_generate_missing_word_list(chapters_dir, tmp_path, capsys):
    path = str(chapters_dir / "Angelic.ebn")
    assert main(["generate", path, "--word-list", str(tmp_path / "none.txt")]) == 1
    assert "Could not read the word list" in capsys.readouterr().err


def test_generate_validation_override(chapters_dir, capsys):
    path = str(chapters_dir / "Angelic.ebn")
    main(["generate", path, "-n", "1", "--seed", "1", "--validation", "0", "--explain"])
    assert "Checked at validation level 0" in capsys.readouterr().out


def test_generate_with_templates(chapters_dir, capsys):
    path = str(chapters_dir / "Angelic.ebn")
    assert main(["generate", path, "-n", "5", "--seed", "1", "--template", "6 2"]) == 0
    names = capsys.readouterr().out.splitlines()
    assert len(names) == 5 and all(len(name.split(" ")) == 2 for name in names)


def test_generate_bad_template(chapters_dir, capsys):
    assert main(["generate", str(chapters_dir / "Angelic.ebn"), "--template", "5|"]) == 1
    assert "Template \"5|\" has a '|'" in capsys.readouterr().err


def test_generate_notes_skipped_templates(chapters_dir, capsys):
    main(["generate", str(chapters_dir / "Angelic.ebn"), "-n", "1", "--seed", "1"])
    error = capsys.readouterr().err
    assert 'Note: skipping 2 templates that can never be used: "0:1", "1:1".' in error


def test_generate_unusable_templates_only(chapters_dir, capsys):
    assert main(["generate", str(chapters_dir / "Angelic.ebn"), "--template", "0"]) == 1
    assert "None of the templates can ever be used" in capsys.readouterr().err


def test_generate_punctuation(chapters_dir, capsys):
    path = str(chapters_dir / "Ancient_Female.ebn")
    main(["generate", path, "-n", "40", "--seed", "2", "--punctuation", "1"])
    assert any(char in capsys.readouterr().out for char in "áéíóú")


def test_analyze_reports_template_use(chapters_dir, capsys):
    main(["analyze", str(chapters_dir / "Ancient_Male.ebn")])
    output = capsys.readouterr().out
    assert "Templates: 14, of which 8 can be used." in output
    assert '  "0:1" needs structure 0, which no seed name has.' in output
    assert "45 of 426 seed words have a structure that no template" in output


def test_evaluate_reports_measurements(chapters_dir, capsys):
    assert main(["evaluate", str(chapters_dir / "Angelic.ebn"), "-n", "60"]) == 0
    output = capsys.readouterr().out
    assert "Angelic: 60 names made with random seed 1." in output
    assert "Settings changed" not in output
    for heading in ("Copies of the seed names", "Plausibility (the chain looks back",
                    "Variety", "Length (letters)", "Effort and failures (per 100 names)"):  # fmt: skip
        assert heading in output
    assert "Within 1 change of a seed name" in output
    assert "Chance of each letter (average)" in output
    assert "Sample: " in output and "Notes:" in output


def test_evaluate_is_repeatable_and_follows_the_seed(chapters_dir, capsys):
    path = str(chapters_dir / "Angelic.ebn")
    main(["evaluate", path, "-n", "40"])
    first = capsys.readouterr().out
    main(["evaluate", path, "-n", "40", "--seed", "1"])
    assert capsys.readouterr().out == first
    main(["evaluate", path, "-n", "40", "--seed", "2"])
    assert capsys.readouterr().out != first


def test_evaluate_with_changed_settings(chapters_dir, capsys):
    path = str(chapters_dir / "Angelic.ebn")
    args = ["evaluate", path, "-n", "30", "--fit", "2", "--no-prefixes", "--near", "2"]
    assert main(args) == 0
    output = capsys.readouterr().out
    assert "Settings changed: --fit 2 --no-prefixes" in output
    assert "Within 2 changes of a seed name" in output


def test_compare_shows_both_batches(chapters_dir, capsys):
    path = str(chapters_dir / "Demonic.ebn")
    assert main(["compare", path, "-n", "40", "--template", "#"]) == 0
    output = capsys.readouterr().out
    assert "Demonic: 40 names made twice with random seed 1:" in output
    assert "  A: the style's own settings" in output
    assert '  B: --template "#"' in output
    heading = next(line for line in output.splitlines() if "Copies of the seed names" in line)
    assert heading.split()[-3:] == ["Seeds", "A", "B"]
    assert "Sample A: " in output and "Sample B: " in output


def test_compare_batch_a_is_the_chapter_as_written(chapters_dir, capsys):
    path = str(chapters_dir / "Angelic.ebn")
    main(["compare", path, "-n", "30", "--seed", "4", "--fit", "1"])
    def first_names(output, prefix):
        line = next(line for line in output.splitlines() if line.startswith(prefix))
        return line.removeprefix(prefix).split(", ")[:3]  # lines may wrap at different places

    sample_a = first_names(capsys.readouterr().out, "Sample A: ")
    main(["evaluate", path, "-n", "30", "--seed", "4"])
    assert first_names(capsys.readouterr().out, "Sample: ") == sample_a


def test_compare_needs_a_change(chapters_dir, capsys):
    assert main(["compare", str(chapters_dir / "Angelic.ebn")]) == 1
    assert "Give at least one setting to change" in capsys.readouterr().err


def test_compare_bad_template(chapters_dir, capsys):
    assert main(["compare", str(chapters_dir / "Angelic.ebn"), "--template", "5|"]) == 1
    assert "Template \"5|\" has a '|'" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["generate", "evaluate", "compare"])
def test_count_must_be_positive(chapters_dir, command, capsys):
    with pytest.raises(SystemExit) as caught:
        main([command, str(chapters_dir / "Angelic.ebn"), "-n", "0"])
    assert caught.value.code == 2
    assert "must be 1 or more" in capsys.readouterr().err


def test_generate_impossible_initial(chapters_dir, capsys):
    assert main(["generate", str(chapters_dir / "Angelic.ebn"), "--initial", "X"]) == 1
    assert "No seed word starts with 'X'" in capsys.readouterr().err


def test_create_converts_a_chapter_into_the_styles_folder(chapters_dir, tmp_path, monkeypatch, capsys):
    chapter = chapters_dir / "Angelic.ebn"
    original = chapter.read_bytes()
    monkeypatch.chdir(tmp_path)
    assert main(["create", str(chapter)]) == 0
    target = Path("styles") / "Angelic.toml"
    assert f"Saved {target}: 266 names; pinned settings: all." in capsys.readouterr().out
    assert target.exists()
    assert chapter.read_bytes() == original  # the chapter itself is left alone

    main(["generate", str(chapter), "-n", "200", "--seed", "3"])
    from_chapter = capsys.readouterr().out
    main(["generate", str(target), "-n", "200", "--seed", "3"])
    assert capsys.readouterr().out == from_chapter


def test_create_does_not_replace_a_style_unless_asked(chapters_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    chapter = str(chapters_dir / "Demonic.ebn")
    assert main(["create", chapter]) == 0
    assert main(["create", chapter]) == 1
    assert "already exists. Add --replace to replace it." in capsys.readouterr().err
    assert main(["create", chapter, "--replace"]) == 0


def test_create_reports_problems(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Path("names.csv").write_text("Ana, Bel", encoding="utf-8")
    assert main(["create", "names.csv"]) == 1
    assert "Unknown style file type '.csv'" in capsys.readouterr().err
    assert main(["create", "nothing"]) == 1
    assert 'no style called "nothing" in styles' in capsys.readouterr().err
    assert not Path("styles").exists()


# --- Styles by name, and plain name lists ----------------------------------


@pytest.fixture
def styles_folder(tmp_path, monkeypatch):
    """An empty working folder with a styles folder in it."""
    monkeypatch.chdir(tmp_path)
    folder = Path("styles")
    folder.mkdir()
    return folder


def test_generate_from_a_plain_list_by_name(styles_folder, capsys):
    (styles_folder / "Fae.txt").write_text("Aelin\nBrannoc\nCaerwyn\nDelwen, Eirian\n", encoding="utf-8")
    assert main(["generate", "fae", "-n", "5", "--seed", "1"]) == 0
    by_name = capsys.readouterr()
    assert len(by_name.out.splitlines()) == 5 and by_name.err == ""
    main(["generate", str(styles_folder / "Fae.txt"), "-n", "5", "--seed", "1"])
    assert capsys.readouterr().out == by_name.out


def test_a_style_file_wins_over_a_plain_list_of_the_same_name(styles_folder, capsys):
    (styles_folder / "Fae.txt").write_text("Aelin\nBrannoc\n", encoding="utf-8")
    (styles_folder / "Fae.toml").write_text('title = "Fae Folk"\nnames = "Caerwyn, Delwen"', encoding="utf-8")
    assert main(["show", "Fae"]) == 0
    output = capsys.readouterr()
    assert "Title:            Fae Folk" in output.out
    txt, toml = styles_folder / "Fae.txt", styles_folder / "Fae.toml"
    assert f"Note: {txt} is also there; using {toml}." in " ".join(output.err.split())


def test_create_from_a_plain_list(styles_folder, capsys):
    (styles_folder / "Fae.txt").write_text("Aelin\nBrannoc\nCaerwyn\n", encoding="utf-8")
    assert main(["create", "Fae"]) == 0
    output = capsys.readouterr()
    toml = styles_folder / "Fae.toml"
    assert f"Saved {toml}: 3 names; pinned settings: none" in output.out
    assert f'The name "Fae" now finds {toml}.' in " ".join(output.err.split())
    assert toml.read_text(encoding="utf-8").startswith("# A namebook style")
    assert "[settings]" not in toml.read_text(encoding="utf-8")


def test_an_unknown_name_lists_the_styles_there(styles_folder, capsys):
    (styles_folder / "Angelic.toml").write_text('names = "Ana"', encoding="utf-8")
    (styles_folder / "Demonic.txt").write_text("Bel", encoding="utf-8")
    assert main(["show", "Angel"]) == 1
    error = " ".join(capsys.readouterr().err.split())
    assert 'There is no file "Angel", and no style called "Angel" in styles.' in error
    assert "Styles there: Angelic, Demonic." in error


def test_command_is_required(capsys):
    with pytest.raises(SystemExit) as caught:
        main([])
    assert caught.value.code == 2


# --- Inferred settings: --infer and create --pin ---------------------------


def test_create_without_pin_saves_only_the_names(chapters_dir, styles_folder, capsys):
    names = "\n".join(load_style(chapters_dir / "Fae_male.ebn").names)
    (styles_folder / "Fae.txt").write_text(names, encoding="utf-8")
    assert main(["create", "Fae"]) == 0
    output = capsys.readouterr().out
    assert "pinned settings: none." in output
    assert "Worked out from the names each time the style is used (add --pin to save them):" in output
    assert "[settings]" not in (styles_folder / "Fae.toml").read_text(encoding="utf-8")


def test_create_with_pin_saves_the_working_settings(chapters_dir, styles_folder, capsys):
    names = "\n".join(load_style(chapters_dir / "Fae_male.ebn").names)
    (styles_folder / "Fae.txt").write_text(names, encoding="utf-8")
    assert main(["create", "Fae", "--pin"]) == 0
    output = capsys.readouterr().out
    assert "pinned settings: all." in output and "Worked out from the names, and pinned:" in output
    saved = load_style(styles_folder / "Fae.toml")
    assert saved.pinned == set(SETTINGS)

    # Pinning changes nothing about the names: the saved style makes the same ones.
    main(["generate", str(styles_folder / "Fae.txt"), "-n", "100", "--seed", "2"])
    from_list = capsys.readouterr().out
    main(["generate", str(styles_folder / "Fae.toml"), "-n", "100", "--seed", "2"])
    assert capsys.readouterr().out == from_list


def test_create_pin_keeps_what_was_pinned(styles_folder, capsys):
    (styles_folder / "Mini.toml").write_text('names = "Ana, Bel"\n[settings]\nshuffle = false', encoding="utf-8")
    assert main(["create", "Mini", "--pin", "--replace"]) == 0
    saved = load_style(styles_folder / "Mini.toml")
    assert saved.options.shuffle is False  # as pinned
    assert saved.options.fit_level == 3  # filling from the chain needs no back-off for 2 names (D81)
    assert saved.options.refuse_copies


def test_generate_with_infer_ignores_the_chapters_settings(chapters_dir, tmp_path, capsys):
    path = chapters_dir / "Angelic.ebn"
    main(["generate", str(path), "-n", "20", "--seed", "1", "--infer"])
    inferred = capsys.readouterr()
    assert "skipping" not in inferred.err  # the chapter's templates are not used
    # The same as the chapter's names in a plain list, which pins nothing.
    names = tmp_path / "Angelic.txt"
    names.write_text("\n".join(load_style(path).names), encoding="utf-8")
    main(["generate", str(names), "-n", "20", "--seed", "1"])
    assert capsys.readouterr().out == inferred.out


def test_compare_a_chapter_with_what_inference_chooses(chapters_dir, capsys):
    assert main(["compare", str(chapters_dir / "Demonic.ebn"), "-n", "40", "--infer"]) == 0
    output = capsys.readouterr().out
    assert "  B: --infer" in output
    # Without Demonic's RR, JJ... clusters, elements are longer but just as many,
    # so structure numbers can still be compared.
    assert "splits the seed names" not in output


def test_compare_notes_when_b_splits_the_names_differently(styles_folder, capsys):
    names = "Ary, Bely, Cory, Dany, Yara, Yelan, Yorin, Fenor, Galin, Haron"
    text = f'names = "{names}"\n[settings]\nletter_y = "consonant"'
    (styles_folder / "Y.toml").write_text(text, encoding="utf-8")
    assert main(["compare", "Y", "-n", "30", "--infer"]) == 0  # --infer makes a final Y a vowel
    output = capsys.readouterr().out
    assert "B splits the seed names into elements differently" in " ".join(output.split())
    # Plausibility reads the names as written, so A and B are scored alike.
    chance = next(line for line in output.splitlines() if "Chance of each letter" in line)
    assert len(chance.split()[-3:]) == 3 and all(value.endswith("%") for value in chance.split()[-3:])


def test_evaluate_with_infer(chapters_dir, capsys):
    main(["evaluate", str(chapters_dir / "Angelic.ebn"), "-n", "30", "--infer"])
    assert "Settings changed: --infer" in capsys.readouterr().out


# --- Length limits ---------------------------------------------------------


def test_generate_with_a_length_range(chapters_dir, capsys):
    path = str(chapters_dir / "Fae_male.ebn")
    assert main(["generate", path, "-n", "30", "--seed", "1", "--min-length", "8", "--max-length", "9"]) == 0
    names = capsys.readouterr().out.splitlines()
    assert len(names) == 30 and all(8 <= len(name.replace("'", "")) <= 9 for name in names)


def test_generate_with_a_length_no_seed_word_has(chapters_dir, capsys):
    assert main(["generate", str(chapters_dir / "Demonic.ebn"), "--max-length", "4"]) == 1
    assert "No seed word has at most 4 letters (the seed words have 5 to 13)" in capsys.readouterr().err


def test_generate_with_a_length_range_the_wrong_way_round(chapters_dir, capsys):
    args = ["generate", str(chapters_dir / "Angelic.ebn"), "--min-length", "9", "--max-length", "5"]
    assert main(args) == 1
    assert "A name cannot have at least 9 letters and at most 5." in capsys.readouterr().err


def test_compare_with_a_length_range(chapters_dir, capsys):
    assert main(["compare", str(chapters_dir / "Angelic.ebn"), "-n", "40", "--max-length", "6"]) == 0
    output = capsys.readouterr().out
    assert "  B: --max-length 6" in output
    assert "Failed: too short or too long (!LEN)" in output


def test_show_gives_the_length_range(styles_folder, capsys):
    (styles_folder / "Short.toml").write_text('names = "Ana, Bel"\n[settings]\nmax_length = 6', encoding="utf-8")
    main(["show", "short"])
    assert "Length:           at most 6 letters" in capsys.readouterr().out


def test_generate_with_weighted_choice(chapters_dir, capsys):
    path = str(chapters_dir / "Angelic.ebn")
    assert main(["generate", path, "-n", "5", "--seed", "1", "--markov", "weighted"]) == 0
    assert len(capsys.readouterr().out.strip().splitlines()) == 5


def test_show_describes_the_markov_chain(chapters_dir, capsys):
    main(["show", str(chapters_dir / "Angelic.ebn")])
    assert "Markov chain:     off (building rules alone)" in capsys.readouterr().out


def test_compare_with_weighted_choice(chapters_dir, capsys):
    path = str(chapters_dir / "Angelic.ebn")
    args = ["compare", path, "-n", "40", "--markov", "weighted", "--markov-strength", "0.5"]
    assert main(args) == 0
    assert "  B: --markov weighted --markov-strength 0.5" in capsys.readouterr().out


def test_markov_strength_must_be_0_to_1(chapters_dir, capsys):
    with pytest.raises(SystemExit):
        main(["generate", str(chapters_dir / "Angelic.ebn"), "--markov-strength", "2"])
    assert "must be 0 to 1" in capsys.readouterr().err


def test_generate_with_fill(chapters_dir, capsys):
    path = str(chapters_dir / "Angelic.ebn")
    assert main(["generate", path, "-n", "5", "--seed", "1", "--markov", "fill"]) == 0
    assert len(capsys.readouterr().out.strip().splitlines()) == 5


def test_fill_with_structural_off_is_refused(chapters_dir, capsys):
    path = str(chapters_dir / "Angelic.ebn")
    assert main(["generate", path, "-n", "5", "--markov", "fill", "--no-structural"]) == 1
    assert "needs structural on" in capsys.readouterr().err


def test_refuse_copies_can_be_switched_on_and_off(chapters_dir, capsys):
    path = str(chapters_dir / "Angelic.ebn")
    assert main(["evaluate", path, "-n", "300", "--refuse-copies"]) == 0
    output = capsys.readouterr().out
    assert "Settings changed: --refuse-copies" in output
    copies = next(line for line in output.splitlines() if "Same as a seed name" in line)
    assert copies.split()[-1] == "0.0%"
    assert "Failed: a seed name (!COPY)" in output


def test_settings_given_on_the_command_line_are_worked_around(styles_folder):
    syllables = [c + v for c in "BDKLMNRST" for v in "AEIOU"]
    names = [(syllables[n % 45] + syllables[n // 45 % 45]).capitalize() for n in range(20)]
    (styles_folder / "Small.txt").write_text("\n".join(names), encoding="utf-8")

    def options(*arguments):
        return _load_settings(_build_parser().parse_args(["generate", "Small", *arguments])).analyses["list"].style.options

    filled = options()
    assert (filled.markov, filled.fit_level, filled.prefixes) == (Markov.FILL, 3, True)
    # With only 20 names, the classic way backs off to fit level 1, without prefixes.
    classic = options("--markov", "off")
    assert (classic.markov, classic.fit_level, classic.prefixes) == (Markov.OFF, 1, False)
    # A setting given is kept as given.
    assert options("--markov", "off", "--fit", "2").fit_level == 2


def test_the_apostrophe_can_be_given_as_a_vowel_or_a_consonant(chapters_dir, styles_folder, capsys):
    def style(*arguments):
        return _load_settings(_build_parser().parse_args(["generate", *arguments])).analyses["list"].style

    angelic = str(chapters_dir / "Angelic.ebn")
    assert style(angelic).setting("letter_apostrophe") is ApostropheMode.VOWEL  # pinned by the chapter
    consonant = style(angelic, "--apostrophe", "consonant")
    assert consonant.setting("letter_apostrophe") is ApostropheMode.CONSONANT
    assert consonant.letter_classes.is_consonant("'")
    # On a plain list, a class given is used as given, with no test run.
    (styles_folder / "Marks.txt").write_text("Ka'ren, Bel, Cor'an, Dana, Eli'a, Fen", encoding="utf-8")
    assert style("Marks", "--apostrophe", "consonant").setting("letter_apostrophe") is ApostropheMode.CONSONANT
    assert main(["compare", angelic, "-n", "20", "--apostrophe", "consonant"]) == 0
    assert "  B: --apostrophe consonant" in capsys.readouterr().out


def test_the_hyphen_can_be_given_as_a_consonant_or_a_joint(chapters_dir, styles_folder, capsys):
    def style(*arguments):
        return _load_settings(_build_parser().parse_args(["generate", *arguments])).analyses["list"].style

    angelic = str(chapters_dir / "Angelic.ebn")
    assert style(angelic).options.hyphen is HyphenMode.CONSONANT  # pinned by the chapter
    assert style(angelic, "--hyphen", "joint").options.hyphen is HyphenMode.JOINT
    (styles_folder / "Joined.txt").write_text("Bel-Abrean, Cor, Dana, Elin-Har, Fen", encoding="utf-8")
    assert style("Joined").options.hyphen is HyphenMode.JOINT  # worked out from the names
    assert style("Joined", "--hyphen", "consonant").options.hyphen is HyphenMode.CONSONANT
    assert main(["compare", "Joined", "-n", "20", "--hyphen", "consonant"]) == 0
    assert "  B: --hyphen consonant" in capsys.readouterr().out


def test_structural_off_on_a_plain_list_uses_weighted_choice(styles_folder, capsys):
    syllables = [c + v for c in "BDKLMNRST" for v in "AEIOU"]
    names = [(syllables[n % 45] + syllables[n // 45 % 45] + "n").capitalize() for n in range(60)]
    (styles_folder / "Plain.txt").write_text("\n".join(names), encoding="utf-8")
    assert main(["generate", "Plain", "-n", "5", "--seed", "1", "--no-structural"]) == 0
    assert len(capsys.readouterr().out.strip().splitlines()) == 5


# --- Several lists (spec 13.5) ----------------------------------------------------


@pytest.fixture
def two_lists(styles_folder):
    given = [(c + v + d + w).capitalize() for c in "LMNRS" for v in "AEI" for d in "LNR" for w in "AO"]
    family = [a + b for a in ("Ash", "Bram", "Cal", "Dun", "El", "Fen") for b in ("wick", "ford", "ton", "by", "wood")]
    text = (
        'title = "Folk"\n\n[settings]\nmarkov = "fill"\n\n[settings.family]\nmarkov_order = 1\n\n'
        '[names]\ngiven = """\n' + "\n".join(given) + '\n"""\n\n'
        'family = """\n' + "\n".join(family) + '\n"""\n'
    )
    (styles_folder / "Folk.toml").write_text(text, encoding="utf-8")
    return "Folk"


def test_generate_makes_names_from_the_first_list(two_lists, capsys):
    assert main(["generate", two_lists, "-n", "6", "--seed", "1"]) == 0
    names = capsys.readouterr().out.splitlines()[:6]
    assert all(len(name.split()) == 1 and name[0] in "LMNRS" for name in names)


def test_a_combination_can_be_given_on_the_command_line(two_lists, capsys):
    assert main(["generate", two_lists, "-n", "6", "--seed", "1", "--combination", "{given} {family}"]) == 0
    names = capsys.readouterr().out.splitlines()[:6]
    assert all(len(name.split()) == 2 and name.split()[1][0] in "ABCDEF" for name in names)
    assert main(["generate", two_lists, "-n", "4", "--seed", "1", "--combination", "{family}, {given}"]) == 0
    names = capsys.readouterr().out.splitlines()[:4]
    assert all(re.fullmatch(r"[A-F]\w+, [LMNRS]\w+", name) for name in names)
    assert main(["generate", two_lists, "--combination", "{surname}"]) == 1
    assert '"surname", which is not one of the style\'s lists (given, family)' in capsys.readouterr().err


def test_explain_shows_each_part(two_lists, capsys):
    assert main(["generate", two_lists, "-n", "1", "--seed", "1", "--explain", "--combination", "{given} {family}"]) == 0
    output = capsys.readouterr().out
    assert '  Combination: "{given} {family}"' in output
    assert '  Part 1 of 2, from list "given": ' in output and '  Part 2 of 2, from list "family": ' in output
    assert '    Template: "#"' in output


def test_show_describes_each_list(two_lists, capsys):
    assert main(["show", two_lists]) == 0
    output = capsys.readouterr().out
    assert "Lists:            given, family: names are made from given unless" in output
    given, family = output.split('List "given":')[1].split('List "family":')
    assert "Pinned settings:  markov\n" in given and "markov_order = " in given  # worked out
    assert "Pinned settings:  markov, markov_order\n" in family and "looks back 1 letter," in family


def test_analyze_shows_each_list(two_lists, capsys):
    assert main(["analyze", two_lists]) == 0
    output = capsys.readouterr().out
    assert output.index('List "given":') < output.index("Folk: 90 seed names") < output.index('List "family":')


def test_evaluate_measures_each_list_against_its_own_names(two_lists, capsys):
    assert main(["evaluate", two_lists, "-n", "50", "--combination", "{given} {family}"]) == 0
    output = capsys.readouterr().out
    assert 'List "given":' in output and 'List "family":' in output
    assert re.search(r"  Names +90 +50\n", output) and re.search(r"  Names +30 +50\n", output)
    assert re.search(r"Sample: \w+ \w+, ", output)


def test_compare_with_another_combination(two_lists, capsys):
    assert main(["compare", two_lists, "-n", "30", "--combination", "{given} {family}"]) == 0
    output = capsys.readouterr().out
    given, family = output.split('List "given":')[1].split('List "family":')
    assert re.search(r"Copies of the seed names +Seeds +A +B\n", given)
    assert re.search(r"Copies of the seed names +Seeds +B\n", family)  # only B uses it


def test_create_pin_shares_what_every_list_has_alike(two_lists, capsys):
    assert main(["create", two_lists, "--replace", "--pin"]) == 0
    assert "pinned settings: all" in capsys.readouterr().out
    text = (Path("styles") / "Folk.toml").read_text(encoding="utf-8")
    settings, given, family = (text.split("[settings]")[1].split("[settings.given]")[0],
                               text.split("[settings.given]")[1].split("[settings.family]")[0],
                               text.split("[settings.family]")[1].split("[names]")[0])
    assert 'markov = "fill"' in settings and "markov_order" not in settings
    assert "markov_order = 1 " not in given and "markov_order = 1 " in family
    assert main(["show", two_lists]) == 0 and "Worked out" not in capsys.readouterr().out
