#!/usr/bin/env python3
"""Register (or remove) the Approval Gate hook for Claude Code or Codex.

    python scripts/install_hook.py                      # Claude Code, ~/.claude/settings.json, every project
    python scripts/install_hook.py --settings <project>/.claude/settings.local.json
    python scripts/install_hook.py --codex --settings <project>/.codex/hooks.json
    python scripts/install_hook.py [--codex] --settings ... --uninstall

Idempotent: re-running replaces the gate's own entry and leaves every other hook alone.
A copy of the original file is kept next to it as <name>.approval-gate.bak.

Codex loads a project's .codex/hooks.json only in a trusted project, and runs a hook only
after you trust it: open `/hooks` in the Codex session and trust "Approval Gate".
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

HOOK = (Path(__file__).resolve().parent.parent / "hook" / "approval_gate_hook.py").as_posix()
PYTHON = Path(sys.executable).as_posix()


def is_gate(group: dict) -> bool:
    return any("approval_gate_hook.py" in json.dumps(h) for h in group.get("hooks", []))


def claude_entry(matcher: str) -> dict:
    return {"matcher": matcher, "hooks": [{"type": "command", "command": PYTHON, "args": ["-I", HOOK], "timeout": 100}]}


def codex_entry(matcher: str) -> dict:
    # Codex takes a command string. Codex doesn't document which shell runs it on Windows, so the
    # command is a bare `python.exe -I hook.py`, which reads the same in cmd, PowerShell and sh.
    if " " in PYTHON or " " in HOOK:
        sys.exit(f"Paths with spaces need quoting this installer doesn't do: {PYTHON} {HOOK}")
    command = f"{PYTHON} -I {HOOK} --agent codex"
    return {"matcher": matcher, "hooks": [{"type": "command", "command": command, "commandWindows": command,
                                           "timeout": 100, "statusMessage": "Asking Approval Gate (glasses)"}]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--codex", action="store_true", help="write a Codex hooks.json entry instead of Claude Code settings")
    parser.add_argument("--settings", type=Path, help="file to edit (default: ~/.claude/settings.json, or ~/.codex/hooks.json with --codex)")
    parser.add_argument("--matcher", help="tools to gate (default: Bash|Write|Edit, or Bash|apply_patch with --codex)")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()

    path: Path = args.settings or (Path.home() / (".codex/hooks.json" if args.codex else ".claude/settings.json"))
    settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    groups = [g for g in settings.get("hooks", {}).get("PermissionRequest", []) if not is_gate(g)]
    if not args.uninstall:
        if args.codex:
            groups.append(codex_entry(args.matcher or "Bash|apply_patch"))
        else:
            groups.append(claude_entry(args.matcher or "Bash|Write|Edit"))
    hooks = settings.setdefault("hooks", {})
    if groups:
        hooks["PermissionRequest"] = groups
    else:
        hooks.pop("PermissionRequest", None)
        if not hooks:
            settings.pop("hooks")

    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".approval-gate.bak"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    agent = "Codex" if args.codex else "Claude Code"
    print(f"{'Removed the gate from' if args.uninstall else f'Registered the gate for {agent} in'} {path}")


if __name__ == "__main__":
    main()
