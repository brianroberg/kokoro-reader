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
  (agent_tools.validate_substitutions, the code finish runs).
An agent's outcome "hold" is a hold, whatever else is true: nothing held is
published, so its claims aren't checked, as finish doesn't check them. Pure."""
from __future__ import annotations

from dataclasses import dataclass, field

from narration.agent_tools import (ToolError, _latest_checks, _sha256, validate_finish,
                                   validate_substitutions)


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
    """The published episode notes. An issue appears as its timestamp and kind only:
    validate_finish checked both against a pattern and a fixed list, so no text the
    session wrote reaches the feed. The free-text note is for Brian, in result.json.
    Every declared substitution is listed, override-respellings included."""
    parts = [metadata.get("description", "").strip()]
    if finish.get("remaining_issues"):
        parts.append("Known issues in this recording:")
        parts += [f"- {i['where']} {i['kind']}" for i in finish["remaining_issues"]]
    if finish.get("substitutions"):
        parts.append("Read with these substitutions:")
        parts += [f"- \"{s['original']}\" read as \"{s['spoken']}\"" for s in finish["substitutions"]]
    parts.append("Recorded automatically.")
    return "\n".join(p for p in parts if p)


def evaluate(state: dict, source: str, prepared: str, metadata: dict) -> GateResult:
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
