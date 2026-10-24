#!/usr/bin/env python3
"""Phase A acceptance, live: a real Claude Code session moves idle -> working -> waiting -> idle -> ended.

    python scripts/deck_live_check.py            # needs claude on PATH; runs one short `claude -p`

Everything is throwaway and local. State goes in a temp dir (APPROVAL_GATE_HOME), the relay runs
on spare ports (18797 phone API, 18798 ingest), and the scratch repo is C:/deck-sandbox/live with a
bare origin next to it. Hooks go in that repo's .claude/ only. A stand-in for the glasses polls
/pending and approves the one request after 2 s, so the session spends real time in `waiting`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SANDBOX = Path("C:/deck-sandbox")
REPO, ORIGIN = SANDBOX / "live", SANDBOX / "live-origin.git"
MARKER = ".command-deck-scratch"
PORT, INGEST_PORT = 18797, 18798
TOKEN = "agt_live-check-device"
PROMPT = (
    "This is a scratch repo for a hook test. Run exactly one Bash command, `git push origin main`, "
    "then reply with the single word: done."
)
ABSOLUTE = re.compile(r"[A-Za-z]:[\\/]|(^|[\s'\"=])/[A-Za-z]/|users[\\/]", re.I)


def run(*args, cwd=None, env=None, timeout=60) -> str:
    out = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if out.returncode:
        sys.exit(f"{' '.join(map(str, args))} failed:\n{out.stdout}{out.stderr}")
    return out.stdout


def rmtree(path: Path) -> None:
    """Git marks its object files read-only on Windows."""

    def unlock(func, name, _):
        os.chmod(name, stat.S_IWRITE)
        func(name)

    shutil.rmtree(path, onexc=unlock)


def make_repo() -> None:
    for path in (REPO, ORIGIN):
        if path.exists():
            if not (path / MARKER).exists() and not (path / "config").exists():
                sys.exit(f"{path} exists and isn't ours; remove it by hand")
            rmtree(path)
    run("git", "init", "-q", "--bare", "-b", "main", str(ORIGIN))
    run("git", "init", "-q", "-b", "main", str(REPO))
    (REPO / MARKER).write_text("created by scripts/deck_live_check.py\n")
    (REPO / "README.md").write_text("# live\n")
    for args in (
        ("config", "user.email", "demo@example.com"),
        ("config", "user.name", "Demo"),
        ("add", "-A"),
        ("commit", "-qm", "Initial commit"),
        ("remote", "add", "origin", ORIGIN.as_posix()),
    ):
        run("git", *args, cwd=REPO)
    (REPO / ".git" / "info" / "exclude").write_text(f".claude/\n{MARKER}\n")


def call(method: str, path: str, body=None, port=PORT):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            text = resp.read()
            return resp.status, json.loads(text) if text else None
    except urllib.error.HTTPError as err:
        return err.code, None
    except OSError:  # the relay is starting or has stopped
        return 0, None


def main() -> None:
    if not shutil.which("claude"):
        sys.exit("claude is not on PATH")
    make_repo()
    home = Path(tempfile.mkdtemp(prefix="deck-live-"))
    env = {**os.environ, "APPROVAL_GATE_HOME": str(home)}
    sys.path.insert(0, str(ROOT / "relay"))
    import relay as R

    R.save_devices(home, {"dev_live01": {"label": "live check", "sha256": R.sha256(TOKEN), "created_at": R.now_iso()}})
    config = home / "deck.projects.json"
    config.write_text(json.dumps({"projects": [{"id": "live", "name": "live", "root": REPO.as_posix()}]}))
    relay = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "relay" / "relay.py"),
            "--port",
            str(PORT),
            "--ingest-port",
            str(INGEST_PORT),
            "--projects",
            str(config),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    transitions: list[str] = []
    stop = threading.Event()
    approved: list[str] = []
    try:
        for _ in range(50):
            if call("GET", "/health")[0] == 200:
                break
            time.sleep(0.1)
        run(
            sys.executable,
            str(ROOT / "scripts" / "install_hook.py"),
            "--deck",
            "--ingest-port",
            str(INGEST_PORT),
            "--settings",
            str(REPO / ".claude" / "settings.json"),
            env=env,
        )
        run(sys.executable, str(ROOT / "relay" / "gatectl.py"), "arm", env=env)

        def glasses() -> None:  # polls like the plugin; approves the first prompt after 2 s on screen
            seen: dict[str, float] = {}
            while not stop.is_set():
                status, req = call("GET", "/pending")
                if status == 200 and req:
                    seen.setdefault(req["id"], time.monotonic())
                    if time.monotonic() - seen[req["id"]] >= 2 and req["id"] not in approved:
                        call("POST", f"/requests/{req['id']}/decide", {"decision": "approve", "nonce": req["nonce"]})
                        approved.append(req["id"])
                stop.wait(0.5)

        def watch() -> None:
            while not stop.is_set():
                status, body = call("GET", "/sessions")
                if status == 200 and body["items"]:
                    state = body["items"][0]["state"]
                    if not transitions or transitions[-1] != state:
                        transitions.append(state)
                        print(f"  {time.strftime('%H:%M:%S')}  {state}", flush=True)
                stop.wait(0.05)

        for target in (glasses, watch):
            threading.Thread(target=target, daemon=True).start()
        print("claude -p running in", REPO, flush=True)
        out = subprocess.run(
            ["claude", "-p", PROMPT, "--setting-sources", "project,local", "--model", "sonnet"],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        print("  claude said:", out.stdout.strip()[:80], flush=True)
        time.sleep(2)
        status, sessions = call("GET", "/sessions")
        _, runs = call("GET", "/runs")
        _, log = call("GET", "/events?since=0")
    finally:
        stop.set()
        relay.terminate()
        relay.wait(10)
    pushed = run("git", "ls-remote", ORIGIN.as_posix(), "refs/heads/main").strip() != ""
    sess = sessions["items"][0] if sessions and sessions["items"] else {}
    blob = json.dumps({"sessions": sessions, "runs": runs})
    recorded = [
        e["state"] for e in (log or {}).get("events", []) if e["type"] == "session" and e["session"] == sess.get("id")
    ]
    want = ["idle", "working", "waiting", "idle", "ended"]
    checks = {
        "the event log has idle, working, waiting, idle, ended": subsequence(want, recorded),
        # In -p mode Stop and SessionEnd land ~0.1 s apart, so a poll can miss the last idle; it must agree with the log.
        "GET /sessions showed working, waiting, ended, in log order": subsequence(
            ["working", "waiting", "ended"], transitions
        )
        and subsequence(transitions, recorded),
        "the glasses stand-in approved one request": len(approved) == 1,
        "the push reached origin": pushed,
        "the session counted the approval": sess.get("counts", {}).get("approved") == 1,
        "its project is `live`": sess.get("project") == "live",
        "no absolute path in /sessions or /runs": not any(ABSOLUTE.search(s) for s in strings(json.loads(blob))),
    }
    print(f"\nGET /sessions: {' -> '.join(transitions)}\nevent log:     {' -> '.join(recorded)}")
    for name, ok in checks.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    shutil.rmtree(home, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


def subsequence(want: list[str], got: list[str]) -> bool:
    it = iter(got)
    return all(any(g == w for g in it) for w in want)


def strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]
    return []


if __name__ == "__main__":
    main()
