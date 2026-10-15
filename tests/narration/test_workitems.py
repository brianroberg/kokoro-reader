import json
import threading
import time

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


def test_open_cannot_resurrect_a_token_that_is_being_revoked(store, monkeypatch):
    # The race: open() reads tokens.json, revoke() reads and writes it, then open()
    # writes its stale copy back, with the revoked hash restored. The store's lock
    # must make revoke() wait for open() to finish.
    a, ta = store.open("a", {})
    real_tokens = store._tokens
    read = threading.Event()

    def slow_tokens():
        tokens = real_tokens()
        if not read.is_set():
            read.set()
            time.sleep(0.2)  # open() holds its stale copy while revoke() tries to run
        return tokens

    monkeypatch.setattr(store, "_tokens", slow_tokens)
    opened = []
    t = threading.Thread(target=lambda: opened.append(store.open("b", {})))
    t.start()
    assert read.wait(5)
    store.revoke(a)
    t.join(5)
    b, tb = opened[0]
    assert store.item_for_token(ta) is None
    assert store.item_for_token(tb) == b
