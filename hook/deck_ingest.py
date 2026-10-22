#!/usr/bin/env python3
"""Command Deck telemetry forwarder, for hooks that can't POST on their own.

Codex hooks are command-only, and Claude Code's `http` hooks don't run on SessionStart, so both
use this. It reads the hook event, drops the text the deck never needs (prompts, assistant
messages, tool output, patch bodies), posts the rest to the relay's loopback ingest port with a
2 s timeout, and exits 0 without printing. A dead relay costs the agent nothing.

Codex reports a shell command's output but not its exit code, so for a Codex `Bash` call this
reads the end of the output, here and only here, for a test runner's failure summary, and sends
`deck_outcome: "failed"` instead of the output.

Stdlib only; run with `python -I`. Registered by scripts/install_hook.py --deck:

    python -I hook/deck_ingest.py --agent codex
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

TIMEOUT = 2
DROP = ("prompt", "last_assistant_message", "tool_response", "transcript_path", "agent_transcript_path",
        "background_tasks", "session_crons", "permission_suggestions", "error_details", "scratchpad_dir", "message")
KEEP_INPUT = ("command", "cmd", "file_path", "notebook_path", "path", "pattern", "url", "subagent_type", "agent_type")
PATCH_HEADER = re.compile(r"^\*\*\* (?:(?:Add|Update|Delete) File|Move to): .+$", re.M)
# The last lines of a failing run, per runner. Read in memory, never forwarded.
TEST_FAILED = re.compile(
    r"^=+ .*\b\d+ (failed|errors?)\b.* =+\s*$"          # pytest
    r"|^FAILED \((failures|errors)=\d+"                  # unittest
    r"|^(#|ℹ) fail [1-9]\d*\s*$"                         # node --test
    r"|^\s*Tests?( Files)?:?\s+.*\b[1-9]\d* failed\b"    # jest, vitest
    r"|^test result: FAILED\b"                           # cargo
    r"|^FAIL\s"                                          # go
    r"|^npm (ERR!|error) (Test failed|code ELIFECYCLE)", re.M)
AGENTS = ("claude-code", "codex")


def state_dir() -> Path:
    if os.environ.get("APPROVAL_GATE_HOME"):
        return Path(os.environ["APPROVAL_GATE_HOME"])
    return Path(os.environ.get("APPDATA") or Path.home() / ".config") / "approval-gate"


def slim(event: dict, agent: str) -> dict:
    out = {k: v for k, v in event.items() if k not in DROP}
    tool_input = event.get("tool_input")
    if isinstance(tool_input, dict):
        kept = {k: tool_input[k] for k in KEEP_INPUT if k in tool_input}
        patch = tool_input.get("command") if event.get("tool_name") == "apply_patch" else None
        if isinstance(patch, str):
            kept["command"] = "\n".join(PATCH_HEADER.findall(patch))  # file names only, never the patch
        out["tool_input"] = kept
    if (agent == "codex" and event.get("hook_event_name") == "PostToolUse" and event.get("tool_name") == "Bash"
            and isinstance(event.get("tool_response"), str) and TEST_FAILED.search(event["tool_response"][-4000:])):
        out["deck_outcome"] = "failed"
    return out


def post(home: Path, agent: str, event: dict) -> None:
    import urllib.request
    url = json.loads((home / "relay.json").read_text())["ingest_url"]
    if not url.startswith("http://127.0.0.1:"):
        return  # ingest is loopback only; anything else is a misconfigured or tampered relay.json
    req = urllib.request.Request(f"{url}?agent={agent}", data=json.dumps(event).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {(home / 'ingest.key').read_text().strip()}",
                                          "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=TIMEOUT) as resp:
        resp.read()


def main() -> None:
    try:
        agent = sys.argv[sys.argv.index("--agent") + 1] if "--agent" in sys.argv else "claude-code"
        event = json.loads(sys.stdin.buffer.read() or b"{}")
        if agent in AGENTS and isinstance(event, dict):
            post(state_dir(), agent, slim(event, agent))
    except Exception:
        pass  # telemetry never blocks or breaks an agent


if __name__ == "__main__":
    main()
