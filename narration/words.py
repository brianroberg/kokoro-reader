"""Word-preservation check (spec: "Quality gate", step 2).

The prepared text must say exactly what the article says. Formatting,
paragraph breaks, pauses, [BREAK] markers and phonetic overrides are invisible
here. Markdown is stripped by text_to_speech.clean_markdown_text, the same
function the CLI and KokoroEngine run before generating audio, so this check
sees exactly the words that will be spoken. Every change to the words
themselves must be declared as a Substitution, and declared substitutions are
bounded so they can't carry new content: each one is short, and all of them
together may add only a few words across the whole text, however many times
each is applied.
"""
from __future__ import annotations

import difflib
import heapq
import math
import re
import unicodedata
from dataclasses import dataclass

from text_to_speech import clean_markdown_text

MAX_SUBSTITUTIONS = 25
MAX_SPOKEN_WORDS = 6         # per declaration
MAX_EXTRA_WORDS = 3          # per declaration: spoken words beyond the original's
MAX_ADDED_WORDS = 20         # across the text: words all the applied substitutions add,
ADDED_WORDS_FRACTION = 0.02  # or this share of the article's words, whichever is larger
_MAX_STATES_FACTOR = 50

_PHONETIC = re.compile(r"\[([^\]]+)\]\(/[^)]*/\)")
_TOKEN = re.compile(r"[^\W_]+(?:'[^\W_]+)*")


@dataclass(frozen=True)
class Substitution:
    original: str  # the article's words
    spoken: str    # what the prepared text says in their place


def added_words_limit(source_words: int) -> int:
    """How many words all the substitutions together may add to the prepared text."""
    return max(MAX_ADDED_WORDS, math.ceil(ADDED_WORDS_FRACTION * source_words))


def words(text: str) -> list[str]:
    """The words the engine will speak, case-folded.

    clean_markdown_text drops heading markers, emphasis markers, images and
    link URLs (keeping link text and phonetic overrides), exactly as the CLI
    does for a .md file. prepare_for_tts only rewrites punctuation, so it
    changes no words and isn't needed here.
    """
    t = clean_markdown_text(text)
    t = unicodedata.normalize("NFKC", t).replace("’", "'").replace("‘", "'")
    t = t.replace("[BREAK]", " ")
    t = _PHONETIC.sub(r"\1", t)
    return [w.casefold() for w in _TOKEN.findall(t)]


def check(source: str, prepared: str, substitutions: list[Substitution]) -> list[str]:
    """Problems with `prepared` as a faithful reading of `source`; [] means it passes."""
    src, prep = words(source), words(prepared)
    problems = _check_substitutions(src, substitutions)
    if problems:
        return problems
    pairs = [(tuple(words(s.original)), tuple(words(s.spoken))) for s in substitutions]
    applied = _align(src, prep, pairs)
    if applied is None:
        return ["could not align the prepared text with the article (too many alternatives)"]
    if applied is False:
        return _describe(src, prep)
    return _check_added_words(src, substitutions, pairs, applied)


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
        if len(sp) > MAX_SPOKEN_WORDS or len(sp) > len(o) + MAX_EXTRA_WORDS:
            problems.append(f"{label} is too long: at most {MAX_SPOKEN_WORDS} spoken words, "
                            f"and at most {MAX_EXTRA_WORDS} more than the original")
        if not _contains(src, o):
            problems.append(f"{label}: the original does not appear in the article")
    return problems


def _contains(seq: list[str], sub: list[str]) -> bool:
    n = len(sub)
    return any(seq[i:i + n] == sub for i in range(len(seq) - n + 1))


def _check_added_words(src, subs, pairs, applied):
    """Across the whole text, the applied substitutions may add only a few words."""
    limit = added_words_limit(len(src))
    added = [n * max(0, len(sp) - len(o)) for n, (o, sp) in zip(applied, pairs)]
    total = sum(added)
    if total <= limit:
        return []
    detail = "; ".join(
        f"substitution {s.original!r} -> {s.spoken!r} is applied {n} time{'s' if n != 1 else ''} "
        f"and adds {a} words" for s, n, a in zip(subs, applied, added) if a)
    return [f"the substitutions add {total} words to the article's {len(src)}; the limit is {limit}: "
            f"{detail}. Shorten or drop them"]


def _align(src, prep, pairs):
    """Align prep to src: prep must be src with some occurrences of each pair's
    original replaced by its spoken form. Returns how many occurrences each pair
    is applied to, for the alignment that adds the fewest words; False if there
    is none; None if the search grows past a bound instead of running away.

    Explores all alternatives (prefix-sharing pairs need it). Every step moves
    forward in both texts, so settling states in (i, j) order settles each one
    after all of its predecessors, and the cheapest path to each is exact."""
    n, m = len(src), len(prep)
    limit = _MAX_STATES_FACTOR * (n + m) + 1000
    extra = [max(0, len(sp) - len(o)) for o, sp in pairs]
    best = {(0, 0): (0, None, None)}  # state -> (words added so far, previous state, pair index)
    heap, done = [(0, 0)], set()
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
    return False


def _describe(src, prep, max_items=3, max_words=12):
    out = []
    ops = difflib.SequenceMatcher(None, src, prep, autojunk=False).get_opcodes()
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            continue
        a = " ".join(src[i1:i2][:max_words]) or "(nothing)"
        b = " ".join(prep[j1:j2][:max_words]) or "(nothing)"
        out.append(f"near word {i1 + 1}: the article has '{a}' where the prepared text has '{b}'")
        if len(out) == max_items:
            break
    return out
