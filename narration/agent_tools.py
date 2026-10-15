"""The six tools an agent session has. Plain functions over a work item;
the HTTP layer resolves the item from the caller's token (agent never names it).

Every tool holds its item's lock for the whole call. The SDK runs sync tools in
worker threads, and two parallel record calls at round 3 would otherwise both
pass the limit. A call that finds the lock held is refused, not queued. Every
read-modify-write of the item's state.json happens inside that lock.

An override's phonemes must come from the phonemes tool. words.check reads a
[word](/phonemes/) override as its displayed word and can't know what the
phonemes say, so a session could voice anything under the override's length cap.
So phonemes() records every lookup in the item's state, and finish accepts an
override only if its phonemes are what a lookup returned for its displayed word
or a respelling of it (words.is_respelling, the rule substitutions use)."""
from __future__ import annotations

import functools
import json
import os
import re

from narration.words import (MAX_SUBSTITUTIONS, Substitution, check as words_check,
                             is_respelling, max_prepared_length, overrides)

MAX_RECORDS = 4
MAX_CHECKS = 8
OUTCOMES = ("clean", "minor", "hold")
# Matched with fullmatch: `$` alone would also accept a trailing newline.
VOICE_RE = re.compile(r"^[a-z]{2}_[a-z]{2,20}$")
MAX_FIELD = 200
# An issue is a kind from this list, a timestamp, and a short note. Only the kind
# and the timestamp reach the published episode notes (gate.notes_for); the note
# is for Brian, in result.json. So no text a session writes can land in the feed.
ISSUE_KINDS = ("mispronunciation", "pacing", "artifact", "truncation", "other")
# m:ss to mmm:ss in ASCII digits, seconds below 60; matched with fullmatch.
WHERE_RE = re.compile(r"^[0-9]{1,3}:[0-5][0-9]$")
MAX_ISSUES = 20
MAX_NOTE = 80
MAX_EDIT_TEXT = 20_000  # characters in replace_text's old, and in its new
MAX_WORD = 64           # characters in a word given to phonemes()
MAX_LOOKUPS = 200       # phonemes() lookups an item keeps, the latest
MAX_SHOWN = 5           # unproven overrides one refusal names


class ToolError(Exception):
    """A refusal the agent can act on. Its message is shown to the agent."""


def _short(value, field, limit=MAX_FIELD):
    if not isinstance(value, str) or len(value) > limit:
        raise ToolError(f"{field} must be a string of at most {limit} characters")
    return value


def _issue(i):
    if not isinstance(i, dict):
        raise ToolError("each remaining issue must be an object with kind, where and note")
    kind, where = i.get("kind"), i.get("where")
    if kind not in ISSUE_KINDS:
        raise ToolError(f"an issue's kind must be one of {ISSUE_KINDS}; got {kind!r}")
    if not isinstance(where, str) or not WHERE_RE.fullmatch(where):
        raise ToolError(f"an issue's where must be a timestamp like 12:40; got {where!r}")
    return {"kind": kind, "where": where, "note": _short(i.get("note", ""), "note", MAX_NOTE)}


def _substitution(s):
    if not isinstance(s, dict):
        raise ToolError("each substitution must be an object with original, spoken and why")
    return {"original": _short(s.get("original"), "original"),
            "spoken": _short(s.get("spoken"), "spoken"),
            "why": _short(s.get("why", ""), "why")}


def _speed(speed) -> float:
    if isinstance(speed, bool) or not isinstance(speed, (int, float)):
        raise ToolError(f"speed must be a number between 0.5 and 2.0; got {speed!r}")
    if not 0.5 <= speed <= 2.0:  # NaN fails this too
        raise ToolError("speed must be between 0.5 and 2.0")
    return float(speed)


def _matches(text: str, old: str) -> int:
    """How many times `old` appears in `text`; 2 or more also when two appearances
    overlap ("aa" in "aaa"), which str.count counts once."""
    first = text.find(old)
    if first < 0:
        return 0
    if text.find(old, first + 1) < 0:
        return 1
    return max(2, text.count(old))


def _unproven_overrides(state: dict, prepared: str, limit: int) -> list[str]:
    """A message for each override in `prepared` whose phonemes no recorded lookup
    returned for its displayed word or a respelling of it, stopping at `limit`: each
    override is compared with every lookup that returned its phonemes (up to
    MAX_LOOKUPS), so scanning thousands of unproven ones would take seconds."""
    looked_up: dict[str, set[str]] = {}
    for lookup in state.get("phoneme_lookups", []):
        looked_up.setdefault(lookup["phonemes"], set()).add(lookup["word"])
    problems = []
    for shown, phonemes in dict.fromkeys(overrides(prepared)):
        if not any(is_respelling(shown, word) for word in looked_up.get(phonemes, ())):
            problems.append(f"override [{shown}](/{phonemes}/) was not produced by the phonemes "
                            f"tool for a respelling of {shown!r}; call phonemes({shown!r}) "
                            "and use its result")
            if len(problems) >= limit:
                break
    return problems


def _locked(method):
    """Run a tool method under its item's lock; refuse if another call holds it."""
    @functools.wraps(method)
    def wrapper(self, item_id, *args, **kwargs):
        lock = self.store.item_lock(item_id)
        if not lock.acquire(blocking=False):
            raise ToolError("another call is in progress for this work item; wait for it to finish")
        try:
            return method(self, item_id, *args, **kwargs)
        finally:
            lock.release()
    return wrapper


class AgentTools:
    def __init__(self, store, tts, verifier, g2p, default_voice="af_heart"):
        self.store, self.tts, self.verifier, self.g2p = store, tts, verifier, g2p
        self.default_voice = default_voice

    def _open_state(self, item_id):
        state = self.store.load_state(item_id)
        if state["status"] != "open":
            raise ToolError("this work item is finished; no further tool calls are accepted")
        return state

    @_locked
    def get_text(self, item_id):
        state = self._open_state(item_id)
        return {"prepared_text": self.store.read_text(item_id, "prepared.md"),
                "metadata": json.loads(self.store.read_text(item_id, "metadata.json")),
                "records_used": state["records"], "records_left": MAX_RECORDS - state["records"]}

    @_locked
    def replace_text(self, item_id, old, new):
        self._open_state(item_id)
        for field, value in (("old", old), ("new", new)):
            if not isinstance(value, str):
                raise ToolError(f"{field} must be a string")
            if len(value) > MAX_EDIT_TEXT:
                raise ToolError(f"{field} is longer than {MAX_EDIT_TEXT} characters")
        if not old:
            raise ToolError("old must not be empty")
        text = self.store.read_text(item_id, "prepared.md")
        found = _matches(text, old)
        if found != 1:
            raise ToolError(f"old must appear exactly once in the prepared text; found {found}")
        result = text.replace(old, new, 1)
        limit = max_prepared_length(len(self.store.read_text(item_id, "source.md")))
        if len(result) > limit:
            raise ToolError(f"this edit would make the prepared text {len(result)} characters "
                            f"long; the limit is {limit} (twice the article's length plus 10,000)")
        self.store.write_text(item_id, "prepared.md", result)
        return {"ok": True}

    @_locked
    def record(self, item_id, voice=None, speed=1.0):
        state = self._open_state(item_id)
        voice = voice or self.default_voice
        if not isinstance(voice, str) or not VOICE_RE.fullmatch(voice):
            raise ToolError(f"voice must look like 'af_heart'; got {voice!r}")
        speed = _speed(speed)
        if state["records"] >= MAX_RECORDS:
            raise ToolError(f"record limit reached ({MAX_RECORDS} per article); "
                            "finish with the best result so far, or with outcome 'hold'")
        state["records"] += 1  # counted before the engine runs
        self.store.save_state(item_id, state)
        text = self.store.read_text(item_id, "prepared.md")
        final = self.store.path(item_id, "recording.wav")
        tmp = final.with_name(".tmp-recording.wav")
        try:
            duration = self.tts.record(text, tmp, voice, speed)
            os.replace(tmp, final)  # the previous round's audio stays until this one succeeds
        finally:
            tmp.unlink(missing_ok=True)  # a failed run's partial file
        state["recorded_round"] = state["records"]
        self.store.save_state(item_id, state)
        return {"round": state["records"], "duration_seconds": round(duration, 1),
                "records_left": MAX_RECORDS - state["records"]}

    @_locked
    def check(self, item_id):
        state = self._open_state(item_id)
        if state["records"] == 0:
            raise ToolError("nothing to check yet: call record first")
        if state["recorded_round"] != state["records"] or not self.store.path(item_id, "recording.wav").exists():
            raise ToolError(f"record round {state['records']} failed and left no recording; call record again")
        if len(state["checks"]) >= MAX_CHECKS:
            raise ToolError(f"check limit reached ({MAX_CHECKS} per article)")
        text = self.store.read_text(item_id, "prepared.md")
        report, warnings = self.verifier.check(self.store.path(item_id, "recording.wav"), text)
        n = len(state["checks"]) + 1
        self.store.write_text(item_id, f"report-{n}.txt", report)
        state["checks"].append({"n": n, "record": state["records"], "warnings": list(warnings)})
        self.store.save_state(item_id, state)
        return {"report": report, "precheck_warnings": list(warnings)}

    @_locked
    def phonemes(self, item_id, word):
        state = self._open_state(item_id)
        if not isinstance(word, str) or not 0 < len(word) <= MAX_WORD:
            raise ToolError(f"word must be 1-{MAX_WORD} characters")
        out = str(self.g2p.phonemes(word))
        # finish accepts an override only with phonemes recorded here (module docstring).
        lookups = state.get("phoneme_lookups", []) + [{"word": word, "phonemes": out}]
        state["phoneme_lookups"] = lookups[-MAX_LOOKUPS:]
        self.store.save_state(item_id, state)
        return {"word": word, "phonemes": out}

    @_locked
    def finish(self, item_id, outcome, remaining_issues, substitutions):
        state = self._open_state(item_id)
        if outcome not in OUTCOMES:
            raise ToolError(f"outcome must be one of {OUTCOMES}")
        # Both counts are bounded before any entry is read, so a huge list costs nothing.
        if not isinstance(remaining_issues, list) or len(remaining_issues) > MAX_ISSUES:
            raise ToolError(f"remaining_issues must be a list of at most {MAX_ISSUES}")
        if not isinstance(substitutions, list) or len(substitutions) > MAX_SUBSTITUTIONS:
            raise ToolError(f"substitutions must be a list of at most {MAX_SUBSTITUTIONS} "
                            "substitutions")
        issues = [_issue(i) for i in remaining_issues]
        subs_in = [_substitution(s) for s in substitutions]
        if state["records"] and state["recorded_round"] != state["records"]:
            raise ToolError(f"record round {state['records']} failed and left no recording; "
                            "call record again, then check, before finish")
        latest = [c for c in state["checks"] if c["record"] == state["records"]]
        if state["records"] == 0 or not latest:
            raise ToolError("call check on the latest recording before finish")
        if outcome in ("clean", "minor") and latest[-1]["warnings"]:
            raise ToolError("the latest check's pre-check flagged: " + "; ".join(latest[-1]["warnings"])
                            + ". Fix and re-record, or finish with outcome 'hold'.")
        prepared = self.store.read_text(item_id, "prepared.md")
        problems = words_check(self.store.read_text(item_id, "source.md"), prepared,
                               [Substitution(s["original"], s["spoken"]) for s in subs_in])
        if problems:
            raise ToolError("the prepared text differs from the article: " + "; ".join(problems)
                            + ". Declare each word change in substitutions, or undo it.")
        unproven = _unproven_overrides(state, prepared, MAX_SHOWN + 1)
        if unproven:
            raise ToolError("; ".join(unproven[:MAX_SHOWN])
                            + ("; and more overrides like these" if len(unproven) > MAX_SHOWN else ""))
        state["finish"] = {"outcome": outcome, "remaining_issues": issues, "substitutions": subs_in}
        state["status"] = "finished"
        self.store.save_state(item_id, state)
        self.store.write_text(item_id, "result.json", json.dumps(state["finish"]))
        self.store.revoke(item_id)
        return {"ok": True}
