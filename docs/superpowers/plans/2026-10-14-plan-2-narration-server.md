# Plan 2: Narration Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The `narration` MCP server. It runs on the Mac, owns every article's work folder, and gives an agent session exactly six tools. It gives the runner its own tools and a download route, and it decides with deterministic code whether a recording may be published.

**Architecture:** A new package `narration/` in kokoro-reader:
- **Pure logic, testable anywhere with fakes:** word preservation (it borrows `clean_markdown_text` from `text_to_speech.py`, so it sees exactly what the engine will speak), work items, the agent tools, and the gate.
- **Thin adapters** over the existing `text_to_speech.py`, `verify_audio.py`, `convert_audio.py` and misaki.
- **One Starlette app** that mounts two MCP 2.x servers (`/agent/mcp`, `/runner/mcp`) behind a bearer-token middleware.

Agent tokens are per work item: the runner's `open` call mints one, and `finish` revokes it.

**Tech Stack:**
- Python ≥3.10;
- `mcp==2.3.0`, where FastMCP is renamed `MCPServer`, in `mcp.server.mcpserver`;
- Starlette and uvicorn, which come with `mcp`;
- pydantic, for tool argument models;
- pytest and Hypothesis.

**Spec:** `docs/superpowers/specs/2026-10-14-unattended-podcast-pipeline-design.md`: "The narration server (MCP)", "The agent session's contract", "Quality gate". Also read `docs/superpowers/plans/2026-10-14-unattended-pipeline-roadmap.md`, both for the per-item token refinement and for spike S1's result.

## Global Constraints

- **No name contains a TTS model's name.** The package is `narration`, the launchd label is `net.robergb.narration`, and the env vars are `NARRATION_*`.
- **The agent never handles a path.** No agent tool takes a file path or a work-item id; the item comes from the caller's token.
- **At most 4 `record` calls per work item.** The server enforces this and counts the call **before** running the engine, so a crash can't buy extra rounds.
- **At most 8 `check` calls per work item.** Each one costs a Gemini call, about 3 cents.
- **`finish` validates its input and runs the claim checks and the word-preservation check.** A refusal is an error the agent can act on. `finalize` (runner) re-runs the same checks and is authoritative.
- **Raise the SDK's `ToolError` for every refusal the agent must read** (spike S1, 2026-10-14). In MCP SDK 2.3.0, a plain exception reaches the session only as `"Error executing tool <name>"`, with the message withheld. Only `mcp.server.mcpserver.exceptions.ToolError` passes its text through. `narration.agent_tools.ToolError` stays a plain exception, so the pure modules don't import the SDK, and `app.py` converts it. `app.py` converts the engines' `ValueError` and `RuntimeError` the same way: `verify_audio` refuses a single call over 15 minutes with a `ValueError` whose message says to add `[BREAK]` markers, and the agent must read that remediation, not a bare "Error executing tool check".
- **Fail fast at startup without `GEMINI_API_KEY`.** With real engines, `python -m narration` exits before it serves if the key is in neither the environment nor `.env`. `--fake-engines` never needs it.
- **The SDK's HTTP client is `httpx2`, not `httpx`** (S1). Starlette's `TestClient` still needs `httpx`, so both are present, and that's expected.
- **Keep the CLIs working.** `text_to_speech.py`, `verify_audio.py` and `convert_audio.py` stay independently callable (ADR-001).
- **Red/green TDD,** and commit each step separately.
- **Testing without the Apple Silicon stack.** The package's tests must not import `mlx_audio`, `torch` or `spacy`. Run them with:

  ```bash
  uv run --no-project --with "mcp==2.3.0" --with uvicorn --with httpx --with hypothesis --with pytest \
    --with numpy --with soundfile --with pydub --with google-genai --with python-dotenv \
    python -m pytest tests/narration tests/test_verify_audio.py -q
  ```

  This works in Linux containers, where `uv sync` of the full project would pull in torch and CUDA. Call this the **light test command** below. It includes numpy, soundfile and pydub because `verify_audio.py` and `narration/words.py` import `text_to_speech.py`, whose `mlx_audio` import is guarded.

## Review Focus

1. **A declared substitution used to smuggle in words.** For example, `"the" → "buy crypto now at"`. The limits must refuse it: the spoken side is at most 6 words and at most 3 more than the original, and the original must appear in the article. Test in Task 2.
2. **A substitution whose spoken form starts with the same words as the original.** `"Pope Leo XIV" → "Pope Leo the Fourteenth"` must still align; a greedy matcher would fail it. Test in Task 2.
3. **`replace_text` whose `old` appears twice, or not at all.** It must refuse, change nothing, and say how many matches it found, like Claude Code's Edit tool. Test in Task 4.
4. **A token reused after `finish`.** Every agent tool must refuse it, and the HTTP layer must return 401. Test in Tasks 4 and 7.
5. **A request whose Host header isn't on the allow-list,** for example a DNS-rebinding page in a browser on the tailnet. It must be refused. Test in Task 7.

---

### Task 1: Package skeleton, dependencies, and `precheck_warnings`

**Files:**
- Modify: `pyproject.toml` (deps), `uv.lock` (regenerated with `uv lock`)
- Modify: `verify_audio.py` (extract `precheck_warnings`, and make `precheck_report` use it)
- Create: `narration/__init__.py`, `tests/__init__.py`, `tests/narration/__init__.py` (all empty)
- Test: `tests/test_verify_audio.py`

**Interfaces:**
- Produces: `verify_audio.precheck_warnings(audio: AudioSegment, source_text: str) -> list[str]`, the same `WARNING:` texts `precheck_report` prints, without the prefix.

- [ ] **Step 1: Write the failing test** (append to `tests/test_verify_audio.py`; `make_segment` already exists there):

```python
from verify_audio import precheck_warnings


def test_precheck_warnings_lists_pace_and_long_silence():
    audio = make_segment([("tone", 1000), ("silence", 6000), ("tone", 1000)])
    text = " ".join(["word"] * 400)  # far too many words for 8 s of audio
    warnings = precheck_warnings(audio, text)
    assert any("WPM" in w for w in warnings)
    assert any("of silence at" in w for w in warnings)


def test_precheck_warnings_empty_for_normal_audio():
    audio = make_segment([("tone", 60000)])
    text = " ".join(["word"] * 160)  # 160 WPM
    assert precheck_warnings(audio, text) == []
```

- [ ] **Step 2: Run them and see them fail.** Run the light test command, with `-k precheck_warnings`. Expected: `ImportError: cannot import name 'precheck_warnings'`.
- [ ] **Step 3: Implement.** Refactor `precheck_report` in `verify_audio.py` into:

```python
def precheck_warnings(audio: AudioSegment, source_text: str) -> list:
    """Deterministic warnings: pace outside 100-250 WPM, or silences over 5 s."""
    duration_ms = len(audio)
    word_count = len(re.sub(r"\[BREAK\]", " ", source_text).split())
    wpm = word_count / (duration_ms / 60000) if duration_ms else 0
    warnings = []
    if not 100 <= wpm <= 250:
        warnings.append(
            f"pace of {wpm:.0f} WPM is outside the normal speech "
            "range (100-250) — audio may be truncated or have extra content."
        )
    for start_ms, end_ms in detect_silence(
        audio, min_silence_len=5000, silence_thresh=-40, seek_step=100
    ):
        warnings.append(
            f"{format_timestamp(end_ms - start_ms)} of silence at "
            f"{format_timestamp(start_ms)} — far longer than a [BREAK] pause."
        )
    return warnings


def precheck_report(audio: AudioSegment, source_text: str) -> str:
    """Deterministic sanity checks that run before any model call."""
    duration_ms = len(audio)
    word_count = len(re.sub(r"\[BREAK\]", " ", source_text).split())
    wpm = word_count / (duration_ms / 60000) if duration_ms else 0
    lines = [
        "## Pre-check (deterministic)",
        f"Duration: {format_timestamp(duration_ms)} | "
        f"{word_count} words | {wpm:.0f} WPM",
    ]
    lines += [f"WARNING: {w}" for w in precheck_warnings(audio, source_text)]
    return "\n".join(lines)
```

- [ ] **Step 4: Run them and see them pass.** Run the light test command. Expected: all of `tests/test_verify_audio.py` passes. The existing `precheck_report` tests prove its output text didn't change.
- [ ] **Step 5: Add the dependencies and the package.** In `pyproject.toml`:
  - add `"mcp==2.3.0"` and `"uvicorn>=0.30"` to `dependencies`;
  - add `"hypothesis"` and `"httpx"` to the `dev` group.

  Then run `uv lock`, which resolves only and installs nothing. Create the empty `narration/__init__.py`, `tests/__init__.py` and `tests/narration/__init__.py`.

  `tests/__init__.py` is not optional. `tests/` has no `__init__.py` today, and in pytest's default import mode a `tests/narration/__init__.py` alone makes `tests/` the package root, so the test modules import as `narration.test_*` and that `narration` shadows the real package: every Plan 2 test then dies at collection with `ModuleNotFoundError: No module named 'narration.words'`. With `tests/__init__.py` they import as `tests.narration.test_*`. The existing tests collect and pass unchanged with it (checked 2026-10-14).
- [ ] **Step 6: Commit as two commits:**
  - "verify_audio: expose precheck_warnings";
  - "narration: package skeleton and MCP dependencies".

---

### Task 2: Word preservation (`narration/words.py`)

**Files:**
- Create: `narration/words.py`
- Test: `tests/narration/test_words.py`

**Interfaces:**
- Produces:
  - `Substitution(original: str, spoken: str)`, a frozen dataclass;
  - `words(text: str) -> list[str]`: the words the engine will speak, case-folded. It runs `text_to_speech.clean_markdown_text` first, the same cleaning the CLI and `KokoroEngine` (Task 4) apply before generating audio, then drops `[BREAK]` markers and reduces phonetic overrides to their word;
  - `check(source: str, prepared: str, substitutions: list[Substitution]) -> list[str]`, which returns problems; an empty list means it passes;
  - `MAX_SUBSTITUTIONS = 25`, `MAX_SPOKEN_WORDS = 6`, `MAX_EXTRA_WORDS = 3`.

- [ ] **Step 1: Write the failing tests:**

```python
from hypothesis import given, settings, strategies as st

from narration.words import Substitution, check, words

ARTICLE = "Pope Leo XIV met the Pengelley family in State College.\n\nIt went well -- mostly."


def test_formatting_pauses_and_overrides_are_invisible():
    prepared = ("Pope Leo XIV met the [Pengelley](/pɛnʤˈɛli/) family, in State College.\n\n"
                "[BREAK]\n\nIt went well... mostly.")
    assert check(ARTICLE, prepared, []) == []


def test_undeclared_word_change_is_a_problem():
    prepared = ARTICLE.replace("well", "badly")
    problems = check(ARTICLE, prepared, [])
    assert problems and "well" in problems[0] and "badly" in problems[0]


def test_declared_substitution_passes():
    prepared = ARTICLE.replace("XIV", "the Fourteenth")
    assert check(ARTICLE, prepared, [Substitution("XIV", "the Fourteenth")]) == []


def test_substitution_sharing_a_prefix_still_aligns():
    prepared = ARTICLE.replace("Pope Leo XIV", "Pope Leo the Fourteenth")
    subs = [Substitution("Pope Leo XIV", "Pope Leo the Fourteenth")]
    assert check(ARTICLE, prepared, subs) == []


def test_smuggling_words_through_a_substitution_is_refused():
    prepared = ARTICLE.replace("the Pengelley", "buy crypto now at the Pengelley")
    problems = check(ARTICLE, prepared, [Substitution("the", "buy crypto now at the")])
    assert any("too long" in p for p in problems)


def test_substitution_original_must_be_in_the_article():
    problems = check(ARTICLE, ARTICLE, [Substitution("Hamilton", "Ham ill ton")])
    assert any("does not appear in the article" in p for p in problems)


def test_too_many_substitutions():
    subs = [Substitution("Pope", f"Pope{i}") for i in range(26)]
    assert any("limit" in p for p in check(ARTICLE, ARTICLE, subs))


def test_words_normalises_quotes_case_and_breaks():
    assert words("It’s [BREAK] THE end") == ["it's", "the", "end"]


def test_heading_markers_and_link_urls_are_not_words():
    # The CLI speaks a heading's text and a link's text, never the '#' or the
    # URL, and KokoroEngine (Task 4) applies the same cleaning. words() must agree.
    article = "# Zoning Alone\n\nSee the [court](https://example.com/zoning) **today**."
    assert words(article) == ["zoning", "alone", "see", "the", "court", "today"]
    prepared = "Zoning Alone\n\n[BREAK]\n\nSee the court today."
    assert check(article, prepared, []) == []


# ---- properties ----
VOCAB = ["the", "pope", "leo", "zoning", "alone", "court", "may", "end", "it", "and",
         "city", "in", "mars", "life", "is", "there", "neighbors", "xiv"]
SPOKEN = ["fourteenth", "saint", "doctor", "number", "one"]
paragraphs = st.lists(st.lists(st.sampled_from(VOCAB), min_size=1, max_size=10),
                      min_size=1, max_size=8)


def render(paras):
    return "\n\n".join(" ".join(p).capitalize() + "." for p in paras)


@st.composite
def formatted(draw, paras):
    """Re-render paragraphs with allowed edits only: splits, [BREAK], commas,
    em dashes, phonetic overrides."""
    out = []
    for p in paras:
        words_ = list(p)
        if draw(st.booleans()) and len(words_) > 1:
            k = draw(st.integers(1, len(words_) - 1))
            chunks = [words_[:k], words_[k:]]
        else:
            chunks = [words_]
        for c in chunks:
            rendered = []
            for w in c:
                choice = draw(st.integers(0, 5))
                if choice == 0:
                    rendered.append(f"[{w}](/fəˈnɛt/)")
                elif choice == 1:
                    rendered.append(w + ",")
                elif choice == 2:
                    rendered.append(w + " --")
                else:
                    rendered.append(w)
            out.append(" ".join(rendered) + ".")
        if draw(st.booleans()):
            out.append("[BREAK]")
    return "\n\n".join(out)


@settings(max_examples=300)
@given(st.data(), paragraphs)
def test_property_allowed_edits_and_declared_substitutions_pass(data, paras):
    present = sorted({w for p in paras for w in p})
    chosen = data.draw(st.lists(st.sampled_from(present), unique=True, max_size=3))
    subs, mapping = [], {}
    for w in chosen:
        spoken = " ".join(data.draw(st.lists(st.sampled_from(SPOKEN), min_size=1, max_size=3)))
        subs.append(Substitution(w, spoken))
        mapping[w] = spoken
    # Apply each substitution to a random subset of its occurrences.
    replaced = [[(mapping[w] if w in mapping and data.draw(st.booleans()) else w) for w in p]
                for p in paras]
    replaced = [" ".join(p).split() for p in replaced]
    prepared = data.draw(formatted(replaced))
    assert check(render(paras), prepared, subs) == []


@settings(max_examples=300)
@given(st.data(), paragraphs)
def test_property_any_undeclared_word_change_fails(data, paras):
    mutated = [list(p) for p in paras]
    pi = data.draw(st.integers(0, len(mutated) - 1))
    p = mutated[pi]
    kind = data.draw(st.sampled_from(["insert", "delete", "replace"]))
    k = data.draw(st.integers(0, len(p) - 1))
    if kind == "insert":
        p.insert(k, data.draw(st.sampled_from(VOCAB)))
    elif kind == "delete":
        del p[k]
    else:
        p[k] = data.draw(st.sampled_from([w for w in VOCAB if w != p[k]]))
    mutated = [m for m in mutated if m]
    prepared = data.draw(formatted(mutated)) if mutated else ""
    assert check(render(paras), prepared, []) != []
```

- [ ] **Step 2: Run them and see them fail.** Run the light test command, with `-k words`. Expected: `ModuleNotFoundError: No module named 'narration.words'`.
- [ ] **Step 3: Implement** `narration/words.py`:

```python
"""Word-preservation check (spec: "Quality gate", step 2).

The prepared text must say exactly what the article says. Formatting,
paragraph breaks, pauses, [BREAK] markers and phonetic overrides are invisible
here. Markdown is stripped by text_to_speech.clean_markdown_text, the same
function the CLI and KokoroEngine run before generating audio, so this check
sees exactly the words that will be spoken. Every change to the words
themselves must be declared as a Substitution, and declared substitutions are
bounded so they can't carry new content.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass

from text_to_speech import clean_markdown_text

MAX_SUBSTITUTIONS = 25
MAX_SPOKEN_WORDS = 6
MAX_EXTRA_WORDS = 3
_MAX_STATES_FACTOR = 50

_PHONETIC = re.compile(r"\[([^\]]+)\]\(/[^)]*/\)")
_TOKEN = re.compile(r"[^\W_]+(?:'[^\W_]+)*")


@dataclass(frozen=True)
class Substitution:
    original: str  # the article's words
    spoken: str    # what the prepared text says in their place


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
    aligned = _aligns(src, prep, pairs)
    if aligned is True:
        return []
    if aligned is None:
        return ["could not align the prepared text with the article (too many alternatives)"]
    return _describe(src, prep)


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


def _aligns(src, prep, pairs):
    """True if prep is src with some occurrences of each pair's original replaced
    by its spoken form. Explores all alternatives (prefix-sharing pairs need it);
    returns None if the search grows past a bound instead of running away."""
    n, m = len(src), len(prep)
    limit = _MAX_STATES_FACTOR * (n + m) + 1000
    stack, seen = [(0, 0)], set()
    while stack:
        i, j = stack.pop()
        if (i, j) in seen:
            continue
        seen.add((i, j))
        if len(seen) > limit:
            return None
        if i == n and j == m:
            return True
        if i < n and j < m and src[i] == prep[j]:
            stack.append((i + 1, j + 1))
        for o, s in pairs:
            if tuple(src[i:i + len(o)]) == o and tuple(prep[j:j + len(s)]) == s:
                stack.append((i + len(o), j + len(s)))
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
```

- [ ] **Step 4: Run them and see them pass.** Run the light test command, with `-k words`. Expected: all pass, including 300 examples of each property. If a property fails, Hypothesis prints the minimal example. Fix the code, not the property, unless the example shows the property itself is wrong; if it does, say so in the commit message.
- [ ] **Step 5: Commit:** "narration: word-preservation check, with property tests".

---

### Task 3: Work items (`narration/workitems.py`)

**Files:**
- Create: `narration/workitems.py`
- Test: `tests/narration/test_workitems.py`

**Interfaces:**
- Produces: `WorkItemStore(root: Path)`, with:
  - `.open(article_text: str, metadata: dict) -> tuple[str, str]`, which returns `(item_id, agent_token)`;
  - `.item_for_token(token: str) -> str | None`;
  - `.revoke(item_id: str) -> None`;
  - `.path(item_id: str, name: str) -> Path`;
  - `.read_text(item_id, name) -> str` and `.write_text(item_id, name, text) -> None` (atomic);
  - `.load_state(item_id) -> dict` and `.save_state(item_id, state: dict) -> None`;
  - `.exists(item_id) -> bool`.
- Produces: `FILES = {"source.md", "prepared.md", "metadata.json", "state.json", "recording.wav", "episode.mp3", "result.json"}`, plus `report-<n>.txt` for n = 1–8. `.path()` refuses any other name, or an id that isn't 12 lowercase hex characters.
- Produces: the initial state, `{"status": "open", "records": 0, "checks": [], "finish": None}`.

- [ ] **Step 1: Write the failing tests:**

```python
import json
import pytest

from narration.workitems import WorkItemStore


@pytest.fixture
def store(tmp_path):
    return WorkItemStore(tmp_path)


def test_open_creates_files_and_a_working_token(store):
    item_id, token = store.open("Some text.", {"title": "T"})
    assert len(item_id) == 12
    assert store.read_text(item_id, "source.md") == "Some text."
    assert store.read_text(item_id, "prepared.md") == "Some text."
    assert json.loads(store.read_text(item_id, "metadata.json")) == {"title": "T"}
    assert store.load_state(item_id)["status"] == "open"
    assert store.item_for_token(token) == item_id


def test_tokens_are_distinct_and_revocable(store):
    a, ta = store.open("a", {})
    b, tb = store.open("b", {})
    assert ta != tb and store.item_for_token(tb) == b
    store.revoke(a)
    assert store.item_for_token(ta) is None
    assert store.item_for_token(tb) == b


def test_unknown_token(store):
    assert store.item_for_token("nope") is None
    assert store.item_for_token("") is None


@pytest.mark.parametrize("bad_id", ["../x", "ABCDEF123456", "abc", "a" * 13])
def test_path_refuses_bad_ids(store, bad_id):
    with pytest.raises(ValueError):
        store.path(bad_id, "source.md")


@pytest.mark.parametrize("bad_name", ["../state.json", "other.txt", "report-9.txt", "report-0.txt"])
def test_path_refuses_unknown_names(store, bad_name):
    item_id, _ = store.open("x", {})
    with pytest.raises(ValueError):
        store.path(item_id, bad_name)


def test_report_names_one_to_eight_allowed(store):
    item_id, _ = store.open("x", {})
    assert store.path(item_id, "report-8.txt").name == "report-8.txt"


def test_tokens_file_holds_only_hashes(store, tmp_path):
    _, token = store.open("x", {})
    assert token not in (tmp_path / "tokens.json").read_text()
```

- [ ] **Step 2: Run them and see them fail.** Run the light test command, with `-k workitems`. Expected: `ModuleNotFoundError`.
- [ ] **Step 3: Implement** `narration/workitems.py`:

```python
"""Work items: one folder per article, owned by the narration server.

The agent never names a path or an item: it holds a per-item token, which
this store maps to an item id. Only token hashes are written to disk.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
from pathlib import Path

ID_RE = re.compile(r"^[0-9a-f]{12}$")
REPORT_RE = re.compile(r"^report-[1-8]\.txt$")
FILES = {"source.md", "prepared.md", "metadata.json", "state.json",
         "recording.wav", "episode.mp3", "result.json"}
INITIAL_STATE = {"status": "open", "records": 0, "checks": [], "finish": None}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class WorkItemStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        (self.root / "items").mkdir(parents=True, exist_ok=True)

    # -- tokens --
    def _tokens(self) -> dict:
        p = self.root / "tokens.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def _save_tokens(self, tokens: dict) -> None:
        _atomic_write(self.root / "tokens.json", json.dumps(tokens).encode())

    def item_for_token(self, token: str):
        if not token:
            return None
        return self._tokens().get(_hash(token))

    def revoke(self, item_id: str) -> None:
        tokens = {h: i for h, i in self._tokens().items() if i != item_id}
        self._save_tokens(tokens)

    # -- items --
    def open(self, article_text: str, metadata: dict):
        item_id = secrets.token_hex(6)
        token = secrets.token_urlsafe(32)
        self.write_text(item_id, "source.md", article_text)
        self.write_text(item_id, "prepared.md", article_text)
        self.write_text(item_id, "metadata.json", json.dumps(metadata))
        self.save_state(item_id, dict(INITIAL_STATE))
        tokens = self._tokens()
        tokens[_hash(token)] = item_id
        self._save_tokens(tokens)
        return item_id, token

    def exists(self, item_id: str) -> bool:
        return bool(ID_RE.match(item_id or "")) and (self.root / "items" / item_id).is_dir()

    def path(self, item_id: str, name: str) -> Path:
        if not ID_RE.match(item_id or ""):
            raise ValueError(f"bad work item id: {item_id!r}")
        if name not in FILES and not REPORT_RE.match(name):
            raise ValueError(f"unknown work item file: {name!r}")
        return self.root / "items" / item_id / name

    def read_text(self, item_id: str, name: str) -> str:
        return self.path(item_id, name).read_text(encoding="utf-8")

    def write_text(self, item_id: str, name: str, text: str) -> None:
        _atomic_write(self.path(item_id, name), text.encode("utf-8"))

    def load_state(self, item_id: str) -> dict:
        return json.loads(self.read_text(item_id, "state.json"))

    def save_state(self, item_id: str, state: dict) -> None:
        self.write_text(item_id, "state.json", json.dumps(state))
```

- [ ] **Step 4: Run them and see them pass.** Run the light test command, with `-k workitems`.
- [ ] **Step 5: Commit:** "narration: work-item store with per-item tokens".

---

### Task 4: Engines and the agent tools (`narration/engines.py`, `narration/fakes.py`, `narration/agent_tools.py`)

**Files:**
- Create: `narration/engines.py` (Protocols plus real adapters, with imports inside the methods)
- Create: `narration/fakes.py` (fake engines for tests and for `--fake-engines`)
- Create: `narration/agent_tools.py`
- Test: `tests/narration/test_agent_tools.py`, `tests/narration/test_engines.py`

**Interfaces:**
- Consumes: `WorkItemStore` (Task 3); `words.check` and `Substitution` (Task 2); `verify_audio.precheck_warnings` (Task 1), used by the real verifier only; `text_to_speech.clean_markdown_text` and `generate_audio`, used by `KokoroEngine` only.
- Produces: the Protocols
  - `TTSEngine.record(text: str, out_path: Path, voice: str, speed: float) -> float` (seconds);
  - `Verifier.check(audio_path: Path, text: str) -> tuple[str, list[str]]` (the report, then the pre-check warnings);
  - `G2P.phonemes(word: str) -> str`;
  - `Converter.to_mp3(wav: Path, mp3: Path, title: str, artist: str, album: str) -> None`.
- Produces: the real adapters `KokoroEngine`, `GeminiVerifier`, `MisakiG2P` and `Mp3Converter`, and the fakes `FakeTTS`, `FakeVerifier`, `FakeG2P` and `FakeConverter`.
- Produces: `ToolError(Exception)`; `MAX_RECORDS = 4`; `MAX_CHECKS = 8`.
- Produces: `AgentTools(store, tts, verifier, g2p, default_voice="af_heart")`, with these methods, each taking `item_id: str` first:
  - `get_text() -> dict`;
  - `replace_text(old, new) -> dict`;
  - `record(voice=None, speed=1.0) -> dict`;
  - `check() -> dict`;
  - `phonemes(word) -> dict`;
  - `finish(outcome, remaining_issues: list[dict], substitutions: list[dict]) -> dict`.

  Shapes:
  - an issue is `{"kind": str, "where": str, "note": str}`;
  - a substitution is `{"original": str, "spoken": str, "why": str}`.

- [ ] **Step 1: Write the failing tests:**

```python
import pytest
from hypothesis import given, settings, strategies as st

from narration.agent_tools import MAX_RECORDS, AgentTools, ToolError
from narration.fakes import FakeG2P, FakeTTS, FakeVerifier
from narration.workitems import WorkItemStore

ARTICLE = "Pope Leo XIV met the family.\n\nIt went well."


@pytest.fixture
def env(tmp_path):
    store = WorkItemStore(tmp_path)
    tts, ver = FakeTTS(), FakeVerifier()
    tools = AgentTools(store, tts, ver, FakeG2P())
    item, token = store.open(ARTICLE, {"title": "T"})
    return store, tools, tts, ver, item, token


def test_get_text(env):
    _, tools, *_rest = env
    item = env[4]
    out = tools.get_text(item)
    assert out["prepared_text"] == ARTICLE and out["records_left"] == MAX_RECORDS


def test_replace_text_exactly_once(env):
    store, tools, *_, item, _ = env
    tools.replace_text(item, "It went well.", "It went\n\nwell.")
    assert "It went\n\nwell." in store.read_text(item, "prepared.md")
    with pytest.raises(ToolError, match="found 0"):
        tools.replace_text(item, "absent", "x")
    store.write_text(item, "prepared.md", "a a")
    with pytest.raises(ToolError, match="found 2"):
        tools.replace_text(item, "a", "b")
    assert store.read_text(item, "prepared.md") == "a a"


def test_record_limit_counts_before_the_engine(env):
    store, tools, tts, *_ = env
    item = env[4]
    for _ in range(MAX_RECORDS):
        tools.record(item)
    with pytest.raises(ToolError, match="limit"):
        tools.record(item)
    assert tts.calls == MAX_RECORDS


def test_record_counts_even_when_the_engine_fails(env):
    store, tools, tts, *_ = env
    item = env[4]
    tts.fail_next = True
    with pytest.raises(Exception):
        tools.record(item)
    assert store.load_state(item)["records"] == 1


def test_record_rejects_odd_voices_and_speeds(env):
    _, tools, *_ = env
    item = env[4]
    with pytest.raises(ToolError):
        tools.record(item, voice="../../etc")
    with pytest.raises(ToolError):
        tools.record(item, speed=9.0)


def test_check_needs_a_recording_and_saves_reports(env):
    store, tools, _, ver, item, _ = env
    with pytest.raises(ToolError, match="record"):
        tools.check(item)
    tools.record(item)
    out = tools.check(item)
    assert out["report"] == "No issues found."
    assert store.read_text(item, "report-1.txt") == "No issues found."


def test_finish_requires_a_check_on_the_latest_recording(env):
    _, tools, *_rest = env
    item = env[4]
    tools.record(item); tools.check(item); tools.record(item)
    with pytest.raises(ToolError, match="latest recording"):
        tools.finish(item, "clean", [], [])


def test_finish_refuses_clean_with_precheck_warnings(env):
    _, tools, _, ver, item, _ = env
    ver.warnings = ["pace of 40 WPM is outside the normal speech range"]
    tools.record(item); tools.check(item)
    with pytest.raises(ToolError, match="pre-check"):
        tools.finish(item, "clean", [], [])
    assert tools.finish(item, "hold", [{"kind": "truncated", "where": "end", "note": "short"}], [])["ok"]


def test_finish_refuses_undeclared_changes_and_accepts_declared(env):
    store, tools, *_ = env
    item = env[4]
    tools.replace_text(item, "XIV", "the Fourteenth")
    tools.record(item); tools.check(item)
    with pytest.raises(ToolError, match="differs from the article"):
        tools.finish(item, "clean", [], [])
    out = tools.finish(item, "clean", [], [{"original": "XIV", "spoken": "the Fourteenth", "why": "numeral"}])
    assert out["ok"]


def test_finish_revokes_the_token_and_closes_the_item(env):
    store, tools, _, _, item, token = env
    tools.record(item); tools.check(item)
    tools.finish(item, "clean", [], [])
    assert store.item_for_token(token) is None
    for call in (lambda: tools.get_text(item), lambda: tools.record(item),
                 lambda: tools.replace_text(item, "It", "It"), lambda: tools.check(item)):
        with pytest.raises(ToolError, match="finished"):
            call()


def test_finish_validates_shapes(env):
    _, tools, *_ = env
    item = env[4]
    tools.record(item); tools.check(item)
    with pytest.raises(ToolError):
        tools.finish(item, "great", [], [])
    with pytest.raises(ToolError):
        tools.finish(item, "minor", [{"kind": "x" * 500, "where": "", "note": ""}], [])


@settings(max_examples=100, deadline=None)
@given(old=st.text(min_size=1, max_size=40), new=st.text(max_size=40))
def test_property_replace_text_never_touches_another_item(tmp_path_factory, old, new):
    store = WorkItemStore(tmp_path_factory.mktemp("s"))
    tools = AgentTools(store, FakeTTS(), FakeVerifier(), FakeG2P())
    a, _ = store.open("alpha beta gamma", {})
    b, _ = store.open("alpha beta gamma", {})
    before = {n: store.read_text(b, n) for n in ("source.md", "prepared.md", "metadata.json", "state.json")}
    try:
        tools.replace_text(a, old, new)
    except ToolError:
        pass
    assert {n: store.read_text(b, n) for n in before} == before
    assert store.read_text(a, "source.md") == "alpha beta gamma"


@settings(max_examples=100, deadline=None)
@given(calls=st.integers(0, 12))
def test_property_engine_runs_at_most_max_records(tmp_path_factory, calls):
    store = WorkItemStore(tmp_path_factory.mktemp("s"))
    tts = FakeTTS()
    tools = AgentTools(store, tts, FakeVerifier(), FakeG2P())
    item, _ = store.open("x", {})
    for _ in range(calls):
        try:
            tools.record(item)
        except ToolError:
            pass
    assert tts.calls == min(calls, MAX_RECORDS)
```

  And `tests/narration/test_engines.py`, for the one adapter whose input can be checked without the model. `KokoroEngine.record` imports `generate_audio` from `text_to_speech` at call time, so patching the module attribute is enough:

```python
import numpy as np
import pytest
import soundfile as sf

import text_to_speech
from narration.engines import KokoroEngine


def test_kokoro_engine_cleans_markdown_like_the_cli(tmp_path, monkeypatch):
    seen = {}

    def fake_generate_audio(text, output_path, voice="af_heart", speed=1.0, **_):
        seen.update(text=text, voice=voice, speed=speed)
        sf.write(output_path, np.zeros(2400, dtype="float32"), 24000)  # 0.1 s of silence

    monkeypatch.setattr(text_to_speech, "generate_audio", fake_generate_audio)
    out = tmp_path / "recording.wav"
    seconds = KokoroEngine().record(
        "# Zoning Alone\n\nSee the [court](https://example.com/zoning) **today**, "
        "said [Pengelley](/pɛnʤˈɛli/).", out, "af_heart", 1.0)
    # The heading marker, the URL and the emphasis are gone; the phonetic override stays.
    assert seen["text"] == "Zoning Alone\n\nSee the court today, said [Pengelley](/pɛnʤˈɛli/)."
    assert (seen["voice"], seen["speed"]) == ("af_heart", 1.0)
    assert seconds == pytest.approx(0.1, abs=0.01)
```

- [ ] **Step 2: Run them and see them fail.** Run the light test command, with `-k "agent_tools or engines"`. Expected: `ModuleNotFoundError`.
- [ ] **Step 3: Implement** `narration/engines.py`:

```python
"""Engine interfaces and the real adapters over the existing toolkit.

Imports of the heavy toolkit modules happen inside methods, so this module
(and everything that imports it) stays importable without MLX or Gemini.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol


class TTSEngine(Protocol):
    def record(self, text: str, out_path: Path, voice: str, speed: float) -> float: ...


class Verifier(Protocol):
    def check(self, audio_path: Path, text: str) -> tuple[str, list[str]]: ...


class G2P(Protocol):
    def phonemes(self, word: str) -> str: ...


class Converter(Protocol):
    def to_mp3(self, wav: Path, mp3: Path, title: str, artist: str, album: str) -> None: ...


class KokoroEngine:
    def record(self, text, out_path, voice, speed):
        from pydub import AudioSegment
        from text_to_speech import clean_markdown_text, generate_audio
        # The CLI's chain for a .md file: clean_markdown_text, then generate_audio
        # (which only runs prepare_for_tts). Without the cleaning, headings,
        # emphasis markers and link URLs are read aloud, and words.check can't
        # catch it because it strips exactly that markup.
        generate_audio(clean_markdown_text(text), str(out_path), voice=voice, speed=speed)
        return len(AudioSegment.from_file(out_path)) / 1000


class GeminiVerifier:
    def check(self, audio_path, text):
        from pydub import AudioSegment
        from verify_audio import precheck_warnings, verify_audio
        warnings = precheck_warnings(AudioSegment.from_file(audio_path), text)
        return verify_audio(str(audio_path), text), warnings


class MisakiG2P:
    def __init__(self):
        from misaki import en
        self._g2p = en.G2P()

    def phonemes(self, word):
        out = self._g2p(word)
        return out[0] if isinstance(out, tuple) else str(out)


class Mp3Converter:
    def to_mp3(self, wav, mp3, title, artist, album):
        from convert_audio import convert_to_mp3
        convert_to_mp3(str(wav), str(mp3), title=title, artist=artist, album=album)
```

  Before relying on it, check `convert_to_mp3`'s parameter names against `convert_audio.py:13`, and adjust the keyword names if they differ.

  Then `narration/fakes.py`:

```python
"""Fake engines for tests and for `python -m narration --fake-engines`."""
from __future__ import annotations

from pathlib import Path


class FakeTTS:
    def __init__(self):
        self.calls = 0
        self.fail_next = False

    def record(self, text, out_path, voice, speed):
        self.calls += 1
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("fake engine failure")
        Path(out_path).write_bytes(b"RIFF fake wav")
        return 60.0


class FakeVerifier:
    def __init__(self):
        self.report = "No issues found."
        self.warnings: list[str] = []

    def check(self, audio_path, text):
        return self.report, list(self.warnings)


class FakeG2P:
    def phonemes(self, word):
        return "fəˈnɛt"


class FakeConverter:
    def __init__(self):
        self.calls = []

    def to_mp3(self, wav, mp3, title, artist, album):
        self.calls.append((title, artist, album))
        Path(mp3).write_bytes(b"ID3 fake mp3")
```

  Then `narration/agent_tools.py`:

```python
"""The six tools an agent session has. Plain functions over a work item;
the HTTP layer resolves the item from the caller's token (agent never names it)."""
from __future__ import annotations

import json
import re

from narration.words import Substitution, check as words_check

MAX_RECORDS = 4
MAX_CHECKS = 8
OUTCOMES = ("clean", "minor", "hold")
VOICE_RE = re.compile(r"^[a-z]{2}_[a-z]{2,20}$")
MAX_FIELD = 200
MAX_ISSUES = 50
MAX_NEW_TEXT = 20_000


class ToolError(Exception):
    """A refusal the agent can act on. Its message is shown to the agent."""


def _short(value, field):
    if not isinstance(value, str) or len(value) > MAX_FIELD:
        raise ToolError(f"{field} must be a string of at most {MAX_FIELD} characters")
    return value


class AgentTools:
    def __init__(self, store, tts, verifier, g2p, default_voice="af_heart"):
        self.store, self.tts, self.verifier, self.g2p = store, tts, verifier, g2p
        self.default_voice = default_voice

    def _open_state(self, item_id):
        state = self.store.load_state(item_id)
        if state["status"] != "open":
            raise ToolError("this work item is finished; no further tool calls are accepted")
        return state

    def get_text(self, item_id):
        state = self._open_state(item_id)
        return {"prepared_text": self.store.read_text(item_id, "prepared.md"),
                "metadata": json.loads(self.store.read_text(item_id, "metadata.json")),
                "records_used": state["records"], "records_left": MAX_RECORDS - state["records"]}

    def replace_text(self, item_id, old, new):
        self._open_state(item_id)
        if not old:
            raise ToolError("old must not be empty")
        if len(new) > MAX_NEW_TEXT:
            raise ToolError(f"new is longer than {MAX_NEW_TEXT} characters")
        text = self.store.read_text(item_id, "prepared.md")
        found = text.count(old)
        if found != 1:
            raise ToolError(f"old must appear exactly once in the prepared text; found {found}")
        self.store.write_text(item_id, "prepared.md", text.replace(old, new, 1))
        return {"ok": True}

    def record(self, item_id, voice=None, speed=1.0):
        state = self._open_state(item_id)
        voice = voice or self.default_voice
        if not VOICE_RE.match(voice):
            raise ToolError(f"voice must look like 'af_heart'; got {voice!r}")
        if not 0.5 <= float(speed) <= 2.0:
            raise ToolError("speed must be between 0.5 and 2.0")
        if state["records"] >= MAX_RECORDS:
            raise ToolError(f"record limit reached ({MAX_RECORDS} per article); "
                            "finish with the best result so far, or with outcome 'hold'")
        state["records"] += 1  # counted before the engine runs
        self.store.save_state(item_id, state)
        text = self.store.read_text(item_id, "prepared.md")
        duration = self.tts.record(text, self.store.path(item_id, "recording.wav"), voice, float(speed))
        return {"round": state["records"], "duration_seconds": round(duration, 1),
                "records_left": MAX_RECORDS - state["records"]}

    def check(self, item_id):
        state = self._open_state(item_id)
        if state["records"] == 0 or not self.store.path(item_id, "recording.wav").exists():
            raise ToolError("nothing to check yet: call record first")
        if len(state["checks"]) >= MAX_CHECKS:
            raise ToolError(f"check limit reached ({MAX_CHECKS} per article)")
        text = self.store.read_text(item_id, "prepared.md")
        report, warnings = self.verifier.check(self.store.path(item_id, "recording.wav"), text)
        n = len(state["checks"]) + 1
        self.store.write_text(item_id, f"report-{n}.txt", report)
        state["checks"].append({"n": n, "record": state["records"], "warnings": list(warnings)})
        self.store.save_state(item_id, state)
        return {"report": report, "precheck_warnings": list(warnings)}

    def phonemes(self, item_id, word):
        self._open_state(item_id)
        if not isinstance(word, str) or not 0 < len(word) <= 64:
            raise ToolError("word must be 1-64 characters")
        return {"word": word, "phonemes": self.g2p.phonemes(word)}

    def finish(self, item_id, outcome, remaining_issues, substitutions):
        state = self._open_state(item_id)
        if outcome not in OUTCOMES:
            raise ToolError(f"outcome must be one of {OUTCOMES}")
        if not isinstance(remaining_issues, list) or len(remaining_issues) > MAX_ISSUES:
            raise ToolError(f"remaining_issues must be a list of at most {MAX_ISSUES}")
        issues = [{"kind": _short(i.get("kind"), "kind"), "where": _short(i.get("where", ""), "where"),
                   "note": _short(i.get("note", ""), "note")} for i in remaining_issues]
        subs_in = [{"original": _short(s.get("original"), "original"),
                    "spoken": _short(s.get("spoken"), "spoken"),
                    "why": _short(s.get("why", ""), "why")} for s in substitutions]
        latest = [c for c in state["checks"] if c["record"] == state["records"]]
        if state["records"] == 0 or not latest:
            raise ToolError("call check on the latest recording before finish")
        if outcome in ("clean", "minor") and latest[-1]["warnings"]:
            raise ToolError("the latest check's pre-check flagged: " + "; ".join(latest[-1]["warnings"])
                            + ". Fix and re-record, or finish with outcome 'hold'.")
        problems = words_check(self.store.read_text(item_id, "source.md"),
                               self.store.read_text(item_id, "prepared.md"),
                               [Substitution(s["original"], s["spoken"]) for s in subs_in])
        if problems:
            raise ToolError("the prepared text differs from the article: " + "; ".join(problems)
                            + ". Declare each word change in substitutions, or undo it.")
        state["finish"] = {"outcome": outcome, "remaining_issues": issues, "substitutions": subs_in}
        state["status"] = "finished"
        self.store.save_state(item_id, state)
        self.store.write_text(item_id, "result.json", json.dumps(state["finish"]))
        self.store.revoke(item_id)
        return {"ok": True}
```

- [ ] **Step 4: Run them and see them pass.** Run the light test command, with `-k "agent_tools or engines"`.
- [ ] **Step 5: Commit, as separate commits:**
  - "narration: engine interfaces, real adapters and fakes";
  - "narration: the six agent tools, with limits and claim checks".

---

### Task 5: The gate and the runner tools (`narration/gate.py`, `narration/runner_tools.py`)

**Files:**
- Create: `narration/gate.py`, `narration/runner_tools.py`
- Test: `tests/narration/test_gate.py`

**Interfaces:**
- Consumes: `WorkItemStore`, `words.check`, `Converter`.
- Produces: `GateResult(action: str, outcome: str | None, reasons: list[str], notes: str)`, where `action` is `"publish"`, `"hold"` or `"retry"`.
- Produces: `evaluate(state: dict, source: str, prepared: str, metadata: dict) -> GateResult`.
- Produces: `RunnerTools(store, converter)`, with:
  - `.open(article_text: str, metadata: dict) -> dict`, returning `{"item_id", "agent_token"}`;
  - `.finalize(item_id: str) -> dict`, returning `{"action", "outcome", "reasons", "notes", "mp3": bool}`;
  - `.status(item_id: str) -> dict`.
- **Metadata keys** come from article-assistant's `--json` output (Plan 1, Task 2): `title`, `authors`, `publication`, `issue_number`, `issue_season`, `date_published`, `description`.
- **MP3 tags:**
  - `title`;
  - `artist` = the authors joined with `"; "`;
  - `album` = `"<publication>, No. <n> (<season>)"`, or just the publication when there's no edition.

- [ ] **Step 1: Write the failing tests:**

```python
import pytest

from narration.agent_tools import AgentTools
from narration.fakes import FakeConverter, FakeG2P, FakeTTS, FakeVerifier
from narration.gate import evaluate
from narration.runner_tools import RunnerTools
from narration.workitems import WorkItemStore

META = {"title": "Zoning Alone", "authors": ["Joseph Lawler"], "publication": "The New Atlantis",
        "issue_number": "86", "issue_season": "Fall 2026",
        "description": "Zoning made us lonely."}
ARTICLE = "Zoning made us lonely.\n\nThe Court may end it."


@pytest.fixture
def env(tmp_path):
    store = WorkItemStore(tmp_path)
    conv = FakeConverter()
    runner = RunnerTools(store, conv)
    tools = AgentTools(store, FakeTTS(), FakeVerifier(), FakeG2P())
    opened = runner.open(ARTICLE, META)
    return store, runner, tools, conv, opened["item_id"], opened["agent_token"]


def test_open_returns_id_and_token(env):
    store, _, _, _, item, token = env
    assert store.item_for_token(token) == item


def test_no_finish_means_retry(env):
    store, runner, _, conv, item, _ = env
    out = runner.finalize(item)
    assert out["action"] == "retry" and not out["mp3"] and conv.calls == []
    assert store.item_for_token(env[5]) is None  # token revoked at finalize too


def test_clean_publishes_with_tags_and_notes(env):
    store, runner, tools, conv, item, _ = env
    tools.record(item); tools.check(item); tools.finish(item, "clean", [], [])
    out = runner.finalize(item)
    assert out["action"] == "publish" and out["mp3"]
    assert conv.calls == [("Zoning Alone", "Joseph Lawler", "The New Atlantis, No. 86 (Fall 2026)")]
    assert out["notes"].startswith("Zoning made us lonely.")
    assert store.path(item, "episode.mp3").exists()


def test_minor_lists_issues_and_substitutions_in_the_notes(env):
    store, runner, tools, conv, item, _ = env
    tools.record(item); tools.check(item)
    tools.finish(item, "minor", [{"kind": "mispronunciation", "where": "0:12", "note": "Lawler"}], [])
    out = runner.finalize(item)
    assert out["action"] == "publish"
    assert "0:12" in out["notes"] and "Lawler" in out["notes"]


def test_agent_hold_is_a_hold(env):
    _, runner, tools, conv, item, _ = env
    tools.record(item); tools.check(item)
    tools.finish(item, "hold", [{"kind": "garbled", "where": "3:00", "note": "paragraph 4"}], [])
    out = runner.finalize(item)
    assert out["action"] == "hold" and conv.calls == []
    assert any("paragraph 4" in r for r in out["reasons"])


def test_gate_rechecks_words_independently(env):
    store, runner, tools, conv, item, _ = env
    tools.record(item); tools.check(item); tools.finish(item, "clean", [], [])
    store.write_text(item, "prepared.md", ARTICLE + " Buy now.")  # tampering after finish
    out = runner.finalize(item)
    assert out["action"] == "hold" and any("buy" in r for r in out["reasons"])


def test_finalize_twice_does_not_convert_twice(env):
    _, runner, tools, conv, item, _ = env
    tools.record(item); tools.check(item); tools.finish(item, "clean", [], [])
    runner.finalize(item)
    runner.finalize(item)
    assert len(conv.calls) == 1


def test_evaluate_without_edition_uses_publication_as_album():
    from narration.gate import album_for
    assert album_for({"publication": "The New Atlantis"}) == "The New Atlantis"
```

- [ ] **Step 2: Run them and see them fail.** Run the light test command, with `-k gate`.
- [ ] **Step 3: Implement** `narration/gate.py`:

```python
"""The authoritative pre-publish gate (spec: "Quality gate"). Pure."""
from __future__ import annotations

from dataclasses import dataclass, field

from narration.words import Substitution, check as words_check


@dataclass
class GateResult:
    action: str                      # "publish" | "hold" | "retry"
    outcome: str | None
    reasons: list[str] = field(default_factory=list)
    notes: str = ""


def album_for(metadata: dict) -> str:
    pub = metadata.get("publication", "")
    if metadata.get("issue_number") and metadata.get("issue_season"):
        return f"{pub}, No. {metadata['issue_number']} ({metadata['issue_season']})"
    return pub


def notes_for(metadata: dict, finish: dict) -> str:
    parts = [metadata.get("description", "").strip()]
    if finish.get("remaining_issues"):
        parts.append("Known issues in this recording:")
        parts += [f"- {i['where']} {i['kind']}: {i['note']}".replace("-  ", "- ")
                  for i in finish["remaining_issues"]]
    if finish.get("substitutions"):
        parts.append("Read with these substitutions:")
        parts += [f"- \"{s['original']}\" read as \"{s['spoken']}\"" for s in finish["substitutions"]]
    parts.append("Recorded automatically.")
    return "\n".join(p for p in parts if p)


def evaluate(state: dict, source: str, prepared: str, metadata: dict) -> GateResult:
    finish = state.get("finish")
    if not finish:
        return GateResult("retry", None, ["the session ended without calling finish"])
    reasons = []
    if finish["outcome"] == "hold":
        held = "; ".join(f"{i['where']} {i['kind']}: {i['note']}" for i in finish["remaining_issues"])
        reasons.append("the agent held it" + (f" ({held})" if held else ""))
    subs = [Substitution(s["original"], s["spoken"]) for s in finish.get("substitutions", [])]
    reasons += words_check(source, prepared, subs)
    latest = [c for c in state.get("checks", []) if c["record"] == state.get("records")]
    if not latest:
        reasons.append("no check ran on the latest recording")
    elif finish["outcome"] in ("clean", "minor") and latest[-1]["warnings"]:
        reasons.append("pre-check warnings on the latest recording: " + "; ".join(latest[-1]["warnings"]))
    action = "hold" if reasons else "publish"
    return GateResult(action, finish["outcome"], reasons, notes_for(metadata, finish))
```

  And `narration/runner_tools.py`:

```python
"""Tools for the runner only (never exposed to the agent)."""
from __future__ import annotations

import json

from narration.gate import album_for, evaluate


class RunnerTools:
    def __init__(self, store, converter):
        self.store, self.converter = store, converter

    def open(self, article_text: str, metadata: dict) -> dict:
        item_id, token = self.store.open(article_text, metadata)
        return {"item_id": item_id, "agent_token": token}

    def status(self, item_id: str) -> dict:
        if not self.store.exists(item_id):
            return {"exists": False}
        state = self.store.load_state(item_id)
        return {"exists": True, "status": state["status"], "records": state["records"],
                "checks": len(state["checks"]), "finish": state["finish"],
                "mp3": self.store.path(item_id, "episode.mp3").exists()}

    def finalize(self, item_id: str) -> dict:
        state = self.store.load_state(item_id)
        self.store.revoke(item_id)
        meta = json.loads(self.store.read_text(item_id, "metadata.json"))
        result = evaluate(state, self.store.read_text(item_id, "source.md"),
                          self.store.read_text(item_id, "prepared.md"), meta)
        mp3 = self.store.path(item_id, "episode.mp3")
        if result.action == "publish" and not mp3.exists():
            self.converter.to_mp3(self.store.path(item_id, "recording.wav"), mp3,
                                  title=meta.get("title", ""),
                                  artist="; ".join(meta.get("authors", [])),
                                  album=album_for(meta))
        if state["status"] != "finalized":
            state["status"] = "finalized"
            self.store.save_state(item_id, state)
        return {"action": result.action, "outcome": result.outcome, "reasons": result.reasons,
                "notes": result.notes, "mp3": mp3.exists()}
```

  Note on `evaluate`: `finalize` is idempotent. A second call recomputes the same result and skips conversion because the MP3 exists. The record of what was published is the runner's (Plan 3).
- [ ] **Step 4: Run them and see them pass.** Run the light test command, with `-k gate`.
- [ ] **Step 5: Commit:** "narration: the pre-publish gate and the runner tools".

---

### Task 6: Bearer-token middleware (`narration/auth.py`)

**Files:**
- Create: `narration/auth.py`
- Test: `tests/narration/test_auth.py`

**Interfaces:**
- Produces: `BearerAuth(app, runner_token: str, store)`, a pure-ASGI middleware with these rules:
  - `/health` is open;
  - `/runner/...` requires `Authorization: Bearer <runner_token>`, compared in constant time;
  - `/agent/...` requires a live item token;
  - anything else gets 404, and a failed check gets 401 JSON.
- Produces: `bearer(headers) -> str | None`, which parses `Authorization: Bearer …` from a Starlette or MCP headers mapping.

- [ ] **Step 1: Write the failing tests:**

```python
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from narration.auth import BearerAuth, bearer
from narration.workitems import WorkItemStore


def _app(tmp_path):
    store = WorkItemStore(tmp_path)
    ok = lambda request: PlainTextResponse("ok")
    inner = Starlette(routes=[Route("/health", ok), Route("/runner/x", ok), Route("/agent/x", ok)])
    return TestClient(BearerAuth(inner, "runner-secret", store)), store


def test_health_is_open(tmp_path):
    client, _ = _app(tmp_path)
    assert client.get("/health").status_code == 200


def test_runner_needs_the_runner_token(tmp_path):
    client, store = _app(tmp_path)
    _, item_token = store.open("x", {})
    assert client.get("/runner/x").status_code == 401
    assert client.get("/runner/x", headers={"Authorization": f"Bearer {item_token}"}).status_code == 401
    assert client.get("/runner/x", headers={"Authorization": "Bearer runner-secret"}).status_code == 200


def test_agent_needs_a_live_item_token(tmp_path):
    client, store = _app(tmp_path)
    item, token = store.open("x", {})
    assert client.get("/agent/x", headers={"Authorization": "Bearer runner-secret"}).status_code == 401
    assert client.get("/agent/x", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    store.revoke(item)
    assert client.get("/agent/x", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_unknown_paths_are_404(tmp_path):
    client, _ = _app(tmp_path)
    assert client.get("/other", headers={"Authorization": "Bearer runner-secret"}).status_code == 404


def test_bearer_parsing():
    assert bearer({"authorization": "Bearer abc"}) == "abc"
    assert bearer({"Authorization": "bearer abc"}) == "abc"
    assert bearer({"authorization": "Basic abc"}) is None
    assert bearer({}) is None and bearer(None) is None
```

- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement** `narration/auth.py`:

```python
"""Bearer-token gate in front of the whole app."""
from __future__ import annotations

import hmac
import json


def bearer(headers) -> str | None:
    if not headers:
        return None
    value = None
    for k, v in dict(headers).items():
        if str(k).lower() == "authorization":
            value = v
    if not value or not value.lower().startswith("bearer "):
        return None
    return value[7:].strip() or None


class BearerAuth:
    def __init__(self, app, runner_token: str, store):
        if not runner_token:
            raise ValueError("a runner token is required")
        self.app, self.runner_token, self.store = app, runner_token, store

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        token = bearer(headers)
        if path == "/health":
            ok = True
        elif path.startswith("/runner/") or path == "/runner":
            ok = token is not None and hmac.compare_digest(token, self.runner_token)
        elif path.startswith("/agent/") or path == "/agent":
            ok = token is not None and self.store.item_for_token(token) is not None
        else:
            return await _respond(send, 404, {"error": "not found"})
        if not ok:
            return await _respond(send, 401, {"error": "unauthorized"})
        return await self.app(scope, receive, send)


async def _respond(send, status, body):
    data = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(data)).encode())]})
    await send({"type": "http.response.body", "body": data})
```

- [ ] **Step 4: Run them and see them pass.**
- [ ] **Step 5: Commit:** "narration: bearer-token middleware (runner token; per-item agent tokens)".

---

### Task 7: The app: two MCP servers, a download route, and the entry point (`narration/app.py`, `narration/__main__.py`)

**Files:**
- Create: `narration/app.py`, `narration/__main__.py`
- Test: `tests/narration/test_app.py`

**Interfaces:**
- Consumes: `AgentTools`, `RunnerTools`, `WorkItemStore`, `BearerAuth`, `bearer`, and `ToolError`.
- Produces: `build_app(store, agent_tools, runner_tools, runner_token, allowed_hosts: list[str]) -> Starlette`, which serves:
  - `/health`;
  - `/agent/mcp` (the six tools);
  - `/runner/mcp` (`open`, `finalize`, `status`);
  - `GET /runner/files/{item_id}/episode.mp3`.
- Produces: `python -m narration`. It reads `NARRATION_DATA_DIR`, `NARRATION_RUNNER_TOKEN_FILE`, `NARRATION_HOST` (default `127.0.0.1`), `NARRATION_PORT` (default `8770`) and `NARRATION_ALLOWED_HOSTS` (comma-separated `host:port`; default `127.0.0.1:8770,localhost:8770`). It takes `--fake-engines` for local smoke tests. With real engines it loads `.env` (as `verify_audio.py` does) and exits at startup if `GEMINI_API_KEY` is still unset; the fake path never needs the key.

- [ ] **Step 1: Write the failing tests:**

```python
import pytest
from starlette.testclient import TestClient

from narration.agent_tools import AgentTools
from narration.app import build_app
from narration.fakes import FakeConverter, FakeG2P, FakeTTS, FakeVerifier
from narration.runner_tools import RunnerTools
from narration.workitems import WorkItemStore

RUNNER = "runner-secret"


@pytest.fixture
def client(tmp_path):
    store = WorkItemStore(tmp_path)
    app = build_app(store, AgentTools(store, FakeTTS(), FakeVerifier(), FakeG2P()),
                    RunnerTools(store, FakeConverter()), RUNNER,
                    allowed_hosts=["testserver", "testserver:80"])
    with TestClient(app) as c:  # runs the lifespan (both session managers)
        yield c, store


def test_health(client):
    c, _ = client
    assert c.get("/health").json() == {"ok": True}


def test_mcp_endpoints_are_token_gated(client):
    c, store = client
    _, token = store.open("x", {})
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    hdr = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    assert c.post("/agent/mcp", json=body, headers=hdr).status_code == 401
    assert c.post("/runner/mcp", json=body, headers={**hdr, "Authorization": f"Bearer {token}"}).status_code == 401
    r = c.post("/agent/mcp", json=body, headers={**hdr, "Authorization": f"Bearer {token}"})
    assert r.status_code != 401


def test_download_needs_runner_token_and_a_finalized_mp3(client):
    c, store = client
    item, _ = store.open("x", {})
    url = f"/runner/files/{item}/episode.mp3"
    assert c.get(url).status_code == 401
    assert c.get(url, headers={"Authorization": f"Bearer {RUNNER}"}).status_code == 404
    store.path(item, "episode.mp3").write_bytes(b"ID3")
    r = c.get(url, headers={"Authorization": f"Bearer {RUNNER}"})
    assert r.status_code == 200 and r.content == b"ID3"
    assert c.get("/runner/files/../../x/episode.mp3",
                 headers={"Authorization": f"Bearer {RUNNER}"}).status_code == 404


def test_host_not_on_allow_list_is_refused(tmp_path):
    store = WorkItemStore(tmp_path)
    _, token = store.open("x", {})
    app = build_app(store, AgentTools(store, FakeTTS(), FakeVerifier(), FakeG2P()),
                    RunnerTools(store, FakeConverter()), RUNNER, allowed_hosts=["mac.tailnet:8770"])
    with TestClient(app) as c:
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        r = c.post("/agent/mcp", json=body, headers={"Authorization": f"Bearer {token}",
                   "Accept": "application/json, text/event-stream", "Content-Type": "application/json"})
        assert r.status_code == 421  # Host check (S1); Content-Type is valid, so 400 can't mask it


def test_engine_errors_reach_the_agent_with_their_message(tmp_path):
    class LongAudioVerifier:  # what verify_audio raises past MAX_SINGLE_CALL_MINUTES
        def check(self, audio_path, text):
            raise ValueError("This audio is 17:30 long, but reliable single-call verification "
                             "tops out at ~15 minutes. Add [BREAK] markers to the text and "
                             "regenerate the audio so verification can be chunked.")

    store = WorkItemStore(tmp_path)
    _, token = store.open("x", {})
    app = build_app(store, AgentTools(store, FakeTTS(), LongAudioVerifier(), FakeG2P()),
                    RunnerTools(store, FakeConverter()), RUNNER,
                    allowed_hosts=["testserver", "testserver:80"])
    hdr = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
           "Authorization": f"Bearer {token}"}

    def call(c, i, name):  # stateless_http=True: tools/call needs no initialize first
        body = {"jsonrpc": "2.0", "id": i, "method": "tools/call",
                "params": {"name": name, "arguments": {}}}
        return c.post("/agent/mcp", json=body, headers=hdr).json()["result"]

    with TestClient(app) as c:
        assert call(c, 1, "record")["isError"] is False
        result = call(c, 2, "check")
    assert result["isError"] is True
    assert "Add [BREAK] markers" in result["content"][0]["text"]


def test_real_engines_need_a_gemini_key_at_startup(tmp_path, monkeypatch):
    from narration.__main__ import main
    (tmp_path / "runner-token").write_text("runner-secret\n")
    monkeypatch.setenv("NARRATION_RUNNER_TOKEN_FILE", str(tmp_path / "runner-token"))
    monkeypatch.setenv("NARRATION_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)  # ignore any .env in the checkout
    with pytest.raises(SystemExit, match="GEMINI_API_KEY"):
        main([])
    served = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: served.append(kw))
    main(["--fake-engines"])  # the smoke path needs no key
    assert served and served[0]["port"] == 8770
```

- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement** `narration/app.py`:

```python
"""The narration HTTP app: two MCP servers behind one bearer-token gate."""
from __future__ import annotations

import contextlib
from typing import Any, Literal

from pydantic import BaseModel, Field
from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from mcp.server.mcpserver.exceptions import ToolError as SDKToolError

from narration.agent_tools import ToolError
from narration.auth import BearerAuth, bearer

AGENT_INSTRUCTIONS = (
    "Tools for recording one article. Start with get_text. Edit the prepared text only "
    "with replace_text, and never change what the article says: declare every word "
    "change as a substitution in finish. Record, then check, at most four rounds. End "
    "with finish."
)


class Issue(BaseModel):
    kind: str = Field(max_length=200)
    where: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=200)


class SubstitutionIn(BaseModel):
    original: str = Field(max_length=200)
    spoken: str = Field(max_length=200)
    why: str = Field(default="", max_length=200)


def build_app(store, agent_tools, runner_tools, runner_token, allowed_hosts):
    security = TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                         allowed_hosts=list(allowed_hosts),
                                         allowed_origins=[])
    agent = MCPServer("narration", instructions=AGENT_INSTRUCTIONS)
    runner = MCPServer("narration-runner")

    def item(ctx: Context) -> str:
        item_id = store.item_for_token(bearer(ctx.headers) or "")
        if item_id is None:
            raise SDKToolError("unknown or expired work-item token")
        return item_id

    def run(fn, *args):
        """Call a tool function; turn our refusals and the engines' errors into the
        SDK's ToolError so the agent sees the message (a plain exception's text
        is withheld; spike S1). ValueError and RuntimeError are what verify_audio
        and text_to_speech raise, e.g. verify_audio's "add [BREAK] markers"
        refusal of a single call over 15 minutes."""
        try:
            return fn(*args)
        except (ToolError, ValueError, RuntimeError) as e:
            raise SDKToolError(str(e)) from e

    @agent.tool()
    def get_text(ctx: Context) -> dict:
        """The prepared text, the article's metadata, and how many record rounds are left."""
        return run(agent_tools.get_text, item(ctx))

    @agent.tool()
    def replace_text(old: str, new: str, ctx: Context) -> dict:
        """Replace text that appears exactly once in the prepared text."""
        return run(agent_tools.replace_text, item(ctx), old, new)

    @agent.tool()
    def record(ctx: Context, voice: str = "af_heart", speed: float = 1.0) -> dict:
        """Generate audio from the prepared text. At most four times per article."""
        return run(agent_tools.record, item(ctx), voice, speed)

    @agent.tool()
    def check(ctx: Context) -> dict:
        """Verify the latest recording against the prepared text; returns the report."""
        return run(agent_tools.check, item(ctx))

    @agent.tool()
    def phonemes(word: str, ctx: Context) -> dict:
        """Look up a pronunciation, for a [word](/phonemes/) override."""
        return run(agent_tools.phonemes, item(ctx), word)

    @agent.tool()
    def finish(outcome: Literal["clean", "minor", "hold"], ctx: Context,
               remaining_issues: list[Issue] = [], substitutions: list[SubstitutionIn] = []) -> dict:
        """End the session. outcome: clean, minor (issues listed), or hold (content missing or garbled)."""
        return run(agent_tools.finish, item(ctx), outcome,
                   [i.model_dump() for i in remaining_issues],
                   [s.model_dump() for s in substitutions])

    @runner.tool(name="open")
    def open_item(article_text: str, metadata: dict[str, Any]) -> dict:
        """Create a work item; returns its id and a per-item agent token."""
        return runner_tools.open(article_text, metadata)

    @runner.tool()
    def finalize(item_id: str) -> dict:
        """Run the gate; on publish, make the MP3. Idempotent."""
        if not store.exists(item_id):
            raise SDKToolError("no such work item")
        return runner_tools.finalize(item_id)

    @runner.tool()
    def status(item_id: str) -> dict:
        """A work item's state."""
        return runner_tools.status(item_id)

    agent_app = agent.streamable_http_app(streamable_http_path="/mcp", stateless_http=True,
                                          json_response=True, transport_security=security)
    runner_app = runner.streamable_http_app(streamable_http_path="/mcp", stateless_http=True,
                                            json_response=True, transport_security=security)

    async def health(request):
        return JSONResponse({"ok": True})

    async def download(request):
        item_id = request.path_params["item_id"]
        if not store.exists(item_id):
            return JSONResponse({"error": "not found"}, status_code=404)
        mp3 = store.path(item_id, "episode.mp3")
        if not mp3.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(mp3, media_type="audio/mpeg")

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with contextlib.AsyncExitStack() as stack:
            await stack.enter_async_context(agent.session_manager.run())
            await stack.enter_async_context(runner.session_manager.run())
            yield

    inner = Starlette(routes=[
        Route("/health", health),
        Route("/runner/files/{item_id}/episode.mp3", download),
        Mount("/agent", app=agent_app),
        Mount("/runner", app=runner_app),
    ], lifespan=lifespan)
    return BearerAuth(inner, runner_token, store)
```

  `BearerAuth` wraps the Starlette app, and `TestClient` passes lifespan events through to it, because the middleware forwards non-http scopes. If `TestClient(app)` doesn't run the lifespan through the wrapper, add `lifespan` handling to `BearerAuth.__call__` for `scope["type"] == "lifespan"`; it already forwards non-http scopes. Then confirm it with `test_health`.

  And `narration/__main__.py`:

```python
"""python -m narration [--fake-engines]"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from narration.agent_tools import AgentTools
from narration.app import build_app
from narration.runner_tools import RunnerTools
from narration.workitems import WorkItemStore


def main(argv=None):
    p = argparse.ArgumentParser(prog="narration")
    p.add_argument("--fake-engines", action="store_true", help="no TTS/Gemini; for smoke tests")
    args = p.parse_args(argv)
    data = Path(os.environ.get("NARRATION_DATA_DIR", str(Path.home() / "narration-data")))
    token_file = Path(os.environ["NARRATION_RUNNER_TOKEN_FILE"])
    runner_token = token_file.read_text().strip()
    host = os.environ.get("NARRATION_HOST", "127.0.0.1")
    port = int(os.environ.get("NARRATION_PORT", "8770"))
    allowed = [h for h in os.environ.get(
        "NARRATION_ALLOWED_HOSTS", f"127.0.0.1:{port},localhost:{port}").split(",") if h]
    if args.fake_engines:
        from narration.fakes import FakeConverter, FakeG2P, FakeTTS, FakeVerifier
        tts, ver, g2p, conv = FakeTTS(), FakeVerifier(), FakeG2P(), FakeConverter()
    else:
        from dotenv import load_dotenv
        load_dotenv()  # the same .env verify_audio.py reads
        if not os.environ.get("GEMINI_API_KEY"):
            raise SystemExit("GEMINI_API_KEY is not set (environment or .env); the Gemini "
                             "verifier needs it. Pass --fake-engines to run without it.")
        from narration.engines import GeminiVerifier, KokoroEngine, MisakiG2P, Mp3Converter
        tts, ver, g2p, conv = KokoroEngine(), GeminiVerifier(), MisakiG2P(), Mp3Converter()
    store = WorkItemStore(data)
    app = build_app(store, AgentTools(store, tts, ver, g2p), RunnerTools(store, conv),
                    runner_token, allowed)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run them and see them pass.** Run the light test command, with `-k app`. `test_engine_errors_reach_the_agent_with_their_message` posts `tools/call` directly and expects `{"isError": true, "content": [{"text": ...}]}` back as JSON: with `stateless_http=True` and `json_response=True` the server accepts a call without `initialize` and answers in one JSON body. If `test_mcp_endpoints_are_token_gated` gets a 406 or a 400 for the bare JSON-RPC post, that's fine: the assertion is only `!= 401`. If it gets a 421 or 403, the Host allow-list rejected the test client. Make the allowed hosts in the test fixture match what `TestClient` sends, which is `testserver`.
- [ ] **Step 5: Commit:** "narration: HTTP app with agent and runner MCP servers, and a download route".

---

### Task 8: An end-to-end smoke test from this container (fake engines, a real `claude -p`)

This proves the wiring that unit tests can't: a real Claude Code session, with only the six tools, completes a work item. Run it in the assistant's container. It costs a few cents.

**Files:**
- Create: `tests/narration/smoke_claude.sh` (manual; not collected by pytest)

- [ ] **Step 1: Write the script:**

```bash
#!/bin/bash
# Manual end-to-end smoke: fake engines, real claude -p, only the narration tools.
set -euo pipefail
W="$(mktemp -d)"; trap 'kill $SERVER 2>/dev/null; rm -rf "$W"' EXIT
echo "runner-$(openssl rand -hex 16)" > "$W/runner-token"
RT="$(cat "$W/runner-token")"
NARRATION_DATA_DIR="$W/data" NARRATION_RUNNER_TOKEN_FILE="$W/runner-token" NARRATION_PORT=8771 \
NARRATION_ALLOWED_HOSTS="127.0.0.1:8771" \
  uv run --no-project --with "mcp==2.3.0" --with uvicorn --with hypothesis --with pydantic \
  --with numpy --with soundfile --with pydub \
  python -m narration --fake-engines > "$W/server.log" 2>&1 & SERVER=$!
# numpy, soundfile, pydub: narration.words imports text_to_speech for clean_markdown_text.
for i in $(seq 1 30); do curl -sf http://127.0.0.1:8771/health >/dev/null && break; sleep 1; done
# Open a work item through the runner MCP endpoint, with a tiny python client using the same SDK.
OPEN=$(uv run --no-project --with "mcp==2.3.0" python tests/narration/smoke_open.py "$RT")
TOKEN=$(echo "$OPEN" | python3 -c 'import json,sys; print(json.load(sys.stdin)["agent_token"])')
ITEM=$(echo "$OPEN" | python3 -c 'import json,sys; print(json.load(sys.stdin)["item_id"])')
cat > "$W/mcp.json" <<EOF
{"mcpServers": {"narration": {"type": "http", "url": "http://127.0.0.1:8771/agent/mcp",
  "headers": {"Authorization": "Bearer $TOKEN"}}}}
EOF
# Flag set from spike S1. No --bare: it reads only ANTHROPIC_API_KEY and can't use the subscription.
# --tools "" is the control that removes built-ins (dontAsk alone let Bash run).
claude -p "Record this article: call get_text, record, check, then finish with outcome clean. Also try to run the shell command id and report whether you could." \
  --tools "" --strict-mcp-config --mcp-config "$W/mcp.json" \
  --allowedTools "mcp__narration__get_text mcp__narration__replace_text mcp__narration__record mcp__narration__check mcp__narration__phonemes mcp__narration__finish" \
  --permission-mode dontAsk --setting-sources "" --settings '{"disableAllHooks":true}' \
  --no-session-persistence --max-budget-usd 0.50 --output-format json > "$W/session.json"
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print("cost:", d.get("total_cost_usd")); print(d.get("result","")[:800])' "$W/session.json"
test -f "$W/data/items/$ITEM/result.json" && echo "PASS: finish recorded" || { echo "FAIL: no result.json"; cat "$W/server.log"; exit 1; }
```

  And `tests/narration/smoke_open.py`, which uses the MCP 2.x client exactly as spike S1 verified it:

```python
"""Open a work item through the runner endpoint; print the result as one JSON line."""
import asyncio, json, sys
from mcp.client import Client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

URL, TOKEN = "http://127.0.0.1:8771/runner/mcp", sys.argv[1]


async def main():
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {TOKEN}"})
    async with http, Client(streamable_http_client(URL, http_client=http)) as client:
        result = await client.call_tool("open", {"article_text": "Pope Leo XIV met the family.",
                                                 "metadata": {"title": "Smoke"}})
        assert not result.is_error, result
        print(result.content[0].text.replace("\n", " "))

asyncio.run(main())
```

  A `-> dict` tool result arrives as JSON text in `content[0].text`, with `structured_content` set to `None` (S1).
- [ ] **Step 2: Run it.** Run `bash tests/narration/smoke_claude.sh`. Expected:
  - `PASS: finish recorded`;
  - the session's text says it couldn't run a shell command;
  - the cost is printed (cents).
- [ ] **Step 3: Commit:** "narration: end-to-end smoke script (fake engines, real claude -p)".

---

### Task 9: Install on the Mac (Brian)

**Files:**
- Create: `deploy/net.robergb.narration.plist`, `docs/narration-server.md` (the install steps below, verbatim)

- [ ] **Step 1: Write the plist template.** The agent writes it; Brian adjusts the paths if his checkout isn't at `~/tools/kokoro`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>net.robergb.narration</string>
  <key>WorkingDirectory</key><string>/Users/robergb/tools/kokoro</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>-lc</string>
    <string>export PATH=$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH; exec uv run python -m narration</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>NARRATION_DATA_DIR</key><string>/Users/robergb/narration-data</string>
    <key>NARRATION_RUNNER_TOKEN_FILE</key><string>/Users/robergb/.config/narration/runner-token</string>
    <key>NARRATION_HOST</key><string>TAILNET_IP</string>
    <key>NARRATION_PORT</key><string>8770</string>
    <key>NARRATION_ALLOWED_HOSTS</key><string>TAILNET_IP:8770,brians-macbook-pro:8770</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/Users/robergb/Library/Logs/narration/out.log</string>
  <key>StandardErrorPath</key><string>/Users/robergb/Library/Logs/narration/err.log</string>
</dict></plist>
```

- [ ] **Step 2: Brian installs it** (fish-safe):

```bash
cd ~/tools/kokoro && git pull && uv sync
mkdir -p ~/.config/narration ~/Library/Logs/narration ~/narration-data
openssl rand -hex 32 > ~/.config/narration/runner-token; chmod 600 ~/.config/narration/runner-token
tailscale ip -4          # put this address in place of TAILNET_IP in the plist
cp deploy/net.robergb.narration.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/(id -u) ~/Library/LaunchAgents/net.robergb.narration.plist
curl -s http://(tailscale ip -4):8770/health      # {"ok":true}
```

  (`(id -u)` is fish syntax. In bash, use `$(id -u)`.)

  The server exits at startup if `GEMINI_API_KEY` is unset. Keep it in the checkout's `.env`, which `load_dotenv` finds from the package's location, or add it to the plist's `EnvironmentVariables`.
- [ ] **Step 3: The agent checks it from the home server's container.** Run `curl -s http://brians-macbook-pro:8770/health`. It should print `{"ok":true}`. The runner token goes to Brian's deploy of Plan 3; it doesn't travel through chat.
- [ ] **Step 4: Commit:** "narration: launchd template and install doc".
- [ ] **Step 5: Open the PR** with `reviewed:none`, and run the review tier the spec calls for. This is new code in no daily-use path, so a single review on the session's model is enough. Its description says:
  - "What breaks if wrong: nothing yet. Nothing calls this server until Plan 3's runner exists. On the Mac it's one LaunchAgent; `launchctl bootout gui/$(id -u)/net.robergb.narration` removes it";
  - the per-item token refinement;
  - the light test command.
