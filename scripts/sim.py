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
    python scripts/sim.py run running      # Phase C: live agents; Running matches the relay; spotlight
    python scripts/sim.py run tick         # Phase C: Run detail's ELAPSED ticks at 1 Hz without a rebuild
    python scripts/sim.py run since        # Phase C: the since-you-looked screen after a close and reopen

`run` needs the devstack (scripts/devstack.py up && seed && sim) and fails on any console error.
scripts/demo.py imports this module and uses the same calls.
"""
import json
import os
import urllib.error
import re
import shutil
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
        self.expected: list[str] = []  # console errors a run provokes on purpose (the simulator logs every non-2xx fetch)
        self.seen = max((e["id"] for e in console()), default=0)
        self.reported: set[int] = set()
        self.rows: tuple[str, int, list[str]] | None = None  # the plugin's latest `[deck] rows` line
        self.rows_id = 0
        SHOTS.mkdir(parents=True, exist_ok=True)

    def entries(self) -> list[dict]:
        fresh = [e for e in console() if e["id"] > self.seen]
        for e in fresh:
            if e["level"] == "error" or e["message"].startswith(("[uncaught]", "[unhandledrejection]")):
                if not any(re.search(p, e["message"]) for p in self.expected) and e["id"] not in self.reported:
                    self.reported.add(e["id"])
                    self.failures.append(f"console error: {e['message'][:160]}")
            if e["message"].startswith("[deck] rows ") and e["id"] > self.rows_id:
                _, _, page, cursor, keys = e["message"].split(" ", 4)
                self.rows, self.rows_id = (page, int(cursor), keys.split(",")), e["id"]
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

    def goto(self, key: str) -> None:
        """Move the cursor straight to the row with this key: the plugin's `[deck] rows` line says
        where every row is, and every swipe logs exactly one `[deck] cursor` line."""
        deadline = time.time() + 4
        while time.time() < deadline:
            self.entries()
            if self.rows and key in self.rows[2]:
                break
            time.sleep(0.15)
        else:
            return self.search(key)
        _, cursor, keys = self.rows
        here = keys[cursor] if cursor < len(keys) else None
        for _ in range(abs(keys.index(key) - cursor)):
            self.seen = max((e["id"] for e in console()), default=self.seen)
            send("down" if keys.index(key) > cursor else "up")
            found = self.expect(r"\[deck\] cursor ", 3)
            here = found.split(" ", 2)[2] if found else None
            time.sleep(0.12)
        if here != key:
            self.search(key)
        time.sleep(0.3)

    def search(self, key: str) -> None:
        """The slow way: to the top, then down until the key comes up."""
        def swipe(direction: str) -> str | None:
            self.seen = max((e["id"] for e in console()), default=self.seen)
            send(direction)
            found = self.expect(r"\[deck\] cursor ", 3)
            return found.split(" ", 2)[2] if found else None
        for _ in range(12):
            send("up")
            time.sleep(0.15)
        time.sleep(0.8)
        here = swipe("up")  # at the top already: this names row 0
        while here is not None and here != key:
            after = swipe("down")
            if after == here:  # the bottom, without finding it
                break
            here = after
        if here != key:
            self.failures.append(f"no row {key!r}")
        time.sleep(0.4)

    def menu(self, index: int, expect: str) -> None:
        """Pick the long-press menu's item at `index`. The simulator's menu opens on its first item and
        wraps at the ends."""
        self.seen = max((e["id"] for e in console()), default=self.seen)
        send("menu")
        if not self.expect(r'EvenHub event: \{"jsonData":\{"eventType":4\}', 6):
            return
        time.sleep(0.6)
        for _ in range(index):
            send("down")
            time.sleep(0.4)
        send("click")
        self.expect(expect, 5)

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
    subprocess.run([sys.executable, str(ROOT / "scripts" / "devstack.py"), "sim"], check=True, capture_output=True,
                   env={**os.environ, "DECK_STATE": str(STATE)})  # the simulator of whichever stack this run drives
    run.seen = 0
    run.expect(r"\[deck\] rebuild deck:focus:", 15)
    time.sleep(STEP + 1)
    if any("since you looked" in e["message"] for e in console()):
        run.do("double", r"page deck:focus: depth 1")  # dismiss the since-you-looked screen


def run_walk() -> None:
    run = Run("walk")
    at_root(run)
    run.shot("0-deck")
    run.goto("projects")
    run.do("click", r"page projects:all: depth 2")
    run.shot("1-projects")
    run.do("click", r"page project:glasses:glasses depth 3")
    run.shot("2-glasses")
    run.goto("sessions")
    run.do("click", r"page sessions:glasses: depth 4")
    run.shot("3-sessions")
    run.do("double", r"page project:glasses:glasses depth 3 cursor 1")
    run.do("double", r"page projects:all: depth 2 cursor 0")
    run.do("double", r"page deck:focus: depth 1 cursor \d")
    run.shot("4-back-at-deck")
    run.finish()


def run_interrupt() -> None:
    run = Run("interrupt")
    at_root(run)
    run.goto("projects")
    run.do("click", r"page projects:all: depth 2")
    run.do("click", r"page project:glasses:glasses depth 3")
    run.goto("running")
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


def deck_get(path: str):
    req = urllib.request.Request(f"http://127.0.0.1:8797{path}", headers={"Authorization": f"Bearer {(STATE / 'dev-token.txt').read_text().strip()}"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.load(resp)


def agent(cmd: list[str], cwd: str) -> subprocess.Popen:
    """A real Claude Code or Codex session in a devstack project, with the dev state dir."""
    log = open(STATE / f"agent-{Path(cwd).name}-{int(time.time() * 1000) % 100000}.log", "w", encoding="utf-8")
    return subprocess.Popen(cmd, cwd=cwd, env={**os.environ, "APPROVAL_GATE_HOME": str(STATE)}, stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL)


def claude(prompt: str, cwd: str) -> subprocess.Popen:
    return agent(["claude", "-p", prompt, "--setting-sources", "project,local", "--model", "sonnet", "--allowedTools", "Bash(python:*)"], cwd)


def codex_cmd() -> list[str]:
    """Codex's npm shim is a .cmd file; run its script with node so the prompt's quotes survive."""
    shim = shutil.which("codex")
    script = Path(shim).parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js" if shim else None
    return ["node", str(script)] if script and script.exists() else ["codex"]


def codex(prompt: str, cwd: str) -> subprocess.Popen:
    # -s danger-full-access: Codex's Windows sandbox can't start while the Codex desktop app holds one
    # of its runtime files (docs/plan-deltas.md A7). The prompt is fixed and the repo is throwaway.
    trust = f'projects."{cwd.lower().replace("/", chr(92) + chr(92))}".trust_level="trusted"'
    return agent([*codex_cmd(), "exec", "--dangerously-bypass-hook-trust", "-c", trust, "-c", "model_reasoning_effort=low",
                  "-s", "danger-full-access", "-C", cwd, prompt], cwd)


def run_running() -> None:
    """Phase C: two Claude Code sessions and one Codex session, live. The Running tab must match
    GET /runs?active=1 within 3 s, rows may change only when a run starts or ends or a minute rolls
    over, and a failing command must pin the spotlight row."""
    run = Run("running")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "devstack.py"), "reset"], check=True, capture_output=True,
                   env={**os.environ, "DECK_STATE": str(STATE)})
    at_root(run)
    sleep = 'python -c "import time; time.sleep({})"'
    agents = [
        claude(f"Run exactly one Bash command and nothing else: {sleep.format(70)} . Then reply with one word: done.", "C:/deck-sandbox/glasses"),
        claude(f"Run these two Bash commands one after the other and nothing else: first {sleep.format(55)} , then "
               "python -c \"import sys; sys.exit(1)\" (it fails on purpose; don't retry). Then reply with one word: done.", "C:/deck-sandbox/hub"),
        codex(f"Run exactly one shell command and nothing else: {sleep.format(65)} . Then reply with one word: done.", "C:/deck-sandbox/hub"),
    ]
    run.goto("running")
    run.do("click", r"page running:focus: depth 2", pause=0)
    run.expect(r"rebuild running:focus:")  # entering the page is its one rebuild
    relay_sets, plugin_sets, upgrades, rebuilds = [], [], [], []
    started = time.time()
    while time.time() - started < 100 and any(a.poll() is None for a in agents):
        ids = frozenset(r["id"] for r in deck_get("/runs?scope=focus&active=1&limit=50")["items"])
        if not relay_sets or relay_sets[-1][1] != ids:
            relay_sets.append((time.time(), ids))
        for e in run.entries():
            run.seen = e["id"]
            m = e["message"]
            if m.startswith("[deck] running "):
                shown = frozenset(x for x in m.split(" ", 2)[2].split(",") if x != "-")
                if not plugin_sets or plugin_sets[-1][1] != shown:
                    plugin_sets.append((time.time(), shown))
            elif m.startswith("[deck] upgrade running:") and re.search(r"\br\d", m):
                upgrades.append((time.time(), m))
            elif m.startswith("[deck] rebuild"):
                rebuilds.append(m)
        time.sleep(0.25)
    run.shot("0-running")
    # Every relay state that lasted 3 s showed up on the glasses within 3 s.
    for i, (t0, ids) in enumerate(relay_sets):
        lasted = (relay_sets[i + 1][0] if i + 1 < len(relay_sets) else time.time()) - t0
        if lasted >= 3 and not any(ids == shown and t0 - 0.5 <= t <= t0 + 3 for t, shown in plugin_sets):
            if not any(ids == shown and t <= t0 and all(s2 == ids for t2, s2 in plugin_sets if t <= t2 <= t0 + 3) for t, shown in plugin_sets):
                run.failures.append(f"relay runs {sorted(ids)} at +{t0 - started:.1f}s not on the glasses within 3 s")
    print(f"  ok    {len(relay_sets)} relay states, {len(plugin_sets)} glasses states, all matched within 3 s" if not run.failures else "  FAIL  mismatch")
    if rebuilds:
        run.failures.append(f"the Running page rebuilt while open: {rebuilds}")
    # Rows change only on start, end or a minute rolling over: at most one batch per state change
    # plus one per minute per run.
    budget = len(relay_sets) + 3 * 2 + 2
    print(f"  {'ok  ' if len(upgrades) <= budget else 'FAIL'}  {len(upgrades)} row updates in {time.time() - started:.0f}s (budget {budget}), 0 rebuilds")
    if len(upgrades) > budget:
        run.failures.append(f"{len(upgrades)} row updates > {budget}: {upgrades}")
    for a in agents:
        a.wait(120)
    run.do("double", r"page deck:focus: depth 1")
    run.expect(r"\[deck\] spotlight err_\w+ exit 1", 8)
    time.sleep(0.8)
    run.shot("1-spotlight")
    run.finish()


def run_since() -> None:
    """Phase C: what happened while the plugin was closed shows as one screen when it opens."""
    run = Run("since")
    at_root(run)
    time.sleep(12)  # the plugin saves its place in the event log every 10 s
    devstack = [sys.executable, str(ROOT / "scripts" / "devstack.py")]
    subprocess.run([*devstack, "nosim"], check=True, capture_output=True)
    time.sleep(11)  # past the relay's 10 s presence window: nobody is watching now
    subprocess.run([*devstack, "seed"], check=True, capture_output=True)  # runs finish, one fails
    gate("arm")
    try:
        inject_approval("git push origin main")
    except urllib.error.HTTPError as err:
        print(f"  ok    gate request refused while nobody watched ({err.code})")
    finally:
        gate("disarm")
    subprocess.run([*devstack, "sim"], check=True, capture_output=True)
    run.seen = 0
    run.expect(r"\[deck\] since you looked \d+ events", 15)
    run.expect(r"rebuild since:all:")
    time.sleep(STEP)
    run.shot("0-since")
    run.do("double", r"page deck:focus: depth 1")
    run.finish()


def run_tick() -> None:
    """Phase C: Run detail's ELAPSED ticks once a second by textContainerUpgrade, never a rebuild."""
    run = Run("tick")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "devstack.py"), "seed"], check=True, capture_output=True)
    at_root(run)
    run.goto("running")
    run.do("click", r"page running:focus: depth 2", pause=1.5)
    run.do("click", r"page run:", pause=0)
    run.expect(r"rebuild run:")
    time.sleep(0.5)
    run.seen = max(e["id"] for e in console())
    time.sleep(5.2)
    ticks = [e["message"] for e in run.entries() if re.match(r"\[deck\] upgrade run:\S+ .*headline", e["message"])]
    rebuilds = [e["message"] for e in console() if e["id"] > run.seen and "rebuild" in e["message"]]
    print(f"  {'ok  ' if 4 <= len(ticks) <= 6 and not rebuilds else 'FAIL'}  {len(ticks)} ELAPSED upgrades in 5.2 s, {len(rebuilds)} rebuilds")
    if not 4 <= len(ticks) <= 6 or rebuilds:
        run.failures.append(f"ELAPSED ticked {len(ticks)} times in 5.2 s with {len(rebuilds)} rebuilds")
    run.shot("0-run")
    run.finish()


def gate_hook(cwd: str, command: str = "git push origin main") -> subprocess.Popen:
    """The real gate hook, as Claude Code runs it, on a PermissionRequest from a session in `cwd`."""
    event = {"session_id": "focus-check", "hook_event_name": "PermissionRequest", "cwd": cwd, "tool_name": "Bash",
             "tool_input": {"command": command}, "permission_mode": "default"}
    proc = subprocess.Popen([sys.executable, "-I", str(ROOT / "hook" / "approval_gate_hook.py")], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, env={**os.environ, "APPROVAL_GATE_HOME": str(STATE)})
    proc.stdin.write(json.dumps(event).encode())
    proc.stdin.close()
    return proc


def ingest(agent: str, event: dict) -> None:
    req = urllib.request.Request(f"http://127.0.0.1:8798/ingest?agent={agent}", data=json.dumps(event).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {(STATE / 'ingest.key').read_text().strip()}"})
    urllib.request.urlopen(req, timeout=5).read()


def run_focus() -> None:
    """Phase E: with focus on glasses, a git push from hub falls back to the terminal in under 0.5 s
    while a glasses prompt still reaches the simulator; the summary's counts match the event log."""
    run = Run("focus")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "devstack.py"), "reset"], check=True, capture_output=True,
                   env={**os.environ, "DECK_STATE": str(STATE)})
    at_root(run)
    run.goto("projects")
    run.do("click", r"page projects:all: depth 2", pause=1.6)
    run.goto("glasses")
    run.menu(0, r"menu 30 on projects:all: row 0")  # Focus on this is the project menu's first item
    if not run.expect(r"\[deck\] focus on glasses"):
        run.finish()
    time.sleep(2.5)
    run.shot("0-focus-on")
    gate("arm")
    try:
        started = time.monotonic()
        hub = gate_hook("C:\\deck-sandbox\\hub")
        try:
            out = hub.communicate(timeout=10)[0]
        except subprocess.TimeoutExpired:
            hub.kill()
            out = b"<still waiting after 10 s>"
        took = time.monotonic() - started
        ok = out == b"" and took < 0.5
        print(f"  {'ok  ' if ok else 'FAIL'}  hub push went to the terminal in {took:.2f} s (hook printed {out!r})")
        if not ok:
            run.failures.append(f"hub push: {took:.2f} s, output {out!r}")
        # A hub session waiting in its terminal shows only as a count on the Deck.
        for name in ("SessionStart", "UserPromptSubmit", "PermissionRequest"):
            ingest("claude-code", {"hook_event_name": name, "session_id": "hub-out", "cwd": "C:\\deck-sandbox\\hub", "tool_name": "Write",
                                   "tool_input": {"file_path": "C:\\deck-sandbox\\hub\\notes.md"}})
        glasses = gate_hook("C:\\deck-sandbox\\glasses")
        run.expect(r"\[deck\] prompt req_\w+ over projects:all:")
        time.sleep(STEP)
        run.shot("1-glasses-prompt")
        run.do("click", r"rebuild result:APPROVED", pause=0)
        out = glasses.communicate(timeout=10)[0]
        allowed = b'"behavior": "allow"' in out
        print(f"  {'ok  ' if allowed else 'FAIL'}  glasses push reached the simulator and the hook printed allow")
        if not allowed:
            run.failures.append(f"glasses hook output {out!r}")
        run.expect(r"restored projects:all:")
        time.sleep(1.5)
        run.do("double", r"page deck:focus: depth 1", pause=2.5)
        run.shot("2-outside-focus")
        note = deck_get("/deck?scope=focus")["outside_focus"]
        print(f"  {'ok  ' if note and note['waiting'] >= 1 else 'FAIL'}  Deck: outside focus {note}")
        if not note or note["waiting"] < 1:
            run.failures.append(f"outside_focus {note}")
        run.goto("focus")
        run.do("click", r"page focus:all: depth 2", pause=1.6)
        run.goto("end")
        run.do("click", r"\[deck\] focus summary", pause=0.5)
        run.shot("3-summary")
    finally:
        gate("disarm")
    shown = json.loads(next(e["message"] for e in reversed(console()) if e["message"].startswith("[deck] focus summary")).split(" ", 3)[3])
    events = deck_get("/events?since=0")["events"]
    start = next(e for e in reversed(events) if e["type"] == "focus_start")
    end = next(e for e in reversed(events) if e["type"] == "focus_end")
    inside = [e for e in events if start["id"] < e["id"] < end["id"] and e.get("project") in start["projects"]]
    counted = {"approved": sum(e["type"] == "approval" and e.get("status") == "approved" for e in inside),
               "denied": sum(e["type"] == "approval" and e.get("status") == "denied" for e in inside),
               "runs": sum(e["type"] == "run" and e.get("status") in ("ok", "failed") for e in inside),
               "todos": sum(e["type"] == "todo" and e.get("status") == "done" for e in inside)}
    match = all(shown[k] == v for k, v in counted.items()) and end["summary"]["approved"] == shown["approved"] == 1
    print(f"  {'ok  ' if match else 'FAIL'}  summary {shown} matches the event log {counted}")
    if not match:
        run.failures.append(f"summary {shown} vs log {counted} / relay {end['summary']}")
    run.finish()


TILES = ("[...document.querySelectorAll('#deck-view .tile')].map(t => [t.querySelector('.label').textContent,"
         " t.querySelector('.value').textContent])")


def run_desktop() -> None:
    """Phase F: the desktop page and the glasses show the same counts within 3 s across a scripted
    run; Open on phone routes the phone page; /present follows the glasses."""
    run = Run("desktop")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "devstack.py"), "reset"], check=True, capture_output=True,
                   env={**os.environ, "DECK_STATE": str(STATE)})
    at_root(run)
    samples = STATE / "desktop-samples.jsonl"
    sampler = subprocess.Popen(["node", str(ROOT / "scripts" / "cdp.mjs"), "sample", "http://127.0.0.1:8799/", TILES, str(samples), "62"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(4)
    rec = "C:\\deck-sandbox\\glasses"
    steps = [  # each changes at least one count on the Deck
        ("claude-code", {"hook_event_name": "SessionStart", "session_id": "dsk-1", "cwd": rec}),
        ("claude-code", {"hook_event_name": "PreToolUse", "session_id": "dsk-1", "cwd": rec, "tool_name": "Bash", "tool_use_id": "toolu_dsk_1",
                         "tool_input": {"command": "npm test"}}),
        ("claude-code", {"hook_event_name": "PreToolUse", "session_id": "dsk-1", "cwd": rec, "tool_name": "Bash", "tool_use_id": "toolu_dsk_2",
                         "tool_input": {"command": "git push origin main"}}),
        ("claude-code", {"hook_event_name": "PermissionRequest", "session_id": "dsk-1", "cwd": rec, "tool_name": "Bash",
                         "tool_input": {"command": "git push origin main"}}),
        ("codex", {"hook_event_name": "SessionStart", "session_id": "dsk-2", "cwd": "C:\\deck-sandbox\\hub", "model": "gpt"}),
        ("claude-code", {"hook_event_name": "TaskCreated", "session_id": "dsk-1", "cwd": rec, "task_id": "9", "task_subject": "write the docs"}),
        ("claude-code", {"hook_event_name": "PostToolUse", "session_id": "dsk-1", "cwd": rec, "tool_name": "Bash", "tool_use_id": "toolu_dsk_2"}),
        ("claude-code", {"hook_event_name": "PostToolUse", "session_id": "dsk-1", "cwd": rec, "tool_name": "Bash", "tool_use_id": "toolu_dsk_1"}),
        ("codex", {"hook_event_name": "SessionEnd", "session_id": "dsk-2", "cwd": "C:\\deck-sandbox\\hub"}),
    ]
    relay_states, glasses_states = [], []
    started = time.time()
    for agent_name, event in steps:
        ingest(agent_name, event)
        until = time.time() + 5.5
        while time.time() < until:
            d = deck_get("/deck?scope=focus")
            state = (d["needs_you"], d["sessions"], d["running"], d["projects"], d["todos_open"])
            if not relay_states or relay_states[-1][1] != state:
                relay_states.append((time.time(), state))
            for e in run.entries():
                run.seen = e["id"]
                if e["message"].startswith("[deck] counts "):
                    c = json.loads(e["message"].split(" ", 2)[2])
                    glasses_states.append((time.time(), (c["needs"], c["sessions"], c["running"], c["projects"], c["todos"])))
            time.sleep(0.25)
    sampler.wait(30)
    names = ("Needs you", "Sessions", "Running", "Projects", "To-dos")
    desktop_states = []
    for line in samples.read_text(encoding="utf-8").splitlines():
        sample = json.loads(line)
        tiles = dict(sample["v"] or [])
        if all(n in tiles for n in names):
            state = tuple(int(tiles[n]) for n in names)
            if not desktop_states or desktop_states[-1][1] != state:
                desktop_states.append((sample["t"] / 1000, state))

    def shown_within(states, t0, value) -> bool:
        before = [s for t, s in states if t <= t0]
        return bool(before and before[-1] == value) or any(s == value and t0 <= t <= t0 + 3 for t, s in states)

    for t0, value in relay_states:
        for name, states in (("glasses", glasses_states), ("desktop", desktop_states)):
            if not shown_within(states, t0, value):
                run.failures.append(f"{name} never showed {value} within 3 s of +{t0 - started:.1f}s")
    print(f"  {'ok  ' if not run.failures else 'FAIL'}  {len(relay_states)} relay states; glasses and desktop matched each within 3 s")

    # Open on phone: a glasses menu routes the phone page, which shares the plugin's WebView.
    run.goto("sessions")
    run.do("click", r"page sessions:focus: depth 2", pause=1.6)
    run.menu(0, r"menu 10 on sessions:focus: row 0")
    run.expect(r'\[deck\] phone route \{"view":"session"')
    time.sleep(1.5)
    urllib.request.urlretrieve("http://127.0.0.1:9898/api/screenshot/webview", str(SHOTS / "desktop-phone-route.png"))
    # /present follows the glasses: it now shows the Sessions page.
    out = subprocess.run(["node", str(ROOT / "scripts" / "cdp.mjs"), "eval", "http://127.0.0.1:8799/present",
                          "document.querySelector('#deck-view h1')?.textContent", "3500"], capture_output=True, text=True, timeout=60)
    heading = json.loads(out.stdout or "null")
    print(f"  {'ok  ' if heading == 'Sessions' else 'FAIL'}  /present follows the glasses: {heading!r}")
    if heading != "Sessions":
        run.failures.append(f"/present showed {heading!r}")
    run.finish()


TODO_COPY = "# TODO\n\n- [ ] README threat model\n- [ ] record demo, rm -r build step\n- [x] ship v1\n"


def run_todo() -> None:
    """Phase D: tap, tap marks a to-do done, flipping exactly one line of a test copy of TODO.md.
    If the line is edited first, the relay answers 409 and the file keeps only that edit."""
    run = Run("todo")
    todo_file = Path("C:/deck-sandbox/glasses/TODO.md")
    todo_file.write_bytes(TODO_COPY.encode())
    target = next(t for t in deck_get("/todos?scope=glasses")["items"] if t["text"] == "README threat model")
    at_root(run)
    run.goto("todos")
    run.do("click", r"page todos:focus: depth 2", pause=1.6)
    run.goto(target["id"])
    run.do("click", rf"page todo:glasses:{target['id']} depth 3", pause=1.6)
    run.shot("0-todo")
    run.do("click", rf"\[deck\] todo {target['id']} done", pause=0.3)
    run.shot("1-done")
    after = todo_file.read_bytes().decode()
    flipped = TODO_COPY.replace("- [ ] README threat model", "- [x] README threat model")
    print(f"  {'ok  ' if after == flipped else 'FAIL'}  TODO.md: exactly one line flipped to [x]")
    if after != flipped:
        run.failures.append(f"TODO.md after done: {after!r}")
    run.expect(r"page todos:focus: depth 2", 4)  # DONE shows, then the list comes back

    todo_file.write_bytes(TODO_COPY.encode())  # the same line again, so the same id
    time.sleep(2.5)
    run.goto(target["id"])
    run.do("click", rf"page todo:glasses:{target['id']} depth 3", pause=1.6)
    run.expected.append(rf"^\[fetch\] 409 Conflict http://127\.0\.0\.1:8797/todos/{target['id']}/done$")
    edited = TODO_COPY.replace("README threat model", "README threat model (edited)")
    todo_file.write_bytes(edited.encode())  # someone edits the line before the second tap
    run.do("click", rf"\[deck\] todo {target['id']} changed", pause=0.8)
    run.shot("2-changed")
    unchanged = todo_file.read_bytes().decode() == edited
    print(f"  {'ok  ' if unchanged else 'FAIL'}  409: TODO.md has only the outside edit")
    if not unchanged:
        run.failures.append("TODO.md changed after a 409")
    todo_file.write_bytes(TODO_COPY.encode())
    run.finish()


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in (*ACTIONS, "shot", "console", "run"):
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd == "run":
        scenarios = {"walk": run_walk, "interrupt": run_interrupt, "running": run_running, "since": run_since, "tick": run_tick, "todo": run_todo, "focus": run_focus, "desktop": run_desktop}
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
