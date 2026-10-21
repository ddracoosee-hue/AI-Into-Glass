#!/usr/bin/env python3
"""Register (or remove) the Approval Gate hook in a Claude Code settings file.

    python scripts/install_hook.py                      # ~/.claude/settings.json, every project
    python scripts/install_hook.py --settings path/.claude/settings.local.json
    python scripts/install_hook.py --uninstall

Idempotent: re-running replaces the gate's own entry and leaves every other hook alone.
A copy of the original file is kept next to it as <name>.approval-gate.bak.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

HOOK = (Path(__file__).resolve().parent.parent / "hook" / "approval_gate_hook.py").as_posix()


def is_gate(group: dict) -> bool:
    return any("approval_gate_hook.py" in json.dumps(h) for h in group.get("hooks", []))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--settings", type=Path, default=Path.home() / ".claude" / "settings.json")
    parser.add_argument("--matcher", default="Bash|Write|Edit")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()

    path: Path = args.settings
    settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    groups = [g for g in settings.get("hooks", {}).get("PermissionRequest", []) if not is_gate(g)]
    if not args.uninstall:
        groups.append({"matcher": args.matcher, "hooks": [{
            "type": "command", "command": Path(sys.executable).as_posix(), "args": ["-I", HOOK], "timeout": 100}]})
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
    print(f"{'Removed the gate from' if args.uninstall else 'Registered the gate in'} {path}")


if __name__ == "__main__":
    main()
