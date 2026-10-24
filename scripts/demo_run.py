#!/usr/bin/env python3
"""Run the Command Deck demo live, and record and cut it. Windows.

    python scripts/demo_stage.py --reset && python scripts/demo_run.py            # rehearse: drive and check, no recording
    python scripts/demo_stage.py --reset && python scripts/demo_run.py --record   # the take, cut into demo/
    python scripts/demo_run.py --cut                                              # re-cut the last take (after editing demo/captions.srt)

Everything on screen is live: two Claude Code sessions (one per repo, kept open with streaming
input) and a Codex run, real hooks, the real relay, plugin and simulator, real files. The glasses
pane is the simulator's own framebuffer (its automation API); the right-hand panes are headless
Chrome screencasts of /present, of the TODO.md file, and of a terminal pane that prints each agent's
own output stream. Nothing is captured from your screen, so you can keep working while it records.

The take records continuously while the driver notes a mark at each moment that matters. The cut
then keeps the beat windows around those marks (BEATS) and burns in one caption per beat from
demo/captions.srt. Deliverables land in H:/glasses/demo/.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = Path("C:/deck-sandbox/demo")
STATE = DEMO / "state"
os.environ["APPROVAL_GATE_HOME"] = str(STATE)  # before sim reads it
sys.path.insert(0, str(ROOT / "scripts"))
import sim  # noqa: E402

OUT = ROOT / "demo"
TAKE = DEMO / "take"  # raw frames on C:: writes to the exFAT H: drive stalled for seconds and froze every pane
PANES = ROOT / "scripts" / "demo_panes"
PANE_PORT = 8790
MODEL = "sonnet"
GLASSES, HUB = DEMO / "demo-glasses", DEMO / "demo-hub"
FPS = 30
FONT = "C\\:/Windows/Fonts/segoeui.ttf"
FONT_BOLD = "C\\:/Windows/Fonts/seguisb.ttf"

# The beat sheet. Each beat is one or more windows of the raw take, (start mark, offset, end mark,
# offset), shown in order under one caption, over a right-hand pane.
BEATS = [
    ("deck", "Three AI agents. Two projects. One glance.", [("deck", 0.0, "deck", 6.0)], "present"),
    ("running", "What's running, where, for how long.", [("b2", 0.0, "run-detail", 6.5)], "present"),
    ("failure", "Failures surface on their own.", [("b3", -0.5, "error-open", 3.5)], "present"),
    ("projects", "Every project is its own deck.", [("b4", 0.0, "sessions", 2.0)], "present"),
    (
        "focus",
        "In focus, nothing else can interrupt.",
        [("b5", 0.0, "focus-on", 2.5), ("hub-asks", -1.0, "outside", 3.0)],
        "present",
    ),
    ("approve", "Prompts cut through any screen. One tap.", [("push-prompt", -1.5, "push-restored", 4.5)], "present"),
    ("deny", "Two taps to say no.", [("rm-prompt", -1.5, "rm-reply", 3.0)], "present"),
    ("todo", "Real files. Real changes.", [("b8", 0.0, "todo-done", 3.0)], "todo"),
    ("summary", "A work session, run from the glasses.", [("b9", 0.0, "summary", 5.5)], "present"),
    ("closing", "Command Deck · built on Even Hub · open source", [], "card"),
]
CLOSING_SECONDS = 8.0
STILLS = [  # (file, beat, mark, offset)
    ("01-deck.png", "deck", "deck", 3.0),
    ("02-running.png", "running", "run-detail", 4.0),
    ("03-approval-interrupt.png", "approve", "push-prompt", 1.0),
    ("04-focus-fallback.png", "focus", "outside", 1.5),
    ("05-todo-done.png", "todo", "todo-done", 1.0),
    ("06-focus-summary.png", "summary", "summary", 2.0),
]


# ---- the cut (pure, tested by scripts/test_demo_run.py) ------------------------------------------


def plan(marks: dict[str, float], beats=BEATS) -> list[dict]:
    """Beat windows in raw-take seconds, and where each lands in the output."""
    out, at = [], 0.0
    for key, caption, windows, pane in beats:
        segs = []
        for start_mark, start_off, end_mark, end_off in windows:
            start, end = marks[start_mark] + start_off, marks[end_mark] + end_off
            if end <= start:
                raise ValueError(f"{key}: window {start_mark}..{end_mark} is empty")
            segs.append((round(start, 3), round(end, 3)))
        length = sum(e - s for s, e in segs) if segs else CLOSING_SECONDS
        out.append(
            {
                "key": key,
                "caption": caption,
                "pane": pane,
                "segments": segs,
                "out_start": round(at, 3),
                "out_end": round(at + length, 3),
            }
        )
        at += length
    return out


def out_time(cut: list[dict], beat: str, raw: float) -> float:
    """Where a raw-take moment shows up in the output."""
    b = next(b for b in cut if b["key"] == beat)
    at = b["out_start"]
    for s, e in b["segments"]:
        if s <= raw <= e:
            return round(at + raw - s, 3)
        at += e - s
    raise ValueError(f"{raw} is not inside beat {beat}")


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def to_srt(cut: list[dict]) -> str:
    return "\n".join(
        f"{i}\n{srt_time(b['out_start'])} --> {srt_time(b['out_end'])}\n{b['caption']}\n" for i, b in enumerate(cut, 1)
    )


def from_srt(text: str) -> list[str]:
    """The caption of each beat, in order. Times come from the plan; wording comes from the file."""
    blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    return [" ".join(b.strip().splitlines()[2:]).strip() for b in blocks]


# ---- the terminal pane -----------------------------------------------------------------------------

HOME_RE = re.compile(re.escape(str(Path.home())).replace(r"\\", r"[\\/]"), re.I)


class Terminal:
    """One transcript per agent; the pane shows one at a time."""

    def __init__(self):
        self.lines: dict[str, deque] = {}
        self.titles: dict[str, str] = {}
        self.current = ""
        self.lock = threading.Lock()

    def open(self, name: str, title: str) -> None:
        with self.lock:
            self.lines.setdefault(name, deque(maxlen=200))
            self.titles[name] = title

    def add(self, name: str, kind: str, text: str) -> None:
        text = HOME_RE.sub("~", text)
        with self.lock:
            for line in text.splitlines() or [""]:
                if self.lines[name] and self.lines[name][-1]["t"] == line:
                    continue  # Codex prints its last message twice
                self.lines[name].append({"k": kind, "t": line})

    def show(self, name: str) -> None:
        self.current = name

    def state(self) -> dict:
        with self.lock:
            return {"title": self.titles.get(self.current, ""), "lines": list(self.lines.get(self.current, []))[-18:]}


def brief(tool: str, args: dict) -> str:
    if tool in ("Bash", "PowerShell"):
        return str(args.get("command", ""))
    path = args.get("file_path") or args.get("path") or args.get("pattern")
    return Path(path).name if isinstance(path, str) and tool in ("Write", "Edit", "Read") else str(path or "")


def demo_env() -> dict:
    return {**os.environ, "APPROVAL_GATE_HOME": str(STATE)}


class Claude:
    """A Claude Code session kept open with streaming input, so prompts go out on cue. Permission
    prompts the hooks don't settle come here (--permission-prompt-tool stdio), as they would to a
    terminal: the session waits until this side answers."""

    def __init__(self, name: str, cwd: Path, term: Terminal, marks: dict, allow: list[str] | None = None):
        self.name, self.term, self.marks = name, term, marks
        self.asks: list[dict] = []
        self.results = 0
        term.open(name, f"claude · {cwd.name}")
        cmd = [
            "claude",
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-prompt-tool",
            "stdio",
            "--setting-sources",
            "project,local",
            "--model",
            MODEL,
        ]
        if allow:  # what the session may run without asking (project settings' rules didn't apply here)
            cmd += ["--allowedTools", *allow]
        self.proc = subprocess.Popen(
            cmd, cwd=cwd, env=demo_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        record_pid(self.proc.pid)
        threading.Thread(target=self.read, daemon=True).start()

    def send(self, text: str) -> None:
        self.term.add(self.name, "user", f"> {text}")
        self.write({"type": "user", "message": {"role": "user", "content": text}})

    def answer(self, behavior: str, message: str = "") -> None:
        for ask in self.asks:
            body = (
                {"behavior": behavior, "message": message}
                if behavior == "deny"
                else {"behavior": "allow", "updatedInput": ask["request"]["input"]}
            )
            self.write(
                {
                    "type": "control_response",
                    "response": {"subtype": "success", "request_id": ask["request_id"], "response": body},
                }
            )
            self.term.add(
                self.name,
                "dim" if behavior == "deny" else "good",
                f"  {'Denied' if behavior == 'deny' else 'Allowed'} at the terminal.",
            )
        self.asks.clear()

    def write(self, msg: dict) -> None:
        try:
            self.proc.stdin.write((json.dumps(msg) + "\n").encode())
            self.proc.stdin.flush()
        except OSError:
            pass

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(20)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()

    def read(self) -> None:
        for raw in self.proc.stdout:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            kind = msg.get("type")
            if kind == "control_request" and msg.get("request", {}).get("subtype") == "can_use_tool":
                req = msg["request"]
                self.asks.append(msg)
                self.term.add(
                    self.name,
                    "ask",
                    f"Allow {req.get('tool_name')}({brief(req.get('tool_name', ''), req.get('input', {}))})? "
                    "Waiting for you, here in the terminal.",
                )
                self.marks.setdefault(f"{self.name}-asks", time.time())
                # Claude Code asks its terminal while the PermissionRequest hook runs; the first answer
                # wins. Only these asks are in the beat sheet: the glasses answer the first two, and the
                # out-of-focus write waits here.
                what = json.dumps(req.get("input", {}))
                if not any(x in what for x in ("git push origin main", "rm -r build", "notes.md")):
                    UNEXPECTED[self.name] = f"{req.get('tool_name')} {what[:120]}"
            elif kind == "control_cancel_request":
                # The hook answered first (the glasses): the terminal's question is withdrawn.
                self.asks = [a for a in self.asks if a["request_id"] != msg.get("request_id")]
                self.term.add(self.name, "dim", "  Answered on the glasses.")
            elif kind == "assistant":
                for part in msg["message"].get("content", []):
                    if part.get("type") == "text" and part.get("text", "").strip():
                        self.term.add(self.name, "text", f"● {part['text'].strip()}")
                    elif part.get("type") == "tool_use":
                        self.term.add(
                            self.name, "tool", f"● {part['name']}({brief(part['name'], part.get('input', {}))})"
                        )
            elif kind == "user":
                for part in msg["message"].get("content", []):
                    if isinstance(part, dict) and part.get("type") == "tool_result":
                        content = part.get("content")
                        text = (
                            content
                            if isinstance(content, str)
                            else " ".join(c.get("text", "") for c in content or [] if isinstance(c, dict))
                        )
                        lines = [l for l in text.splitlines() if l.strip()][:4] or ["(no output)"]
                        cls = "bad" if part.get("is_error") else "result"
                        for i, line in enumerate(lines):
                            self.term.add(self.name, cls, f"  {'⎿' if i == 0 else ' '}  {line[:160]}")
            elif kind == "result":
                self.results += 1
                self.marks[f"{self.name}-result-{self.results}"] = time.time()


class Codex:
    """`codex exec`, its output printed as it arrives."""

    SKIP = re.compile(
        r"^(hook: |\d{4}-\d\d-\d\dT|warning: |OpenAI Codex|-{4,}|workdir:|model:|provider:|approval:|sandbox:|reasoning|session id|tokens used|[\d,]+$)"
    )

    def __init__(self, cwd: Path, prompt: str, term: Terminal, marks: dict):
        self.term, self.marks = term, marks
        term.open("codex", f"codex · {cwd.name}")
        term.add("codex", "user", f"> {prompt}")
        trust = f'projects."{str(cwd).lower()}".trust_level="trusted"'.replace("\\", "\\\\")
        # -s danger-full-access: Codex's Windows sandbox can't start while the Codex desktop app holds
        # one of its runtime files (docs/plan-deltas.md A7). The prompt only runs the test suite.
        cmd = [
            *sim.codex_cmd(),
            "exec",
            "--dangerously-bypass-hook-trust",
            "-c",
            trust,
            "-c",
            "model_reasoning_effort=low",
            "-s",
            "danger-full-access",
            "-C",
            str(cwd),
            prompt,
        ]
        self.proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=demo_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        record_pid(self.proc.pid)
        threading.Thread(target=self.read, daemon=True).start()

    def read(self) -> None:
        mode, started = "header", False
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            if not started:
                started = line.strip() == "user"
                continue
            if self.SKIP.match(line.strip()):
                continue
            if line.strip() in ("codex", "exec", "user", "thinking"):
                mode = line.strip()
                continue
            if mode == "user":
                continue
            if mode == "exec":
                m = re.search(r"-Command '(.*)' in ", line) or re.search(r"^(.*) in [A-Z]:\\", line)
                if m:
                    self.term.add("codex", "tool", f"● Bash({m[1]})")
                    continue
                if re.match(r"^\s*(succeeded|exited)", line):
                    self.term.add("codex", "good" if "succeeded" in line else "bad", f"  ⎿  {line.strip()}")
                    mode = "output"
                    continue
            if mode == "output":
                if line.strip():
                    self.term.add(
                        "codex", "bad" if re.search(r"FAIL|Error|Traceback", line) else "result", f"     {line[:160]}"
                    )
                continue
            if mode == "codex" and line.strip():
                self.term.add("codex", "text", f"● {line.strip()}")


AGENT_PIDS: list[int] = []


def record_pid(pid: int) -> None:
    AGENT_PIDS.append(pid)
    (STATE / "agents.json").write_text(json.dumps(AGENT_PIDS))


# ---- panes and capture ------------------------------------------------------------------------------


def pane_server(term: Terminal) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, body: bytes, ctype: str):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/terminal", "/todo", "/card"):
                return self.send((PANES / f"{path[1:]}.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/state.json":
                return self.send(json.dumps(term.state()).encode(), "application/json")
            if path == "/todo.json":
                lines = (GLASSES / "TODO.md").read_text(encoding="utf-8").splitlines()
                return self.send(
                    json.dumps({"name": "demo-glasses/TODO.md", "lines": lines}).encode(), "application/json"
                )
            self.send_response(404)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", PANE_PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class Capture:
    """The simulator's framebuffer and three Chrome screencasts, each frame named by its epoch ms."""

    def __init__(self, take: Path = TAKE):
        self.take = take
        if take.exists():
            shutil.rmtree(take)
        (take / "glasses").mkdir(parents=True)
        self.stopfile = take / "stop"
        self.stop = threading.Event()
        self.late: list[tuple[float, float]] = []  # (from, to): the framebuffer went unread, and changed meanwhile
        cdp = ["node", str(ROOT / "scripts" / "cdp.mjs"), "record"]
        self.casts = [
            subprocess.Popen(
                [*cdp, url, str(take / name), w, h, "1", "900", str(self.stopfile)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for name, url, w, h in (
                ("terminal", f"http://127.0.0.1:{PANE_PORT}/terminal", "768", "540"),
                ("present", "http://127.0.0.1:8799/present", "1536", "1080"),
                ("todo", f"http://127.0.0.1:{PANE_PORT}/todo", "768", "540"),
            )
        ]
        self.thread = threading.Thread(target=self.glasses, daemon=True)
        self.thread.start()
        time.sleep(4)  # the screencasts' first frames

    def glasses(self) -> None:
        last, fetched = b"", time.time()
        while not self.stop.is_set():
            try:
                png = urllib.request.urlopen(f"{sim.BASE}/api/screenshot/glasses", timeout=2).read()
                now, since = time.time(), fetched
                fetched = now
                if png != last:
                    if now - since > 0.5:  # this change reaches the video up to that much late
                        self.late.append((since, now))
                    (self.take / "glasses" / f"{int(now * 1000)}.png").write_bytes(png)
                    last = png
            except OSError:
                pass
            time.sleep(1 / 25)

    def finish(self) -> None:
        self.stop.set()
        self.stopfile.touch()
        for p in self.casts:
            p.wait(30)
        self.thread.join(5)
        subprocess.run(
            [
                "node",
                str(ROOT / "scripts" / "cdp.mjs"),
                "shot",
                f"http://127.0.0.1:{PANE_PORT}/card",
                str(self.take / "card.png"),
                "1920",
                "1080",
                "1500",
            ],
            capture_output=True,
            timeout=60,
        )


# ---- the drive ----------------------------------------------------------------------------------------

UNEXPECTED: dict = {}  # a session that asks in its terminal when nothing in the beat sheet should


def wait_for(check, seconds: float, what: str, run: sim.Run):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if UNEXPECTED:
            run.failures.append(f"unexpected terminal prompt: {UNEXPECTED}")
            raise RuntimeError(f"unexpected terminal prompt: {UNEXPECTED}")
        got = check()
        if got:
            return got
        run.entries()  # keep reading the console so errors are caught
        time.sleep(0.25)
    run.failures.append(f"timed out waiting for {what}")
    return None


def counts() -> tuple:
    d = sim.deck_get("/deck?scope=focus")
    return d["sessions"], d["running"]


def drive(record: bool) -> dict:
    marks: dict[str, float] = {}
    mark = lambda name: marks.__setitem__(name, time.time())
    term = Terminal()
    server = pane_server(term)
    run = sim.Run("demo")
    sim.at_root(run)
    capture = Capture() if record else None
    initial_build = len(list((GLASSES / "build").iterdir()))
    mark("start")
    hub = Claude("hub", HUB, term, marks)
    glasses = Claude(
        "glasses",
        GLASSES,
        term,
        marks,
        allow=[f"{t}(python tools/render.py{x})" for t in ("Bash", "PowerShell") for x in ("", ":*")],
    )
    hub.send("Reply with the single word: ready.")
    glasses.send(
        "Use the Bash tool to run python tools/render.py once. It takes about half a minute. Then reply with the single word: rendered."
    )
    codex = Codex(
        HUB,
        "Run the test suite right away, before anything else: python -m unittest discover -s tests. "
        "Don't change any files and don't try to fix anything. Then reply with one short sentence.",
        term,
        marks,
    )
    term.show("codex")
    summary = None
    try:
        # B1: three sessions, two running (the render and the suite).
        wait_for(lambda: counts() == (3, 2), 90, "3 sessions and 2 running", run)
        mark("deck")
        time.sleep(6.3)
        # B2: Running, then the suite's own row, with ELAPSED ticking.
        mark("b2")
        run.goto("running")
        run.do("click", r"page running:focus: depth 2", pause=1.4)
        test_run = wait_for(
            lambda: next(
                (
                    r["id"]
                    for r in sim.deck_get("/runs?scope=focus&active=1")["items"]
                    if r["agent"] == "codex" and "unittest" in r["target"]
                ),
                None,
            ),
            20,
            "the suite's run",
            run,
        )
        run.goto(test_run or "")
        run.do("click", r"page run:", pause=0)
        mark("run-detail")
        time.sleep(6.5)
        # B3: the suite fails; the spotlight pins to the Deck; open it.
        wait_for(
            lambda: any(e["agent"] == "codex" for e in sim.deck_get("/deck?scope=focus")["errors"]),
            60,
            "the suite's failure",
            run,
        )
        time.sleep(1.2)
        mark("b3")
        run.do("double", r"page running:focus: depth 2", pause=1.0)
        run.do("double", r"page deck:focus: depth 1", pause=0)
        err = run.expect(r"\[deck\] spotlight err_\w+ tests failed", 6)
        time.sleep(1.2)
        run.goto(f"error:{err.split()[2]}" if err else "error")
        run.do("click", r"page error:", pause=0)
        mark("error-open")
        time.sleep(3.7)
        # B4: Projects › demo-glasses › Sessions, and /present follows.
        term.show("glasses")
        mark("b4")
        run.do("double", r"page deck:focus: depth 1", pause=1.2)
        run.goto("projects")
        run.do("click", r"page projects:all: depth 2", pause=1.5)
        run.goto("demo-glasses")
        run.do("click", r"page project:demo-glasses:demo-glasses depth 3", pause=1.5)
        run.goto("sessions")
        run.do("click", r"page sessions:demo-glasses: depth 4", pause=0)
        mark("sessions")
        time.sleep(2.2)
        # B5: focus on demo-glasses; a write from demo-hub goes to its own terminal at once.
        mark("b5")
        run.do("double", r"page project:demo-glasses:demo-glasses depth 3", pause=1.0)
        run.do("double", r"page projects:all: depth 2", pause=1.2)
        run.goto("demo-glasses")
        run.menu(0, r"menu 30 on projects:all: row 0")
        run.expect(r"\[deck\] focus on demo-glasses")
        mark("focus-on")
        time.sleep(2.6)
        term.show("hub")
        hub.send(
            "Create a file notes.md containing the line: retry budget is 3. Use the Write tool, one call. If it isn't allowed, don't retry."
        )
        wait_for(lambda: marks.get("hub-asks"), 60, "the hub session's terminal prompt", run)
        time.sleep(0.8)
        run.do("double", r"page deck:focus: depth 1", pause=0)
        wait_for(
            lambda: (sim.deck_get("/deck?scope=focus")["outside_focus"] or {}).get("waiting"),
            10,
            "outside focus: 1 waiting",
            run,
        )
        time.sleep(0.6)
        mark("outside")
        time.sleep(3.2)
        # B6: an approval cuts in at any depth; one tap; back to the same row.
        term.show("glasses")
        run.goto("sessions")
        run.do("click", r"page sessions:focus: depth 2", pause=1.0)
        glasses.send("Run exactly one Bash command: git push origin main. Then reply in one short sentence.")
        run.expect(r"\[deck\] prompt req_\w+ over sessions:focus:", 60)
        mark("push-prompt")
        time.sleep(1.6)
        run.do("click", r"rebuild result:APPROVED", pause=0)
        run.expect(r"restored sessions:focus:")
        mark("push-restored")
        wait_for(lambda: marks.get("glasses-result-2"), 40, "Claude continuing after the push", run)
        time.sleep(3.2)
        # B7: rm -r build; two taps; denied, and build/ is still there.
        glasses.send(
            "Run exactly one Bash command: rm -r build. If it is denied, don't retry; reply in one short sentence."
        )
        run.expect(r"\[deck\] prompt req_\w+ over sessions:focus:", 60)
        mark("rm-prompt")
        time.sleep(1.6)
        run.do("double", r"rebuild result:DENIED", pause=0)
        mark("denied")
        wait_for(lambda: marks.get("glasses-result-3"), 40, "Claude's reply to the deny", run)
        intact = len(list((GLASSES / "build").iterdir()))
        term.add("glasses", "good" if intact == initial_build else "bad", f"  build/ still has {intact} files.")
        mark("rm-reply")
        time.sleep(3.2)
        # B8: To-dos › README threat model; tap, tap; the line flips in TODO.md.
        mark("b8")
        run.do("double", r"page deck:focus: depth 1", pause=1.2)
        run.goto("todos")
        run.do("click", r"page todos:focus: depth 2", pause=1.5)
        todo = next(t for t in sim.deck_get("/todos?scope=demo-glasses")["items"] if t["text"] == "README threat model")
        run.goto(todo["id"])
        run.do("click", rf"page todo:demo-glasses:{todo['id']} depth 3", pause=1.6)
        run.do("click", rf"\[deck\] todo {todo['id']} done", pause=0)
        mark("todo-done")
        time.sleep(3.2)
        # B9: end focus from the Deck's menu: the summary.
        mark("b9")
        run.expect(r"page todos:focus: depth 2", 4)
        run.do("double", r"page deck:focus: depth 1", pause=1.2)
        run.menu(2, r"menu 3 on deck:focus:")
        summary = run.expect(r"\[deck\] focus summary", 8)
        mark("summary")
        time.sleep(6.0)
    except RuntimeError as err:
        print(f"Stopped: {err}")
    finally:
        TAKE.mkdir(parents=True, exist_ok=True)
        for name in term.lines:  # for reading after a failed rehearsal
            (TAKE / f"transcript-{name}.txt").write_text("\n".join(l["t"] for l in term.lines[name]), encoding="utf-8")
        hub.answer("deny", "Not now; the demo is over.")
        for session in (hub, glasses):
            session.close()
        if codex.proc.poll() is None:
            codex.proc.kill()
        if capture:
            capture.finish()
        server.shutdown()
        sim.gate("disarm")
    checks = {
        "every step happened, no console errors": not run.failures,
        "the push reached origin": _pushed(),
        "build/ intact after the deny": len(list((GLASSES / "build").iterdir())) == initial_build,
        "TODO.md: README threat model is [x]": "- [x] README threat model"
        in (GLASSES / "TODO.md").read_text(encoding="utf-8"),
        "the out-of-focus write never happened": not (HUB / "notes.md").exists(),
        "summary: 1 approved, 1 denied, 1 to-do": bool(summary)
        and json.loads(summary.split(" ", 3)[3]) | {"minutes": 0, "runs": 0}
        == {"minutes": 0, "approved": 1, "denied": 1, "runs": 0, "todos": 1},
    }
    late = []
    if capture:  # a stalled capture shows a change late: past a second, captions and stills can miss their moment
        start, end = marks["start"], marks.get("summary", time.time()) + 6.0
        late = [(round(a - start, 2), round(b - start, 2)) for a, b in capture.late if b > start and a < end]
        worst = max((b - a for a, b in late), default=0.0)
        checks[f"every glasses change captured within 1 s (worst {worst:.1f} s)"] = worst <= 1.0
    start_ms = int(marks["start"] * 1000)
    marks = {k: round(v - marks["start"], 3) for k, v in marks.items()}
    return {
        "marks": marks,
        "start_ms": start_ms,
        "checks": checks,
        "failures": run.failures,
        "seconds": marks.get("summary", 0),
        "late": late,
    }


def _pushed() -> bool:
    """The stage leaves one commit unpushed; after the approved push, origin has it."""
    head = lambda repo: subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "main"], capture_output=True, text=True
    ).stdout.strip()
    return bool(head(GLASSES)) and head(DEMO / "demo-glasses-origin.git") == head(GLASSES)


# ---- compose ---------------------------------------------------------------------------------------------


def ffmpeg() -> str:
    found = shutil.which("ffmpeg") or str(
        Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe"
    )
    if not Path(found).exists():
        sys.exit("ffmpeg isn't installed (winget install Gyan.FFmpeg.Essentials)")
    return found


def ff(*args: str) -> None:
    out = subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True)
    if out.returncode:
        sys.exit(f"ffmpeg failed: {out.stderr[-2000:]}")


def frames_to_video(folder: Path, t0_ms: int, t1_ms: int, out: Path, size: str, flags: str = "lanczos") -> None:
    """A constant-30-fps video of a folder of <epoch-ms> frames, from t0 to t1."""
    frames = sorted((int(p.stem), p) for p in folder.iterdir() if p.stem.isdigit())
    if not frames:
        raise SystemExit(f"no frames in {folder}")
    before = [f for f in frames if f[0] <= t0_ms]
    seq = ([(t0_ms, before[-1][1])] if before else [(t0_ms, frames[0][1])]) + [
        f for f in frames if t0_ms < f[0] < t1_ms
    ]
    lines = ["ffconcat version 1.0"]
    for (t, path), nxt in zip(seq, seq[1:] + [(t1_ms, None)]):
        lines += [f"file '{path.as_posix()}'", f"duration {(nxt[0] - t) / 1000:.3f}"]
    lines.append(f"file '{seq[-1][1].as_posix()}'")
    listing = out.with_suffix(".txt")
    listing.write_text("\n".join(lines))
    if folder.name == "glasses":
        # The simulator's framebuffer is RGBA: dark pixels are green with alpha 0, and brightness is
        # alpha. Laid over black it looks like the display; with the alpha dropped it is a green slab.
        w, h = 576, 288
        ff(
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-filter_complex",
            f"color=c=black:s={w}x{h}:r={FPS}[bg];[0:v]fps={FPS},format=rgba[fg];[bg][fg]overlay=shortest=1,"
            f"scale={size}:flags={flags},format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "14",
            "-r",
            str(FPS),
            str(out),
        )
        return
    ff(
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(listing),
        "-vf",
        f"fps={FPS},scale={size}:flags={flags},format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "14",
        "-r",
        str(FPS),
        str(out),
    )


def compose(work: Path, pane: str, s: float, e: float, caption: Path, out: Path) -> None:
    """One window of the take, s to e seconds in: the glasses on the left, the terminal over `pane`
    on the right, and the caption burned in. Reads the pane videos frames_to_video left in `work`."""
    span = ["-ss", f"{s:.3f}", "-t", f"{e - s:.3f}"]
    label = "Even Hub simulator · G2 display"
    ff(
        *span,
        "-i",
        str(work / "glasses.mp4"),
        *span,
        "-i",
        str(work / "terminal.mp4"),
        *span,
        "-i",
        str(work / f"{pane}.mp4"),
        "-filter_complex",
        f"color=c=0x070b08:s=1920x1080:r={FPS}:d={e - s:.3f}[bg];"
        "[bg][0:v]overlay=0:252[a];[a][1:v]overlay=1152:0[b];[b][2:v]overlay=1152:540[c];"
        "[c]drawbox=x=1150:y=0:w=2:h=1080:color=0x22362a:t=fill,drawbox=x=1152:y=539:w=768:h=2:color=0x22362a:t=fill,"
        f"drawtext=fontfile='{FONT}':text='{label}':fontsize=30:fontcolor=0x9bb8a3:x=(1152-text_w)/2:y=190,"
        f"drawtext=fontfile='{FONT_BOLD}':textfile='{caption.as_posix().replace(':', chr(92) + ':')}':fontsize=46:fontcolor=white:"
        "box=1:boxcolor=black@0.7:boxborderw=22:x=(w-text_w)/2:y=h-170[v]",
        "-map",
        "[v]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "16",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(FPS),
        str(out),
    )


def cut(take: dict) -> None:
    marks = take["marks"]
    t0 = int(take["start_ms"])
    end = t0 + int((max(marks.values()) + 2) * 1000)
    work = TAKE / "work"
    work.mkdir(exist_ok=True)
    sources = {
        "glasses": ("glasses", "1152:576", "neighbor"),
        "terminal": ("terminal", "768:540", "lanczos"),
        "present": ("present", "768:540", "lanczos"),
        "todo": ("todo", "768:540", "lanczos"),
    }
    for name, (folder, size, flags) in sources.items():
        frames_to_video(TAKE / folder, t0, end, work / f"{name}.mp4", size, flags)
    plan_ = plan(marks)
    # Wording comes from demo/captions.srt when it exists (edit it, then --cut); times always come
    # from this take's marks, so the file is rewritten to match the video.
    srt = OUT / "captions.srt"
    wording = from_srt(srt.read_text(encoding="utf-8")) if srt.exists() else []
    if len(wording) == len(plan_):
        for beat, text in zip(plan_, wording):
            beat["caption"] = text
    srt.write_text(to_srt(plan_), encoding="utf-8")
    captions = [b["caption"] for b in plan_]
    pieces = []
    for i, beat in enumerate(plan_):
        caption = work / f"caption-{i}.txt"
        caption.write_text(captions[i] if i < len(captions) else beat["caption"], encoding="utf-8")
        if beat["pane"] == "card":
            out = work / f"beat-{i:02d}.mp4"
            ff(
                "-loop",
                "1",
                "-t",
                str(CLOSING_SECONDS),
                "-i",
                str(TAKE / "card.png"),
                "-vf",
                f"scale=1920:1080,fps={FPS},format=yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "14",
                "-r",
                str(FPS),
                str(out),
            )
            pieces.append(out)
            continue
        for j, (s, e) in enumerate(beat["segments"]):
            out = work / f"beat-{i:02d}-{j}.mp4"
            compose(work, beat["pane"], s, e, caption, out)
            pieces.append(out)
    listing = work / "pieces.txt"
    listing.write_text("\n".join(f"file '{p.as_posix()}'" for p in pieces))
    final = OUT / "command-deck-demo.mp4"
    ff(
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(listing),
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(final),
    )
    for name, beat, mark_name, offset in STILLS:
        t = out_time(plan_, beat, marks[mark_name] + offset)
        ff("-ss", f"{t:.3f}", "-i", str(final), "-frames:v", "1", str(OUT / name))
    approve = next(b for b in plan_ if b["key"] == "approve")
    start = approve["out_start"]
    length = min(8.0, approve["out_end"] - start)
    gif = OUT / "deck-loop.gif"
    for width, fps in ((960, 12), (800, 10), (640, 10)):
        ff(
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{length:.3f}",
            "-i",
            str(final),
            "-filter_complex",
            f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer:bayer_scale=4",
            str(gif),
        )
        if gif.stat().st_size <= 5 * 1024 * 1024:
            break
    total = plan_[-1]["out_end"]
    print(
        f"Cut {final} ({total:.1f} s), {len(STILLS)} stills, {gif.name} ({gif.stat().st_size / 1e6:.2f} MB, {length:.1f} s)"
    )
    for b in plan_:
        print(f"  {b['out_start']:6.1f}-{b['out_end']:6.1f}  {b['key']:<9} {b['caption']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run, record and cut the Command Deck demo.")
    parser.add_argument("--record", action="store_true", help="record the take and cut it into demo/")
    parser.add_argument("--cut", action="store_true", help="re-cut the last take (C:/deck-sandbox/demo/take)")
    args = parser.parse_args()
    if args.cut:
        take = json.loads((TAKE / "take.json").read_text())
        return cut(take)
    OUT.mkdir(exist_ok=True)
    started = time.time()
    result = drive(record=args.record)
    print(f"\nDrive took {time.time() - started:.0f} s; beats end at {result['seconds']:.0f} s into the take.")
    for name, ok in result["checks"].items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    for failure in result["failures"]:
        print(f"    - {failure}")
    for a, b in result.get("late", []):
        print(f"    - a glasses change captured {b - a:.1f} s late, at {a:.1f}-{b:.1f} s into the take")
    try:
        cut_plan = plan({k: v for k, v in result["marks"].items()})
        total = cut_plan[-1]["out_end"]
        print(f"  {'PASS' if total <= 120 else 'FAIL'}  the cut would run {total:.1f} s (target 94, cap 120)")
        for b in cut_plan:
            print(f"        {b['key']:<9} {b['out_end'] - b['out_start']:5.1f} s")
    except (KeyError, ValueError) as err:
        print(f"  FAIL  no cut plan: {err}")
        total = None
    ok = all(result["checks"].values()) and total is not None and total <= 120
    if args.record:
        (TAKE / "take.json").write_text(json.dumps(result, indent=2))
        if ok:
            cut(result)
        else:
            print("Not cut: the take failed a check. Reset and record again.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
