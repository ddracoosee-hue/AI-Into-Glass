#!/usr/bin/env python3
"""Drive the Even Hub simulator's automation API, for tests and the demo recording.

Start the simulator with `--automation-port 9898` (`npm run simulate` in plugin/ does), then:

    python scripts/sim.py click            # tap: approve / arm
    python scripts/sim.py double           # double-tap: deny / disarm
    python scripts/sim.py down | up        # swipe: full command / summary
    python scripts/sim.py shot out.png     # glasses framebuffer as PNG
    python scripts/sim.py console          # the plugin's console output
    python scripts/sim.py long | menu      # long-press; open the contextual menu
    python scripts/sim.py run walk         # Phase B: Deck > Projects > glasses > Sessions, back x3
    python scripts/sim.py run interrupt    # Phase B: an approval at depth 3 returns to the same row

`run` needs the devstack (scripts/devstack.py up && seed && sim) and fails on any console error.
scripts/demo.py imports this module and uses the same calls.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:9898"
ACTIONS = {"click": "click", "double": "double_click", "down": "down", "up": "up", "long": "long_press",
           "release": "long_press_release", "menu": "context_menu"}
ROOT = Path(__file__).resolve().parent.parent
STATE = Path(os.environ.get("APPROVAL_GATE_HOME", "C:/deck-sandbox/state"))
SHOTS = Path(os.environ.get("SIM_SHOTS", STATE / "shots"))
STEP = 1.2  # past the 0.7 s lockout and the 0.35 s tap window


def ping() -> bool:
    try:
        return urllib.request.urlopen(f"{BASE}/api/ping", timeout=2).read().strip() == b"pong"
    except OSError:
        return False


def send(action: str) -> None:
    """One touchpad gesture: click, double, down or up."""
    body = json.dumps({"action": ACTIONS[action]}).encode()
    req = urllib.request.Request(f"{BASE}/api/input", data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5).read()


def shot(path: str) -> str:
    with open(path, "wb") as f:
        f.write(urllib.request.urlopen(f"{BASE}/api/screenshot/glasses", timeout=5).read())
    return path


def console() -> list[dict]:
    return json.load(urllib.request.urlopen(f"{BASE}/api/console", timeout=5))["entries"]


class Run:
    """A scripted pass over the plugin: gestures in, `[deck]` console lines out, any error fails it."""

    def __init__(self, name: str):
        self.name, self.failures = name, []
        self.seen = max((e["id"] for e in console()), default=0)
        SHOTS.mkdir(parents=True, exist_ok=True)

    def entries(self) -> list[dict]:
        fresh = [e for e in console() if e["id"] > self.seen]
        for e in fresh:
            if e["level"] == "error" or e["message"].startswith(("[uncaught]", "[unhandledrejection]")):
                self.failures.append(f"console error: {e['message'][:160]}")
        return fresh

    def expect(self, pattern: str, timeout: float = 6.0) -> str | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for e in self.entries():
                self.seen = e["id"]
                if re.search(pattern, e["message"]):
                    print(f"  ok    {e['message'][:110]}")
                    return e["message"]
            time.sleep(0.15)
        self.failures.append(f"never saw /{pattern}/")
        print(f"  FAIL  never saw /{pattern}/")
        return None

    def do(self, action: str, expect: str | None = None, pause: float = STEP) -> str | None:
        send(action)
        found = self.expect(expect) if expect else None
        time.sleep(pause)
        return found

    def shot(self, label: str) -> None:
        shot(str(SHOTS / f"{self.name}-{label}.png"))

    def finish(self) -> None:
        self.entries()
        print(f"{self.name}: {'PASS' if not self.failures else 'FAIL'}")
        for f in self.failures:
            print("  -", f)
        sys.exit(1 if self.failures else 0)


def gate(*args: str) -> None:
    subprocess.run([sys.executable, str(ROOT / "relay" / "gatectl.py"), *args], env={**os.environ, "APPROVAL_GATE_HOME": str(STATE)},
                   capture_output=True, check=True)


def inject_approval(summary: str = "git push origin main") -> str:
    """What the gate hook does: POST /requests with the hook key, as a session in the glasses project."""
    body = {"session": {"agent": "claude-code", "name": "glasses", "cwd": "C:/deck-sandbox/glasses"}, "tool": "Bash",
            "summary": summary, "detail": summary, "risk": "high"}
    req = urllib.request.Request("http://127.0.0.1:8797/requests", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {(STATE / 'hook.key').read_text().strip()}", "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=5))["id"]


def at_root(run: Run) -> None:
    """Start from a fresh plugin at the Deck root."""
    subprocess.run([sys.executable, str(ROOT / "scripts" / "devstack.py"), "sim"], check=True, capture_output=True)
    run.seen = 0
    run.expect(r"\[deck\] rebuild deck:focus:", 15)
    time.sleep(STEP + 1)


def run_walk() -> None:
    run = Run("walk")
    at_root(run)
    run.shot("0-deck")
    for _ in range(3):
        run.do("down", pause=0.3)
    run.do("click", r"page projects:all: depth 2")
    run.shot("1-projects")
    run.do("click", r"page project:glasses:glasses depth 3")
    run.shot("2-glasses")
    run.do("down", pause=0.3)
    run.do("click", r"page sessions:glasses: depth 4")
    run.shot("3-sessions")
    run.do("double", r"page project:glasses:glasses depth 3 cursor 1")
    run.do("double", r"page projects:all: depth 2 cursor 0")
    run.do("double", r"page deck:focus: depth 1 cursor 3")
    run.shot("4-back-at-deck")
    run.finish()


def run_interrupt() -> None:
    run = Run("interrupt")
    at_root(run)
    for _ in range(3):
        run.do("down", pause=0.3)
    run.do("click", r"page projects:all: depth 2")
    run.do("click", r"page project:glasses:glasses depth 3")
    run.do("down", pause=0.3)
    run.do("down", pause=0.6)
    run.shot("0-before")
    gate("arm")
    try:
        rid = inject_approval()
        run.expect(rf"prompt {rid} over project:glasses:glasses cursor 2")
        time.sleep(STEP)
        run.shot("1-prompt")
        run.do("click", r"rebuild result:APPROVED", pause=0.2)
        run.shot("2-approved")
        run.expect(r"restored project:glasses:glasses cursor 2")
        run.expect(r"rebuild project:glasses:glasses")
        time.sleep(0.8)
        run.shot("3-restored")
    finally:
        gate("disarm")
    run.finish()


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in (*ACTIONS, "shot", "console", "run"):
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd == "run":
        scenarios = {"walk": run_walk, "interrupt": run_interrupt}
        if len(sys.argv) < 3 or sys.argv[2] not in scenarios:
            sys.exit(f"run one of: {', '.join(scenarios)}")
        return scenarios[sys.argv[2]]()
    if cmd in ACTIONS:
        send(cmd)
        print("ok")
    elif cmd == "shot":
        print(shot(sys.argv[2] if len(sys.argv) > 2 else "glasses.png"))
    else:
        for entry in console():
            print(entry["level"], entry["message"])


if __name__ == "__main__":
    main()
