"""Punctuation modes (spec 7.6), applied to each finished word in uppercase.

- **Accents** (``punct:1``): a doubled vowel becomes the vowel with an acute
  accent and a tripled vowel the vowel with a circumflex, so AICANAARO becomes
  AICANÁRO and CIIIRDAN becomes CÎRDAN.
- **Diaeresis** (``punct:2``, once called the Quenya mode): EA and EO, where the
  vowels are said separately, get a diaeresis: on the second vowel at the
  start of a word (Eärendil, Eönwë), otherwise on the E (Fëanor). A
  word-final E becomes Ë (Manwë).
- **Vowel harmony** (``punct:4``, once called the Finnish mode): the first vowel
  that is clearly back (A, O, U) or front (Ä, Ö, Y) decides; E and I go
  either way. Words starting back turn Ä and Ö into A and O, words starting
  front do the opposite, so VÄINO becomes VÄINÖ.

The letter tables are data, so other languages' conventions can be added.
"""

from __future__ import annotations

import re

from namebook.style import Punctuation

ACUTE = {"A": "Á", "E": "É", "I": "Í", "O": "Ó", "U": "Ú", "Y": "Ý"}
CIRCUMFLEX = {"A": "Â", "E": "Ê", "I": "Î", "O": "Ô", "U": "Û"}

DIAERESIS_PAIRS = {"EA": ("EÄ", "ËA"), "EO": ("EÖ", "ËO")}  # (at the start of a word, elsewhere)
DIAERESIS_FINAL = {"E": "Ë"}

HARMONY_BACK = "AOU"
HARMONY_FRONT = "ÄÖY"
HARMONY_TO_FRONT = {"A": "Ä", "O": "Ö"}
HARMONY_TO_BACK = {front: back for back, front in HARMONY_TO_FRONT.items()}


def punctuate(word: str, punctuation: Punctuation) -> str:
    """Apply the chosen modes to one word, in uppercase: accents, then diaeresis, then vowel harmony."""
    if Punctuation.ACCENTS in punctuation:
        word = add_accents(word)
    if Punctuation.DIAERESIS in punctuation:
        word = diaeresis(word)
    if Punctuation.VOWEL_HARMONY in punctuation:
        word = vowel_harmony(word)
    return word


def add_accents(word: str) -> str:
    """AA → Á and AAA → Â. Runs of four or more are left alone."""

    def replace(match: re.Match[str]) -> str:
        run = match.group(0)
        table = {2: ACUTE, 3: CIRCUMFLEX}.get(len(run), {})
        return table.get(run[0], run)

    return re.sub(r"(.)\1+", replace, word)


def diaeresis(word: str) -> str:
    result = []
    pos = 0
    while pos < len(word):
        pair = word[pos : pos + 2]
        if pair in DIAERESIS_PAIRS:
            at_start, elsewhere = DIAERESIS_PAIRS[pair]
            result.append(at_start if pos == 0 else elsewhere)
            pos += 2
        else:
            result.append(word[pos])
            pos += 1
    text = "".join(result)
    if text and text[-1] in DIAERESIS_FINAL:
        text = text[:-1] + DIAERESIS_FINAL[text[-1]]
    return text


def vowel_harmony(word: str) -> str:
    for char in word:
        if char in HARMONY_BACK:
            table = HARMONY_TO_BACK
            break
        if char in HARMONY_FRONT:
            table = HARMONY_TO_FRONT
            break
    else:
        return word
    return "".join(table.get(char, char) for char in word)
