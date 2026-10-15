"""python -m narration [--fake-engines]: serve the narration app with uvicorn."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from narration.agent_tools import AgentTools
from narration.app import build_app
from narration.auth import _check_runner_token
from narration.runner_tools import RunnerTools
from narration.workitems import WorkItemStore

EPILOG = """\
environment:
  NARRATION_RUNNER_TOKEN_FILE  file holding the runner's bearer token (required): one
                               line, ASCII, no spaces or control characters
  NARRATION_DATA_DIR           where work items are kept (default ~/narration-data)
  NARRATION_HOST               address to listen on (default 127.0.0.1)
  NARRATION_PORT               port to listen on (default 8770)
  NARRATION_ALLOWED_HOSTS      comma-separated host:port values the Host header may
                               have (default 127.0.0.1:PORT,localhost:PORT)
  GEMINI_API_KEY               the verifier's key, from the environment or .env;
                               not needed with --fake-engines
"""


def _runner_token() -> str:
    """The runner token from NARRATION_RUNNER_TOKEN_FILE, its trailing newline (and
    any other trailing whitespace) dropped; a one-line exit if it can't be used.
    The check is BearerAuth's own, run before any engine loads."""
    name = os.environ.get("NARRATION_RUNNER_TOKEN_FILE")
    if not name:
        raise SystemExit("NARRATION_RUNNER_TOKEN_FILE is not set: point it at a file holding "
                         "the runner's bearer token")
    path = Path(name)
    try:
        token = path.read_text(encoding="utf-8").rstrip()
    except OSError as e:
        raise SystemExit(f"cannot read the runner token file {path}: {e.strerror or e}") from None
    except UnicodeDecodeError:
        raise SystemExit(f"the runner token file {path} is not UTF-8 text") from None
    try:
        _check_runner_token(token)
    except ValueError as e:
        raise SystemExit(f"the runner token file {path} is not usable: {e}") from None
    return token


def _port() -> int:
    value = os.environ.get("NARRATION_PORT", "8770")
    try:
        port = int(value)
    except ValueError:
        port = -1
    if not 0 < port < 65536:
        raise SystemExit(f"NARRATION_PORT must be a port number; got {value!r}")
    return port


def _allowed_hosts(port: int) -> list[str]:
    value = os.environ.get("NARRATION_ALLOWED_HOSTS", f"127.0.0.1:{port},localhost:{port}")
    hosts = [h.strip() for h in value.split(",") if h.strip()]
    if not hosts:
        raise SystemExit("NARRATION_ALLOWED_HOSTS is empty: list the host:port values clients "
                         "use, comma-separated, or unset it for the local defaults")
    return hosts


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m narration", epilog=EPILOG,
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                description="The narration MCP server: an agent server at "
                                            "/agent/mcp and a runner server at /runner/mcp.")
    p.add_argument("--fake-engines", action="store_true",
                   help="no TTS, Gemini or G2P: fake engines, for smoke tests")
    args = p.parse_args(argv)
    runner_token = _runner_token()
    data = Path(os.environ.get("NARRATION_DATA_DIR", str(Path.home() / "narration-data")))
    host = os.environ.get("NARRATION_HOST", "127.0.0.1")
    port = _port()
    allowed = _allowed_hosts(port)
    if args.fake_engines:
        from narration.fakes import FakeConverter, FakeG2P, FakeTTS, FakeVerifier
        tts, ver, g2p, conv = FakeTTS(), FakeVerifier(), FakeG2P(), FakeConverter()
    else:
        import dotenv
        dotenv.load_dotenv()  # the same .env verify_audio.py reads
        if not os.environ.get("GEMINI_API_KEY"):
            raise SystemExit("GEMINI_API_KEY is not set (environment or .env); the Gemini "
                             "verifier needs it. Pass --fake-engines to run without it.")
        from narration.engines import GeminiVerifier, KokoroEngine, MisakiG2P, Mp3Converter
        tts, ver, g2p, conv = KokoroEngine(), GeminiVerifier(), MisakiG2P(), Mp3Converter()
    store = WorkItemStore(data)
    app = build_app(store, AgentTools(store, tts, ver, g2p), RunnerTools(store, conv, g2p),
                    runner_token, allowed)
    # No root_path, ever: BearerAuth matches absolute request paths (/health, /agent/...,
    # /runner/...), and a root path would move them out from under its gate.
    # lifespan "on": the lifespan starts both MCP session managers; if it fails, the
    # server must not start (under "auto" uvicorn would serve without them).
    uvicorn.run(app, host=host, port=port, lifespan="on")


if __name__ == "__main__":
    main()
