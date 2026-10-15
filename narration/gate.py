"""The authoritative pre-publish gate (spec: "Quality gate").

finish accepts outcome clean or minor only when its claims hold; the gate checks
them again on the files as they are at finalize time, with the code finish uses
where it can, so it can't publish what finish would have refused, nor refuse what
finish accepted:
- the finish record has the shapes finish allows (agent_tools.validate_finish:
  only an issue's kind and timestamp, and each substitution, reach the notes);
- the latest record round left a recording, of the text prepared.md holds now
  (state["recorded_sha256"]);
- a check of that round returned a report (agent_tools._latest_checks: a failed
  check is counted in checks_used but has no entry), and its pre-check found
  nothing, no truncation and no long silence;
- the prepared text says what the article says, with exactly the declared
  substitutions, and every override's phonemes came from a lookup
  (agent_tools.validate_substitutions, the code finish runs);
- the engine would say the same for the prepared text as for the article with
  those substitutions and the prepared text's overrides (speech_matches, which
  finish runs too). The word check can't fully mirror how misaki reads signs,
  symbols and abbreviations; this compares misaki's own output.
An agent's outcome "hold" is a hold, whatever else is true: nothing held is
published, so its claims aren't checked, as finish doesn't check them. Pure,
apart from the G2P it is handed."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from narration.agent_tools import (ToolError, _issue, _latest_checks, _sha256, _substitution,
                                   validate_finish, validate_substitutions)
from narration.words import _PHONETIC, Substitution, apply_with_reason, check, engine_text

# misaki's stress and length marks: kept in the comparison with the letters. Its
# punctuation, pause marks and spaces are not compared.
_MARKS = frozenset("ˈˌːˑ")
_AROUND = 20  # phoneme characters shown on each side of a difference


@dataclass
class GateResult:
    action: str                      # "publish" | "hold" | "retry"
    outcome: str | None
    reasons: list[str] = field(default_factory=list)
    notes: str = ""


_TEXT_FIELDS = ("title", "publication", "issue_number", "issue_season", "date_published",
                "description")


def _text(value) -> str:
    """A metadata field as text: a string (stripped), a number as written, else ""."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _meta(metadata) -> dict:
    """The metadata as the notes, the MP3 tags and status read it, whatever shape
    article-assistant gave it: each text field a string ("" when missing, null or not
    text), and authors a list of non-empty strings (a lone string is one author;
    null, empty and non-text entries are dropped)."""
    if not isinstance(metadata, dict):
        metadata = {}
    authors = metadata.get("authors")
    if isinstance(authors, str):
        authors = [authors]
    authors = [a.strip() for a in authors if isinstance(a, str) and a.strip()] \
        if isinstance(authors, list) else []
    return {**{k: _text(metadata.get(k)) for k in _TEXT_FIELDS}, "authors": authors}


def album_for(metadata: dict) -> str:
    """"<publication>, No. <n> (<season>)", or just the publication when there's no
    edition."""
    meta = _meta(metadata)
    edition = (f"No. {meta['issue_number']} ({meta['issue_season']})"
               if meta["issue_number"] and meta["issue_season"] else "")
    return ", ".join(part for part in (meta["publication"], edition) if part)


def notes_for(metadata: dict, finish: dict) -> str:
    """The published episode notes. An issue appears as its timestamp and kind only,
    which agent_tools checks against a pattern and a fixed list; its free-text note
    is for Brian, in result.json. Every declared substitution is listed,
    override-respellings included, its original and spoken text as written: agent_tools
    allows one line of plain text for each. Each entry is checked again here with
    finish's own validators, and one that fails (in a record written before a rule
    existed) is left out."""
    issues = _valid(_issue, finish.get("remaining_issues"))
    subs = _valid(_substitution, finish.get("substitutions"))
    parts = [_meta(metadata)["description"]]
    if issues:
        parts.append("Known issues in this recording:")
        parts += [f"- {i['where']} {i['kind']}" for i in issues]
    if subs:
        parts.append("Read with these substitutions:")
        parts += [f"- \"{s['original']}\" read as \"{s['spoken']}\"" for s in subs]
    parts.append("Recorded automatically.")
    return "\n".join(p for p in parts if p)


def _valid(validator, entries) -> list[dict]:
    """The entries that pass `validator` (agent_tools' own), as it returns them."""
    valid = []
    for entry in entries if isinstance(entries, list) else []:
        try:
            valid.append(validator(entry))
        except ToolError:
            continue
    return valid


def speech_matches(g2p, source: str, prepared: str, substitutions: list[Substitution]) -> list[str]:
    """Problems with what the engine would say for `prepared`, against what it would
    say for the article with the declared substitutions and the prepared text's
    overrides (words.apply); [] when the two sound the same. Both texts are read as
    the engine reads them (words.engine_text), through the engine's own G2P, and
    compared as letters and stress and length marks only, so pacing punctuation,
    pauses and paragraph breaks are free. One problem at most, for the first place
    they differ."""
    applied, why = apply_with_reason(source, prepared, substitutions)
    if applied is None:
        if check(source, prepared, substitutions):  # it says where; this only says so
            why += " (the word check says where)"
        return [f"{why}, so what the engine would say could not be compared with the article"]
    article_text = engine_text(applied)
    said, heard = g2p.phonemes_for_text(engine_text(prepared)), g2p.phonemes_for_text(article_text)
    (a, a_at), (b, b_at) = _sounds(said), _sounds(heard)
    if a == b:
        return []
    d = len(os.path.commonprefix([a, b]))
    pa = a_at[d] if d < len(a_at) else len(said)
    pb = b_at[d] if d < len(b_at) else len(heard)
    line = heard.count("\n", 0, pb)
    lines = [text for text in re.split(r"\n+", article_text.strip()) if text.strip()]
    begins = " ".join(_PHONETIC.sub(r"\1", lines[line]).split()[:8]) if line < len(lines) else ""
    return [f"the engine would not say what the article says: in line {line + 1} of the article "
            f"as read aloud (beginning {begins!r}), the prepared text sounds like "
            f"'{_around(said, pa)}' where the article, with the declared substitutions, sounds "
            f"like '{_around(heard, pb)}'"]


def _sounds(phonemes: str) -> tuple[str, list[int]]:
    """The phonemes as compared (letters and misaki's stress and length marks), and
    where each kept character is in `phonemes`."""
    at = [k for k, ch in enumerate(phonemes) if ch.isalpha() or ch in _MARKS]
    return "".join(phonemes[k] for k in at), at


def _around(phonemes: str, at: int) -> str:
    start, end = max(0, at - _AROUND), at + _AROUND
    shown = phonemes[start:end].replace("\n", " / ")
    return ("…" if start else "") + shown + ("…" if end < len(phonemes) else "")


def evaluate(state: dict, source: str, prepared: str, metadata: dict, g2p) -> GateResult:
    finish = state.get("finish")
    if not finish:
        return GateResult("retry", None, ["the session ended without calling finish"])
    try:
        issues, subs = validate_finish(finish.get("outcome"), finish.get("remaining_issues"),
                                       finish.get("substitutions"))
    except ToolError as e:
        # Nothing from a malformed record reaches the notes.
        return GateResult("hold", None, [f"the finish record is malformed: {e}"],
                          notes_for(metadata, {}))
    outcome = finish["outcome"]
    notes = notes_for(metadata, {"remaining_issues": issues, "substitutions": subs})
    if outcome == "hold":
        held = "; ".join(f"{i['where']} {i['kind']}: {i['note']}" for i in issues)
        return GateResult("hold", outcome, ["the agent held it" + (f" ({held})" if held else "")],
                          notes)
    reasons = _recording_problems(state, prepared)
    try:
        validate_substitutions(state, source, prepared, subs)
    except ToolError as e:
        reasons.append(str(e))
    if not reasons:  # the slow check last: its answer can't change a hold
        try:
            reasons += speech_matches(g2p, source, prepared,
                                      [Substitution(s["original"], s["spoken"]) for s in subs])
        except RuntimeError as e:  # the G2P adapter's failures; a later finalize retries
            reasons.append(f"the speech comparison could not run: {e}")
    return GateResult("hold" if reasons else "publish", outcome, reasons, notes)


def _recording_problems(state: dict, prepared: str) -> list[str]:
    """What finish's claims about the recording say, checked again: the latest round
    recorded this text, and a check of it returned a report with no pre-check warnings."""
    n = state.get("records", 0)
    if not n:
        return ["nothing was recorded"]
    if state.get("recorded_round") != n:
        return [f"record round {n} left no recording"]
    problems = []
    if _sha256(prepared) != state.get("recorded_sha256"):
        problems.append(f"the prepared text changed after round {n} was recorded, so the "
                        "recording does not say it")
    latest = _latest_checks(state)
    if not latest:
        problems.append(f"no check of the latest recording (round {n}) returned a report")
    elif latest[-1]["warnings"]:
        problems.append("the latest check's pre-check flagged: " + "; ".join(latest[-1]["warnings"]))
    return problems
