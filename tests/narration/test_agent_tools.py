import re
import threading

import pytest
from hypothesis import given, settings, strategies as st

from narration import agent_tools
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


def test_a_failed_record_keeps_the_previous_audio_and_blocks_check_and_finish(env):
    store, tools, tts, _, item, _ = env
    tools.record(item); tools.check(item)
    wav = store.path(item, "recording.wav")
    round_one = wav.read_bytes()
    tts.fail_next = True
    with pytest.raises(Exception):
        tools.record(item)  # round 2 is counted, but produced nothing
    state = store.load_state(item)
    assert (state["records"], state["recorded_round"]) == (2, 1)
    assert wav.read_bytes() == round_one  # round 1's audio, untouched
    assert not list(wav.parent.glob(".tmp-*"))  # and the partial file is gone
    with pytest.raises(ToolError, match="record again"):
        tools.check(item)  # must not verify round 1's audio as round 2's
    with pytest.raises(ToolError, match="record again"):
        tools.finish(item, "clean", [], [])
    tools.record(item)
    assert store.load_state(item)["recorded_round"] == 3


def test_a_second_call_while_one_runs_is_refused(env):
    store, tools, tts, _, item, _ = env
    for _ in range(MAX_RECORDS - 1):
        tools.record(item)
    started, release = threading.Event(), threading.Event()
    tts.hold = (started, release)  # the next run blocks until the test releases it
    results = []

    def call():
        try:
            results.append(tools.record(item))
        except ToolError as e:
            results.append(e)

    first = threading.Thread(target=call)
    first.start()
    assert started.wait(5)  # round 4 is inside the engine
    with pytest.raises(ToolError, match="in progress"):
        tools.record(item)  # the parallel call is refused, not queued
    release.set()
    first.join(5)
    tts.hold = None
    assert isinstance(results[0], dict) and results[0]["round"] == MAX_RECORDS
    assert tts.calls == MAX_RECORDS
    with pytest.raises(ToolError, match="limit"):
        tools.record(item)


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
    assert tools.finish(item, "hold", [{"kind": "truncation", "where": "9:40", "note": "short"}], [])["ok"]


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
    ok = {"kind": "mispronunciation", "where": "12:40", "note": "Pengelley"}
    for bad in ({**ok, "kind": "garbled"},          # kind comes from a fixed list
                {**ok, "where": "near the end"},   # where is a timestamp
                {**ok, "where": "1234:00"},
                {**ok, "note": "x" * 81}):         # note is short, and never published
        with pytest.raises(ToolError):
            tools.finish(item, "minor", [bad], [])
    with pytest.raises(ToolError, match="at most 20"):
        tools.finish(item, "minor", [ok] * 21, [])
    assert tools.finish(item, "minor", [ok], [])["ok"]


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


# ---- every tool holds the item's lock, and a finished item takes no calls ----
def _every_tool(tools, item):
    return {"get_text": lambda: tools.get_text(item),
            "replace_text": lambda: tools.replace_text(item, "It", "It"),
            "record": lambda: tools.record(item),
            "check": lambda: tools.check(item),
            "phonemes": lambda: tools.phonemes(item, "Leo"),
            "finish": lambda: tools.finish(item, "hold", [], [])}


def test_every_tool_is_refused_while_another_call_runs(env):
    store, tools, tts, _, item, _ = env
    started, release = threading.Event(), threading.Event()
    tts.hold = (started, release)
    running = threading.Thread(target=tools.record, args=(item,))
    running.start()
    try:
        assert started.wait(5)
        for name, call in _every_tool(tools, item).items():
            with pytest.raises(ToolError, match="in progress"):
                call()
    finally:
        release.set()
        running.join(5)
    state = store.load_state(item)
    assert (state["records"], state["recorded_round"], state["status"]) == (1, 1, "open")


def test_every_tool_refuses_a_finished_item(env):
    store, tools, _, _, item, _ = env
    tools.record(item); tools.check(item)
    tools.finish(item, "clean", [], [])
    for name, call in _every_tool(tools, item).items():
        with pytest.raises(ToolError, match="finished"):
            call()
    assert store.load_state(item)["status"] == "finished"


# ---- R1: an override's phonemes must come from a phonemes() lookup of that word ----
NAMED = "Pengelley met the family.\n\nIt went well."
FAKE = FakeG2P().phonemes("anything")


@pytest.fixture
def named(tmp_path):
    store = WorkItemStore(tmp_path)
    tools = AgentTools(store, FakeTTS(), FakeVerifier(), FakeG2P())
    item, _ = store.open(NAMED, {})
    return store, tools, item


def _record_and_check(tools, item):
    tools.record(item)
    tools.check(item)


def test_an_override_from_a_lookup_of_its_own_word_passes(named):
    store, tools, item = named
    out = tools.phonemes(item, "Pengelley")
    assert out == {"word": "Pengelley", "phonemes": FAKE}
    tools.replace_text(item, "Pengelley", f"[Pengelley](/{out['phonemes']}/)")
    _record_and_check(tools, item)
    assert tools.finish(item, "clean", [], [])["ok"]


def test_an_override_from_a_lookup_of_a_respelling_passes(named):
    store, tools, item = named
    ph = tools.phonemes(item, "Pengelly")["phonemes"]  # respelled to steer the engine
    tools.replace_text(item, "Pengelley", f"[Pengelley](/{ph}/)")
    _record_and_check(tools, item)
    assert tools.finish(item, "clean", [], [])["ok"]


def test_hand_written_phonemes_are_refused(named):
    store, tools, item = named
    tools.phonemes(item, "Pengelley")  # looked up, but the override doesn't use the result
    tools.replace_text(item, "Pengelley", "[Pengelley](/pɛnʤˈɛli/)")
    _record_and_check(tools, item)
    message = ("override [Pengelley](/pɛnʤˈɛli/) was not produced by the phonemes tool for a "
               "respelling of 'Pengelley'; call phonemes('Pengelley') and use its result")
    with pytest.raises(ToolError, match=re.escape(message)):
        tools.finish(item, "clean", [], [])
    assert store.load_state(item)["status"] == "open"


def test_a_lookup_of_an_unrelated_word_is_refused(named):
    store, tools, item = named
    ph = tools.phonemes(item, "badly")["phonemes"]
    tools.phonemes(item, "Pengelley")
    tools.replace_text(item, "Pengelley", f"[Pengelley](/{ph}/)")  # its own lookup: fine
    tools.replace_text(item, "well.", f"[well](/{ph}/).")          # phonemes of "badly"
    _record_and_check(tools, item)
    with pytest.raises(ToolError) as refused:
        tools.finish(item, "clean", [], [])
    assert f"override [well](/{ph}/) was not produced" in str(refused.value)
    assert "Pengelley" not in str(refused.value)  # only the bad override is named


def test_a_lookup_made_for_another_item_does_not_count(named):
    store, tools, item = named
    other, _ = store.open("Pengelley spoke.", {})
    ph = tools.phonemes(other, "Pengelley")["phonemes"]
    tools.replace_text(item, "Pengelley", f"[Pengelley](/{ph}/)")
    _record_and_check(tools, item)
    with pytest.raises(ToolError, match="not produced by the phonemes tool"):
        tools.finish(item, "clean", [], [])


def test_an_override_already_in_the_article_still_needs_a_lookup(tmp_path):
    store = WorkItemStore(tmp_path)
    tools = AgentTools(store, FakeTTS(), FakeVerifier(), FakeG2P())
    item, _ = store.open("See [Home](/home/) today.", {})  # a relative link reads as an override
    _record_and_check(tools, item)
    with pytest.raises(ToolError, match=re.escape("call phonemes('Home')")):
        tools.finish(item, "clean", [], [])


def test_lookups_are_kept_in_state_across_tool_calls(named):
    store, tools, item = named
    tools.phonemes(item, "Pengelly")
    tools.get_text(item)
    tools.replace_text(item, "It went", "It went")
    _record_and_check(tools, item)
    with pytest.raises(ToolError):
        tools.finish(item, "great", [], [])
    assert store.load_state(item)["phoneme_lookups"] == [{"word": "Pengelly", "phonemes": FAKE}]


def test_only_the_last_200_lookups_are_kept(named):
    store, tools, item = named
    for k in range(205):
        tools.phonemes(item, f"word{k}")
    lookups = store.load_state(item)["phoneme_lookups"]
    assert len(lookups) == 200
    assert (lookups[0]["word"], lookups[-1]["word"]) == ("word5", "word204")


# ---- R2: size caps ----
def test_replace_text_refuses_a_prepared_text_far_longer_than_the_article(env):
    store, tools, _, _, item, _ = env
    limit = 2 * len(ARTICLE) + 10_000
    room = limit - len(ARTICLE)  # how far the prepared text may still grow
    with pytest.raises(ToolError, match=f"the limit is {limit}"):
        tools.replace_text(item, "well.", "well." + "x" * (room + 1))
    assert store.read_text(item, "prepared.md") == ARTICLE
    tools.replace_text(item, "well.", "well." + "x" * room)  # exactly at the limit
    assert len(store.read_text(item, "prepared.md")) == limit
    with pytest.raises(ToolError, match=f"the limit is {limit}"):
        tools.replace_text(item, "met", "met!")


def test_replace_text_refuses_old_or_new_over_20000_characters(tmp_path):
    store = WorkItemStore(tmp_path)
    tools = AgentTools(store, FakeTTS(), FakeVerifier(), FakeG2P())
    article = "word " * 6000 + "end."  # long enough that a 20,001-character edit fits the size cap
    item, _ = store.open(article, {})
    with pytest.raises(ToolError, match="old is longer than 20000 characters"):
        tools.replace_text(item, "word " * 4001, "x")
    with pytest.raises(ToolError, match="new is longer than 20000 characters"):
        tools.replace_text(item, "end.", "y" * 20_001)
    assert store.read_text(item, "prepared.md") == article
    tools.replace_text(item, "end.", "y" * 20_000)  # at the cap


def test_finish_refuses_too_many_substitutions_before_the_word_check(env, monkeypatch):
    _, tools, _, _, item, _ = env
    _record_and_check(tools, item)
    calls = []
    monkeypatch.setattr(agent_tools, "words_check", lambda *a: calls.append(a) or [])
    sub = {"original": "XIV", "spoken": "the Fourteenth", "why": "numeral"}
    for n in (agent_tools.MAX_SUBSTITUTIONS + 1, 10_000):
        with pytest.raises(ToolError, match="at most 25 substitutions"):
            tools.finish(item, "clean", [], [sub] * n)
    assert calls == []
    assert tools.finish(item, "clean", [], [sub] * agent_tools.MAX_SUBSTITUTIONS)["ok"]
    assert len(calls) == 1  # the limit itself reaches the check


def test_finish_refuses_too_many_issues_before_the_word_check(env, monkeypatch):
    _, tools, _, _, item, _ = env
    _record_and_check(tools, item)
    calls = []
    monkeypatch.setattr(agent_tools, "words_check", lambda *a: calls.append(a) or [])
    ok = {"kind": "pacing", "where": "1:05", "note": ""}
    for n in (agent_tools.MAX_ISSUES + 1, 10_000):
        with pytest.raises(ToolError, match="at most 20"):
            tools.finish(item, "minor", [ok] * n, [])
    assert calls == []


# ---- arguments the schema should have stopped, refused all the same ----
def test_record_refuses_malformed_voices_and_speeds_without_counting_a_round(env):
    store, tools, tts, _, item, _ = env
    for voice in ("af_heart\n", "AF_HEART", "af_", 7):
        with pytest.raises(ToolError, match="voice"):
            tools.record(item, voice=voice)
    for speed in ("fast", None, float("nan"), float("inf"), 0.4):
        with pytest.raises(ToolError, match="speed"):
            tools.record(item, speed=speed)
    assert (store.load_state(item)["records"], tts.calls) == (0, 0)


def test_replace_text_refuses_non_text(env):
    store, tools, _, _, item, _ = env
    with pytest.raises(ToolError):
        tools.replace_text(item, 5, "x")
    with pytest.raises(ToolError):
        tools.replace_text(item, "It", None)
    with pytest.raises(ToolError, match="empty"):
        tools.replace_text(item, "", "x")
    assert store.read_text(item, "prepared.md") == ARTICLE


@pytest.mark.parametrize("where", ["12:40\n", "12:60", "\u0661\u0662:\u0664\u0660", "12:4", ":40", 1240])
def test_finish_refuses_a_where_that_is_not_a_plain_timestamp(env, where):
    _, tools, _, _, item, _ = env
    _record_and_check(tools, item)
    with pytest.raises(ToolError, match="timestamp"):
        tools.finish(item, "minor", [{"kind": "pacing", "where": where, "note": ""}], [])


def test_finish_refuses_issues_and_substitutions_that_are_not_objects(env):
    store, tools, _, _, item, _ = env
    _record_and_check(tools, item)
    with pytest.raises(ToolError, match="object"):
        tools.finish(item, "minor", ["12:40 garbled"], [])
    with pytest.raises(ToolError, match="object"):
        tools.finish(item, "clean", [], [["XIV", "the Fourteenth"]])
    with pytest.raises(ToolError, match="list"):
        tools.finish(item, "clean", [], "XIV")
    with pytest.raises(ToolError, match="original"):
        tools.finish(item, "clean", [], [{"spoken": "the Fourteenth"}])
    assert store.load_state(item)["status"] == "open"


def test_replace_text_counts_overlapping_matches_as_more_than_one(env):
    store, tools, _, _, item, _ = env
    store.write_text(item, "prepared.md", "baaa")
    with pytest.raises(ToolError, match="found 2"):  # str.count finds "aa" once
        tools.replace_text(item, "aa", "x")
    assert store.read_text(item, "prepared.md") == "baaa"
    tools.replace_text(item, "baa", "bxa")
    assert store.read_text(item, "prepared.md") == "bxaa"


def test_a_refusal_names_at_most_five_unproven_overrides(tmp_path):
    store = WorkItemStore(tmp_path)
    tools = AgentTools(store, FakeTTS(), FakeVerifier(), FakeG2P())
    names = ["Abbott", "Barlow", "Carver", "Dobson", "Ellery", "Fenwick", "Garrow"]
    item, _ = store.open(" ".join(names) + " met.", {})
    for name in names:
        tools.replace_text(item, name, f"[{name}](/{FAKE}/)")
    _record_and_check(tools, item)
    with pytest.raises(ToolError) as refused:
        tools.finish(item, "clean", [], [])
    message = str(refused.value)
    assert message.count("was not produced by the phonemes tool") == 5
    assert message.endswith("; and more overrides like these")
    assert "Fenwick" not in message and "Garrow" not in message


def test_finish_refuses_minor_with_precheck_warnings_and_needs_the_latest_check_clear(env):
    store, tools, _, ver, item, _ = env
    issue = {"kind": "truncation", "where": "9:40", "note": "cut short"}
    ver.warnings = ["0:07 of silence at 3:10 — far longer than a [BREAK] pause."]
    tools.record(item); tools.check(item)
    with pytest.raises(ToolError, match="pre-check flagged: 0:07 of silence"):
        tools.finish(item, "minor", [issue], [])
    ver.warnings = []
    tools.record(item)
    with pytest.raises(ToolError, match="latest recording"):  # round 1's check doesn't count
        tools.finish(item, "minor", [issue], [])
    tools.check(item)
    assert tools.finish(item, "minor", [issue], [])["ok"]
    assert store.load_state(item)["finish"]["remaining_issues"] == [issue]
