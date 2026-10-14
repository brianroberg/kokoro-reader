# Plan 1: Prerequisites Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the small changes in three repos that the unattended pipeline depends on:
- the skill's final name and layout;
- correct New Atlantis metadata, with description and date, as JSON;
- an agentbox image that can run a given command, with GitHub optionally left out of its egress, and with uv.

**Architecture:** Three independent PRs, one per repo. Each is delivered from the bot's fork with a branch push and a PR; never push to `main` and never merge. No task here changes runtime behaviour for existing users, except one: `article-assistant metadata` reads the page's JSON-LD graph correctly, which it silently failed to do before (see Task 2).

**Tech Stack:** Python 3 with pytest (kokoro-reader, article-assistant: `uv run python -m pytest`); bash (agentbox image scripts), tested with a plain-bash harness that stubs commands on `PATH`.

**Spec:** `docs/superpowers/specs/2026-10-14-unattended-podcast-pipeline-design.md`, "Order of work" item 0.

## Global Constraints

- **No name may contain a TTS model's name** (Brian, PR #7). This affects new names only; repo names stay.
- **Red/green TDD.** Write the failing test first, run it and see it fail for the right reason, then write the code.
- **Commit each fix separately,** with the change named in the subject.
- **article-assistant's `CLAUDE.md` says "User will manage git commits."** Delivery here is still a PR from the bot's fork, which needs commits on the fork's branch (Brian's standing fork-and-PR rule). Say so in that PR's description.
- **Run `uv run ruff check .` and `uv run ruff format .` in article-assistant before each commit** (its `CLAUDE.md`).
- **agentbox's `CLAUDE.md` rules apply there:**
  - assert on observable behaviour by stubbing commands and counting invocations, not by matching error messages;
  - image code stays bash.

## Review Focus

1. **A JSON-LD graph whose author is an `@id` reference to a `Person` node.** The real New Atlantis page has exactly this shape. The author must come out as the person's name, not empty and not the URL. Test in Task 2.
2. **The site-wide header names the *current* issue on every page** (a nav link reading "No. 86 – Fall 2026"), and "Related" cards link other issues. The edition must come from the article's own header `aside` (the `/issues/no-NN-season-year` link next to the `h1`), never from a page-wide search. Otherwise an older article, or a web-only one, is labelled with the current issue. Test in Task 2.
3. **No `datePublished` at all.** The date falls back to the start of the edition's season, not to today and not to nothing. Test in Task 2.
4. **`AGENTBOX_GITHUB=off` with GitHub unreachable.** The firewall must not even try to fetch GitHub's IP ranges, and the sandbox must start. Test in Task 4.
5. **An entrypoint given a command.** It must still apply the firewall before running it, and run it as `node`, never as root. Test in Task 3.

---

### Task 1: Rename the skill to `recording-article-audio` (kokoro-reader #6)

The skill is a flat file, `.claude/skills/article-tts-recording.md`. Claude Code loads project skills from `.claude/skills/<name>/SKILL.md`, so the rename also moves it into that layout.

**Files:**
- Move: `.claude/skills/article-tts-recording.md` → `.claude/skills/recording-article-audio/SKILL.md`
- Modify: `README.md:13,19`; `docs/adr-001-no-orchestration-script.md:9,35`
- Test: `tests/test_readme_docs.py`

**Interfaces:**
- Produces: the skill at `.claude/skills/recording-article-audio/SKILL.md` with `name: recording-article-audio`. Plan 3 reads it from that path.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_readme_docs.py`):

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_skill_lives_at_its_gerund_name():
    skill = ROOT / ".claude" / "skills" / "recording-article-audio" / "SKILL.md"
    assert skill.exists()
    assert "name: recording-article-audio" in skill.read_text()
    assert not (ROOT / ".claude" / "skills" / "article-tts-recording.md").exists()


def test_docs_name_the_skill_by_its_new_name():
    for doc in ("README.md", "docs/adr-001-no-orchestration-script.md"):
        text = (ROOT / doc).read_text()
        assert "article-tts-recording" not in text, doc
    readme = (ROOT / "README.md").read_text()
    assert ".claude/skills/recording-article-audio/SKILL.md" in readme
    assert "/recording-article-audio" in readme
```

- [ ] **Step 2: Run them and see them fail.** Run `uv run python -m pytest tests/test_readme_docs.py -v`. Expected: both new tests FAIL (the file doesn't exist; the old name is in the README).
- [ ] **Step 3: Do the rename:**

```bash
mkdir -p .claude/skills/recording-article-audio
git mv .claude/skills/article-tts-recording.md .claude/skills/recording-article-audio/SKILL.md
sed -i 's/^name: article-tts-recording$/name: recording-article-audio/' .claude/skills/recording-article-audio/SKILL.md
sed -i 's#\.claude/skills/article-tts-recording\.md#.claude/skills/recording-article-audio/SKILL.md#g; s#/article-tts-recording#/recording-article-audio#g' README.md docs/adr-001-no-orchestration-script.md
grep -rn "article-tts-recording" . --include=*.md --include=*.py | grep -v docs/superpowers
```

  Expected: the final `grep` prints nothing. The plan and spec documents keep the old name on purpose, because they describe the rename.
- [ ] **Step 4: Run the whole suite and see it pass.** Run `uv run python -m pytest -q`. On Linux, tests that need MLX skip or are mocked as they are today. Expected: everything that passed before still passes, plus the two new tests.
- [ ] **Step 5: Commit.**

```bash
git add -A .claude/skills README.md docs/adr-001-no-orchestration-script.md tests/test_readme_docs.py
git commit -m "Rename the skill to recording-article-audio, in the SKILL.md layout (#6)"
```

- [ ] **Step 6: Open the PR** from the bot's fork, closing #6. Its description says: "What breaks if wrong: `/article-tts-recording` stops working in your interactive sessions, and the new name is `/recording-article-audio`. You'd notice the first time you invoke it by the old name."

---

### Task 2: New Atlantis metadata: the JSON-LD graph, description, date, the edition from the article header, and `--json` (article-assistant #3)

**Context, checked against the live page on 2026-10-14 (`/publications/zoning-alone`):**
- The page's JSON-LD is a Yoast **`@graph`**: `{"@context": …, "@graph": [Article, WebPage, Person, …]}`. The extractor looks only for a top-level `"@type": "Article"`, so on today's pages it silently falls back to the `<h1>` and the byline, and never reads `datePublished`.
- In the graph, `Article.author` is `{"@id": "…/authors/joseph-lawler#person"}`, a reference that is resolved through the `Person` node's `name`.
- The description is in `<meta property="og:description">`, in `<meta name="description">`, and in the `WebPage` node.
- **The article's edition is a link in its header `aside`,** beside the `h1`: `<a class="link-transition" href="https://www.thenewatlantis.com/issues/no-86-fall-2026">Fall 2026</a>`. A web-only piece's `aside` has the type and a date but no `/issues/` link. Spike S4 checked this on three pages on 2026-10-14.
- ⚠️ **The text "No. 86 – Fall 2026" also appears on every page,** in a site-wide nav link to the *current* issue. "Related" cards link other issues too. The existing page-wide regexes (`No. N (Season Year)`, then any "Season Year" text) can therefore attach the current issue to any article. On today's layout, read the `aside` only, and keep the page-wide search only as a fallback for pages without that header (old layouts, and this repo's existing tests).
- **Subscriber-only ("gated") pages still serve their full text.** `zoning-alone` is gated, and its `div.gutenberg-content` has 4,415 words. This matches this repo's commit cc0927c, "New Atlantis serves full content without auth".

**Files:**
- Modify: `article_assistant.py`: `NewAtlantisExtractor.extract_metadata`, a new `season_start_date()`, `_run_metadata`, and `main()` (the `--json` flag)
- Test: `tests/test_extractors.py` (New Atlantis cases) and `tests/test_functional.py` (CLI `--json`)

**Interfaces:**
- Produces: `article_assistant.py metadata URL --json` prints one JSON object:

```json
{"title": "...", "authors": ["..."], "publication": "The New Atlantis",
 "issue_number": "86", "issue_season": "Fall 2026",
 "date_published": "2026-09-08T15:39:03+00:00", "description": "..."}
```

  A missing key means the value was absent. Plan 3's runner calls this.
- Produces: `season_start_date(season: str, year: int) -> str | None`, returning `"YYYY-MM-DD"`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_extractors.py`). The HTML mirrors the live page's structure:

```python
import json as _json

from article_assistant import NewAtlantisExtractor, season_start_date

GRAPH_PAGE = """
<html><head>
<meta name="description" content="Zoning made us lonely. The Court may end it.">
<meta property="og:description" content="Zoning made us lonely. The Court may end it.">
<script type="application/ld+json">
{"@context": "https://schema.org", "@graph": [
  {"@type": "Article", "@id": "https://www.thenewatlantis.com/publications/zoning-alone#article",
   "author": {"@id": "https://www.thenewatlantis.com/authors/joseph-lawler#person"},
   "headline": "Zoning Alone", "datePublished": "2026-09-08T15:39:03+00:00"},
  {"@type": "WebPage", "@id": "https://www.thenewatlantis.com/publications/zoning-alone",
   "description": "Zoning made us lonely. The Court may end it."},
  {"@type": "Person", "@id": "https://www.thenewatlantis.com/authors/joseph-lawler#person",
   "name": "Joseph Lawler"}
]}
</script></head>
<body>
<nav><div class="hidden lg:block"><a class="link-transition" href="https://www.thenewatlantis.com/issues/no-87-winter-2027">No. 87 – Winter 2027</a></div></nav>
<div class="article-header"><div><h1>Zoning Alone</h1></div>
  <aside><span>Essay</span><a class="link-transition" href="https://www.thenewatlantis.com/issues/no-86-fall-2026">Fall 2026</a></aside></div>
<div class="gutenberg-content"><p>Body.</p></div>
<section class="related"><a href="https://www.thenewatlantis.com/issues/no-23-winter-2009">Winter 2009</a></section>
</body></html>
"""
WEB_ONLY_PAGE = """
<html><body>
<nav><div class="hidden lg:block"><a class="link-transition" href="https://www.thenewatlantis.com/issues/no-86-fall-2026">No. 86 – Fall 2026</a></div></nav>
<div class="article-header"><div><h1>Are We Really on the Path to AGI?</h1></div>
  <aside><span>Essay</span><span>October 7, 2026</span></aside></div>
<section class="related"><a href="https://www.thenewatlantis.com/issues/no-85-summer-2026">Summer 2026</a></section>
</body></html>
"""
URL = "https://www.thenewatlantis.com/publications/zoning-alone"


class TestNewAtlantisGraph:
    def test_reads_title_date_and_author_from_the_graph(self):
        md = NewAtlantisExtractor().extract_metadata(GRAPH_PAGE, URL)
        assert md["title"] == "Zoning Alone"
        assert md["date_published"] == "2026-09-08T15:39:03+00:00"
        assert md["authors"] == ["Joseph Lawler"]

    def test_reads_the_description(self):
        md = NewAtlantisExtractor().extract_metadata(GRAPH_PAGE, URL)
        assert md["description"] == "Zoning made us lonely. The Court may end it."

    def test_edition_comes_from_the_article_header_not_the_nav_or_related(self):
        # The nav names a different (current) issue and a Related card links another;
        # only the header aside's link is the article's own edition.
        md = NewAtlantisExtractor().extract_metadata(GRAPH_PAGE, URL)
        assert md["issue_number"] == "86"
        assert md["issue_season"] == "Fall 2026"

    def test_web_only_piece_has_no_edition(self):
        md = NewAtlantisExtractor().extract_metadata(WEB_ONLY_PAGE, URL)
        assert "issue_number" not in md and "issue_season" not in md

    def test_parenthesised_issue_marker_still_works(self):
        page = "<html><body><h1>T</h1><p>From No. 79 (Winter 2025) issue</p></body></html>"
        md = NewAtlantisExtractor().extract_metadata(page, URL)
        assert (md["issue_number"], md["issue_season"]) == ("79", "Winter 2025")

    def test_date_falls_back_to_the_season_start(self):
        page = ('<html><body><div><div><h1>T</h1></div><aside>'
                '<a href="https://www.thenewatlantis.com/issues/no-86-fall-2026">Fall 2026</a>'
                '</aside></div></body></html>')
        md = NewAtlantisExtractor().extract_metadata(page, URL)
        assert md["date_published"] == "2026-09-01"


class TestSeasonStartDate:
    def test_seasons(self):
        assert season_start_date("Spring", 2026) == "2026-03-01"
        assert season_start_date("Summer", 2026) == "2026-06-01"
        assert season_start_date("Fall", 2026) == "2026-09-01"
        assert season_start_date("Autumn", 2026) == "2026-09-01"
        # Winter YYYY is the first issue of year YYYY: meteorological winter
        # starts the December before.
        assert season_start_date("Winter", 2026) == "2025-12-01"

    def test_unknown(self):
        assert season_start_date("Monsoon", 2026) is None
```

  And in `tests/test_functional.py`:

```python
from unittest.mock import patch
import json
import sys

import article_assistant
from tests.test_extractors import GRAPH_PAGE, URL


def test_metadata_json_flag(capsys):
    with patch.object(article_assistant, "fetch_article_content", return_value=GRAPH_PAGE), \
         patch.object(sys, "argv", ["article_assistant.py", "metadata", URL, "--json"]):
        article_assistant.main()
    out = json.loads(capsys.readouterr().out)
    assert out["title"] == "Zoning Alone"
    assert out["issue_number"] == "86"
    assert out["description"].startswith("Zoning made us lonely")
```

- [ ] **Step 2: Run them and see them fail.** Run `uv run python -m pytest tests/test_extractors.py tests/test_functional.py -v`. Expected failures:
  - `ImportError: cannot import name 'season_start_date'`;
  - after a stub is added, title, date and author assertions fail, because the graph isn't read;
  - `--json` is "unrecognized arguments".
- [ ] **Step 3: Implement.** In `article_assistant.py`, add after `infer_edition_number`:

```python
SEASON_START = {"spring": (0, "03-01"), "summer": (0, "06-01"),
                "fall": (0, "09-01"), "autumn": (0, "09-01"),
                "winter": (-1, "12-01")}


def season_start_date(season, year):
    """First day of an edition's meteorological season, as YYYY-MM-DD.

    "Winter 2026" is the first issue of 2026 (see infer_edition_number), and
    meteorological winter starts the previous December, so it maps to
    2025-12-01. Returns None for an unknown season.
    """
    entry = SEASON_START.get(str(season).lower())
    if entry is None:
        return None
    year_offset, month_day = entry
    return f"{int(year) + year_offset}-{month_day}"


def _jsonld_nodes(data):
    """Flatten a JSON-LD payload into its nodes (handles @graph and lists)."""
    if isinstance(data, list):
        return [n for n in data if isinstance(n, dict)]
    if isinstance(data, dict):
        if isinstance(data.get("@graph"), list):
            return [n for n in data["@graph"] if isinstance(n, dict)]
        return [data]
    return []


def _has_type(node, wanted):
    t = node.get("@type")
    return t == wanted or (isinstance(t, list) and wanted in t)
```

  Replace the JSON-LD loop in `NewAtlantisExtractor.extract_metadata` with:

```python
        nodes = []
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                nodes.extend(_jsonld_nodes(json.loads(script.string or "")))
            except json.JSONDecodeError:
                continue
        by_id = {n["@id"]: n for n in nodes if isinstance(n.get("@id"), str)}

        article = next((n for n in nodes if _has_type(n, "Article")), None)
        if article:
            metadata["title"] = (article.get("headline") or "").strip()
            authors = article.get("author", [])
            if isinstance(authors, dict):
                authors = [authors]
            names = []
            for author in authors:
                if not isinstance(author, dict):
                    continue
                if not author.get("name") and isinstance(author.get("@id"), str):
                    author = by_id.get(author["@id"], author)
                if author.get("name"):
                    names.append(re.sub(r"\s+", " ", author["name"].strip()))
            if names:
                metadata["authors"] = names
            if article.get("datePublished"):
                metadata["date_published"] = article["datePublished"]

        description = None
        for attrs in ({"property": "og:description"}, {"name": "description"}):
            tag = soup.find("meta", attrs=attrs)
            if tag and tag.get("content", "").strip():
                description = tag["content"].strip()
                break
        if description is None:
            page = next((n for n in nodes if _has_type(n, "WebPage")), None)
            if page and page.get("description"):
                description = page["description"].strip()
        if description:
            metadata["description"] = description
```

  Add this helper, next to `_jsonld_nodes`:

```python
ISSUE_SLUG_RE = re.compile(r"/issues/no-(\d+)-(winter|spring|summer|fall|autumn)-(\d{4})", re.I)


def _edition_from_header(soup):
    """The article's own edition, read from the header aside beside its h1.

    Returns (number, "Season Year") when the aside links an issue, "none" when
    the aside exists but links no issue (a web-only piece), and None when the
    page has no such header (older layouts), so callers may fall back.
    """
    h1 = soup.find("h1")
    holder = h1.parent.parent if h1 is not None and h1.parent is not None else None
    aside = holder.select_one("aside") if holder is not None else None
    if aside is None:
        return None
    for link in aside.select('a[href*="/issues/"]'):
        m = ISSUE_SLUG_RE.search(link.get("href", ""))
        if m:
            return m.group(1), f"{m.group(2).title()} {m.group(3)}"
    return "none"
```

  Then, in `extract_metadata`, put this in front of the existing issue-marker block:

```python
        edition = _edition_from_header(soup)
        if isinstance(edition, tuple):
            metadata["issue_number"], metadata["issue_season"] = edition
```

  Wrap the existing page-wide blocks, both the `No.` regex and the season-only patterns, in `if edition is None:`, so they run only on pages without the header. Don't change those blocks otherwise.

  Just before `metadata["publication"] = "The New Atlantis"`, add the date fallback:

```python
        if not metadata.get("date_published") and metadata.get("issue_season"):
            parts = metadata["issue_season"].split()
            if len(parts) == 2 and parts[1].isdigit():
                fallback = season_start_date(parts[0], int(parts[1]))
                if fallback:
                    metadata["date_published"] = fallback
```

  In `main()`, add to `metadata_parser`:

```python
    metadata_parser.add_argument(
        "--json", action="store_true", default=False,
        help="Print the extracted metadata as one JSON object instead of YAML",
    )
```

  At the end of `_run_metadata`, replace the last two lines with:

```python
    if getattr(args, "json", False):
        print(json.dumps(metadata, ensure_ascii=False))
        return
    markdown_header = format_markdown_header(metadata, args.creation_date)
    print(markdown_header)
```

- [ ] **Step 4: Run the whole suite and see it pass,** with lint. Run `uv run python -m pytest -q && uv run ruff check . && uv run ruff format --check .`. Expected: all pass. The existing JSON-LD test (a top-level `"@type": "Article"`) passes too, because `_jsonld_nodes` handles a plain dict.
- [ ] **Step 5: Commit, as separate commits:**
  - "Read the New Atlantis JSON-LD @graph and resolve author references";
  - "Extract the article description";
  - "Read the edition from the article header (not the site-wide nav); fall back to the season start date";
  - "metadata --json".

  Use `git add -p` to split them.
- [ ] **Step 6: Open the PR** from the bot's fork, closing #3. Its description says:
  - "What breaks if wrong: the YAML header for New Atlantis articles now takes the title and author from the page's structured data rather than its headline and byline. They should be identical; if not, you'd see a different title or author in a note's header";
  - and the commit-policy note from Global Constraints.

---

### Task 3: agentbox entrypoint runs a given command (after the firewall, as `node`)

**Files:**
- Modify: `image/entrypoint.sh` (last line)
- Create: `image/tests/test_entrypoint.sh`, `image/tests/lib.sh` (a plain-bash test harness: stub commands on `PATH`, record calls)

**Interfaces:**
- Produces: `docker run agentbox:latest <cmd> [args…]` runs `<cmd>` as `node` after `init-firewall.sh`. With no arguments it behaves exactly as today (`agentbox-start.sh`). Plan 3's images rely on this.

- [ ] **Step 1: Write the harness**, `image/tests/lib.sh`:

```bash
#!/bin/bash
# Minimal test harness: stub commands on PATH and record each invocation.
set -euo pipefail
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin"; : > "$T/calls"
stub() {  # stub NAME [EXIT_CODE] — records "NAME args…" to $T/calls
  local name="$1" code="${2:-0}"
  printf '#!/bin/bash\necho "%s $*" >> "%s/calls"\nexit %s\n' "$name" "$T" "$code" > "$T/bin/$name"
  chmod +x "$T/bin/$name"
}
fail() { echo "FAIL: $*"; exit 1; }
calls() { cat "$T/calls"; }
```

- [ ] **Step 2: Write the failing test,** `image/tests/test_entrypoint.sh`. It runs a copy of `entrypoint.sh` with the absolute paths rewritten to stubs:

```bash
#!/bin/bash
source "$(dirname "$0")/lib.sh"
ENTRY="$(dirname "$0")/../entrypoint.sh"
for c in gosu stat chown id; do stub "$c"; done
stub fw   # stands in for /usr/local/bin/init-firewall.sh
stub start  # stands in for agentbox-start.sh
sed -e "s#/usr/local/bin/init-firewall.sh#$T/bin/fw#; s#/usr/local/bin/agentbox-start.sh#$T/bin/start#" "$ENTRY" > "$T/entry.sh"

# With a command: firewall first, then gosu node <cmd>, never <cmd> directly.
: > "$T/calls"
PATH="$T/bin:$PATH" bash "$T/entry.sh" /usr/bin/python3 -m runner >/dev/null 2>&1 || true
calls | head -1 | grep -qx "fw " || fail "firewall did not run first: $(calls)"
calls | grep -qx "gosu node /usr/bin/python3 -m runner" || fail "command not run via gosu node: $(calls)"

# Without a command: unchanged default.
: > "$T/calls"
PATH="$T/bin:$PATH" bash "$T/entry.sh" >/dev/null 2>&1 || true
calls | grep -qx "gosu node $T/bin/start" || fail "default start changed: $(calls)"

# Firewall failure: nothing else runs (fail closed).
stub fw 1; : > "$T/calls"
PATH="$T/bin:$PATH" bash "$T/entry.sh" /usr/bin/true >/dev/null 2>&1 && fail "entrypoint succeeded despite firewall failure"
calls | grep -q "^gosu" && fail "ran a command after the firewall failed: $(calls)"
echo "PASS test_entrypoint"
```

- [ ] **Step 3: Run it and see it fail.** Run `bash image/tests/test_entrypoint.sh`. Expected: `FAIL: command not run via gosu node`, because today it always runs the start script.
- [ ] **Step 4: Implement.** Replace the last line of `image/entrypoint.sh`:

```bash
log "dropping privileges to node"
if [ "$#" -gt 0 ]; then
    exec gosu node "$@"
fi
exec gosu node /usr/local/bin/agentbox-start.sh
```

- [ ] **Step 5: Run it and see it pass.** Run `bash image/tests/test_entrypoint.sh`. Expected: `PASS test_entrypoint`.
- [ ] **Step 6: Commit:** "entrypoint: run a given command as node after the firewall".

---

### Task 4: agentbox `AGENTBOX_GITHUB=off` leaves GitHub out of the egress allowlist

**Files:**
- Modify: `image/init-firewall.sh`, the GitHub-ranges block near line 112
- Create: `image/tests/test_firewall_github.sh`

**Interfaces:**
- Produces: the env var `AGENTBOX_GITHUB`, either `on` (the default, today's behaviour) or `off`. With `off`, no request goes to `api.github.com/meta`, and no GitHub ranges are added.

- [ ] **Step 1: Write the failing test,** `image/tests/test_firewall_github.sh`. It stubs every external command the script calls, and makes `curl` fail as if GitHub were unreachable:

```bash
#!/bin/bash
source "$(dirname "$0")/lib.sh"
FW="$(dirname "$0")/../init-firewall.sh"
for c in iptables ip6tables ipset ip aggregate dig; do stub "$c"; done
stub curl 7          # GitHub unreachable
printf '#!/bin/bash\necho "jq $*" >> "%s/calls"\ncat >/dev/null; echo "{}"\n' "$T" > "$T/bin/jq"; chmod +x "$T/bin/jq"
printf 'nameserver 127.0.0.11\n' > "$T/resolv.conf"
sed -e "s#/etc/resolv.conf#$T/resolv.conf#g" "$FW" > "$T/fw.sh"

: > "$T/calls"
AGENTBOX_NET=restricted AGENTBOX_GITHUB=off PATH="$T/bin:$PATH" bash "$T/fw.sh" >/dev/null 2>&1 \
  || fail "firewall failed with GitHub off and unreachable: $(calls | tail -5)"
calls | grep -q "^curl .*api.github.com" && fail "fetched GitHub meta despite AGENTBOX_GITHUB=off"

: > "$T/calls"
AGENTBOX_NET=restricted PATH="$T/bin:$PATH" bash "$T/fw.sh" >/dev/null 2>&1 \
  && fail "default should still fail closed when GitHub meta is unreachable"
echo "PASS test_firewall_github"
```

- [ ] **Step 2: Run it and see it fail.** Run `bash image/tests/test_firewall_github.sh`. Expected: `FAIL: firewall failed with GitHub off and unreachable`. Domain resolution with `dig` stubbed may also need the stub to print an IP. If the first failure is about resolving `api.anthropic.com`, make the `dig` stub print `160.79.104.10`, then rerun until the failure is the GitHub one.
- [ ] **Step 3: Implement.** Wrap the GitHub block in `init-firewall.sh`, from `log "fetching GitHub IP ranges..."` through `log "GitHub: ${#resolved_ips[@]} ranges"`:

```bash
resolved_ips=()
if [ "${AGENTBOX_GITHUB:-on}" = "off" ]; then
    log "GitHub: left out (AGENTBOX_GITHUB=off)"
else
    log "fetching GitHub IP ranges..."
    # … the existing fetch / validate / aggregate lines, unchanged …
    log "GitHub: ${#resolved_ips[@]} ranges"
fi
```

  The existing `resolved_ips=()` line moves above the `if`, so later code that reads the array still works when it's empty.
- [ ] **Step 4: Run both agentbox tests and see them pass.** Run `bash image/tests/test_firewall_github.sh && bash image/tests/test_entrypoint.sh`.
- [ ] **Step 5: Document it.** Add `AGENTBOX_GITHUB=on` to `env.example`, with the comment "off = no GitHub in the egress allowlist (for agents that never touch git)". Add one line to the README's egress section.
- [ ] **Step 6: Commit:** "firewall: AGENTBOX_GITHUB=off leaves GitHub out of the allowlist".

---

### Task 5: agentbox: uv in the image (agentbox #6)

**This task's spec is agentbox issue #6, which is already written: install from PyPI only, pin an exact version with a 14-day cooldown, verify hashes, and use a venv because of PEP 668.** Read it in full: `gh api repos/brianroberg/agentbox/issues/6 --jq .body`. Implement exactly what it says; this plan doesn't restate it, so that the two can't drift apart.

**Files:**
- Create: `image/uv.requirements.txt` (the pinned version plus hashes, generated as the issue describes)
- Modify: `image/Dockerfile` (the install step, as the issue describes)
- Create: `image/tests/test_uv_pin.sh`

- [ ] **Step 1: Write the failing test,** `image/tests/test_uv_pin.sh`:

```bash
#!/bin/bash
source "$(dirname "$0")/lib.sh"
REQ="$(dirname "$0")/../uv.requirements.txt"
DF="$(dirname "$0")/../Dockerfile"
[ -f "$REQ" ] || fail "uv.requirements.txt missing"
grep -Eq '^uv==[0-9]+\.[0-9]+\.[0-9]+' "$REQ" || fail "uv not pinned to an exact version"
grep -q -- '--hash=sha256:' "$REQ" || fail "no hashes in uv.requirements.txt"
grep -q -- '--require-hashes' "$DF" || fail "Dockerfile does not install with --require-hashes"
grep -Eq 'astral\.sh/uv/install\.sh|github\.com/astral-sh/uv/releases' "$DF" && fail "Dockerfile uses a forbidden install source"
echo "PASS test_uv_pin"
```

- [ ] **Step 2: Run it and see it fail.** Run `bash image/tests/test_uv_pin.sh`. Expected: `FAIL: uv.requirements.txt missing`.
- [ ] **Step 3: Implement it per issue #6.** Generate `uv.requirements.txt` from `https://pypi.org/pypi/uv/json`, choosing the newest version whose files are at least 14 days old, with hashes for the `manylinux` x86_64 wheel. Add the Dockerfile step the issue specifies.
- [ ] **Step 4: Run all three agentbox tests and see them pass.**
- [ ] **Step 5: Commit:** "image: install uv from PyPI, pinned and hash-verified (#6)".
- [ ] **Step 6: Open one PR for Tasks 3–5** from the bot's fork, closing #6. Its description says:
  - "What breaks if wrong: a sandbox that fails to start (Tasks 3–4), or an image build that fails (Task 5). You'd see it at the next `./deploy.sh` or `agentbox new`. With no arguments and `AGENTBOX_GITHUB` unset, behaviour is unchanged";
  - "Not verified here: an actual `docker build`. This environment has no Docker, so the first build happens on the VM."
