# Unattended article-to-podcast pipeline: design

**Status:** draft for Brian's review, 2026-10-14. Not approved yet. Nothing gets built until it is.
**Decided with:** Brian, by voice, 2026-10-14. The assistant made the calls under "Decisions made here (open to veto)".
**Relationship to ADR-001:** an agent still orchestrates each recording and makes the judgment calls. What changes is that the session runs on a schedule, with nobody at the keyboard. See `docs/adr-002-unattended-runs.md`.

## Goal

New articles from the sources Brian follows appear as checked episodes in his Audiobookshelf podcast feed, with nothing for him to do. He hears about an article only when it needs his judgment.

## What Brian decided

- **Intake is automatic from sources he follows.** He doesn't pick individual articles.
- **The first source is The New Atlantis, every article.** Back issues are out of scope. On the first run, everything older than the **Fall 2026 edition (No. 86)** counts as back catalog. The Fall 2026 edition itself gets recorded.
- **Nothing is New Atlantis-specific beyond its config entry.** v1 has one source, but no choice may make a second source harder than adding an entry, plus an extractor if the site needs one.
- **The work runs overnight on the MacBook,** at home and on power. Episodes don't need to appear the night an article is posted.
- **Quality rule:**
  - When only minor issues remain (for example a mispronounced name), the episode publishes, with the issues listed in its notes.
  - When content is missing or garbled, it's held and Brian gets a push saying why.
- **Notices come by push.** Holds and repeated failures notify at once. A summary of the night's results arrives each morning.
- **The agent runs on Brian's Claude subscription.**
- **Approach: a nightly job on the Mac, as a first implementation.** Brian's longer-term aim is to contain agents off his main laptop. The design keeps a seam so the agent can move later (see "Containment").
- **Cost is measured per article.** The cap of 5 articles a night is a starting point, tuned from real numbers once it runs.
- **Property-based tests are in scope** (see "Testing").

## Not in v1

- More than one source.
- Testing new TTS models (roadmap item 3).
- Removing the Gemini dependency, and TTS models that can redo just a passage instead of the whole recording (future work).
- A line in the morning briefing. The morning push covers the summary.
- Back issues.

## Architecture

Each night a scheduled job on the Mac runs these steps in order:

1. **Discover.** For each source, list candidate articles and add any new in-scope ones to the record as `queued`.
2. **Pick.** Take up to 5 `queued` articles, oldest first.
3. **Per article:**
   1. **Fetch (runner).** Run article-assistant to save the article's text and metadata in its work folder. This step is deterministic; the agent doesn't fetch anything.
   2. **Record (agent session).** One headless Claude Code session prepares the text, records, checks and fixes, following the skill. It ends by writing `result.json`.
   3. **Gate (runner).** Validate `result.json`, then run the word-preservation check (see "Quality gate").
   4. **Publish (runner).** Convert to MP3 with tags, upload to Audiobookshelf with episode notes, and record the outcome.
4. **Notify.** Send pushes for holds and for repeated failures, plus one morning summary if anything happened.

**Who does what, and why.** The runner, which is plain code, does everything that needs the network or a credential: fetching, publishing and pushing. The agent session gets only what judgment is needed for: preparing the text, deciding what to fix, and re-recording. So the agent never holds the Audiobookshelf key or the push token, and never needs open web access. Its only network use is the check step, which calls Gemini through a fixed command.

### Containment

- **A separate macOS user** (working name `kokoro-agent`) owns the work. It has its own checkouts of this repo and article-assistant, its own data folder, and its own Claude Code login. It can't read Brian's home folder or files.
- **The agent's tools are an allow-list,** set in the session's Claude Code settings:
  - **Bash runs only three commands,** and nothing else:
    - `record`, which wraps `text_to_speech.py`;
    - `check`, which wraps `verify_audio.py`;
    - `phonemes`, which looks up a pronunciation with misaki's G2P.
  - **File reads and edits stay inside that article's work folder.**
  - **No web tools.**
- **Keys live with the wrappers, not the agent's environment.** Each wrapper loads only the key it needs (the Gemini key for `check`).
- **The seam for moving the agent later.** The agent touches the work only through those three commands plus edits to `prepared.md`. Later, the commands can become calls to a small service on the Mac over the tailnet, and the agent can run in a sandbox elsewhere (roadmap item 4). The skill and the result contract stay the same.

Approaches considered and set aside:
- **An agent on the home server, calling into the Mac.** The assistant that would host it holds unrelated credentials, so a hostile article would put more at risk there, not less.
- **A fixed script, with the model asked only which issues to fix.** It's the most contained option, but it reverses part of ADR-001 and can't test pronunciation fixes the way the skill does.

## Sources

Each source is an entry in `sources.toml`:

```toml
[[source]]
id = "new-atlantis"
name = "The New Atlantis"
podcast = "The New Atlantis"        # Audiobookshelf podcast the episodes go to
feed = "https://www.thenewatlantis.com/feed"
listing = "new-atlantis-issues"     # optional extra discovery, named per site
extractor = "new-atlantis"          # article-assistant mode
start = { edition = 86, date = "2026-09-08" }
text_rules = []                     # source-specific preparation notes for the agent
```

- **Discovery** reads the feed, plus the optional per-site listing. The New Atlantis feed returned only the 10 most recent items on 2026-10-14, and nine of them came out together on 9/8. An issue with more than 10 articles would overflow the feed, so the listing (the site's `/issues` page, which shows "No. 86 Fall 2026") fills the gap. *How exactly to read an edition's article list from that page gets settled in the implementation plan, against the live page.*
- **In scope** means the article's edition is 86 or later. A piece without an edition (web-only) is in scope if it was published on or after the start date.
- **First run:** everything discovered that's out of scope is recorded as `baseline` (seen, never recorded).

## State and work folders

**The record** is one SQLite file in the agent user's data folder, with one row per article, keyed by its URL. Each row holds:
- the source, title, edition and publication date;
- its status: `baseline`, `queued`, `in_progress`, `published`, `published_with_notes`, `held` or `failed`;
- the number of failed nights and the last error;
- the remaining issues, and the number of rounds;
- the Claude tokens and cost (from `claude -p --output-format json`), an estimated Gemini cost, and the run time;
- the Audiobookshelf episode id, and timestamps.

**Each article gets a work folder,** `data/work/<source>/<slug>/`, which is kept after the run:
- `source.md` and `metadata.yaml`, as fetched;
- `prepared.md`, the agent's edited text;
- `report-<n>.txt`, one per check;
- `result.json`, and the final WAV and MP3.

These folders later become the test set for comparing TTS models.

## The agent session's contract

**Input:** the work folder, the skill, and the source's `text_rules`.

**Output:** `result.json`:

```json
{
  "outcome": "clean | minor | hold",
  "rounds": 2,
  "remaining_issues": [{"kind": "mispronunciation", "where": "12:40", "note": "Pengelley"}],
  "substitutions": [{"from": "XIV", "to": "the Fourteenth", "why": "roman numeral"}],
  "audio": "recording.wav"
}
```

- **At most 4 record-and-check rounds per article.** `record` counts its calls per work folder and refuses a fifth, so the limit doesn't rest on the prompt alone.
- **`hold` is for missing or garbled content.** `minor` is for issues the skill would "likely ignore" or couldn't fix.
- **Every word change goes in `substitutions`.** The quality gate rejects any undeclared change.

## Quality gate (runner, deterministic)

1. **Validate `result.json`** against its schema. If it's invalid or missing, the article is marked `failed`.
2. **Word-preservation check.** This enforces the skill's rule, "never change what the article says." Both texts are reduced to plain words. In `prepared.md`, a `[word](/phonemes/)` override is read as its displayed word. Allowed punctuation and pause changes are set aside, and each declared substitution is reversed. The two word sequences must then match exactly; any difference holds the article. This also stops a hijacked session from adding words of its own.
3. **Outcome:**

   | Outcome | Action |
   |---|---|
   | `clean` | Publish. |
   | `minor` | Publish, with the issues in the episode notes. |
   | `hold` | Hold, and push the reason. |
   | A gate failure | Hold. |
   | A tool or network error | Mark `failed` and retry the next night. After 3 failed nights, hold and push. |

## Publishing

- **The episode title, notes and date come from the metadata.** This needs article-assistant #3 (the New Atlantis description and publication date).
- **`publish_audio.py` must learn to set an episode's description.** Today it uploads only a file and a title.
- **Publishing is idempotent.** It only runs from `in_progress`, and the episode id is saved, so a rerun can't double-publish.

## The nightly job

- A launchd job at 01:00. launchd runs a missed calendar job at the next wake, so a night the Mac was asleep or away just shifts the work.
- **One run at a time,** enforced by a lock file.
- **No new article starts after 06:00.**
- **Dry-run mode** does everything except publish and push.
- **The pushes** use a dedicated push topic and token for this job, not one shared with other services.

## Testing

**Built test first throughout.** Pure logic is kept apart from I/O, so it's tested without network calls.

- **Example tests, with saved fixtures** (a feed sample and an issue-page sample):
  - discovery parsing and the in-scope rule;
  - the first-run baseline;
  - every state transition;
  - the cap and the ordering;
  - `result.json` validation;
  - outcome mapping;
  - the summary text.
- **Property tests (Hypothesis):**
  - No article is ever published twice, across any sequence of runs, crashes and reruns.
  - Every article processed in a run ends in exactly one status.
  - A night never starts more than the cap.
  - Nothing out of scope is ever queued.
  - **Word preservation:**
    - for any article text and any mix of allowed edits plus declared substitutions, the check passes;
    - for any undeclared word change (insert, delete or replace), it fails.
- **Containment tests:**
  - A scripted check confirms that `kokoro-agent` can't read Brian's home folder.
  - A planted test article carries hidden instructions, for example to fetch a URL or edit a file outside the folder. The session must make no calls outside the allow-list, and any text it inserts must fail the word check.
- **Acceptance:** one real article end to end in dry run, then published. Brian listens to it before the schedule is turned on.

## Order of work

0. **Prerequisites:**
   - this repo's #6 (rename the skill to `recording-article-audio`);
   - article-assistant #3 (the New Atlantis description and publication date).
1. **v1, as above.**
2. **Cost review** after a couple of weeks, to set the cap from the recorded numbers.
3. **A model-testing harness** that replays saved work folders through a candidate TTS or checking model. This also covers this repo's #3 (local models for the check).
4. **Move the agent off the laptop.** The three commands become a tailnet service on the Mac, and the agent runs in a sandbox elsewhere.
5. **More sources.**

Future work Brian named: remove the Gemini dependency, and use newer TTS models that can redo a single passage instead of the whole recording.

## Decisions made here (open to veto)

| Decision | Why | If it's wrong |
|---|---|---|
| The runner fetches and publishes; the agent doesn't | Keeps the Audiobookshelf key, the push token and open web access out of the agent's reach | Little lost; the skill's fetch and publish steps simply move to code |
| SQLite for the record | One file; safe updates mid-run | A JSON file would also work; it's a small swap |
| Code lives in this repo, in a `pipeline/` folder | Uses the toolkit directly, and keeps article-assistant light (ADR-001) | Moving it to its own repo later is mechanical |
| Web-only pieces count from the start date | Brian asked for every article; the edition rule can't cover pieces without an edition | A web-only piece could be recorded that he'd have skipped. He'd see it in the feed and the summary |
| Up to 4 rounds per article | The skill expects 2–4 for a long article | Too low means more holds; too high costs more. Both show up in the recorded numbers |
| Start at 01:00, no new article after 06:00 | Overnight, and done before he's up | Change the launchd times |

## Open questions to settle early in the plan

- **Does a scheduled wake work on this MacBook when it's closed and on power?** If not, it has to be left open and awake overnight (for example with `caffeinate`).
- **Can a second macOS user run headless Claude Code on Brian's subscription?** This needs to be confirmed, along with how its login is kept.
- **How does The New Atlantis's `/issues` page mark which articles belong to an edition?** This decides how the listing is read.
