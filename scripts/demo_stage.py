#!/usr/bin/env python3
"""Stage the Command Deck demo: two throwaway repos, their hooks, and the whole stack. Windows.

    python scripts/demo_stage.py              # build the repos and start relay, Vite and simulator; armed, empty log
    python scripts/demo_stage.py --reset      # back to the starting state (under 30 s)
    python scripts/demo_stage.py --teardown   # stop everything and delete C:/deck-sandbox/demo

Everything lives in C:/deck-sandbox/demo: `demo-glasses` and `demo-hub`, each with a bare origin
beside it so `git push` works offline, and `state/` as APPROVAL_GATE_HOME. The deck config names
only these two repos, so no real project name or path can reach the video. Hooks go in each repo's
own .claude/ and .codex/ (git-excluded), never in your user settings. Then run scripts/demo_run.py.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = Path("C:/deck-sandbox/demo")
STATE = DEMO / "state"
MARKER = ".command-deck-demo"
GIT_ID = ["-c", "user.email=demo@example.com", "-c", "user.name=Demo"]

sys.path.insert(0, str(ROOT / "scripts"))
import devstack as D  # noqa: E402  (same process helpers, pointed at the demo state below)

D.STATE, D.PIDS = STATE, STATE / "devstack.json"
D.PROJECTS = {"demo-glasses": DEMO / "demo-glasses", "demo-hub": DEMO / "demo-hub"}

NOTES = {
    "welcome": "# Welcome\n\nThese notes render to small pages you can read on the glasses.\n",
    "deploy": "# Deploy\n\nPush to main, then watch the hub run its suite.\n",
    "focus": "# Focus\n\nOne or two projects at a time. Everything else waits.\n",
    "approvals": "# Approvals\n\nA tap approves. A double-tap denies. Nothing approves itself.\n",
    "todos": "# To-dos\n\nThey live in TODO.md, where you already keep them.\n",
    "threat-model": "# Threat model\n\nThe phone can read the deck while it is open. It can never run anything.\n",
}

RENDER = '''#!/usr/bin/env python3
"""Render notes/*.md to build/: one page per note per theme, plus an index.

Throttled to a page every half second, so a full render takes about half a minute and the demo
has something running. `--fast` skips the throttle.
"""
import html
import sys
import time
from pathlib import Path

THEMES = ("green", "amber", "mono", "large", "compact", "dim", "bold", "wide", "narrow", "print")
root = Path(__file__).resolve().parent.parent
out = root / "build"
out.mkdir(exist_ok=True)
pages = []
for note in sorted((root / "notes").glob("*.md")):
    lines = note.read_text(encoding="utf-8").splitlines()
    title = lines[0].lstrip("# ")
    body = "".join(f"<p>{html.escape(l)}</p>" for l in lines[1:] if l.strip())
    for theme in THEMES:
        name = f"{note.stem}-{theme}.html"
        (out / name).write_text(f"<!doctype html><title>{html.escape(title)}</title><body class={theme}><h1>{html.escape(title)}</h1>{body}", encoding="utf-8")
        pages.append(name)
        print(f"rendered {name}", flush=True)
        if "--fast" not in sys.argv:
            time.sleep(0.5)
(out / "index.html").write_text("".join(f'<a href="{p}">{p}</a><br>' for p in pages), encoding="utf-8")
print(f"{len(pages)} pages in build/", flush=True)
'''

QUEUE = '''"""A tiny job queue with retries."""
from collections import deque


class Queue:
    def __init__(self):
        self.jobs = deque()
        self.done = []

    def push(self, job):
        self.jobs.append(job)

    def run(self, worker):
        while self.jobs:
            job = self.jobs.popleft()
            self.done.append(worker(job))
        return self.done
'''

RETRY = '''"""Retry with exponential backoff."""


def backoff(attempt: int, base: float = 1.0, cap: float = 30.0) -> float:
    """Seconds to wait before retry `attempt` (1-based): base, 2x, 4x... never more than `cap`."""
    delay = base * 2 ** attempt  # off by one: attempt 1 should wait `base`, not 2x base
    return min(delay, cap + 2)   # and the cap leaks by two seconds


def retry(fn, attempts: int = 3):
    last = None
    for i in range(1, attempts + 1):
        try:
            return fn(i)
        except Exception as err:  # noqa: BLE001
            last = err
    raise last
'''

# About 20 s in all: each test waits like the I/O it stands in for. One fails, for a real reason.
TESTS = """import time
import unittest

from hub.queue import Queue
from hub.retry import backoff, retry

IO = 2.0  # seconds each test spends waiting, like a call to a slow service


class QueueTests(unittest.TestCase):
    def setUp(self):
        time.sleep(IO)

    def test_runs_in_order(self):
        q = Queue()
        for n in range(3):
            q.push(n)
        self.assertEqual(q.run(lambda n: n * 10), [0, 10, 20])

    def test_empty_queue(self):
        self.assertEqual(Queue().run(str), [])

    def test_done_accumulates(self):
        q = Queue()
        q.push("a")
        q.run(str.upper)
        q.push("b")
        self.assertEqual(q.run(str.upper), ["A", "B"])

    def test_worker_sees_every_job(self):
        seen = []
        q = Queue()
        for job in "xyz":
            q.push(job)
        q.run(seen.append)
        self.assertEqual(seen, list("xyz"))


class RetryTests(unittest.TestCase):
    def setUp(self):
        time.sleep(IO)

    def test_retry_returns_first_success(self):
        self.assertEqual(retry(lambda i: i), 1)

    def test_retry_raises_the_last_error(self):
        def fail(i):
            raise ValueError(i)
        with self.assertRaises(ValueError):
            retry(fail)

    def test_retry_counts_attempts(self):
        calls = []
        def flaky(i):
            calls.append(i)
            if i < 3:
                raise RuntimeError
            return "ok"
        self.assertEqual((retry(flaky), calls), ("ok", [1, 2, 3]))

    def test_backoff_grows(self):
        self.assertLess(backoff(2), backoff(3))

    def test_backoff_never_negative(self):
        self.assertGreater(backoff(1), 0)

    def test_backoff_caps_at_30s(self):
        self.assertEqual(backoff(10), 30.0)

    def test_backoff_base(self):
        self.assertGreaterEqual(backoff(1, base=2.0), 2.0)


if __name__ == "__main__":
    unittest.main()
"""

REPOS = {
    "demo-glasses": {
        "README.md": (
            "# demo-glasses\n\nA tiny static site for notes you read on smart glasses.\n"
            "`python tools/render.py` renders `notes/*.md` into `build/`.\n"
        ),
        "TODO.md": "# TODO\n\n- [ ] README threat model\n- [ ] record demo, rm -r build step\n- [ ] tune row height\n- [ ] pair the phone\n",
        "tools/render.py": RENDER,
        ".gitignore": "build/\n__pycache__/\n",
        **{f"notes/{name}.md": text for name, text in NOTES.items()},
    },
    "demo-hub": {
        "README.md": "# demo-hub\n\nA small job hub: a queue, retries with backoff, and a test suite that takes about 20 seconds.\n"
        "Run it with `python -m unittest discover -s tests`.\n",
        "TODO.md": "# TODO\n\n- [ ] wire Codex hooks\n- [ ] retry on 429\n- [ ] docs for the hub API\n",
        "hub/__init__.py": "",
        "hub/queue.py": QUEUE,
        "hub/retry.py": RETRY,
        "tests/__init__.py": "",
        "tests/test_hub.py": TESTS,
        ".gitignore": "__pycache__/\n",
    },
}
# Claude Code may run these without asking; anything else (push, rm, writes) asks.
# On Windows Claude Code may use its PowerShell tool for the same command, so both forms are allowed.
# Rules use Claude Code 2.1's `cmd *` form (the form it suggests itself).
ALLOW = {
    "demo-glasses": [f"{t}(python tools/render.py{s})" for t in ("Bash", "PowerShell") for s in ("", " *")]
    + ["Read", "Glob", "Grep"],
    "demo-hub": [f"{t}(python -m unittest{s})" for t in ("Bash", "PowerShell") for s in ("", " *")]
    + ["Read", "Glob", "Grep"],
}


def git(*args: str, cwd: Path | None = None) -> str:
    out = subprocess.run(["git", *GIT_ID, *args], cwd=cwd, capture_output=True, text=True)
    if out.returncode:
        sys.exit(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout.strip()


def rmtree(path: Path) -> None:
    def unlock(func, name, _):
        os.chmod(name, stat.S_IWRITE)
        func(name)

    if path.exists():
        shutil.rmtree(path, onexc=unlock)


def env() -> dict:
    return {**os.environ, "APPROVAL_GATE_HOME": str(STATE)}


def build_repos() -> None:
    for name, files in REPOS.items():
        repo, origin = DEMO / name, DEMO / f"{name}-origin.git"
        git("init", "-q", "--bare", "-b", "main", str(origin))
        git("init", "-q", "-b", "main", str(repo))
        for rel, text in files.items():
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        git("add", "-A", cwd=repo)
        git("commit", "-qm", f"Start {name}", cwd=repo)
        git("remote", "add", "origin", origin.as_posix(), cwd=repo)
        git("push", "-q", "-u", "origin", "main", cwd=repo)
        if name == "demo-glasses":  # one commit waits to be pushed: the demo's approval sends it
            (repo / "notes" / "release.md").write_text(
                "# Release\n\nRows fit the line. Prompts cut through.\n", encoding="utf-8"
            )
            git("add", "-A", cwd=repo)
            git("commit", "-qm", "Add the release note", cwd=repo)
        (repo / MARKER).touch()
        (repo / ".git" / "info" / "exclude").write_text(f".claude/\n.codex/\n{MARKER}\n")


def install_hooks() -> None:
    """The gate and the deck's telemetry, in each repo's own settings. Never in ~/.claude or ~/.codex."""
    install = [sys.executable, str(ROOT / "scripts" / "install_hook.py"), "--deck"]
    for name in REPOS:
        repo = DEMO / name
        settings = repo / ".claude" / "settings.json"
        subprocess.run([*install, "--settings", str(settings)], env=env(), check=True, capture_output=True)
        data = json.loads(settings.read_text(encoding="utf-8"))
        data["permissions"] = {"allow": ALLOW[name]}
        settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        subprocess.run(
            [*install, "--codex", "--settings", str(repo / ".codex" / "hooks.json")],
            env=env(),
            check=True,
            capture_output=True,
        )


def write_config() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    config = {
        "projects": [
            {"id": name, "name": name, "root": (DEMO / name).as_posix(), "todos": ["TODO.md"]} for name in REPOS
        ],
        "unmapped": "hide",
    }
    (STATE / "deck.projects.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    import relay as R

    R.save_devices(
        STATE, {"dev_demo01": {"label": "demo simulator", "sha256": R.sha256(D.token()), "created_at": R.now_iso()}}
    )


def render_build() -> None:
    subprocess.run(
        [sys.executable, "tools/render.py", "--fast"], cwd=DEMO / "demo-glasses", check=True, capture_output=True
    )


def fresh_relay() -> None:
    D.kill("relay")
    time.sleep(0.4)
    for name in ("deck.log", "deck.log.1", "focus.json", "armed", "gate.log"):
        (STATE / name).unlink(missing_ok=True)
    D.start_relay()
    subprocess.run(
        [sys.executable, str(ROOT / "relay" / "gatectl.py"), "arm"], env=env(), check=True, capture_output=True
    )


def stop_agents() -> None:
    """Agents demo_run.py started and left behind (it records their pids)."""
    agents = STATE / "agents.json"
    for pid in json.loads(agents.read_text()) if agents.exists() else []:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    agents.unlink(missing_ok=True)


def free_ports() -> None:
    """The demo uses the standard ports; the devstack must be down."""
    subprocess.run([sys.executable, str(ROOT / "scripts" / "devstack.py"), "down"], capture_output=True)


def stage() -> None:
    if DEMO.exists() and not all((DEMO / name / MARKER).exists() for name in REPOS):
        sys.exit(f"{DEMO} exists and isn't a staged demo; remove it by hand")
    free_ports()
    if not DEMO.exists():
        build_repos()
        install_hooks()
    write_config()
    render_build()
    fresh_relay()
    D.start_vite()
    if not D.wait_for("http://127.0.0.1:5180/"):
        sys.exit("vite did not start; see C:/deck-sandbox/demo/state/vite.log")
    D.start_sim()
    print("Staged. Relay 8797 (ingest 8798), /present on http://127.0.0.1:8799/present, simulator 9898. Gate armed.")
    print("Next: python scripts/demo_run.py   (rehearse)   or   python scripts/demo_run.py --record")


def reset() -> None:
    started = time.monotonic()
    if not all((DEMO / name / MARKER).exists() for name in REPOS):
        sys.exit("Nothing staged yet: run python scripts/demo_stage.py first")
    stop_agents()
    D.kill("simulator", gentle=True)
    for name in REPOS:
        repo, origin = DEMO / name, DEMO / f"{name}-origin.git"
        first = git("rev-list", "--max-parents=0", "HEAD", cwd=repo)
        tip = (
            git("rev-list", "-1", "--grep=^Add the release note$", "--all", cwd=repo)
            if name == "demo-glasses"
            else first
        )
        git("reset", "-q", "--hard", tip or first, cwd=repo)
        git("clean", "-qfdx", "-e", ".claude", "-e", ".codex", "-e", MARKER, cwd=repo)
        git("update-ref", "refs/heads/main", first, cwd=origin)
        git("fetch", "-q", "origin", cwd=repo)
    install_hooks()  # idempotent; picks up any change to the hooks or ALLOW
    render_build()
    fresh_relay()
    if not D.wait_for("http://127.0.0.1:5180/", 2):
        D.start_vite()
        D.wait_for("http://127.0.0.1:5180/")
    D.start_sim()
    print(f"Reset in {time.monotonic() - started:.1f} s.")


def teardown() -> None:
    stop_agents()
    if STATE.exists():
        for name in ("simulator", "vite", "relay"):
            D.kill(name)
    time.sleep(0.5)
    rmtree(DEMO)
    print(f"Removed {DEMO}.")


def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--reset":
        reset()
    elif arg == "--teardown":
        teardown()
    elif arg == "":
        stage()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
