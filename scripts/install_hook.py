#!/usr/bin/env python3
"""Register (or remove) the Approval Gate hook for Claude Code or Codex.

    python scripts/install_hook.py                      # Claude Code, ~/.claude/settings.json, every project
    python scripts/install_hook.py --settings <project>/.claude/settings.local.json
    python scripts/install_hook.py --codex --settings <project>/.codex/hooks.json
    python scripts/install_hook.py [--codex] --settings ... --uninstall
    python scripts/install_hook.py --deck [--codex] --settings ...   # also Command Deck telemetry

Idempotent: re-running replaces the gate's own entries and leaves every other hook alone.
A copy of the original file is kept next to it as <name>.approval-gate.bak.

--deck adds fire-and-forget telemetry for the Command Deck: Claude Code gets `http` hooks that post
to the relay's loopback ingest port (2 s timeout; SessionStart, which `http` can't serve, goes through
hook/deck_ingest.py), and Codex gets hook/deck_ingest.py with `async: true`. The ingest key reaches
Claude Code as DECK_INGEST_TOKEN in the `env` of settings.local.json, never in a committed
settings.json. Claude Code also exports that variable to the agent's own shell: with it the agent can
post telemetry, but it can't create or decide an approval.

Codex loads a project's .codex/hooks.json only in a trusted project, and runs a hook only
after you trust it: open `/hooks` in the Codex session and trust "Approval Gate".
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
from pathlib import Path

HOOK = (Path(__file__).resolve().parent.parent / "hook" / "approval_gate_hook.py").as_posix()
FORWARDER = (Path(__file__).resolve().parent.parent / "hook" / "deck_ingest.py").as_posix()
PYTHON = Path(sys.executable).as_posix()
CLAUDE_DECK_EVENTS = (
    "UserPromptSubmit",
    "PreToolUse",
    "PermissionRequest",
    "PermissionDenied",
    "PostToolUse",
    "PostToolUseFailure",
    "Notification",
    "SubagentStart",
    "SubagentStop",
    "TaskCreated",
    "TaskCompleted",
    "Stop",
    "StopFailure",
    "CwdChanged",
    "SessionEnd",
)
CODEX_DECK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "SessionEnd",
    "Interrupt",
)
TOKEN_VAR = "DECK_INGEST_TOKEN"


def is_gate(group: dict) -> bool:
    return any("approval_gate_hook.py" in json.dumps(h) for h in group.get("hooks", []))


def is_deck(group: dict) -> bool:
    return any("deck_ingest.py" in json.dumps(h) or "/ingest?agent=" in json.dumps(h) for h in group.get("hooks", []))


def state_dir() -> Path:
    if os.environ.get("APPROVAL_GATE_HOME"):
        return Path(os.environ["APPROVAL_GATE_HOME"])
    return Path(os.environ.get("APPDATA") or Path.home() / ".config") / "approval-gate"


def ingest_key() -> str:
    """The relay creates it on first start; create it here if the relay hasn't run yet."""
    path = state_dir() / "ingest.key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"dik_{secrets.token_urlsafe(32)}")
    return path.read_text().strip()


def deck_entries(codex: bool, port: int) -> dict[str, list[dict]]:
    if codex:
        command = f"{PYTHON} -I {FORWARDER} --agent codex"
        hook = {"type": "command", "command": command, "commandWindows": command, "async": True, "timeout": 2}
        return {event: [{"hooks": [dict(hook)]}] for event in CODEX_DECK_EVENTS}
    http = {
        "type": "http",
        "url": f"http://127.0.0.1:{port}/ingest?agent=claude-code",
        "headers": {"Authorization": f"Bearer ${TOKEN_VAR}"},
        "allowedEnvVars": [TOKEN_VAR],
        "timeout": 2,
    }
    entries = {event: [{"hooks": [dict(http)]}] for event in CLAUDE_DECK_EVENTS}
    entries["SessionStart"] = [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": PYTHON,
                    "args": ["-I", FORWARDER, "--agent", "claude-code"],
                    "async": True,
                    "timeout": 2,
                }
            ]
        }
    ]
    return entries


def write(path: Path, settings: dict) -> None:
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".approval-gate.bak"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def env_file(path: Path) -> Path:
    """Where the ingest token goes: never a project's committed settings.json."""
    return (
        path.with_name("settings.local.json")
        if path.name == "settings.json" and path.parent != Path.home() / ".claude"
        else path
    )


def claude_entry(matcher: str) -> dict:
    return {"matcher": matcher, "hooks": [{"type": "command", "command": PYTHON, "args": ["-I", HOOK], "timeout": 100}]}


def codex_entry(matcher: str) -> dict:
    # Codex takes a command string. Codex doesn't document which shell runs it on Windows, so the
    # command is a bare `python.exe -I hook.py`, which reads the same in cmd, PowerShell and sh.
    if " " in PYTHON or " " in HOOK:
        sys.exit(f"Paths with spaces need quoting this installer doesn't do: {PYTHON} {HOOK}")
    command = f"{PYTHON} -I {HOOK} --agent codex"
    return {
        "matcher": matcher,
        "hooks": [
            {
                "type": "command",
                "command": command,
                "commandWindows": command,
                "timeout": 100,
                "statusMessage": "Asking Approval Gate (glasses)",
            }
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--codex", action="store_true", help="write a Codex hooks.json entry instead of Claude Code settings"
    )
    parser.add_argument(
        "--settings",
        type=Path,
        help="file to edit (default: ~/.claude/settings.json, or ~/.codex/hooks.json with --codex)",
    )
    parser.add_argument("--matcher", help="tools to gate (default: Bash|Write|Edit, or Bash|apply_patch with --codex)")
    parser.add_argument("--deck", action="store_true", help="also register Command Deck telemetry")
    parser.add_argument("--ingest-port", type=int, default=8798)
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()

    path: Path = args.settings or (Path.home() / (".codex/hooks.json" if args.codex else ".claude/settings.json"))
    settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    hooks = settings.setdefault("hooks", {})
    for event in list(hooks):
        hooks[event] = [g for g in hooks[event] if not is_gate(g) and not is_deck(g)]
    if not args.uninstall:
        gate = (
            codex_entry(args.matcher or "Bash|apply_patch")
            if args.codex
            else claude_entry(args.matcher or "Bash|Write|Edit")
        )
        hooks.setdefault("PermissionRequest", []).append(gate)
        if args.deck:
            for event, groups in deck_entries(args.codex, args.ingest_port).items():
                hooks.setdefault(event, []).extend(groups)
    for event in [e for e, groups in hooks.items() if not groups]:
        del hooks[event]
    if not hooks:
        settings.pop("hooks")

    token_path = env_file(path)
    if not args.codex and (args.deck or args.uninstall):
        local = (
            settings
            if token_path == path
            else (json.loads(token_path.read_text(encoding="utf-8")) if token_path.exists() else {})
        )
        env = local.setdefault("env", {})
        env.pop(TOKEN_VAR, None)
        if args.deck and not args.uninstall:
            env[TOKEN_VAR] = ingest_key()
        if not env:
            local.pop("env")
        if token_path != path and (local or token_path.exists()):
            write(token_path, local)

    write(path, settings)
    agent = "Codex" if args.codex else "Claude Code"
    what = "the gate and Command Deck telemetry" if args.deck else "the gate"
    print(f"{'Removed the gate from' if args.uninstall else f'Registered {what} for {agent} in'} {path}")
    if args.deck and not args.codex and not args.uninstall:
        print(f"{TOKEN_VAR} is in {token_path}")


if __name__ == "__main__":
    main()
