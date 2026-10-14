"""Word-preservation check (spec: "Quality gate", step 2).

The prepared text must say exactly what the article says. Formatting,
paragraph breaks, pauses and [BREAK] markers are invisible here.

Both texts go through text_to_speech.clean_markdown_text, the same function
the CLI and KokoroEngine run before generating audio, and are then reduced to
a sequence of tokens that must match in order: words; numbers with internal
punctuation ("3.5", "1,000", "1/2", "12:30"), each kept whole; and the symbols
the engine speaks (% & + @ $ £ €), each its own token. So both texts must carry
the same symbols and numbers in the same places. A phonetic override counts as
its displayed word. The check does not verify that an override's phonemes say
that word; it only bounds the override's shape (one displayed word, one
pronunciation, of limited length), so an override cannot carry extra words.

Every change to the words themselves must be declared as a Substitution, and
declared substitutions are bounded so they can't carry new content: each one is
short, and across the whole text all of them together may add only a few words
and change only a few words (respellings, which only reshape a word's letters,
don't count as changes), however many times each is applied.
"""
from __future__ import annotations

import difflib
import heapq
import math
import re
import unicodedata
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
RESPELLING_RATIO = 0.6       # a substitution this similar (letters and digits only) is a respelling
_MAX_STATES_FACTOR = 50

_PHONETIC = re.compile(r"\[([^\]]+)\]\(/([^)]*)/\)")
# A spoken symbol, a number with internal punctuation, or a word.
_TOKEN = re.compile(r"[%&+@$£€]|\d+(?:[.,/:]\d+)+|[^\W_]+(?:'[^\W_]+)*")


@dataclass(frozen=True)
class Substitution:
    original: str  # the article's words
    spoken: str    # what the prepared text says in their place


def added_words_limit(source_words: int) -> int:
    """How many words all the substitutions together may add to the prepared text."""
    return max(MAX_ADDED_WORDS, math.ceil(ADDED_WORDS_FRACTION * source_words))


def _spoken_text(text: str) -> str:
    """The text as the engine receives it, minus [BREAK] markers."""
    t = clean_markdown_text(text)
    t = unicodedata.normalize("NFKC", t).replace("’", "'").replace("‘", "'")
    return t.replace("[BREAK]", " ")


def words(text: str) -> list[str]:
    """The tokens the engine will speak, case-folded: words, numbers and spoken symbols.

    clean_markdown_text drops heading markers, emphasis markers, images and
    link URLs (keeping link text and phonetic overrides), exactly as the CLI
    does for a .md file. prepare_for_tts only rewrites punctuation, so it
    changes no words and isn't needed here. A phonetic override reduces to
    its displayed word.
    """
    return [w.casefold() for w in _TOKEN.findall(_PHONETIC.sub(r"\1", _spoken_text(text)))]


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
        return problems + [_describe(src, prep, aligned)]
    return problems + _check_budgets(src, substitutions, pairs, aligned)


def _check_overrides(prepared: str) -> list[str]:
    """Each phonetic override is one displayed word with one bounded pronunciation."""
    problems = []
    for m in _PHONETIC.finditer(_spoken_text(prepared)):
        shown, phonemes = m.group(1).strip(), m.group(2)
        why = []
        if len(_TOKEN.findall(shown)) != 1:
            why.append("the displayed text must be exactly one word")
        if not phonemes:
            why.append("the phonemes are empty")
        else:
            if re.search(r"\s", phonemes):
                why.append("the phonemes contain whitespace (an override pronounces one word)")
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
    """Whether `spoken` only reshapes the letters and digits of `original`."""
    a, b = _squash(original), _squash(spoken)
    if not (a and b):
        return False
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= RESPELLING_RATIO


def _squash(tokens: tuple) -> str:
    return "".join(ch for ch in "".join(tokens) if ch.isalnum())


def _check_budgets(src, subs, pairs, applied):
    """Across the whole text, the applied substitutions may add only a few words
    and change only a few (respellings don't count as changes)."""
    limit = added_words_limit(len(src))
    added = [n * max(0, len(sp) - len(o)) for n, (o, sp) in zip(applied, pairs)]
    if sum(added) > limit:
        headline = f"the substitutions add {sum(added)} words to the article's {len(src)}"
        return [_over_budget(headline, limit, subs, applied, added, "adds")]
    changed = [0 if _is_respelling(o, sp) else n * max(len(o), len(sp))
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
    """Where the alignment got furthest: words src[:i] and prep[:j] are explained,
    and the next words differ with no declared substitution to account for it."""
    i: int
    j: int


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
    return _Stuck(*furthest)


def _describe(src, prep, stuck, context=3, max_words=12):
    """The first span that no equal word or declared substitution explains, with
    a few words of context on each side, from both texts."""
    ops = difflib.SequenceMatcher(None, src[stuck.i:], prep[stuck.j:], autojunk=False).get_opcodes()
    _, i1, i2, j1, j2 = next(op for op in ops if op[0] != "equal")
    article = _excerpt(src, stuck.i + i1, stuck.i + i2, context, max_words)
    prepared = _excerpt(prep, stuck.j + j1, stuck.j + j2, context, max_words)
    return (f"near word {stuck.i + i1 + 1} of the article: "
            f"the article reads '{article}' but the prepared text reads '{prepared}'")


def _excerpt(ws, start, end, context, max_words):
    """ws[start:end] in brackets, with `context` words on each side."""
    span = " ".join(ws[start:end][:max_words]) + (" ..." if end - start > max_words else "")
    parts = ws[max(0, start - context):start] + [f"[{span or '(nothing)'}]"] + ws[end:end + context]
    return " ".join(parts)
