"""Word-preservation check (spec: "Quality gate", step 2).

The prepared text must say exactly what the article says. Formatting,
paragraph breaks, pauses and [BREAK] markers are invisible here. The server's
gate additionally compares the engine's own phoneme output (Task 5); this
module is the fast, explainable pre-check.

Both texts are read as the engine receives them: text_to_speech.clean_markdown_text
(the cleaning the CLI and KokoroEngine apply), then every run of whitespace other
than a newline made one plain space (as KokoroEngine does: misaki misplaces an
override after a no-break, thin or ideographic space or a tab on the same line),
then prepare_for_tts (which turns "--" into "... " before generate_audio hands
the text to misaki). Nothing else is done to the characters: no Unicode
normalisation (a fullwidth "＄" is not "$"). The only other edits are to drop [BREAK] markers, read a phonetic
override as its displayed word, and read a curly apostrophe as a straight one,
as misaki does.

Each run of non-whitespace characters is split into tokens, following what
misaki (the engine's grapheme-to-phoneme step) was checked to voice:
- numbers. A "-", "." or "-." right before the digits is part of the number at
  the start of the run, or after any character that is neither part of a word
  (a letter, a digit, or an apostrophe right after one) nor another "-" or
  ".": "-5", "(-5", "fell(-5", "5;-6", "'-5", "$-5", ".5" and "-.5" are not "5",
  but "..5", ".-5", "x-5" and "x'-5" read "five", and the hyphen in "5-6" is a
  range. An apostrophe next to a digit is dropped, as misaki drops it: "5'6"
  is "56" ("fifty-six") and "1990's" is "1990s";
- dotted words with no space ("example.com", "U.S.", "e.g."), one token each;
- the dotted abbreviations of misaki's lexicon, matched case-sensitively, which
  keep their dot (DOTTED_ABBREVIATIONS: "No. 5" is "number five", "No 5" is "no
  five"; a lowercase "no." is plain "no");
- words, and every other character on its own.
A run with a symbol in it (a character that is none of those and not silent:
"$", "%", "/", "≤") is one token, its silent marks gone: "$(-5)" is "$-5" and
"(5%)," is "5%". So any change of spacing around a symbol is a change ("≤-5"
is "less five", "≤ -5" "less or equal to minus five"), even where misaki reads
the two alike ("$.5" and "$ .5"). All tokens must match, in order, on both
sides; only the silent punctuation the engine doesn't voice (SILENT) is free.

Bounds on the work. Cleaning and tokenizing take time linear in each text's
length. Before any search, the check refuses a prepared text longer than twice
the article plus 10,000 characters, more than MAX_SUBSTITUTIONS substitutions,
an original longer than MAX_SPOKEN_WORDS words, and more prepared words (m)
than the article's (n) plus added_words_limit(n). Then finding each original in
the article takes at most n x MAX_SPOKEN_WORDS comparisons, and the alignment
settles at most 50 x (n + m) + 1000 states, each against at most
MAX_SUBSTITUTIONS substitutions found beforehand (a little under a millisecond
per article word in the worst case measured). A word of a substitution's spoken
side or of an override is at most 64 characters, and only words of at most 64
letters are compared as respellings.

Accepted residuals, where misaki and the check still differ:
- misaki deletes a minus glued after "+ × % @ § © ∞ ~ = < ^ ‰ _ ` – >", after a
  run of apostrophes, and after a comma glued to a word or a digit ("rose,-5",
  "5,-6"), where the check keeps it: "5" to "<-5" is refused though it sounds
  the same, while a minus deleted that way passes ("-5" to "<-5", "fell -5" to
  "fell_-5", "×-5" to "× -5", "rose, -5" to "rose,-5");
- the other way, dots before a sign and a quote between the sign and the digits
  make misaki say a minus the check doesn't see ("..-5" and "-‘5" read "minus
  five", here "5"), or drop the number;
- dashes in a run with a currency sign: "$5-6" reads "five dollarssix" and
  "$5–6" "five six", the same token here; the review found "5$-" can add a
  "dollar" that "5$" lacks, also the same token here;
- misaki sometimes drops a "-5" entirely (after a quote, a colon or an em dash);
- zero-width characters: misaki reads some as spaces ("five\u200bdollars" is
  "five dollars"); here they are symbols, so edits around them are refused;
- "No." before a newline reads "no" (misaki reads each line on its own), and a
  lowercase "no." before a number reads "number" ("no. 3"), while here "No." is
  always "no." and "no." always "no" (DOTTED_ABBREVIATIONS is case-sensitive);
- URLs: paths, ports and "@" ("example.com/news" has no "dot") are not modelled;
- "'05" ("zero five") and "’05" ("five") are the same token here;
- case is folded, though a case change can change the speech ("us" and "US",
  "NO. 3"), because the skill uses capitals to steer pronunciation;
- ".'5" reads "point five" in misaki, "5" here;
- a silent mark glued to a year can switch misaki from "twenty twenty-three" to
  "two thousand twenty-three";
- misaki garbles "-$3.50" and ranges such as "10,000-20,000";
- override phonemes are not checked to say their word until Task 4's
  provenance rule.
And the check refuses some edits the engine reads the same way (a sentence-final
"No." turned into "No,", "Dr." into "Dr", "1,000" into "1000", spacing around a
symbol, an override joined by a hyphen to a digit or to nothing, "5-[x](/…/)"),
since a refusal only costs a retry.

A phonetic override is validated for shape (one displayed word, a pronunciation
with a letter, no whitespace, limited length) and must stand alone, a whole,
separately spaced word with at most an opening bracket or quote before it and
closing punctuation after it, or a hyphen joining it to a letter (a name such as
"Ocasio-[Cortez](/…/)" or "[Pengelley](/…/)-Smith"), since misaki misplaces one
that shares a token with anything else. Its phonemes are not checked to say that word: an override
can pronounce any single word. The server validates override provenance
separately (Task 4).

Every change to the words themselves must be declared as a Substitution, and
declared substitutions are bounded so they can't carry new content: each one is
short, and across the whole text all of them together may add only a few words
and change only a few words, however many times each is applied. Only a
respelling is free of the second budget, and "respelling" is a syntactic
notion here (same letters spaced differently, or two long, very alike words),
not a judgment that the sounds are the same, so respellings are capped by
number too, and the published notes list every substitution and a reader can
see what was changed.
"""
from __future__ import annotations

import difflib
import heapq
import math
import re
from dataclasses import dataclass
from typing import NamedTuple

from text_to_speech import clean_markdown_text, prepare_for_tts

MAX_SUBSTITUTIONS = 25
MAX_SPOKEN_WORDS = 6         # per declaration
MAX_EXTRA_WORDS = 3          # per declaration: spoken words beyond the original's
MAX_ADDED_WORDS = 20         # across the text: words all the applied substitutions add,
ADDED_WORDS_FRACTION = 0.02  # or this share of the article's words, whichever is larger
MIN_OVERRIDE_PHONEMES = 12   # a phonetic override's phonemes: at most this many characters,
OVERRIDE_PHONEMES_PER_CHAR = 3  # or this many per displayed character, whichever is larger
RESPELLING_RATIO = 0.75      # single words this similar (and no more related) are respellings,
MIN_RESPELLING_LETTERS = 6   # if both have at least this many letters
MAX_RESPELLINGS = 50         # across the text: applications of respellings,
RESPELLINGS_FRACTION = 0.05  # or this share of the article's words, whichever is larger
_MAX_STATES_FACTOR = 50
_MAX_TOKEN = 64              # characters in one word of a substitution's spoken side or an override
_RESYNC_WINDOW = 12          # words per side searched for where the texts agree again

# Single characters that read as nothing, which the skill may add or drop for pacing.
# Each was checked against misaki spaced, glued before and after a word, and before
# and after a digit; "~" ("tilde") and "÷" ("divided by") are spoken, so they are not
# here. Next to a digit "-", ".", "," and "'" can be spoken; the number rules cover that.
SILENT = frozenset(".,;:!?\"'‘’“”()[]{}-–—…^<>‰_`")

# Abbreviations misaki reads differently with their dot ("No. 5" is "number five"), so
# the dot stays in the token: exactly the keys with a dot in misaki 0.9.4's
# us_gold.json (us_silver.json has none), case and all, and "vs.", which
# Lexicon.get_special_case reads as "versus". Matched case-sensitively, then
# case-folded like every token: "No." is the token "no.", "no." is "no".
DOTTED_ABBREVIATIONS = frozenset(
    {"Dr.", "Esq.", "Etc.", "Mr.", "Mrs.", "Ms.", "No.", "etc.", "vs."})

# ASCII only, as the engine's own link syntax is: [word](/phonemes/). Neither part
# may hold a "[", which keeps the search linear on long runs of brackets.
_PHONETIC = re.compile(r"\[([^\[\]]+)\]\(/([^)\[]*)/\)")
_WORD = r"[^\W_]+(?:'[^\W_]+)*"
# Digits, joined by number punctuation or an apostrophe, with any letters glued on
# ("3.5", "5'6", "1990's", "3rd"): one token.
_NUMBER = re.compile(r"\d+(?:[.,/:']\d+)*(?:'?" + _WORD + ")?")
_ABBREVIATION = "|".join(re.escape(a[:-1])
                         for a in sorted(DOTTED_ABBREVIATIONS, key=len, reverse=True))
# A number, a dotted word ("example.com"), a dotted abbreviation, a word, or any
# other single character.
_TOKEN = re.compile(rf"{_NUMBER.pattern}|{_WORD}(?:\.{_WORD})+|(?:{_ABBREVIATION})\.(?![^\W_])"
                    rf"|{_WORD}|\S")
_SIGNS = ("-.", "-", ".")  # what may stand right before a number's digits, longest first
# What may touch a phonetic override: an opening bracket or quote before its "[", and
# closing marks after its ")", then whitespace; or a hyphen with a letter beyond it.
# Anything else glued to it lands in the same spaCy token, and misaki then puts the
# override on the wrong word.
_BEFORE_OVERRIDE = frozenset("([{\"“‘'")
_AFTER_OVERRIDE = frozenset(".,;:!?)]}\"”’'")
_WORD_CHAR = re.compile(r"[^\W_]")
_DIGIT_APOSTROPHE = re.compile(r"(?<=\d)'|'(?=\d)")
_SPACES = re.compile(r"[^\S\n]+")


@dataclass(frozen=True)
class Substitution:
    original: str  # the article's words
    spoken: str    # what the prepared text says in their place


def added_words_limit(source_words: int) -> int:
    """How many words all the substitutions together may add to the prepared text."""
    return max(MAX_ADDED_WORDS, math.ceil(ADDED_WORDS_FRACTION * source_words))


def max_prepared_length(source_length: int) -> int:
    """The most characters the prepared text may have, for an article of this many:
    check() refuses a longer one before reading it, and replace_text won't make one."""
    return 2 * source_length + 10_000


def normalize_spaces(text: str) -> str:
    """Every run of whitespace other than a newline made one plain space. KokoroEngine
    applies it after clean_markdown_text, and so does the check (_spoken_text): misaki
    misplaces an override after a no-break, thin or ideographic space or a tab earlier
    on the same line."""
    return _SPACES.sub(" ", text)


def _spoken_text(text: str) -> str:
    """The text as the engine receives it, minus [BREAK] markers."""
    text = prepare_for_tts(normalize_spaces(clean_markdown_text(text)))
    return text.replace("’", "'").replace("‘", "'").replace("[BREAK]", " ")


def _is_word_char(text: str, k: int) -> bool:
    """A letter or digit, or an apostrophe right after one (part of a word, as in
    "rock'"; a quote mark at the start of a run is not)."""
    if _WORD_CHAR.match(text[k]):
        return True
    return text[k] == "'" and k > 0 and bool(_WORD_CHAR.match(text[k - 1]))


def _is_symbol(ch: str) -> bool:
    """A character that is a token of its own: not whitespace, silent or part of a word."""
    return not (ch.isspace() or ch in SILENT or _WORD_CHAR.match(ch))


def _sign_belongs(run: str, start: int) -> bool:
    """Whether a "-", "." or "-." at run[start], right before a number's digits, is
    part of the number: at the start of the run, or after any character that is
    neither part of a word nor another "-" or ".". misaki reads "(-5", "fell(-5",
    "5;-6", "'-5" and "$-5" with "minus", but "..5", ".-5", "x-5" and "x'-5" without."""
    return start == 0 or (run[start - 1] not in "-." and not _is_word_char(run, start - 1))


def _run_tokens(run: str) -> list[str]:
    """The tokens of one run of non-whitespace characters."""
    tokens, pos = [], 0
    while pos < len(run):
        for sign in _SIGNS:
            number = _NUMBER.match(run, pos + len(sign)) if run.startswith(sign, pos) else None
            if number and _sign_belongs(run, pos):
                tokens.append(sign + number.group())
                pos = number.end()
                break
        else:
            token = _TOKEN.match(run, pos)
            tokens.append(token.group())
            pos = token.end()
    return tokens


def _tokens_shown(text: str) -> list[tuple[str, str]]:
    """Case-folded tokens of already-cleaned text, silent punctuation dropped, each
    with how a message shows it. An apostrophe next to a digit goes, as misaki drops
    it ("5'6" says "fifty-six"). A run with a symbol in it is one token (_merge),
    shown as written, so a message can show the spacing that differs."""
    out = []
    for run in text.split():
        tokens = [_DIGIT_APOSTROPHE.sub("", tok).casefold()
                  for tok in _run_tokens(run) if tok not in SILENT]
        if any(_is_symbol(ch) for ch in run):
            out.append((_merge(tokens), run.casefold()))
        else:
            out.extend((tok, tok) for tok in tokens)
    return out


def _tokens(text: str) -> list[str]:
    return [tok for tok, _ in _tokens_shown(text)]


def _merge(tokens: list[str]) -> str:
    """A run with a symbol in it, as one token: its tokens run together, silent
    punctuation gone ("$(-5)" and "$-5" are "$-5", "(5%)," is "5%"), so any change
    of spacing around a symbol is a change ("$5" and "$ 5", "≤-5" and "≤ -5"). Where
    two words or numbers would run into each other a space stays: "3-4%" ("three
    four percent") is "3 4%", not "34%" ("thirty-four percent")."""
    merged = ""
    for tok in tokens:
        if merged and merged[-1].isalnum() and tok[0].isalnum():
            merged += " "
        merged += tok
    return merged


def _words_of(spoken: str) -> tuple[list[str], list[str]]:
    """words() of text already through _spoken_text, and how messages show them."""
    pairs = _tokens_shown(_PHONETIC.sub(r"\1", spoken))
    return [tok for tok, _ in pairs], [shown for _, shown in pairs]


def words(text: str) -> list[str]:
    """The tokens the engine will speak, case-folded: words, numbers and symbols.

    clean_markdown_text drops heading markers, emphasis markers, images and
    link URLs (keeping link text and phonetic overrides), exactly as the CLI
    does for a .md file; prepare_for_tts then turns "--" into "... ", which
    can change what misaki reads ("--5" says "five", "No -- 5" says "number
    five"). A phonetic override reduces to its displayed word.
    """
    return _words_of(_spoken_text(text))[0]


def overrides(text: str) -> list[tuple[str, str]]:
    """Each phonetic override in `text`, in order, as (displayed text, phonemes), read
    as the check reads them: in the text the engine receives (so markup around one is
    gone first), the phonemes after strip('/')."""
    return [_override_parts(m) for m in _PHONETIC.finditer(_spoken_text(text))]


def is_respelling(a: str, b: str) -> bool:
    """Whether `b` is `a` spelled differently (or the same), by the rule that exempts a
    substitution from the changed-words budget (_is_respelling), applied to their
    words(). Text with no words is not a respelling of anything."""
    wa, wb = tuple(words(a)), tuple(words(b))
    return bool(wa and wb) and _is_respelling(wa, wb)


def check(source: str, prepared: str, substitutions: list[Substitution]) -> list[str]:
    """Problems with `prepared` as a faithful reading of `source`; [] means it passes."""
    if len(prepared) > max_prepared_length(len(source)):
        return [f"the prepared text is far longer than the article: {len(prepared)} "
                f"characters against {len(source)}"]
    if len(substitutions) > MAX_SUBSTITUTIONS:
        return [_too_many(substitutions)]
    spoken = _spoken_text(prepared)
    (src, src_shown), (prep, prep_shown) = _words_of(_spoken_text(source)), _words_of(spoken)
    limit = added_words_limit(len(src))
    if len(prep) > len(src) + limit:
        return [f"the prepared text has {len(prep)} words against the article's {len(src)}; "
                f"substitutions may add at most {limit}"]
    problems = _check_substitutions(src, substitutions)
    if problems:
        return problems
    problems = _check_overrides(spoken)
    pairs = [(tuple(words(s.original)), tuple(words(s.spoken))) for s in substitutions]
    aligned = _align(src, prep, pairs)
    if aligned is None:
        return problems + ["could not align the prepared text with the article "
                           "(too many alternatives)"]
    if isinstance(aligned, _Stuck):
        return problems + [_describe(src, prep, pairs, aligned, (src_shown, prep_shown))]
    return problems + _check_budgets(src, substitutions, pairs, aligned)


def _stands_alone(spoken: str, start: int, end: int) -> bool:
    """Whether the override at spoken[start:end] is a word of its own: whitespace (or
    the start) or an opening bracket or quote before it, and after it closing marks,
    if any, then whitespace or the end. misaki reads '(x)', '"x",', 'x.', 'x...' (what
    prepare_for_tts makes of "x --") and 'x?!' with the override in place. A hyphen
    may join it to a letter on either side, as in a name ("Jean-[Luc](/lˈuk/)",
    "[Pengelley](/pɛnʤˈɛli/)-Smith"), which misaki also reads in place; not to a digit
    ("[Covid](/…/)-19" loses "19") or to nothing ("-[is](/ɪz/) not" loses "not")."""
    hyphen_before = start >= 2 and spoken[start - 1] == "-" and spoken[start - 2].isalpha()
    if (start and not spoken[start - 1].isspace() and spoken[start - 1] not in _BEFORE_OVERRIDE
            and not hyphen_before):
        return False
    if spoken[end:end + 1] == "-" and spoken[end + 1:end + 2].isalpha():
        return True
    while end < len(spoken) and spoken[end] in _AFTER_OVERRIDE:
        end += 1
    return end == len(spoken) or spoken[end].isspace()


def _override_parts(m: re.Match) -> tuple[str, str]:
    """An override's displayed text and its phonemes, as the engine reads them."""
    return m.group(1).strip(), m.group(2).strip("/")


def _check_overrides(spoken: str) -> list[str]:
    """Each phonetic override is one displayed word with one bounded pronunciation,
    standing alone: misaki misplaces an override that shares a spaCy token with
    anything else ("Pengel[ley](/lˈA/) family" loses "family", "-[is](/ɪz/) not"
    loses "not", "[Covid](/kˈOvɪd/)-19" loses "19", "[1](/wˈʌn/),000" loses
    "thousand"), and every later override on the line then shifts."""
    problems = []
    for m in _PHONETIC.finditer(spoken):
        shown, phonemes = _override_parts(m)
        why = []
        if not _stands_alone(spoken, m.start(), m.end()):
            why.append("it is glued to the text around it; an override must be a whole, "
                       "separately spaced word, with at most an opening bracket or quote "
                       "before it and closing punctuation after it (override a possessive "
                       "whole, as [Pengelley's](/…/))")
        shown_words = _tokens(shown)
        if len(shown_words) != 1:
            why.append("the displayed text must be exactly one word")
        elif len(shown_words[0]) > _MAX_TOKEN:
            why.append(f"the displayed word is longer than {_MAX_TOKEN} characters")
        if not phonemes:
            why.append("the phonemes are empty")
        else:
            if re.search(r"\s", phonemes):
                why.append("the phonemes contain whitespace (an override pronounces one word)")
            if not any(ch.isalpha() for ch in phonemes):
                why.append("the phonemes contain no letter")
            cap = max(MIN_OVERRIDE_PHONEMES, OVERRIDE_PHONEMES_PER_CHAR * len(shown))
            if len(phonemes) > cap:
                why.append(f"the phonemes are {len(phonemes)} characters; "
                           f"at most {cap} for this displayed text")
        if why:
            problems.append(f"phonetic override {m.group(0)!r} is not allowed: {'; '.join(why)}")
    return problems


def _too_many(subs: list[Substitution]) -> str:
    return f"{len(subs)} substitutions declared; the limit is {MAX_SUBSTITUTIONS}"


def _check_substitutions(src: list[str], subs: list[Substitution]) -> list[str]:
    if len(subs) > MAX_SUBSTITUTIONS:  # before reading any of them
        return [_too_many(subs)]
    problems = []
    for s in subs:
        o, sp = words(s.original), words(s.spoken)
        label = f"substitution {s.original!r} -> {s.spoken!r}"
        if not o or not sp:
            problems.append(f"{label} has no words on one side")
            continue
        if o == sp:
            problems.append(f"{label} changes nothing")
            continue
        if len(o) > MAX_SPOKEN_WORDS:
            problems.append(f"{label}: the original is too long: at most {MAX_SPOKEN_WORDS} words")
        if any(len(word) > _MAX_TOKEN for word in sp):
            problems.append(f"{label}: a spoken word is longer than {_MAX_TOKEN} characters")
        if len(sp) > MAX_SPOKEN_WORDS or len(sp) > len(o) + MAX_EXTRA_WORDS:
            problems.append(f"{label} is too long: at most {MAX_SPOKEN_WORDS} spoken words, "
                            f"and at most {MAX_EXTRA_WORDS} more than the original")
        if len(o) <= MAX_SPOKEN_WORDS and not _contains(src, o):  # a longer one is refused above
            problems.append(f"{label}: the original does not appear in the article")
    return problems


def _contains(seq: list[str], sub: list[str]) -> bool:
    n = len(sub)
    return any(seq[i:i + n] == sub for i in range(len(seq) - n + 1))


def _squash(tokens: tuple) -> str:
    """The tokens run together, whitespace and silent characters dropped ("U.S."
    and "US", "DOJ" and "D O J"), except a number's sign, which is spoken, and a
    separator where a digit meets a digit or a letter, the punctuation itself if
    any: "55" and "5 5", "1000" and "1 000", "3.5" and "3 5", "-5" and "5", "$-5"
    and "$5", "3rd" and "3 rd" all sound different."""
    chars = []  # (character, spoken although it is in SILENT)
    for i, tok in enumerate(tokens):
        if i:
            chars.append((" ", False))
        # A "-" left in a token is always a sign; a "." is one before a digit when no
        # letter or digit comes right before it ("$.5", "-.5"; not "3.5" or "u.s").
        chars += [(ch, ch == "-" or (ch == "." and tok[k + 1:k + 2].isdigit()
                                      and not (k and tok[k - 1].isalnum())))
                  for k, ch in enumerate(tok)]
    squashed, gap = "", ""
    for ch, spoken in chars:
        if not spoken and (ch.isspace() or ch in SILENT):
            gap += ch
            continue
        before = squashed[-1:]
        if gap and before.isalnum() and ch.isalnum() and (before.isdigit() or ch.isdigit()):
            squashed += gap.strip() or " "
        squashed, gap = squashed + ch, ""
    return squashed


def _is_respelling(original: tuple, spoken: tuple) -> bool:
    """Whether `spoken` is `original` spelled differently, not another word.

    Either the two are identical once squashed (whitespace and silent characters
    gone, except as separators and signs next to digits; "DOJ" and "D O J",
    "U.S." and "US", but not "3.5" and "3 5" or "-5" and "5"), or each is one
    word, of letters only, at least MIN_RESPELLING_LETTERS long, that starts with
    the same letter, neither inside the other ("the" and "they", "legal" and
    "illegal"), and that are very alike ("Pengelley" and "Pengelly"). Shorter
    words that are alike ("form" and "from", "not" and "now") differ in meaning
    too often to be exempt."""
    if _squash(original) == _squash(spoken):
        return True
    if len(original) != 1 or len(spoken) != 1:
        return False
    a, b = original[0], spoken[0]
    return (a.isalpha() and b.isalpha() and min(len(a), len(b)) >= MIN_RESPELLING_LETTERS
            and max(len(a), len(b)) <= _MAX_TOKEN
            and a[0] == b[0] and a not in b and b not in a
            and difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= RESPELLING_RATIO)


def _changed_words(original: tuple, spoken: tuple) -> int:
    """Words that differ per application: what is left of each side once the
    tokens they share at the start and end are set aside."""
    shortest = min(len(original), len(spoken))
    head = 0
    while head < shortest and original[head] == spoken[head]:
        head += 1
    tail = 0
    while tail < shortest - head and original[-1 - tail] == spoken[-1 - tail]:
        tail += 1
    return max(len(original), len(spoken)) - head - tail


def _respellings_limit(source_words: int) -> int:
    """How many times, in all, respellings may be applied."""
    return max(MAX_RESPELLINGS, math.ceil(RESPELLINGS_FRACTION * source_words))


def _check_budgets(src, subs, pairs, applied):
    """Across the whole text, the applied substitutions may add only a few words
    and change only a few (respellings don't count as changes), and respellings
    may be applied only so many times."""
    limit = added_words_limit(len(src))
    added = [n * max(0, len(sp) - len(o)) for n, (o, sp) in zip(applied, pairs)]
    if sum(added) > limit:
        headline = f"the substitutions add {sum(added)} words to the article's {len(src)}"
        return [_over_budget(headline, limit, subs, applied, added, "and adds {} words")]
    problems = []
    respelling = [_is_respelling(o, sp) for o, sp in pairs]
    changed = [0 if r else n * _changed_words(o, sp)
               for r, n, (o, sp) in zip(respelling, applied, pairs)]
    if sum(changed) > limit:
        headline = f"the substitutions change {sum(changed)} of the article's {len(src)} words"
        problems.append(_over_budget(headline, limit, subs, applied, changed,
                                     "and changes {} words"))
    respelled = [n if r else 0 for r, n in zip(respelling, applied)]
    cap = _respellings_limit(len(src))
    if sum(respelled) > cap:
        headline = (f"respellings are applied {sum(respelled)} times in all, "
                    f"in an article of {len(src)} words")
        problems.append(_over_budget(headline, cap, subs, applied, respelled, "",
                                     "Respell fewer places, or use phonetic overrides"))
    return problems


def _over_budget(headline, limit, subs, applied, counts, what, advice="Shorten or drop them"):
    detail = "; ".join(
        f"substitution {s.original!r} -> {s.spoken!r} is applied {n} time{'s' if n != 1 else ''}"
        + (" " + what.format(c) if what else "") for s, n, c in zip(subs, applied, counts) if c)
    return f"{headline}; the limit is {limit}: {detail}. {advice}"


class _Stuck(NamedTuple):
    """Where the alignment got furthest: src[:i] and prep[:j] are explained, and
    the next words differ with no declared substitution to account for it. The
    last `plain` words explained were equal words, not substitutions."""
    i: int
    j: int
    plain: int


def _align(src, prep, pairs):
    """Align prep to src: prep must be src with some occurrences of each pair's
    original replaced by its spoken form. Returns how many occurrences each pair
    is applied to, for the alignment that adds the fewest words; a _Stuck if
    there is none; None if the search grows past a bound instead of running away.

    Explores all alternatives (prefix-sharing pairs need it). Every step moves
    forward in both texts, so settling states in (i, j) order settles each one
    after all of its predecessors, and the cheapest path to each is exact."""
    n, m = len(src), len(prep)
    limit = _MAX_STATES_FACTOR * (n + m) + 1000
    extra = [max(0, len(sp) - len(o)) for o, sp in pairs]
    # Where each pair's original starts in src, and where its spoken form starts in
    # prep, found once rather than at every state.
    starts_here = [[] for _ in range(n + 1)]
    for k, (o, _) in enumerate(pairs):
        for i in _occurrences(src, o):
            starts_here[i].append(k)
    spoken_at = [bytearray(m + 1) for _ in pairs]
    for k, (_, sp) in enumerate(pairs):
        for j in _occurrences(prep, sp):
            spoken_at[k][j] = 1
    best = {(0, 0): (0, None, None)}  # state -> (words added so far, previous state, pair index)
    heap, done = [(0, 0)], set()
    furthest = (0, 0)
    while heap:
        i, j = heapq.heappop(heap)
        if (i, j) in done:
            continue
        done.add((i, j))
        if len(done) > limit:
            return None
        if i == n and j == m:
            counts, state = [0] * len(pairs), (n, m)
            while state != (0, 0):
                _, state, k = best[state]
                if k is not None:
                    counts[k] += 1
            return counts
        furthest = max(furthest, (i, j))
        cost = best[(i, j)][0]
        steps = []
        if i < n and j < m and src[i] == prep[j]:
            steps.append((i + 1, j + 1, 0, None))
        for k in starts_here[i]:
            if spoken_at[k][j]:
                o, sp = pairs[k]
                steps.append((i + len(o), j + len(sp), extra[k], k))
        for ni, nj, c, k in steps:
            if (ni, nj) not in best or cost + c < best[(ni, nj)][0]:
                best[(ni, nj)] = (cost + c, (i, j), k)
                heapq.heappush(heap, (ni, nj))
    plain, state = 0, furthest
    while state != (0, 0) and best[state][2] is None:
        plain, state = plain + 1, best[state][1]
    return _Stuck(*furthest, plain)


def _occurrences(seq: list[str], sub: tuple) -> list[int]:
    """Where `sub` starts in `seq`."""
    n, first = len(sub), sub[0]
    return [i for i in range(len(seq) - n + 1)
            if seq[i] == first and tuple(seq[i:i + n]) == sub]


def _substitution_at(src, prep, pairs, i, j) -> bool:
    return any(tuple(src[i:i + len(o)]) == o and tuple(prep[j:j + len(sp)]) == sp
               for o, sp in pairs)


def _explained_at(src, prep, pairs, i, j) -> bool:
    """Whether an equal word or a declared substitution accounts for the texts here
    (or both texts have ended)."""
    if i == len(src) and j == len(prep):
        return True
    if i < len(src) and j < len(prep) and src[i] == prep[j]:
        return True
    return _substitution_at(src, prep, pairs, i, j)


def _describe(src, prep, pairs, stuck, shown, context=3, window=_RESYNC_WINDOW):
    """The first span that no equal word or declared substitution explains, ending
    where the texts agree again, with a few plain words of context each side.
    `shown` holds how each text's words appear in the message."""
    i, j = stuck.i, stuck.j
    span = None
    for total in range(1, 2 * window + 1):
        for a in range(max(0, total - window), min(window, total) + 1):
            b = total - a
            if (i + a <= len(src) and j + b <= len(prep)
                    and _explained_at(src, prep, pairs, i + a, j + b)):
                span = a, b
                break
        if span:
            break
    truncated = span is None
    a, b = span or (min(window, len(src) - i), min(window, len(prep) - j))
    src_shown, prep_shown = shown
    before = src_shown[i - min(context, stuck.plain):i]
    after = []
    p, q = i + a, j + b
    while (len(after) < context and p < len(src) and q < len(prep) and src[p] == prep[q]
           and not _substitution_at(src, prep, pairs, p, q)):
        after.append(src_shown[p])
        p, q = p + 1, q + 1
    return (f"near word {max(1, min(i + 1, len(src)))} of the article: the article reads "
            f"'{_excerpt(before, src_shown[i:i + a], after, truncated)}' but the prepared "
            f"text reads '{_excerpt(before, prep_shown[j:j + b], after, truncated)}'")


def _excerpt(before, span, after, truncated):
    inner = " ".join(span) + (" ..." if truncated else "")
    return " ".join([*before, f"[{inner or '(nothing)'}]", *after])
