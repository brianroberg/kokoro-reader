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
or a respelling of it (words.is_respelling, the rule substitutions use).

The claims finish accepts are about the text that was recorded: record keeps the
sha256 of the exact text it handed the engine (state["recorded_sha256"], which
the gate checks too), and check and finish refuse once prepared.md differs from
it. outcome "hold" is always accepted, whatever was recorded or checked: nothing
held is published."""
from __future__ import annotations

import functools
import hashlib
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


def _sha256(text: str) -> str:
    """The hash of the text as prepared.md stores it (UTF-8)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _latest_checks(state: dict) -> list[dict]:
    return [c for c in state["checks"] if c["record"] == state["records"]]


def _checks_used(state: dict) -> int:
    """Check calls so far, failed ones included (state["checks"] holds the ones
    that returned a report)."""
    return state.get("checks_used", len(state["checks"]))


def _changed(state: dict) -> str:
    n = state["records"]
    tail = ("record again, or undo the edit" if n < MAX_RECORDS
            else "undo the edit (no record rounds are left), or finish with outcome 'hold'")
    return f"the prepared text changed since round {n} was recorded; {tail}"


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
        if not self.store.exists(item_id):
            raise ValueError("no such work item")
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
            raise ToolError(f"this work item is {state['status']}; no further tool calls are accepted")
        return state

    def _unchanged(self, item_id, state, text=None) -> bool:
        """Whether prepared.md is still the text the latest round recorded."""
        if text is None:
            text = self.store.read_text(item_id, "prepared.md")
        return _sha256(text) == state.get("recorded_sha256")

    def _what_next(self, item_id, state) -> str:
        """How the session can still finish, for a refusal at a limit."""
        n, latest = state["records"], _latest_checks(state)
        hold = "finish with outcome 'hold'"
        if (not n or state["recorded_round"] != n or (latest and latest[-1]["warnings"])
                or (not latest and _checks_used(state) >= MAX_CHECKS)):
            return hold
        steps = []
        if not self._unchanged(item_id, state):
            steps.append(f"undo the edits made since round {n} was recorded")
        if not latest:
            steps.append(f"call check on round {n}")
        return ", then ".join(steps + ["finish with outcome clean or minor"]) + f"; or {hold}"

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
        voice = self.default_voice if voice is None else voice
        if not isinstance(voice, str) or not VOICE_RE.fullmatch(voice):
            raise ToolError(f"voice must look like 'af_heart'; got {voice!r}")
        speed = _speed(speed)
        if state["records"] >= MAX_RECORDS:
            raise ToolError(f"record limit reached ({MAX_RECORDS} per article); "
                            + self._what_next(item_id, state))
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
        state["recorded_sha256"] = _sha256(text)  # what this round's audio says
        self.store.save_state(item_id, state)
        return {"round": state["records"], "duration_seconds": round(duration, 1),
                "records_left": MAX_RECORDS - state["records"]}

    @_locked
    def check(self, item_id):
        state = self._open_state(item_id)
        n = state["records"]
        if n == 0:
            raise ToolError("nothing to check yet: call record first")
        wav = self.store.path(item_id, "recording.wav")
        if state["recorded_round"] != n or not wav.exists():
            raise ToolError(f"record round {n} failed and left no recording; "
                            + ("call record again" if n < MAX_RECORDS
                               else "no record rounds are left, so finish with outcome 'hold'"))
        if _checks_used(state) >= MAX_CHECKS:
            raise ToolError(f"check limit reached ({MAX_CHECKS} per article); "
                            + self._what_next(item_id, state))
        text = self.store.read_text(item_id, "prepared.md")
        if not self._unchanged(item_id, state, text):
            raise ToolError(_changed(state))
        attempt = _checks_used(state) + 1
        state["checks_used"] = attempt  # counted before the verifier runs, as record is
        self.store.save_state(item_id, state)
        report, warnings = self.verifier.check(wav, text)
        self.store.write_text(item_id, f"report-{attempt}.txt", report)
        state["checks"].append({"n": attempt, "record": n, "warnings": list(warnings)})
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
        if outcome == "clean" and issues:
            raise ToolError("outcome 'clean' means no remaining issues: use 'minor' for small "
                            "ones, or fix them")
        if outcome != "clean" and not issues:
            raise ToolError(f"outcome {outcome!r} needs at least one remaining issue saying what "
                            "is wrong")
        if outcome != "hold":  # nothing held is published, so hold needs no claims
            self._check_claims(item_id, state, subs_in)
        state["finish"] = {"outcome": outcome, "remaining_issues": issues, "substitutions": subs_in}
        state["status"] = "finished"
        self.store.save_state(item_id, state)
        self.store.write_text(item_id, "result.json", json.dumps(state["finish"]))
        self.store.revoke(item_id)
        return {"ok": True}

    def _check_claims(self, item_id, state, subs_in):
        """What outcome clean or minor claims: the latest round recorded the text as it
        is now, a check of that round found no pre-check warnings, the text says what
        the article says, and every override's phonemes came from the phonemes tool."""
        n = state["records"]
        hold = "finish with outcome 'hold'"
        if n == 0:
            raise ToolError("nothing is recorded yet: record and check before finishing with "
                            f"outcome clean or minor, or {hold}")
        if state["recorded_round"] != n:
            raise ToolError(f"record round {n} failed and left no recording; "
                            + (f"call record again, then check, before finish; or {hold}"
                               if n < MAX_RECORDS else f"no record rounds are left, so {hold}"))
        prepared = self.store.read_text(item_id, "prepared.md")
        if not self._unchanged(item_id, state, prepared):
            raise ToolError(_changed(state))
        latest = _latest_checks(state)
        if not latest:
            if _checks_used(state) >= MAX_CHECKS:
                raise ToolError(f"the latest recording (round {n}) has no check, and no checks "
                                f"are left; {hold}")
            raise ToolError(f"call check on the latest recording before finish; or {hold}")
        if latest[-1]["warnings"]:
            flagged = "; ".join(latest[-1]["warnings"]).rstrip(".")
            advice = ("Fix and re-record, or finish with outcome 'hold'."
                      if n < MAX_RECORDS and _checks_used(state) < MAX_CHECKS
                      else "No record round or check is left to fix it: finish with outcome 'hold'.")
            raise ToolError(f"the latest check's pre-check flagged: {flagged}. {advice}")
        problems = words_check(self.store.read_text(item_id, "source.md"), prepared,
                               [Substitution(s["original"], s["spoken"]) for s in subs_in])
        if problems:
            raise ToolError("the prepared text differs from the article: " + "; ".join(problems)
                            + ". Declare each word change in substitutions, or undo it.")
        unproven = _unproven_overrides(state, prepared, MAX_SHOWN + 1)
        if unproven:
            raise ToolError("; ".join(unproven[:MAX_SHOWN])
                            + ("; and more overrides like these" if len(unproven) > MAX_SHOWN else ""))
