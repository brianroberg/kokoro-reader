"""Tools for the runner only (never exposed to the agent). A refusal is a ToolError,
as for the agent's tools; the HTTP layer converts it."""
from __future__ import annotations

import json
import os

from narration.agent_tools import ToolError, _checks_used, _sha256
from narration.gate import album_for, evaluate


class RunnerTools:
    def __init__(self, store, converter):
        self.store, self.converter = store, converter

    def open(self, article_text: str, metadata: dict) -> dict:
        if not isinstance(article_text, str) or not article_text.strip():
            raise ToolError("article_text must be a non-empty string")
        if not isinstance(metadata, dict):
            raise ToolError("metadata must be an object")
        item_id, token = self.store.open(article_text, metadata)
        return {"item_id": item_id, "agent_token": token}

    def status(self, item_id: str) -> dict:
        """The item's status (open, finished, finalized or abandoned), its round counts,
        and whether prepared.md is still the text the latest round recorded. A snapshot:
        it doesn't wait for a running tool call."""
        if not self.store.exists(item_id):
            return {"exists": False}
        state = self.store.load_state(item_id)
        prepared = self.store.read_text(item_id, "prepared.md")
        return {"exists": True, "status": state["status"], "records": state["records"],
                "recorded_round": state["recorded_round"], "checks_used": _checks_used(state),
                "checks": len(state["checks"]),
                "prepared_matches_recording": _sha256(prepared) == state.get("recorded_sha256"),
                "finish": state["finish"],
                "mp3": self.store.path(item_id, "episode.mp3").exists()}

    def finalize(self, item_id: str, force: bool = False) -> dict:
        if not self.store.exists(item_id):
            raise ToolError("no such work item")
        with self.store.item_lock(item_id):  # wait for a running record, check or finish
            state = self.store.load_state(item_id)
            if state["status"] == "open":
                if not force:
                    raise ToolError("this work item is still open: the session has not called finish. "
                                    "Pass force=True to abandon it, once the runner's own timeout has passed.")
                state["status"] = "abandoned"  # terminal; the gate below answers retry
                self.store.save_state(item_id, state)
            self.store.revoke(item_id)
            meta = json.loads(self.store.read_text(item_id, "metadata.json"))
            result = evaluate(state, self.store.read_text(item_id, "source.md"),
                              self.store.read_text(item_id, "prepared.md"), meta)
            mp3 = self.store.path(item_id, "episode.mp3")
            if result.action == "publish" and not mp3.exists():
                self._convert(item_id, mp3, meta)
            if state["status"] == "finished":
                state["status"] = "finalized"
                self.store.save_state(item_id, state)
            return {"action": result.action, "outcome": result.outcome, "reasons": result.reasons,
                    "notes": result.notes, "mp3": mp3.exists()}

    def _convert(self, item_id, mp3, meta):
        """recording.wav to episode.mp3, with the tags. episode.mp3 appears only once the
        conversion has succeeded, so a later finalize never takes a partial file for a
        finished one."""
        tmp = mp3.with_name(".tmp-episode.mp3")
        try:
            self.converter.to_mp3(self.store.path(item_id, "recording.wav"), tmp,
                                  title=meta.get("title", ""),
                                  artist="; ".join(meta.get("authors", [])),
                                  album=album_for(meta))
            os.replace(tmp, mp3)
        finally:
            tmp.unlink(missing_ok=True)
