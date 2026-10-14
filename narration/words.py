"""Word-preservation check (spec: "Quality gate", step 2).

The prepared text must say exactly what the article says. Formatting,
paragraph breaks, pauses and [BREAK] markers are invisible here.

Both texts go through text_to_speech.clean_markdown_text, the function the CLI
and KokoroEngine run before generating audio, and nothing else is done to the
characters the engine will receive. In particular there is no Unicode
normalisation, which the engine doesn't do either, so a fullwidth "＄" is a
different token from "$". The only edits are to drop [BREAK] markers, read a
phonetic override as its displayed word, and read a curly apostrophe as a
straight one.

Each run of non-whitespace characters is split into tokens: numbers (a leading
"-" or "." at the start of the run is part of the number: "-5" and ".5" are not
"5"), words, and every other character on its own. All of those must match,
in order, on both sides, except the silent punctuation the engine doesn't
voice and the skill may edit for pacing (SILENT: , . ; : ! ? quotes brackets
dashes ellipses and a few marks that read as nothing).

A phonetic override is validated for shape (one displayed word, a pronunciation
with a letter, no whitespace, limited length) but its phonemes are not checked
to say that word: an override can pronounce any single word. The server
validates override provenance separately (Task 4).

Every change to the words themselves must be declared as a Substitution, and
declared substitutions are bounded so they can't carry new content: each one is
short, and across the whole text all of them together may add only a few words
and change only a few words, however many times each is applied. Only a
respelling (the same sounds spelled differently) is free of the second budget.
"""
from __future__ import annotations

import difflib
import heapq
import math
import re
from dataclasses import dataclass
from typing import NamedTuple

from text_to_speech import clean_markdown_text

MAX_SUBSTITUTIONS = 25
MAX_SPOKEN_WORDS = 6         # per declaration
MAX_EXTRA_WORDS = 3          # per declaration: spoken words beyond the original's
MAX_ADDED_WORDS = 20         # across the text: words all the applied substitutions add,
ADDED_WORDS_FRACTION = 0.02  # or this share of the article's words, whichever is larger
MIN_OVERRIDE_PHONEMES = 12   # a phonetic override's phonemes: at most this many characters,
OVERRIDE_PHONEMES_PER_CHAR = 3  # or this many per displayed character, whichever is larger
RESPELLING_RATIO = 0.75      # single words this similar (and no more related) are respellings
_MAX_STATES_FACTOR = 50
_RESYNC_WINDOW = 12          # words per side searched for where the texts agree again

# Single characters that read as nothing, which the skill may add or drop for pacing.
SILENT = frozenset(".,;:!?\"'‘’“”()[]{}-–—…~^<>÷‰_`")

# ASCII only, as the engine's own link syntax is: [word](/phonemes/).
_PHONETIC = re.compile(r"\[([^\]]+)\]\(/([^)]*)/\)")
# A number (a leading - or . counts only at the start of a run, and a number
# glued to letters is a word), a word, or any other single character.
_TOKEN = re.compile(r"\A[-.]?\d+(?:[.,/:]\d+)*(?![^\W_])"
                    r"|\d+(?:[.,/:]\d+)*(?![^\W_])"
                    r"|[^\W_]+(?:'[^\W_]+)*"
                    r"|\S")


@dataclass(frozen=True)
class Substitution:
    original: str  # the article's words
    spoken: str    # what the prepared text says in their place


def added_words_limit(source_words: int) -> int:
    """How many words all the substitutions together may add to the prepared text."""
    return max(MAX_ADDED_WORDS, math.ceil(ADDED_WORDS_FRACTION * source_words))


def _spoken_text(text: str) -> str:
    """The text as the engine receives it, minus [BREAK] markers."""
    return clean_markdown_text(text).replace("’", "'").replace("‘", "'").replace("[BREAK]", " ")


def _tokens(text: str) -> list[str]:
    """Case-folded tokens of already-cleaned text, silent punctuation dropped."""
    return [tok.casefold() for run in text.split() for tok in _TOKEN.findall(run)
            if tok not in SILENT]


def words(text: str) -> list[str]:
    """The tokens the engine will speak, case-folded: words, numbers and symbols.

    clean_markdown_text drops heading markers, emphasis markers, images and
    link URLs (keeping link text and phonetic overrides), exactly as the CLI
    does for a .md file. prepare_for_tts only rewrites punctuation, so it
    changes no words and isn't needed here. A phonetic override reduces to
    its displayed word.
    """
    return _tokens(_PHONETIC.sub(r"\1", _spoken_text(text)))


def check(source: str, prepared: str, substitutions: list[Substitution]) -> list[str]:
    """Problems with `prepared` as a faithful reading of `source`; [] means it passes."""
    src, prep = words(source), words(prepared)
    problems = _check_substitutions(src, substitutions)
    if problems:
        return problems
    problems = _check_overrides(prepared)
    pairs = [(tuple(words(s.original)), tuple(words(s.spoken))) for s in substitutions]
    aligned = _align(src, prep, pairs)
    if aligned is None:
        return problems + ["could not align the prepared text with the article "
                           "(too many alternatives)"]
    if isinstance(aligned, _Stuck):
        return problems + [_describe(src, prep, pairs, aligned)]
    return problems + _check_budgets(src, substitutions, pairs, aligned)


def _check_overrides(prepared: str) -> list[str]:
    """Each phonetic override is one displayed word with one bounded pronunciation."""
    problems = []
    for m in _PHONETIC.finditer(_spoken_text(prepared)):
        shown, phonemes = m.group(1).strip(), m.group(2).strip("/")  # as the engine reads it
        why = []
        if len(_tokens(shown)) != 1:
            why.append("the displayed text must be exactly one word")
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


def _check_substitutions(src: list[str], subs: list[Substitution]) -> list[str]:
    problems = []
    if len(subs) > MAX_SUBSTITUTIONS:
        problems.append(f"{len(subs)} substitutions declared; the limit is {MAX_SUBSTITUTIONS}")
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
        if len(sp) > MAX_SPOKEN_WORDS or len(sp) > len(o) + MAX_EXTRA_WORDS:
            problems.append(f"{label} is too long: at most {MAX_SPOKEN_WORDS} spoken words, "
                            f"and at most {MAX_EXTRA_WORDS} more than the original")
        if not _contains(src, o):
            problems.append(f"{label}: the original does not appear in the article")
    return problems


def _contains(seq: list[str], sub: list[str]) -> bool:
    n = len(sub)
    return any(seq[i:i + n] == sub for i in range(len(seq) - n + 1))


def _is_respelling(original: tuple, spoken: tuple) -> bool:
    """Whether `spoken` is the same sounds as `original`, spelled differently.

    Either the two are identical once whitespace and silent characters are gone
    (letters, digits and symbols all kept: "DOJ" and "D O J"), or each is one
    word, of letters only, that starts with the same letter, neither inside the
    other ("not" and "now" differ in meaning, "the" and "they" or "legal" and
    "illegal" are different words), and that are very alike."""
    if "".join(original) == "".join(spoken):
        return True
    if len(original) != 1 or len(spoken) != 1:
        return False
    a, b = original[0], spoken[0]
    return (a.isalpha() and b.isalpha() and a[0] == b[0] and a not in b and b not in a
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


def _check_budgets(src, subs, pairs, applied):
    """Across the whole text, the applied substitutions may add only a few words
    and change only a few (respellings don't count as changes)."""
    limit = added_words_limit(len(src))
    added = [n * max(0, len(sp) - len(o)) for n, (o, sp) in zip(applied, pairs)]
    if sum(added) > limit:
        headline = f"the substitutions add {sum(added)} words to the article's {len(src)}"
        return [_over_budget(headline, limit, subs, applied, added, "adds")]
    changed = [0 if _is_respelling(o, sp) else n * _changed_words(o, sp)
               for n, (o, sp) in zip(applied, pairs)]
    if sum(changed) > limit:
        headline = f"the substitutions change {sum(changed)} of the article's {len(src)} words"
        return [_over_budget(headline, limit, subs, applied, changed, "changes")]
    return []


def _over_budget(headline, limit, subs, applied, counts, verb):
    detail = "; ".join(
        f"substitution {s.original!r} -> {s.spoken!r} is applied {n} time{'s' if n != 1 else ''} "
        f"and {verb} {c} words" for s, n, c in zip(subs, applied, counts) if c)
    return f"{headline}; the limit is {limit}: {detail}. Shorten or drop them"


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
        for k, (o, sp) in enumerate(pairs):
            if tuple(src[i:i + len(o)]) == o and tuple(prep[j:j + len(sp)]) == sp:
                steps.append((i + len(o), j + len(sp), extra[k], k))
        for ni, nj, c, k in steps:
            if (ni, nj) not in best or cost + c < best[(ni, nj)][0]:
                best[(ni, nj)] = (cost + c, (i, j), k)
                heapq.heappush(heap, (ni, nj))
    plain, state = 0, furthest
    while state != (0, 0) and best[state][2] is None:
        plain, state = plain + 1, best[state][1]
    return _Stuck(*furthest, plain)


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


def _describe(src, prep, pairs, stuck, context=3, window=_RESYNC_WINDOW):
    """The first span that no equal word or declared substitution explains, ending
    where the texts agree again, with a few plain words of context each side."""
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
    before = src[i - min(context, stuck.plain):i]
    after = []
    p, q = i + a, j + b
    while (len(after) < context and p < len(src) and q < len(prep) and src[p] == prep[q]
           and not _substitution_at(src, prep, pairs, p, q)):
        after.append(src[p])
        p, q = p + 1, q + 1
    return (f"near word {max(1, min(i + 1, len(src)))} of the article: "
            f"the article reads '{_excerpt(before, src[i:i + a], after, truncated)}' "
            f"but the prepared text reads '{_excerpt(before, prep[j:j + b], after, truncated)}'")


def _excerpt(before, span, after, truncated):
    inner = " ".join(span) + (" ..." if truncated else "")
    return " ".join([*before, f"[{inner or '(nothing)'}]", *after])
