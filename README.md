# New Book of Names

New Book of Names makes names. Give it a list of example names, and it
learns how they are put together and makes new ones that sound like them:
from a list of Old Norse names, names that could have been Old Norse.

It comes with 43 styles, most with separate lists of male, female and
family names and places:

- **History:** Akkadian, Ancient Chinese, Ancient Egyptian, Ancient Greek,
  Ancient Persian, Brythonic, Byzantine Greek, Japanese, Latin, Middle High
  German, Nahuatl, Old English, Old French, Old Irish, Old Norse, Old
  Slavic, Pre-Islamic Arabic and Sanskrit.
- **The Elder Scrolls:** Altmer, Argonian, Bosmer, Breton, Dunmer,
  Imperial, Khajiit, Nord, Orc and Redguard.
- **Middle-earth:** Dwarvish, Hobbit, Quenya and Sindarin.
- **A Song of Ice and Fire:** Northmen, Valyrian and Westerosi.
- **Star Trek:** Andorian, Bajoran, Cardassian, Ferengi, Klingon, Romulan,
  Trill and Vulcan.

You can add your own; a plain list of names is enough. It is free software
(see "License").

## Getting started on Windows

1. On the project's GitHub page, open **Releases** and download
   `New-Book-of-Names-<version>-windows.zip`.
2. Unzip it anywhere, open the `New Book of Names` folder, and
   double-click `New Book of Names.exe`.

The program isn't signed, so the first time Windows may say "Windows
protected your PC": choose **More info**, then **Run anyway**.

The styles are in the `styles` folder beside the program. Styles you save
go there too.

## The interface

The interface has three columns: the style and its lists of names on the
left, the settings in the middle, and making names on the right. Nothing
is written to a file until you press Save.

### Style and lists

The left column is for choosing a style and editing its names. Choose a
style from the Style box, then a list from the List box: the list's names
appear below, one per line, and you can edit them. `+` adds a list, and
`⋯` renames or removes the one shown. New, Open…, Save and Save as… are at
the bottom; Open… can open a style or a plain list of names from any
folder.

### Settings

The middle column holds the settings that shape the names, for the list
shown. Rest the mouse on a setting's name to see what it does, when you
might change it, and why it has its value now.

Each setting is either pinned (●), saved in the style file, or inferred
(○), chosen by testing on the names each time the style is opened. A
setting pinned for one list alone is marked ◉. Changing a setting pins
it; clicking its mark pins or unpins it.

- **Infer optimum settings** chooses every setting that isn't pinned, from
  the names as they are now. Press it after changing the names.
- **Pin all** pins every setting, so that Save writes them all into the
  style file.
- **Unpin all** unpins every setting, so that each is inferred again next
  time.

### Making names

The right column makes names and shows how each was built.

- **Names:** how many to make.
- **Starts with:** letters every name must begin with, such as `Th`.
- **Seed:** leave it empty for new names each time. The same seed, with the
  same settings, always makes the same names.
- **Full name** (for a style of several lists): names come from the first
  list. To combine lists, give each part of the name a list, such as male
  then surname; `+` adds a part, and "(none)" in a part's box removes it.
  The line below shows the full name written out. You can edit it for
  anything the boxes can't do: a list's name in braces stands for a name
  from it, and other text is kept as written, as in `{male}, son of
  {male}`.

**Make names** (or F5) makes them. Click a name to see how it was built.
Ctrl+click or Shift+click chooses several, and **Copy** (Ctrl+C) copies
them, or all of them if none is chosen. **Save names…** saves them as a
text file. The log at the bottom says what happened.

Light and Dark, at the top right, change the look; the interface
remembers your choice.

## Styles

A style is a set of example names for one kind of name, such as the names
of a people, or their places. It can hold several lists: the Old Norse
style has male names, female names and places. Each list is learned on its
own, so each keeps its own sound.

A style also has settings. Any setting the style doesn't pin is worked out
from its names when it is opened, so a list of names alone makes a
complete style.

### Making your own

The simplest style is a text file of names, one per line, in the `styles`
folder. It appears in the Style box under its file name. Commas also
separate names, and lines starting with `#` are skipped.

Saving in the interface makes a style file (`.toml`), which holds a title,
a description, any pinned settings, and the lists of names. It is plain
text, so you can also write or edit one in any text editor (save it as
UTF-8):

```toml
title = "Old Norse"
description = "Names of the Viking Age."

[settings]
refuse_copies = true

[names]
male = """
Arnbjorn
Baldur
"""

female = """
Astrid
Fenja
"""
```

Only the names are required. Settings under `[settings]` apply to every
list; a section such as `[settings.female]` pins settings for that list
alone. List first the names the style is most often used for, since names
come from the first list unless you combine lists.

## The command line

Everything the interface does can also be done from the command line, for
making names in bulk or from scripts, along with tools for measuring how
well names match a style. It needs the program run from source (see
"Running from source"). A style in the `styles` folder can be named
instead of giving its path, without minding capitals: `quenya` finds
`styles\Quenya.toml`.

| Command | What it does |
| --- | --- |
| `window` | opens the interface |
| `generate` | makes names |
| `show` | describes a style, with each setting and why it has its value |
| `analyze` | shows how a style's names split into letter groups |
| `evaluate` | makes names and measures how they compare with the style's own |
| `compare` | measures a style as it is against changed settings |
| `create` | saves a style or a list of names as a style file |

```powershell
python -m namebook generate Quenya -n 20
python -m namebook generate Breton --combination "{male} {surname}" --initial Th
python -m namebook show Quenya
python -m namebook evaluate Quenya
python -m namebook compare Quenya --markov fill
python -m namebook create C:\names\Elves.txt --pin
```

Add `--help` to any command for all its options.

### Making names

- `-n` sets how many names to make.
- `--seed` makes the run repeatable: the same seed always gives the same
  names. Without it, a seed is chosen and printed at the end.
- `--explain` shows how each name was built, step by step.
- `--initial` makes only names starting with the given letters.
- `--min-length` and `--max-length` limit how many letters a name has.
- `--combination` puts a style's lists together, as the interface's full
  name does: `--combination "{male} {surname}"`. Given several times, with
  a weight after a colon, it mixes them:
  `--combination "{male} {surname}:3" --combination "{female} {surname}"`.
- `--word-list FILE` never makes the words in a text file, one per line;
  `*` stands for any letters, so `*mog*` blocks every name containing
  "mog".

### Changing settings for one run

Any setting can be given for one run, overriding the style's; the settings
it doesn't give are then worked out around it. The interface's notes on
each setting say what it does.

- `--markov weighted`, `fill` or `off`: how words are put together.
  Weighted sounds closest to the real names, fill varies more, and off is
  the loosest.
- `--markov-order` (1 to 5) and `--markov-strength` (0 to 1): how many
  letters back the Markov chain looks, and how much it counts.
- `--fit` (0 to 3) and `--validation` (0 to 2): how closely names must
  follow the list, and how strictly they are checked.
- `--structural`, `--statistical`, `--prefixes`, `--suffixes`, `--shuffle`
  and `--refuse-copies`, each with a `--no-` form to turn it off.
- `--template RECIPE`: the shape of a whole name, such as `"# #"` for two
  words.
- `--punctuation N`: marks added to names: 1 accents, 2 diaeresis, 4 vowel
  harmony, added together to combine them.
- `--apostrophe vowel|consonant` and `--hyphen consonant|joint`: how the
  apostrophe and the hyphen count.
- `--infer` ignores the settings the style pins and works them all out
  from the names.

### Measuring names

`evaluate` makes 1,000 names and compares them with the style's own: how
many copy or nearly copy a real name, how natural their letters are, how
varied their beginnings, endings and shapes are, and how long they are.
`compare` makes the same names twice, with the style as it is and with the
settings you change, and shows the two side by side. Both use seed 1
unless you give `--seed`, so the same command gives the same report.

### Saving styles

`create` saves a list of names, or any style, as a style file in the
`styles` folder, named after it. `--pin` also saves the settings worked
out from the names, so the style keeps them even if a later version would
choose differently. It won't replace a style file that already exists
unless you add `--replace`.

## What names can contain

Names may use any Latin letter, with or without accents (Ł, Č, Ā, ß, the
Turkish ı, the Hawaiian ʻokina), apostrophes, hyphens and spaces. Other
alphabets, such as Greek or Cyrillic, are not supported. Curly
apostrophes, dashes and unusual spaces are read as plain ones. A style may
hold up to 50,000 names, each up to 60 characters, in a file of up to
5 MB.

## Running from source

You need Python 3.11 or newer, from [python.org](https://www.python.org/).
Download the source (on the GitHub page, **Code**, then **Download ZIP**)
and unzip it, or clone it with git. Then, in PowerShell, from the project
folder:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[test]"
.venv\Scripts\python -m namebook window
```

The first two lines set up a private Python environment in `.venv` (once);
the last opens the interface. From the project folder, write
`.venv\Scripts\python -m namebook` for the commands above.

## Building the Windows program

```powershell
.venv\Scripts\python -m pip install -e ".[build]"
.venv\Scripts\python packaging\build.py
```

This makes `dist\New Book of Names\`, holding the program, the styles,
this file and the license, and the same folder zipped, ready to attach to
a release.

## Running the tests

```powershell
.venv\Scripts\python -m pytest
```

Tests that need files not present in your copy are skipped.

## Acknowledgements

- New Book of Names is inspired by Everchanging Book of Names, by Sami
  Pyörre (1997–2002), and its way of building names from the pieces of
  example names. It can open that program's chapter files (`.ebn`), which
  keep the settings they were written with.
- P_2(R), of the 17th Shard forums, for the idea of guiding names with
  Markov chains.
- The names in the bundled styles were gathered from the sources each
  style's description names, such as Wikidata, UESP and Memory Beta.

## License

Copyright (C) 2026 NotBasileus.

New Book of Names is free software: you can share it and change it under
the terms of the GNU General Public License, version 3 or (at your option)
any later version, as published by the Free Software Foundation. Anything
you make from it and share must be shared under the same license, with its
source. It comes with no warranty. The full license is in `LICENSE`.
