#!/usr/bin/env python3
"""Record real hook payloads from Claude Code and Codex, then scrub them into test fixtures.

    python -I scripts/record_hooks.py record --out <dir>          # as a hook: stdin -> <dir>/<n>-<Event>.json
    python scripts/record_hooks.py scrub <dir> <fixtures-dir> [OLD=NEW]   # copy with the real home replaced;
                                                                      # OLD=NEW also moves a recording's root

`record` never prints, so as a PermissionRequest hook it never decides anything. `scrub` swaps the
home directory (every spelling) for C:/Users/demo and drops prompt and assistant text, so the
fixtures can be committed: they keep the real field names, ids, and path shapes.
Stdlib only.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

FAKE_HOME = "C:\\Users\\demo"
# Text the agent or the user wrote. The fixtures keep the field, not the words.
TEXT_FIELDS = ("prompt", "last_assistant_message", "message", "error_details", "task_description")


def record(out: Path) -> None:
    raw = sys.stdin.buffer.read()
    try:
        event = json.loads(raw or b"{}")
        name = re.sub(r"[^A-Za-z]", "", str(event.get("hook_event_name", "unknown")))[:40] or "unknown"
    except ValueError:
        event, name = {"unparsed": raw.decode("utf-8", "replace")}, "unparsed"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{time.time_ns()}-{name}.json"
    path.write_text(json.dumps(event, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def home_spellings(home: str, user: str) -> list[tuple[str, str]]:
    fake_user = Path(FAKE_HOME).name
    fwd, fake_fwd = home.replace("\\", "/"), FAKE_HOME.replace("\\", "/")
    pairs = [(home, FAKE_HOME), (fwd, fake_fwd), (home.replace("\\", "\\\\"), FAKE_HOME.replace("\\", "\\\\"))]
    if re.match(r"^[A-Za-z]:/", fwd):
        pairs.append((f"/{fwd[0].lower()}{fwd[2:]}", f"/{fake_fwd[0].lower()}{fake_fwd[2:]}"))
    pairs.append((user, fake_user))
    return sorted(pairs, key=lambda p: len(p[0]), reverse=True)


def scrub_value(value, pairs):
    if isinstance(value, str):
        for real, fake in pairs:
            value = re.sub(re.escape(real), fake.replace("\\", "\\\\"), value, flags=re.I)
        return value
    if isinstance(value, list):
        return [scrub_value(v, pairs) for v in value]
    if isinstance(value, dict):
        return {
            k: (f"<{k} omitted>" if k in TEXT_FIELDS and isinstance(v, str) and v else scrub_value(v, pairs))
            for k, v in value.items()
        }
    return value


def scrub(src: Path, dst: Path, rebase: str | None = None) -> None:
    home = str(Path.home())
    pairs = home_spellings(home, Path(home).name)
    if rebase:
        old, new = rebase.split("=", 1)
        pairs = [
            (old, new),
            (old.replace("\\", "/"), new.replace("\\", "/")),
            (old.replace("\\", "\\\\"), new.replace("\\", "\\\\")),
        ] + pairs
    dst.mkdir(parents=True, exist_ok=True)
    for i, path in enumerate(sorted(src.glob("*.json"), key=lambda p: int(p.name.split("-")[0])), 1):
        event = scrub_value(json.loads(path.read_text(encoding="utf-8")), pairs)
        (dst / f"{i:03d}-{path.name.split('-', 1)[1]}").write_text(
            json.dumps(event, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    print(f"{i} fixtures -> {dst}")


def main() -> None:
    if len(sys.argv) >= 4 and sys.argv[1] == "record" and sys.argv[2] == "--out":
        try:
            record(Path(sys.argv[3]))
        except Exception:
            pass  # a recorder must never disturb the session it watches
    elif len(sys.argv) in (4, 5) and sys.argv[1] == "scrub":
        scrub(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4] if len(sys.argv) == 5 else None)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
