#!/usr/bin/env python3
"""Run the whole Command Deck on this PC for development and the simulator tests. Windows.

    python scripts/devstack.py up        # relay (8797 + ingest 8798), Vite (5180), simulator (9898), paired
    python scripts/devstack.py seed      # replay the hook fixtures as live sessions in two projects
    python scripts/devstack.py sim       # restart only the simulator (a fresh plugin at the Deck)
    python scripts/devstack.py nosim     # close the simulator (nobody watching)
    python scripts/devstack.py hooks     # project-level gate + telemetry hooks in both projects
    python scripts/devstack.py reset     # restart the relay with an empty registry and event log
    python scripts/devstack.py down      # stop what `up` started

State lives in C:/deck-sandbox/state (APPROVAL_GATE_HOME), never in %APPDATA%\\approval-gate, so
your own paired devices, hook key and gate.log are untouched. The two projects, `glasses` and
`hub`, are throwaway folders under C:/deck-sandbox with a TODO.md each.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
SANDBOX = Path("C:/deck-sandbox")
STATE = SANDBOX / "state"
PROJECTS = {"glasses": SANDBOX / "glasses", "hub": SANDBOX / "hub"}
SIM_EXE = ROOT / "plugin" / "node_modules" / "@evenrealities" / "sim-win32-x64" / "bin" / "evenhub-simulator.exe"
PIDS = STATE / "devstack.json"
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
FIXTURES = ROOT / "tests" / "fixtures" / "hooks"
REC = "C:\\deck-sandbox\\rec"

sys.path.insert(0, str(ROOT / "relay"))
import relay as R  # noqa: E402


def env() -> dict:
    return {**os.environ, "APPROVAL_GATE_HOME": str(STATE)}


def token() -> str:
    path = STATE / "dev-token.txt"
    if not path.exists():
        path.write_text(f"agt_{secrets.token_urlsafe(24)}")
    return path.read_text().strip()


def prepare() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    for pid, folder in PROJECTS.items():
        folder.mkdir(parents=True, exist_ok=True)
        if not (folder / ".git").exists():
            subprocess.run(["git", "init", "-q", "-b", "main", str(folder)], check=True)
            (folder / "README.md").write_text(f"# {pid}\n")
            subprocess.run(["git", "-C", str(folder), "-c", "user.email=demo@example.com", "-c", "user.name=Demo", "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(folder), "-c", "user.email=demo@example.com", "-c", "user.name=Demo", "commit", "-qm", f"Start {pid}"], check=True)
    (PROJECTS["glasses"] / "TODO.md").write_text("# TODO\n\n- [ ] README threat model\n- [ ] record demo, rm -r build step\n- [x] ship v1\n")
    (PROJECTS["hub"] / "TODO.md").write_text("# TODO\n\n- [ ] wire Codex hooks\n")
    config = {"projects": [{"id": pid, "name": "hub-orch" if pid == "hub" else pid, "root": folder.as_posix(), "todos": ["TODO.md"]}
                           for pid, folder in PROJECTS.items()]}
    (STATE / "deck.projects.json").write_text(json.dumps(config, indent=2))
    R.save_devices(STATE, {"dev_devstack": {"label": "devstack simulator", "sha256": R.sha256(token()), "created_at": R.now_iso()}})


def spawn(name: str, args: list[str], cwd: Path) -> int:
    log = open(STATE / f"{name}.log", "w", encoding="utf-8")
    proc = subprocess.Popen(args, cwd=cwd, env=env(), stdout=log, stderr=subprocess.STDOUT, creationflags=DETACHED)
    pids = json.loads(PIDS.read_text()) if PIDS.exists() else {}
    pids[name] = proc.pid
    PIDS.write_text(json.dumps(pids))
    return proc.pid


def wait_for(url: str, seconds: float = 20) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1).read()
            return True
        except OSError:
            time.sleep(0.3)
    return False


def kill(name: str, gentle: bool = False) -> None:
    """Stop a process `up` started. `gentle` asks its window to close first, so the simulator's
    WebView flushes localStorage to disk the way a real close does."""
    pids = json.loads(PIDS.read_text()) if PIDS.exists() else {}
    pid = pids.pop(name, None)
    if pid:
        if gentle:
            subprocess.run(["taskkill", "/PID", str(pid), "/T"], capture_output=True)
            for _ in range(30):
                if str(pid) not in subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout:
                    break
                time.sleep(0.2)
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    PIDS.write_text(json.dumps(pids))


def start_sim() -> None:
    kill("simulator", gentle=True)
    link = f"http://127.0.0.1:5180/?relay={quote('http://127.0.0.1:8797', safe='')}&token={token()}"
    spawn("simulator", [str(SIM_EXE), link, "--automation-port", "9898"], ROOT / "plugin")
    if not wait_for("http://127.0.0.1:9898/api/ping"):
        sys.exit("simulator did not start; see C:/deck-sandbox/state/simulator.log")


def up() -> None:
    prepare()
    start_relay()
    spawn("vite", ["node", str(ROOT / "plugin" / "node_modules" / "vite" / "bin" / "vite.js")], ROOT / "plugin")
    if not wait_for("http://127.0.0.1:5180/"):
        sys.exit("vite did not start; see C:/deck-sandbox/state/vite.log")
    start_sim()
    print("Up: relay 8797 (ingest 8798), vite 5180, simulator 9898. State:", STATE)


def start_relay() -> None:
    kill("relay")
    spawn("relay", [sys.executable, str(ROOT / "relay" / "relay.py"), "--projects", str(STATE / "deck.projects.json")], ROOT)
    if not wait_for("http://127.0.0.1:8797/health"):
        sys.exit("relay did not start; see C:/deck-sandbox/state/relay.log")


def reset() -> None:
    kill("relay")
    time.sleep(0.5)
    for name in ("deck.log", "deck.log.1", "focus.json", "armed"):
        (STATE / name).unlink(missing_ok=True)
    start_relay()
    print("Relay restarted with an empty registry.")


def hooks() -> None:
    """The gate and Command Deck telemetry, in each project's own settings. Never global."""
    install = [sys.executable, str(ROOT / "scripts" / "install_hook.py"), "--deck"]
    for folder in PROJECTS.values():
        subprocess.run([*install, "--settings", str(folder / ".claude" / "settings.json")], env=env(), check=True, capture_output=True)
        subprocess.run([*install, "--codex", "--settings", str(folder / ".codex" / "hooks.json")], env=env(), check=True, capture_output=True)
        exclude = folder / ".git" / "info" / "exclude"
        if ".claude/" not in exclude.read_text():
            exclude.write_text(exclude.read_text() + ".claude/\n.codex/\n")
    print("Hooks installed in", ", ".join(str(f) for f in PROJECTS.values()))


def down() -> None:
    for name in ("simulator", "vite", "relay"):
        kill(name)
    print("Down.")


def post(agent: str, event: dict) -> None:
    key = (STATE / "ingest.key").read_text().strip()
    req = urllib.request.Request(f"http://127.0.0.1:8798/ingest?agent={agent}", data=json.dumps(event).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5).read()


def replay(agent: str, folder: str, session_suffix: str, stop_before: str | None, project: str) -> None:
    sys.path.insert(0, str(ROOT / "hook"))
    import deck_ingest
    for path in sorted((FIXTURES / agent).glob("*.json")):
        text = path.read_text(encoding="utf-8").replace(json.dumps(REC)[1:-1], json.dumps(str(PROJECTS[project]).replace("/", "\\"))[1:-1])
        text = text.replace(REC.replace("\\", "/"), PROJECTS[project].as_posix())
        event = json.loads(text)
        if stop_before and event["hook_event_name"] == stop_before:
            break
        event["session_id"] = f"{event['session_id'][:-len(session_suffix)]}{session_suffix}"
        post(agent, deck_ingest.slim(event, agent) if agent == "codex" else event)


def seed() -> None:
    """Three live sessions: Claude Code in glasses (stopped short of its push, so it waits) and in
    hub (idle), Codex in hub (mid-run)."""
    replay("claude-code", "glasses", "0001", "PreToolUse", "glasses")  # just started
    events = sorted((FIXTURES / "claude-code").glob("*.json"))
    push_at = next(i for i, p in enumerate(events) if "git push" in p.read_text(encoding="utf-8") and "PreToolUse" in p.name)
    replay_until(events, push_at + 2, "glasses", "0002")
    replay("claude-code", "hub", "0003", "SessionEnd", "hub")
    replay("codex", "hub", "0004", "Stop", "hub")
    print("Seeded 4 sessions in glasses and hub.")


def replay_until(paths: list[Path], count: int, project: str, suffix: str) -> None:
    for path in paths[:count]:
        text = path.read_text(encoding="utf-8").replace(json.dumps(REC)[1:-1], json.dumps(str(PROJECTS[project]).replace("/", "\\"))[1:-1])
        event = json.loads(text.replace(REC.replace("\\", "/"), PROJECTS[project].as_posix()))
        event["session_id"] = f"{event['session_id'][:-len(suffix)]}{suffix}"
        post("claude-code", event)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "up":
        up()
    elif cmd == "down":
        down()
    elif cmd == "seed":
        seed()
    elif cmd == "sim":
        start_sim()
    elif cmd == "nosim":
        kill("simulator", gentle=True)
    elif cmd == "hooks":
        hooks()
    elif cmd == "reset":
        reset()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
