import hashlib
import json
import threading

import pytest

from narration import agent_tools, gate
from narration.agent_tools import MAX_ISSUES, MAX_NOTE, AgentTools, ToolError
from narration.fakes import FakeConverter, FakeG2P, FakeTTS, FakeVerifier
from narration.gate import evaluate, notes_for
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


def test_finalize_refuses_an_open_item_unless_forced(env):
    store, runner, _, conv, item, token = env
    with pytest.raises(ToolError, match="still open"):
        runner.finalize(item)
    assert store.item_for_token(token) == item  # the session can still finish
    out = runner.finalize(item, force=True)  # the runner's timeout policy decided
    assert out["action"] == "retry" and not out["mp3"] and conv.calls == []
    assert store.item_for_token(token) is None  # abandoned: the token dies here
    assert runner.status(item)["status"] == "abandoned"
    assert runner.finalize(item)["action"] == "retry"  # terminal; no force needed now


def test_finalize_waits_for_a_running_record(env):
    store, runner, tools, _, item, _ = env
    started, release = threading.Event(), threading.Event()
    tools.tts.hold = (started, release)
    recorder = threading.Thread(target=tools.record, args=(item,))
    recorder.start()
    assert started.wait(5)  # round 1 is inside the engine
    out = []
    finalizer = threading.Thread(target=lambda: out.append(runner.finalize(item, force=True)))
    finalizer.start()
    finalizer.join(0.2)
    assert finalizer.is_alive()  # blocked behind the record, not interleaved with it
    release.set()
    recorder.join(5); finalizer.join(5)
    assert out[0]["action"] == "retry"
    assert store.load_state(item)["recorded_round"] == 1  # the record's state write landed


def test_clean_publishes_with_tags_and_notes(env):
    store, runner, tools, conv, item, _ = env
    tools.record(item); tools.check(item); tools.finish(item, "clean", [], [])
    out = runner.finalize(item)
    assert out["action"] == "publish" and out["mp3"]
    assert conv.calls == [("Zoning Alone", "Joseph Lawler", "The New Atlantis, No. 86 (Fall 2026)")]
    assert out["notes"].startswith("Zoning made us lonely.")
    assert store.path(item, "episode.mp3").exists()


def test_minor_lists_issues_in_the_notes_but_never_their_free_text(env):
    store, runner, tools, conv, item, _ = env
    tools.record(item); tools.check(item)
    tools.finish(item, "minor", [{"kind": "mispronunciation", "where": "0:12",
                                  "note": "Lawler; see example.com"}], [])
    out = runner.finalize(item)
    assert out["action"] == "publish"
    assert "- 0:12 mispronunciation" in out["notes"]
    assert "example.com" not in out["notes"]  # the note is for Brian, not the feed ...
    result = json.loads(store.read_text(item, "result.json"))
    assert result["remaining_issues"][0]["note"] == "Lawler; see example.com"  # ... so it's in result.json


def test_agent_hold_is_a_hold(env):
    _, runner, tools, conv, item, _ = env
    tools.record(item); tools.check(item)
    tools.finish(item, "hold", [{"kind": "artifact", "where": "3:00", "note": "paragraph 4"}], [])
    out = runner.finalize(item)
    assert out["action"] == "hold" and conv.calls == []
    assert any("paragraph 4" in r for r in out["reasons"])


def test_gate_rechecks_words_independently(env):
    store, runner, tools, conv, item, _ = env
    tools.record(item); tools.check(item); tools.finish(item, "clean", [], [])
    store.write_text(item, "prepared.md", ARTICLE + " Buy now.")  # tampering after finish
    out = runner.finalize(item)
    assert out["action"] == "hold" and any("buy" in r for r in out["reasons"])


def test_evaluate_holds_when_the_latest_round_has_no_recording():
    state = {"status": "finished", "records": 2, "recorded_round": 1,
             "checks": [{"n": 1, "record": 1, "warnings": []}],
             "finish": {"outcome": "clean", "remaining_issues": [], "substitutions": []}}
    out = evaluate(state, ARTICLE, ARTICLE, META)
    assert out.action == "hold" and any("no recording" in r for r in out.reasons)


def test_finalize_twice_does_not_convert_twice(env):
    _, runner, tools, conv, item, _ = env
    tools.record(item); tools.check(item); tools.finish(item, "clean", [], [])
    runner.finalize(item)
    runner.finalize(item)
    assert len(conv.calls) == 1


def test_evaluate_without_edition_uses_publication_as_album():
    from narration.gate import album_for
    assert album_for({"publication": "The New Atlantis"}) == "The New Atlantis"


# ---- R1: the gate repeats finish's claims, on the files as they are now ----
def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _state(**changes):
    """A finished item whose every claim holds for ARTICLE, with `changes` applied."""
    state = {"status": "finished", "records": 1, "recorded_round": 1, "checks_used": 1,
             "recorded_sha256": _sha(ARTICLE), "checks": [{"n": 1, "record": 1, "warnings": []}],
             "finish": {"outcome": "clean", "remaining_issues": [], "substitutions": []}}
    state.update(changes)
    return state


ISSUE = {"kind": "pacing", "where": "1:10", "note": "rushed"}


def test_evaluate_publishes_when_every_claim_holds():
    out = evaluate(_state(), ARTICLE, ARTICLE, META)
    assert (out.action, out.outcome, out.reasons) == ("publish", "clean", [])


def test_evaluate_holds_when_the_prepared_text_is_not_what_was_recorded():
    paced = ARTICLE.replace("lonely.", "lonely...")  # passes the word check; not what was recorded
    out = evaluate(_state(), ARTICLE, paced, META)
    assert out.action == "hold" and any("changed" in r for r in out.reasons)
    out = evaluate(_state(recorded_sha256=None), ARTICLE, ARTICLE, META)
    assert out.action == "hold" and any("changed" in r for r in out.reasons)


def test_evaluate_holds_when_nothing_was_recorded():
    out = evaluate(_state(records=0, recorded_round=None, recorded_sha256=None, checks=[]),
                   ARTICLE, ARTICLE, META)
    assert out.action == "hold" and any("nothing was recorded" in r for r in out.reasons)


@pytest.mark.parametrize("changes", [
    # round 2 was recorded but only round 1 was checked
    {"records": 2, "recorded_round": 2, "checks_used": 2},
    # a check of round 1 was attempted but failed: counted, with no report
    {"checks_used": 1, "checks": []},
    # three attempts on round 2, all failed
    {"records": 2, "recorded_round": 2, "checks_used": 4},
])
def test_evaluate_holds_without_a_successful_check_of_the_latest_round(changes):
    out = evaluate(_state(**changes), ARTICLE, ARTICLE, META)
    assert out.action == "hold" and any("no check" in r for r in out.reasons)


@pytest.mark.parametrize("outcome, issues", [("clean", []), ("minor", [ISSUE])])
def test_evaluate_holds_on_precheck_warnings_for_clean_and_minor(outcome, issues):
    warned = [{"n": 1, "record": 1, "warnings": ["0:07 of silence at 3:10"]}]
    finish = {"outcome": outcome, "remaining_issues": issues, "substitutions": []}
    out = evaluate(_state(checks=warned, finish=finish), ARTICLE, ARTICLE, META)
    assert out.action == "hold" and any("0:07 of silence at 3:10" in r for r in out.reasons)


def test_only_the_latest_rounds_latest_check_counts():
    checks = [{"n": 1, "record": 1, "warnings": ["cut short"]},
              {"n": 2, "record": 2, "warnings": []}]
    out = evaluate(_state(records=2, recorded_round=2, checks_used=2, checks=checks),
                   ARTICLE, ARTICLE, META)
    assert out.action == "publish"
    checks = [{"n": 1, "record": 1, "warnings": []}, {"n": 2, "record": 1, "warnings": ["cut"]}]
    out = evaluate(_state(checks_used=2, checks=checks), ARTICLE, ARTICLE, META)
    assert out.action == "hold"


def test_evaluate_holds_when_the_words_differ():
    edited = ARTICLE.replace("may end", "will end")
    out = evaluate(_state(recorded_sha256=_sha(edited)), ARTICLE, edited, META)
    assert out.action == "hold"
    assert any("may" in r and "will" in r for r in out.reasons)


def test_a_finalize_after_an_edit_since_the_recording_holds(env):
    store, runner, tools, conv, item, _ = env
    tools.record(item); tools.check(item); tools.finish(item, "clean", [], [])
    store.write_text(item, "prepared.md", ARTICLE.replace("lonely.", "lonely..."))
    out = runner.finalize(item)
    assert out["action"] == "hold" and conv.calls == []
    assert any("changed" in r for r in out["reasons"])


# ---- R2: one substitution validation, shared with finish ----
NAMED = "Pengelley met the family.\n\nIt went well."
RESPELLING = {"original": "Pengelley", "spoken": "Penjelly", "why": "pronunciation"}


@pytest.fixture
def named(tmp_path):
    store = WorkItemStore(tmp_path)
    conv = FakeConverter()
    runner = RunnerTools(store, conv)
    tools = AgentTools(store, FakeTTS(), FakeVerifier(), FakeG2P())
    opened = runner.open(NAMED, META)
    return store, runner, tools, conv, opened["item_id"]


def test_the_gate_and_finish_share_the_substitution_validation():
    assert gate.validate_substitutions is agent_tools.validate_substitutions
    assert gate.validate_finish is agent_tools.validate_finish


def test_the_gate_publishes_a_declared_override_respelling_that_finish_accepted(named):
    store, runner, tools, conv, item = named
    ph = tools.phonemes(item, "Penjelly")["phonemes"]  # a respelled lookup
    tools.replace_text(item, "Pengelley", f"[Pengelley](/{ph}/)")
    tools.record(item); tools.check(item)
    assert tools.finish(item, "clean", [], [RESPELLING])["ok"]  # applied 0 times, but declared
    out = runner.finalize(item)
    assert out["action"] == "publish", out["reasons"]
    assert '- "Pengelley" read as "Penjelly"' in out["notes"]  # published all the same


def _finish_then_rewrite(store, tools, item, declared=(), **finish):
    """A clean finish, then the finish record rewritten behind finish's back."""
    tools.record(item); tools.check(item); tools.finish(item, "clean", [], list(declared))
    state = store.load_state(item)
    state["finish"].update(finish)
    store.save_state(item, state)


def test_the_gate_holds_an_unused_substitution(named):
    store, runner, tools, conv, item = named
    unused = {"original": "family", "spoken": "buy pills now", "why": "x"}
    _finish_then_rewrite(store, tools, item, substitutions=[unused])
    out = runner.finalize(item)
    assert out["action"] == "hold" and conv.calls == []
    assert any("does not use it" in r for r in out["reasons"])


def test_the_gate_holds_a_substitution_declared_twice(named):
    store, runner, tools, conv, item = named
    tools.replace_text(item, "family", "whole family")
    sub = {"original": "family", "spoken": "whole family", "why": "x"}
    _finish_then_rewrite(store, tools, item, [sub], substitutions=[sub, sub])
    out = runner.finalize(item)
    assert out["action"] == "hold" and any("declared twice" in r for r in out["reasons"])


def test_the_gate_holds_an_override_no_lookup_produced(named):
    store, runner, tools, conv, item = named
    tools.record(item); tools.check(item); tools.finish(item, "clean", [], [])
    text = NAMED.replace("Pengelley", "[Pengelley](/pɛnʤˈɛli/)")  # hand-written phonemes
    store.write_text(item, "prepared.md", text)
    state = store.load_state(item)
    state["recorded_sha256"] = _sha(text)  # even as if this text had been recorded
    store.save_state(item, state)
    out = runner.finalize(item)
    assert out["action"] == "hold"
    assert any("not produced by the phonemes tool" in r for r in out["reasons"])


def test_notes_publish_every_substitution_and_issues_as_timestamp_and_kind():
    finish = {"outcome": "minor",
              "remaining_issues": [{"kind": "pacing", "where": "12:40", "note": "see x.com"}],
              "substitutions": [RESPELLING,
                                {"original": "XIV", "spoken": "the Fourteenth", "why": "numeral"}]}
    notes = notes_for(META, finish)
    assert notes.splitlines() == [
        "Zoning made us lonely.",
        "Known issues in this recording:",
        "- 12:40 pacing",
        "Read with these substitutions:",
        '- "Pengelley" read as "Penjelly"',
        '- "XIV" read as "the Fourteenth"',
        "Recorded automatically."]


# ---- R4: finalize and status ----
def test_finalize_and_status_of_an_unknown_item(env):
    _, runner, *_ = env
    with pytest.raises(ToolError, match="no such work item"):
        runner.finalize("0123456789ab")
    with pytest.raises(ToolError, match="no such work item"):
        runner.finalize("../../etc")
    assert runner.status("0123456789ab") == {"exists": False}


def test_status_reports_the_round_counts_and_whether_the_text_is_what_was_recorded(env):
    store, runner, tools, conv, item, _ = env
    status = runner.status(item)
    assert (status["exists"], status["status"], status["records"], status["checks_used"],
            status["recorded_round"], status["prepared_matches_recording"]) == (
        True, "open", 0, 0, None, False)
    tools.record(item)
    assert runner.status(item)["prepared_matches_recording"] is True
    tools.replace_text(item, "lonely.", "lonely...")
    status = runner.status(item)
    assert status["prepared_matches_recording"] is False and status["recorded_round"] == 1
    tools.replace_text(item, "lonely...", "lonely.")
    tools.check(item)
    assert (runner.status(item)["checks_used"], runner.status(item)["checks"]) == (1, 1)
    tools.finish(item, "clean", [], [])
    assert runner.status(item)["status"] == "finished"
    runner.finalize(item)
    status = runner.status(item)
    assert status["status"] == "finalized" and status["mp3"] is True
    assert status["finish"]["outcome"] == "clean"


class BrokenConverter(FakeConverter):
    def __init__(self):
        super().__init__()
        self.fail = True

    def to_mp3(self, wav, mp3, title, artist, album):
        if self.fail:
            from pathlib import Path
            Path(mp3).write_bytes(b"ID3 half")  # a crash mid-write
            raise RuntimeError("the MP3 conversion failed: disk full")
        super().to_mp3(wav, mp3, title, artist, album)


def test_a_failed_conversion_leaves_no_mp3_and_can_be_retried(tmp_path):
    store, conv = WorkItemStore(tmp_path), BrokenConverter()
    runner = RunnerTools(store, conv)
    tools = AgentTools(store, FakeTTS(), FakeVerifier(), FakeG2P())
    item = runner.open(ARTICLE, META)["item_id"]
    tools.record(item); tools.check(item); tools.finish(item, "clean", [], [])
    with pytest.raises(RuntimeError, match="disk full"):
        runner.finalize(item)
    mp3 = store.path(item, "episode.mp3")
    assert not mp3.exists() and not list(mp3.parent.glob(".tmp-*"))
    assert runner.status(item)["status"] == "finished"
    conv.fail = False
    out = runner.finalize(item)
    assert out["action"] == "publish" and out["mp3"] and mp3.read_bytes() == b"ID3 fake mp3"


def test_a_hold_makes_no_mp3(env):
    store, runner, tools, conv, item, _ = env
    tools.record(item); tools.check(item)
    tools.finish(item, "hold", [ISSUE], [])
    out = runner.finalize(item)
    assert out["action"] == "hold" and out["mp3"] is False and conv.calls == []
    assert runner.status(item)["status"] == "finalized"


# ---- R5: the gate re-validates the finish record's shapes with agent_tools' rules ----
@pytest.mark.parametrize("finish", [
    {"outcome": "great", "remaining_issues": [], "substitutions": []},
    {"outcome": "minor", "remaining_issues": [{**ISSUE, "where": "near the end"}],
     "substitutions": []},
    {"outcome": "minor", "remaining_issues": [{**ISSUE, "where": "1:10\n<b>"}], "substitutions": []},
    {"outcome": "minor", "remaining_issues": [{**ISSUE, "kind": "<script>"}], "substitutions": []},
    {"outcome": "minor", "remaining_issues": [{**ISSUE, "note": "x" * (MAX_NOTE + 1)}],
     "substitutions": []},
    {"outcome": "minor", "remaining_issues": [ISSUE] * (MAX_ISSUES + 1), "substitutions": []},
    {"outcome": "clean", "remaining_issues": [ISSUE], "substitutions": []},
    {"outcome": "minor", "remaining_issues": [], "substitutions": []},
    {"outcome": "clean", "remaining_issues": [], "substitutions": "none"},
    {"outcome": "clean", "remaining_issues": [], "substitutions": [{"original": 5}]},
    {"outcome": "clean"},
])
def test_a_malformed_finish_record_is_held_and_never_reaches_the_notes(finish):
    out = evaluate(_state(finish=finish), ARTICLE, ARTICLE, META)
    assert out.action == "hold" and out.reasons
    assert out.notes == "Zoning made us lonely.\nRecorded automatically."
