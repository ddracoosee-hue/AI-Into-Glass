"""Command Deck registry: sessions, runs, to-dos, cards and focus, built from hook events.

The plugin never derives state on its own; it reads what this builds. Every string that leaves
here has been scrubbed: absolute paths become project-relative (or just a file name), the home
directory never appears, secret-shaped strings become [secret]. Prompts, assistant messages and
tool output are read only to be dropped. Stdlib only. Contract: contract.md, "Contract v2".
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import unicodedata
from collections import OrderedDict, deque
from datetime import datetime
from pathlib import Path

from projects import OTHER, Placement, Projects, norm

AGENTS = {"claude-code": "cc", "codex": "cdx"}
STATES = ("idle", "working", "waiting", "quiet", "ended")
QUIET_AFTER = 15 * 60  # a killed terminal sends no SessionEnd
ENDED_KEEP = 10 * 60
MAX_SESSIONS, MAX_RUNS = 200, 2000
TARGET_MAX = 64
TITLE_MAX = 64
EVENTS_KEPT = 1000
LOG_ROTATE = 10 * 1024 * 1024
LINK_WINDOW = 30  # seconds between a session's PermissionRequest and the gate request it belongs to
FOCUS_MINUTES = (25, 50, None)
MAX_FOCUS = 2
CARD_TTL, CARD_TTL_MAX = 1800, 86400
CARD_LEVELS = ("info", "warn", "error")
SUBAGENT_TOOLS = ("Agent", "Task", "spawn_agent")
SAFE_ID = re.compile(r"[\w.:-]{1,100}")
VIEWS = ("deck", "needs", "sessions", "running", "projects", "project", "todos", "focus", "session", "run", "todo", "commits", "card", "error")

_P = r"""[^\s'"`;|&<>(),]"""
PATH_TOKEN = re.compile(
    rf"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]{_P}*"
    rf"|(?<![\w\\])\\\\[^\\/\s'\"]+[\\/]{_P}*"
    rf"|(?<![\w/\\.:-])/[A-Za-z]/{_P}*"
    rf"|(?<![\w/\\.:-])/(?:home|Users|tmp|var|mnt|opt|etc|usr|private|root|srv|Volumes)/{_P}*"
    rf"|(?<![\w/\\.~-])~[\\/]{_P}*"
    rf"|(?:\$HOME|\$\{{HOME\}}|\$env:USERPROFILE|%USERPROFILE%)(?:[\\/]{_P}*)?", re.I)
HOME_TOKEN = re.compile(r"^(?:~|\$HOME|\$\{HOME\}|\$env:USERPROFILE|%USERPROFILE%)", re.I)
SECRETS = [  # the same shapes the gate hook redacts
    (r"\bsk-(ant-)?[A-Za-z0-9_-]{16,}", "[secret]"),
    (r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}|AKIA[0-9A-Z]{16}|xox[abpr]-[A-Za-z0-9-]{10,}|AIza[\w-]{30,})", "[secret]"),
    (r"\b(agt_|dik_|eyJ)[\w-]{10,}(\.[\w-]{10,}\.[\w-]+)?", "[secret]"),
    (r"(?i)\b(\w*(token|secret|passw(or)?d|api_?key|auth)\w*)\s*=\s*('[^']*'|\"[^\"]*\"|\S+)", r"\1=[secret]"),
    (r"(?i)(authorization:\s*(bearer|basic|token)\s+)[^\s'\"]+", r"\1[secret]"),
    (r"(?i)(--?(password|passwd|token|api-key|secret)[= ])\S+", r"\1[secret]"),
    (r"(?i)(://[^/\s:@]+:)[^@\s/]+@", r"\1[secret]@"),
]
PATCH_FILE = re.compile(r"^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+?)\s*$", re.M)


def iso(t: float) -> str:
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def visible(text: str) -> str:
    """One line, no control, format or bidi characters, single spaces."""
    text = "".join("?" if unicodedata.category(c) in ("Cc", "Cf", "Co", "Cs") and c not in "\n\t" else c for c in text)
    return " ".join(" ; ".join(part.strip() for part in text.splitlines() if part.strip()).split())


def safe_id(value, prefix: str) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value if SAFE_ID.fullmatch(value) else f"{prefix}{hashlib.sha1(value.encode()).hexdigest()[:16]}"


def shell_command(value) -> str | None:
    """Codex may send argv. For `bash -lc <script>` the script is what runs."""
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value and all(isinstance(a, str) for a in value):
        return value[2] if len(value) == 3 and value[1] in ("-c", "-lc", "-Command", "/c") else " ".join(value)
    return None


class Scrubber:
    def __init__(self, projects: Projects, user_home: str):
        self.projects = projects
        self.home = norm(user_home)
        fwd = self.home
        spellings = {fwd, fwd.replace("/", "\\")}
        if re.match(r"^[A-Za-z]:/", fwd):
            spellings.add(f"/{fwd[0].lower()}{fwd[2:]}")
        self.home_re = re.compile("|".join(re.escape(s) for s in sorted(spellings, key=len, reverse=True)), re.I)

    def path(self, path: str, place: Placement) -> str:
        if m := HOME_TOKEN.match(path):
            path = self.home + "/" + path[m.end():].lstrip("\\/")
        return self.projects.display(norm(path), place.base, place.project)

    def text(self, text: str, place: Placement, limit: int = TARGET_MAX) -> str:
        """A command or label: paths made relative, secrets and the home directory gone, one line."""
        text = PATH_TOKEN.sub(lambda m: self.path(m[0], place), text)
        for pattern, repl in SECRETS:
            text = re.sub(pattern, repl, text)
        return clip(self.home_re.sub("~", visible(text)), limit)

    def target(self, tool: str, tool_input, place: Placement) -> str:
        """What a tool call acts on, as a noun: the command, the file, the pattern. Never contents."""
        if not isinstance(tool_input, dict):
            return ""
        if tool in ("Bash", "PowerShell", "exec_command", "shell"):
            command = shell_command(tool_input.get("command") or tool_input.get("cmd"))
            return self.text(command, place) if command else ""
        if tool == "apply_patch":
            patch = tool_input.get("command") if isinstance(tool_input.get("command"), str) else tool_input.get("patch")
            files = PATCH_FILE.findall(patch) if isinstance(patch, str) else []
            if not files:
                return ""
            first = self.path(files[0], place) if re.match(r"^([A-Za-z]:|/|\\\\|~)", files[0]) else norm(files[0])
            more = f" +{len(files) - 1} more" if len(files) > 1 else ""
            return clip(self.text(first, place, 200), TARGET_MAX - len(more)) + more
        for field in ("file_path", "notebook_path", "path"):
            if isinstance(tool_input.get(field), str) and tool not in ("Glob", "Grep"):
                return self.text(self.path(tool_input[field], place), place)
        if tool in ("Glob", "Grep") and isinstance(tool_input.get("pattern"), str):
            return self.text(tool_input["pattern"], place)
        if tool == "WebFetch" and isinstance(tool_input.get("url"), str):
            m = re.match(r"^\w+://([^/?#\s]+)([^?#\s]*)", tool_input["url"])
            return self.text((m[1].split("@")[-1] + m[2]) if m else "", place)
        if tool in SUBAGENT_TOOLS:
            return self.text(str(tool_input.get("subagent_type") or tool_input.get("agent_type") or "agent"), place)
        return ""


class Deck:
    """Thread-safe. The relay calls gate_* while holding its own lock; nothing here takes that lock."""

    def __init__(self, home: Path, projects: Projects, user_home: str | os.PathLike | None = None, clock=time.time):
        self.home, self.projects, self.now = home, projects, clock
        self.scrub = Scrubber(projects, str(user_home or Path.home()))
        self.cond = threading.Condition(threading.RLock())
        self.sessions: dict[str, dict] = {}
        self.sp: dict[str, dict] = {}  # session id -> private: place, state_t, perm_t
        self.runs: OrderedDict[str, dict] = OrderedDict()  # start order
        self.closed_ids: OrderedDict[str, None] = OrderedDict()  # a Post that beat its Pre
        self.tasks: dict[str, dict] = {}  # Claude Code task list entries, as to-dos
        self.cards: dict[str, dict] = {}
        self.errors: deque[dict] = deque(maxlen=10)
        self.gate: dict[str, dict] = {}  # live gate requests: id -> agent, project, tool, summary, session
        self.events: deque[dict] = deque(maxlen=EVENTS_KEPT)
        self.seq = 0
        self.armed: bool | None = None
        self.log_path = home / "deck.log"
        self.focus = self._load_focus()
        self.last_summary: dict | None = None
        self.ingest_errors = 0
        self._err_logged = 0.0
        self._todo_cache: dict[str, tuple[tuple, list]] = {}
        self._load_tail()

    # ---- event log ----------------------------------------------------------------

    def _load_tail(self) -> None:
        try:
            with open(self.log_path, "rb") as f:
                f.seek(max(0, f.seek(0, 2) - 512 * 1024))
                lines = f.read().decode("utf-8", "replace").splitlines()[1:]
        except OSError:
            return
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and isinstance(event.get("id"), int):
                self.events.append(event)
                self.seq = max(self.seq, event["id"])

    def emit(self, type_: str, persist: bool = True, **fields) -> dict:
        with self.cond:
            self.seq += 1
            event = {**fields, "id": self.seq, "ts": iso(self.now()), "type": type_}
            self.events.append(event)
            if persist:
                self._write(event)
            self.cond.notify_all()
            return event

    def view(self, route) -> dict | None:
        """The page the glasses show, so the presenter view can follow them. Display state only:
        kept in memory, never in deck.log, and it changes nothing an agent does."""
        if not isinstance(route, dict) or route.get("view") not in VIEWS:
            return None
        clean = {"view": route["view"]}
        for field in ("scope", "id"):
            if isinstance(route.get(field), str) and SAFE_ID.fullmatch(route[field].replace("(", "").replace(")", "")):
                clean[field] = route[field]
        with self.cond:
            last = next((e for e in reversed(self.events) if e["type"] == "view"), None)
            if last and last.get("route") == clean:
                return clean
            self.emit("view", persist=False, route=clean)
            return clean

    def _write(self, event: dict) -> None:
        try:
            if self.log_path.exists() and self.log_path.stat().st_size > LOG_ROTATE:
                os.replace(self.log_path, self.log_path.with_name("deck.log.1"))
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError:
            pass  # the log is a record, not a dependency

    def events_since(self, since: int, wait: float = 0, alive=lambda: True) -> tuple[list[dict], int, bool]:
        """Events after `since`, waiting up to `wait` seconds for the first one. `alive` is polled each second."""
        deadline = time.monotonic() + wait
        with self.cond:
            while True:
                reset = since > self.seq
                if reset:
                    since = 0
                fresh = [e for e in self.events if e["id"] > since]
                left = deadline - time.monotonic()
                if fresh or left <= 0 or not alive():
                    gap = bool(self.events) and since and self.events[0]["id"] > since + 1
                    return fresh, self.seq, reset or bool(gap)
                self.cond.wait(min(left, 1.0))

    def log_error(self, reason: str) -> None:
        """Ingest problems go to deck.log at most every 10 s, as a count. Never the payload."""
        with self.cond:
            self.ingest_errors += 1
            if self.now() - self._err_logged >= 10:
                self._err_logged = self.now()
                self._write({"ts": iso(self.now()), "type": "ingest_error", "reason": reason[:80], "count": self.ingest_errors})

    # ---- ingest -------------------------------------------------------------------

    def ingest(self, agent: str, event: dict) -> None:
        if event.get("type") == "card":
            return self.card(event)
        name = event.get("hook_event_name")
        sid = safe_id(event.get("session_id"), "s")
        if agent not in AGENTS or not isinstance(name, str) or not sid:
            return self.log_error(f"unusable event from {agent!r}")
        with self.cond:
            sess_id = f"{AGENTS[agent]}:{sid}"
            sess = self._session(sess_id, agent, event)
            t = self.now()
            sess["last_event_at"], self.sp[sess_id]["last_t"] = iso(t), t
            handler = getattr(self, f"_on_{name}", None)
            if handler:
                handler(sess, event, t)
            self._cap()

    def _session(self, sess_id: str, agent: str, event: dict) -> dict:
        sess = self.sessions.get(sess_id)
        cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else None
        if sess is None or (sess["state"] == "ended" and event.get("hook_event_name") == "SessionStart"):
            place = self.projects.place(cwd or "")
            t = self.now()
            sess = {"id": sess_id, "agent": agent, "title": "", "project": place.project, "folder": place.folder,
                    "tag": place.tag, "model": "", "state": "idle", "waiting_for": None, "started_at": iso(t),
                    "last_event_at": iso(t), "current": None,
                    "counts": {"tools": 0, "approved": 0, "denied": 0, "failed": 0}, "error": False, "muted": False}
            self.sessions[sess_id] = sess
            self.sp[sess_id] = {"place": place, "state_t": t, "perm_t": 0.0, "last_t": t, "cwd": cwd}
            self.emit("session", session=sess_id, agent=agent, project=place.project, state="idle", waiting_for=None)
        elif cwd and cwd != self.sp[sess_id]["cwd"] and event.get("hook_event_name") == "SessionStart":
            self._place(sess, cwd)
        return sess

    def _place(self, sess: dict, cwd: str) -> None:
        place = self.projects.place(cwd)
        self.sp[sess["id"]].update(place=place, cwd=cwd)
        sess.update(project=place.project, folder=place.folder, tag=place.tag)

    def _state(self, sess: dict, state: str, waiting_for: str | None = None) -> None:
        if sess["state"] == state and sess["waiting_for"] == waiting_for:
            return
        sess["state"], sess["waiting_for"] = state, waiting_for
        self.sp[sess["id"]]["state_t"] = self.now()
        self.emit("session", session=sess["id"], agent=sess["agent"], project=sess["project"], state=state, waiting_for=waiting_for)

    def _settle(self, sess: dict) -> None:
        """After a run ends: still waiting if another run of this session waits, else working."""
        if any(r["status"] == "waiting" and r["session"] == sess["id"] for r in self.runs.values()):
            return self._state(sess, "waiting", "approval")
        if sess["state"] in ("waiting", "quiet", "idle"):
            self._state(sess, "working")

    def _on_SessionStart(self, sess, event, t):
        if isinstance(event.get("session_title"), str):
            sess["title"] = self.scrub.text(event["session_title"], self.sp[sess["id"]]["place"], TITLE_MAX)
        if isinstance(event.get("model"), str):
            sess["model"] = clip(visible(event["model"]), 32)

    def _on_UserPromptSubmit(self, sess, event, t):
        if isinstance(event.get("session_title"), str):
            sess["title"] = self.scrub.text(event["session_title"], self.sp[sess["id"]]["place"], TITLE_MAX)
        if isinstance(event.get("model"), str) and not sess["model"]:
            sess["model"] = clip(visible(event["model"]), 32)
        self._state(sess, "working")

    def _on_PreToolUse(self, sess, event, t):
        tool = clip(visible(str(event.get("tool_name") or "tool")), 40)
        rid = safe_id(event.get("tool_use_id"), "r") or f"run_{self.seq + 1}"
        if rid in self.runs or rid in self.closed_ids:
            return
        target = self.scrub.target(tool, event.get("tool_input"), self.sp[sess["id"]]["place"])
        ctx = safe_id(event.get("agent_id"), "a")
        for run in list(self.runs.values()):
            # A tool that was approved would have finished before this thread asked for another, so a
            # permission still open here was answered in the terminal. A late Post corrects it.
            if run["session"] == sess["id"] and run["status"] == "waiting" and run.get("_ctx") == ctx:
                self._close(run, "ended", t)
                run["_inferred"] = True
        parent = self._subagent_run(sess, event.get("agent_id"))
        self._open(sess, rid, "tool", tool, target, t, parent)["_ctx"] = ctx
        if isinstance(event.get("model"), str) and not sess["model"]:
            sess["model"] = clip(visible(event["model"]), 32)
        self._settle(sess)

    def _open(self, sess, rid, kind, tool, target, t, parent=None) -> dict:
        run = {"id": rid, "session": sess["id"], "agent": sess["agent"], "project": sess["project"], "kind": kind,
               "tool": tool, "target": target, "started_at": iso(t), "ended_at": None, "status": "running",
               "parent": parent, "_t": t, "_end": None}
        self.runs[rid] = run
        sess["counts"]["tools"] += kind == "tool"
        sess["current"] = {"tool": tool, "target": target, "since": iso(t), "run": rid}
        self.emit("run", run=rid, session=sess["id"], agent=sess["agent"], project=sess["project"], kind=kind,
                  tool=tool, target=target, status="running")
        return run

    def _close(self, run: dict, status: str, t: float | None = None) -> None:
        if run["status"] not in ("running", "waiting"):
            return
        t = t if t is not None else self.now()
        run.update(status=status, ended_at=iso(t), _end=t)
        self.closed_ids[run["id"]] = None
        while len(self.closed_ids) > MAX_RUNS:
            self.closed_ids.popitem(last=False)
        self.emit("run", run=run["id"], session=run["session"], agent=run["agent"], project=run["project"],
                  kind=run["kind"], tool=run["tool"], target=run["target"], status=status, elapsed_s=int(t - run["_t"]))

    def _subagent_run(self, sess, agent_id) -> str | None:
        aid = safe_id(agent_id, "a")
        if not aid:
            return None
        for run in reversed(self.runs.values()):
            if run["session"] == sess["id"] and run.get("_agent") == aid:
                return run["id"]
        return None

    def _finish_tool(self, sess, event, status: str, t) -> dict | None:
        rid = safe_id(event.get("tool_use_id"), "r")
        run = self.runs.get(rid) if rid else None
        if run is None and rid and rid not in self.closed_ids:
            # A fire-and-forget Post can land before its Pre. Record it closed so the late Pre is ignored.
            tool = clip(visible(str(event.get("tool_name") or "tool")), 40)
            target = self.scrub.target(tool, event.get("tool_input"), self.sp[sess["id"]]["place"])
            run = self._open(sess, rid, "tool", tool, target, t)
        if run and run.pop("_inferred", False):
            run["status"] = "running"  # the terminal approved it after all
        if run:
            self._close(run, status, t)
            sess["current"] = None if sess["current"] and sess["current"].get("run") == run["id"] else sess["current"]
        self._settle(sess)
        return run

    def _on_PostToolUse(self, sess, event, t):
        failed = event.get("deck_outcome") == "failed"  # set by hook/deck_ingest.py for Codex test runs
        run = self._finish_tool(sess, event, "failed" if failed else "ok", t)
        if failed:
            self._failure(sess, run, "tests failed")

    def _on_PostToolUseFailure(self, sess, event, t):
        interrupted = event.get("is_interrupt") is True
        run = self._finish_tool(sess, event, "interrupted" if interrupted else "failed", t)
        if not interrupted:
            m = re.match(r"^Exit code (\d+)", event.get("error") or "") if isinstance(event.get("error"), str) else None
            self._failure(sess, run, f"exit {m[1]}" if m else "failed")

    def _failure(self, sess, run, detail: str) -> None:
        sess["counts"]["failed"] += 1
        sess["error"] = True
        error = {"id": f"err_{self.seq + 1}", "session": sess["id"], "agent": sess["agent"], "project": sess["project"],
                 "tool": run["tool"] if run else "", "target": run["target"] if run else "", "run": run["id"] if run else None,
                 "kind": "tool", "detail": detail, "at": iso(self.now())}
        self.errors.appendleft(error)
        self.emit("error", **self._error_fields(error))

    @staticmethod
    def _error_fields(error: dict) -> dict:
        return {("error" if k == "id" else k): v for k, v in error.items()}

    def _on_PermissionRequest(self, sess, event, t):
        tool = clip(visible(str(event.get("tool_name") or "tool")), 40)
        target = self.scrub.target(tool, event.get("tool_input"), self.sp[sess["id"]]["place"])
        run = next((r for r in reversed(self.runs.values())
                    if r["session"] == sess["id"] and r["status"] == "running" and r["tool"] == tool), None)
        if run is None:
            run = self._open(sess, f"perm_{self.seq + 1}", "tool", tool, target, t)
            run["_ctx"] = safe_id(event.get("agent_id"), "a")
        run["status"] = "waiting"
        sess["current"] = {"tool": tool, "target": run["target"], "since": iso(t), "run": run["id"]}
        self.sp[sess["id"]]["perm_t"] = t
        self._state(sess, "waiting", "approval")
        for rid, g in self.gate.items():  # the gate's own request may have arrived first
            if g["session"] is None and g["agent"] == sess["agent"] and g["project"] == sess["project"] and t - g["t"] < LINK_WINDOW:
                g["session"] = sess["id"]
                break

    def _on_PermissionDenied(self, sess, event, t):
        run = self.runs.get(safe_id(event.get("tool_use_id"), "r") or "")
        if run:
            self._close(run, "denied", t)
        sess["counts"]["denied"] += 1
        self._settle(sess)

    def _on_Notification(self, sess, event, t):
        kind = event.get("notification_type")
        if kind == "idle_prompt" and sess["state"] in ("idle", "working", "quiet"):
            self._state(sess, "waiting", "input")
        elif kind == "permission_prompt" and sess["state"] != "waiting":
            self._state(sess, "waiting", "approval")

    def _on_SubagentStart(self, sess, event, t):
        aid = safe_id(event.get("agent_id"), "a")
        kind = clip(visible(str(event.get("agent_type") or "agent")), 32)
        run = next((r for r in reversed(self.runs.values()) if r["session"] == sess["id"] and r["status"] == "running"
                    and r["tool"] in SUBAGENT_TOOLS and not r.get("_agent")), None)
        if run is None:
            run = self._open(sess, f"agent:{aid}", "subagent", kind, kind, t)
        run.update(kind="subagent", _agent=aid)

    def _on_SubagentStop(self, sess, event, t):
        aid = safe_id(event.get("agent_id"), "a")
        for run in reversed(self.runs.values()):
            if run["session"] == sess["id"] and run.get("_agent") == aid:
                self._close(run, "ok", t)
                break

    def _on_TaskCreated(self, sess, event, t):
        self._task(sess, event, done=False)

    def _on_TaskCompleted(self, sess, event, t):
        self._task(sess, event, done=True)

    def _task(self, sess, event, done: bool) -> None:
        tid = event.get("task_id")
        if not isinstance(tid, (str, int)):
            return
        key = "task_" + hashlib.sha1(f"{sess['id']}\0{tid}".encode()).hexdigest()[:16]
        text = self.scrub.text(str(event.get("task_subject") or "task"), self.sp[sess["id"]]["place"], TARGET_MAX)
        known = self.tasks.get(key)
        self.tasks[key] = {"id": key, "project": sess["project"], "text": text, "source": "claude-task",
                           "file": None, "done": done, "session": sess["id"]}
        if not known or known["done"] != done:
            self.emit("todo", todo=key, project=sess["project"], text=text, source="claude-task",
                      status="done" if done else "added")

    def _on_Stop(self, sess, event, t, failed: str | None = None):
        for run in list(self.runs.values()):
            if run["session"] == sess["id"] and run["status"] in ("running", "waiting"):
                self._close(run, "ended", t)
        sess["current"] = None
        if failed:
            sess["error"] = True
            error = {"id": f"err_{self.seq + 1}", "session": sess["id"], "agent": sess["agent"], "project": sess["project"],
                     "tool": "", "target": "", "run": None, "kind": "stop", "detail": failed, "at": iso(t)}
            self.errors.appendleft(error)
            self.emit("error", **self._error_fields(error))
        self._state(sess, "idle")

    def _on_StopFailure(self, sess, event, t):
        kind = event.get("error") if isinstance(event.get("error"), str) and re.fullmatch(r"[a-z_]{1,32}", event["error"]) else "unknown"
        self._on_Stop(sess, event, t, failed=kind)

    def _on_Interrupt(self, sess, event, t):
        for run in list(self.runs.values()):
            if run["session"] == sess["id"] and run["status"] in ("running", "waiting"):
                self._close(run, "interrupted", t)
        self._state(sess, "idle")

    def _on_CwdChanged(self, sess, event, t):
        if isinstance(event.get("new_cwd"), str):
            self._place(sess, event["new_cwd"])

    def _on_SessionEnd(self, sess, event, t):
        for run in list(self.runs.values()):
            if run["session"] == sess["id"] and run["status"] in ("running", "waiting"):
                self._close(run, "ended", t)
        sess["current"] = None
        self._state(sess, "ended")

    def mute(self, sid: str, muted: bool) -> dict | None:
        """Hide a session from the glasses. Its approvals then go straight to the terminal; muting
        never approves anything."""
        with self.cond:
            sess = self.sessions.get(sid)
            if not sess:
                return None
            if sess["muted"] != muted:
                sess["muted"] = muted
                self.emit("session", session=sid, agent=sess["agent"], project=sess["project"], state=sess["state"],
                          waiting_for=sess["waiting_for"], muted=muted)
            return self.public_session(sess)

    # ---- cards --------------------------------------------------------------------

    def card(self, body: dict) -> None:
        """Any script on the PC can post one; it shows in Needs you until it expires or is cleared."""
        cid = body.get("id") if isinstance(body.get("id"), str) and re.fullmatch(r"[\w-]{1,40}", body["id"]) else None
        with self.cond:
            if body.get("clear") is True:
                if cid and self.cards.pop(cid, None):
                    self.emit("card", card=cid, status="cleared")
                return
            title = body.get("title")
            if not isinstance(title, str) or not title.strip():
                return self.log_error("card without a title")
            project = body.get("project") if body.get("project") in self.projects.projects else OTHER
            place = Placement(project, self.projects.projects[project].root if project != OTHER else "", project)
            ttl = body.get("ttl") if isinstance(body.get("ttl"), int) and 60 <= body["ttl"] <= CARD_TTL_MAX else CARD_TTL
            cid = cid or f"card_{self.seq + 1}"
            t = self.now()
            card = {"id": cid, "title": self.scrub.text(title, place, TARGET_MAX), "project": project,
                    "level": body.get("level") if body.get("level") in CARD_LEVELS else "info",
                    "at": iso(t), "_t": t, "_until": t + ttl}
            self.cards[cid] = card
            self.emit("card", card=cid, title=card["title"], project=project, level=card["level"], status="posted")

    # ---- the v1 gate (called by relay.Relay under its own lock) ---------------------

    def _gate_project(self, cwd: str) -> str:
        if cwd.startswith("~"):
            cwd = self.scrub.home + "/" + cwd[1:].lstrip("\\/")
        return self.projects.place(cwd).project

    def gate_route(self, session: dict, tool: str) -> str | None:
        """Why a new approval should go straight to the terminal, or None to send it to the glasses."""
        with self.cond:
            project = self._gate_project(session.get("cwd", ""))
            if self.focus and project not in self.focus["projects"]:
                return "outside focus"
            linked = self._link(session.get("agent", ""), project, tool)
            if linked and self.sessions[linked]["muted"]:
                return "muted"
            return None

    def _link(self, agent: str, project: str, tool: str) -> str | None:
        t, best = self.now(), None
        for sid, sess in self.sessions.items():
            p = self.sp[sid]
            if (sess["agent"] == agent and sess["project"] == project and sess["state"] == "waiting"
                    and t - p["perm_t"] < LINK_WINDOW):
                score = (sess["current"] is not None and sess["current"]["tool"] == tool, p["perm_t"])
                if best is None or score > best[0]:
                    best = (score, sid)
        return best[1] if best else None

    def gate_opened(self, req: dict) -> None:
        with self.cond:
            s = req["session"]
            project = self._gate_project(s.get("cwd", ""))
            link = self._link(s.get("agent", ""), project, req["tool"])
            self.gate[req["id"]] = {"agent": s.get("agent"), "project": project, "tool": req["tool"],
                                    "summary": req["summary"], "risk": req["risk"], "session": link, "t": self.now()}
            self.emit("approval", request=req["id"], session=link, agent=s.get("agent"), project=project,
                      tool=req["tool"], summary=self._gate_summary(req, project), status="pending")

    def _gate_summary(self, req: dict, project: str) -> str:
        root = self.projects.projects[project].root if project in self.projects.projects else ""
        return self.scrub.text(req["summary"], Placement(project, root, project), TARGET_MAX)

    def gate_closed(self, req: dict) -> None:
        with self.cond:
            g = self.gate.pop(req["id"], None)
            if g is None:
                return
            sess = self.sessions.get(g["session"] or "")
            status = req["status"]
            if sess and status in ("approved", "denied"):
                sess["counts"][status] += 1
                if status == "denied":
                    for run in reversed(self.runs.values()):
                        if run["session"] == sess["id"] and run["status"] == "waiting":
                            self._close(run, "denied")
                            break
                    self._settle(sess)
            self.emit("approval", request=req["id"], session=g["session"], agent=g["agent"], project=g["project"],
                      tool=g["tool"], summary=self._gate_summary(req, g["project"]), status=status,
                      **({"by": req["decided_by"]} if req.get("decided_by") else {}))

    def gate_refused(self, body: dict, reason: str) -> None:
        """A request that went straight to the terminal: nobody watching, outside focus, or muted."""
        with self.cond:
            s = body.get("session") if isinstance(body.get("session"), dict) else {}
            project = self._gate_project(s.get("cwd", "")) if isinstance(s.get("cwd"), str) else OTHER
            agent = s.get("agent") if s.get("agent") in AGENTS else None
            link = self._link(agent or "", project, str(body.get("tool")))
            summary = body.get("summary") if isinstance(body.get("summary"), str) else ""
            root = self.projects.projects[project].root if project in self.projects.projects else ""
            self.emit("approval", request=None, session=link, agent=agent, project=project,
                      tool=clip(str(body.get("tool")), 40), summary=self.scrub.text(summary, Placement(project, root, project)),
                      status="refused", reason=reason)

    # ---- focus --------------------------------------------------------------------

    def _load_focus(self) -> dict | None:
        try:
            focus = json.loads((self.home / "focus.json").read_text(encoding="utf-8"))
            return focus if isinstance(focus, dict) and isinstance(focus.get("projects"), list) else None
        except (OSError, ValueError):
            return None

    def _save_focus(self) -> None:
        path = self.home / "focus.json"
        if self.focus is None:
            path.unlink(missing_ok=True)
            return
        tmp = path.with_name("focus.json.tmp")
        tmp.write_text(json.dumps(self.focus), encoding="utf-8")
        os.replace(tmp, path)

    def set_focus(self, projects, minutes) -> dict | str:
        if not isinstance(projects, list) or not 1 <= len(projects) <= MAX_FOCUS or len(set(projects)) != len(projects):
            return f"projects must be a list of 1 to {MAX_FOCUS} project ids"
        if any(p not in self.projects.projects for p in projects):
            return "unknown project"
        if minutes not in FOCUS_MINUTES:
            return "minutes must be 25, 50 or null"
        with self.cond:
            if self.focus:
                self.end_focus("replaced")
            t = self.now()
            self.focus = {"projects": projects, "minutes": minutes, "started_at": iso(t), "ends_at": iso(t + minutes * 60) if minutes else None,
                          "_t": t, "_until": t + minutes * 60 if minutes else None, "_seq": self.seq}
            self._save_focus()
            self.emit("focus_start", projects=projects, minutes=minutes, ends_at=self.focus["ends_at"])
            return self.public_focus()

    def end_focus(self, reason: str) -> dict | None:
        with self.cond:
            if not self.focus:
                return None
            focus, self.focus = self.focus, None
            self._save_focus()
            summary = self.summarize(focus)
            self.last_summary = {**summary, "reason": reason, "projects": focus["projects"], "ended_at": iso(self.now())}
            self.emit("focus_end", reason=reason, projects=focus["projects"], summary=summary)
            return self.last_summary

    def summarize(self, focus: dict) -> dict:
        """What the focus block did, counted from the event log since it started."""
        inside = set(focus["projects"])
        counts = {"minutes": int((self.now() - focus["_t"]) // 60), "approved": 0, "denied": 0, "runs": 0, "todos": 0}
        for e in self.events:
            if e["id"] <= focus["_seq"] or e.get("project") not in inside:
                continue
            if e["type"] == "approval" and e.get("status") in ("approved", "denied"):
                counts[e["status"]] += 1
            elif e["type"] == "run" and e.get("status") in ("ok", "failed"):
                counts["runs"] += 1
            elif e["type"] == "todo" and e.get("status") == "done":
                counts["todos"] += 1
        return counts

    def public_focus(self) -> dict | None:
        f = self.focus
        if not f:
            return None
        left = max(0, int(f["_until"] - self.now())) if f.get("_until") else None
        return {"projects": f["projects"], "minutes": f["minutes"], "started_at": f["started_at"], "ends_at": f["ends_at"], "left_s": left}

    # ---- upkeep ---------------------------------------------------------------------

    def sweep(self, armed: bool | None = None) -> None:
        with self.cond:
            t = self.now()
            if armed is not None and armed != self.armed:
                if self.armed is not None:
                    self.emit("armed", armed=armed)
                self.armed = armed
                if not armed and self.focus:
                    self.end_focus("disarmed")
            if self.focus and self.focus.get("_until") and t >= self.focus["_until"]:
                self.end_focus("timer")
            for sid, sess in list(self.sessions.items()):
                idle_for = t - self.sp[sid]["last_t"]
                if sess["state"] == "working" and idle_for >= QUIET_AFTER:
                    self._state(sess, "quiet")
                elif sess["state"] == "ended" and t - self.sp[sid]["state_t"] >= ENDED_KEEP:
                    self._drop(sid)
            for cid, card in list(self.cards.items()):
                if t >= card["_until"]:
                    del self.cards[cid]

    def _drop(self, sid: str) -> None:
        self.sessions.pop(sid, None)
        self.sp.pop(sid, None)
        for rid in [rid for rid, r in self.runs.items() if r["session"] == sid]:
            del self.runs[rid]
        for key in [k for k, task in self.tasks.items() if task["session"] == sid]:
            del self.tasks[key]

    def _cap(self) -> None:
        while len(self.sessions) > MAX_SESSIONS:
            ended = [s for s in self.sessions if self.sessions[s]["state"] == "ended"]
            self._drop(min(ended or self.sessions, key=lambda s: self.sp[s]["last_t"]))
        while len(self.runs) > MAX_RUNS:
            closed = next((rid for rid, r in self.runs.items() if r["status"] not in ("running", "waiting")), None)
            del self.runs[closed or next(iter(self.runs))]

    # ---- reads --------------------------------------------------------------------

    def in_scope(self, project: str, scope: str) -> bool:
        if scope == "all":
            return True
        if scope == "focus":
            return not self.focus or project in self.focus["projects"]
        return project == scope

    def visible_project(self, project: str) -> bool:
        return project != OTHER or self.projects.unmapped != "hide"

    def public_session(self, sess: dict) -> dict:
        return {**sess, "project_name": self.projects.name(sess["project"]),
                "state_for_s": int(self.now() - self.sp[sess["id"]]["state_t"])}

    def public_run(self, run: dict) -> dict:
        end = run["_end"] if run["_end"] is not None else self.now()
        out = {k: v for k, v in run.items() if not k.startswith("_")}
        return {**out, "project_name": self.projects.name(run["project"]), "elapsed_s": int(end - run["_t"])}

    def session_list(self, scope: str) -> list[dict]:
        order = {"waiting": 0, "working": 1, "idle": 2, "quiet": 3, "ended": 4}
        with self.cond:
            rows = [s for s in self.sessions.values() if self.in_scope(s["project"], scope) and self.visible_project(s["project"])]
            rows.sort(key=lambda s: (order[s["state"]], self.sp[s["id"]]["state_t"] if s["state"] == "waiting" else -self.sp[s["id"]]["last_t"]))
            return [self.public_session(s) for s in rows]

    def session_detail(self, sid: str) -> dict | None:
        with self.cond:
            sess = self.sessions.get(sid)
            if not sess:
                return None
            history = [e for e in self.events if e.get("session") == sid][-20:]
            pending = [rid for rid, g in self.gate.items() if g["session"] == sid]
            return {**self.public_session(sess), "events": history, "pending_request": pending[0] if pending else None}

    def run_list(self, scope: str, active: bool) -> list[dict]:
        with self.cond:
            rows = [r for r in self.runs.values() if self.in_scope(r["project"], scope) and self.visible_project(r["project"])
                    and (not active or r["status"] in ("running", "waiting"))
                    and not self.sessions.get(r["session"], {}).get("muted")]
            rows.sort(key=lambda r: r["_t"])
            return [self.public_run(r) for r in rows]

    def run_detail(self, rid: str) -> dict | None:
        with self.cond:
            return self.public_run(self.runs[rid]) if rid in self.runs else None

    def needs(self, scope: str) -> list[dict]:
        """What wants the user: live approvals first, then sessions waiting in the terminal, then cards."""
        level = {"error": 0, "warn": 1, "info": 2}
        with self.cond:
            t, items, covered = self.now(), [], set()
            for rid, g in self.gate.items():
                if self.in_scope(g["project"], scope):
                    items.append({"kind": "approval", "id": rid, "session": g["session"], "agent": g["agent"], "project": g["project"],
                                  "project_name": self.projects.name(g["project"]), "tool": g["tool"],
                                  "title": self._gate_summary({"summary": g["summary"]}, g["project"]), "risk": g["risk"],
                                  "for_s": int(t - g["t"])})
                    covered.add(g["session"])
            waiting = [s for s in self.sessions.values() if s["state"] == "waiting" and s["id"] not in covered and not s["muted"]
                       and self.in_scope(s["project"], scope) and self.visible_project(s["project"])]
            for s in sorted(waiting, key=lambda s: self.sp[s["id"]]["state_t"]):
                cur = s["current"] or {}
                items.append({"kind": "session", "id": s["id"], "session": s["id"], "agent": s["agent"], "project": s["project"],
                              "project_name": self.projects.name(s["project"]), "tool": cur.get("tool", "") if s["waiting_for"] == "approval" else "",
                              "title": (cur.get("target") or cur.get("tool") or "") if s["waiting_for"] == "approval" else "waiting for input",
                              "waiting_for": s["waiting_for"], "for_s": int(t - self.sp[s["id"]]["state_t"])})
            cards = [c for c in self.cards.values() if self.in_scope(c["project"], scope)]
            for c in sorted(cards, key=lambda c: (level[c["level"]], c["at"])):
                items.append({"kind": "card", "id": c["id"], "session": None, "agent": None, "project": c["project"],
                              "project_name": self.projects.name(c["project"]), "tool": "", "title": c["title"],
                              "level": c["level"], "for_s": int(t - c["_t"])})
            return items

    def todo_list(self, scope: str, include_done: bool = False) -> list[dict]:
        out = []
        for pid in self.projects.projects:
            if self.in_scope(pid, scope):
                out += [t for t in self._file_todos(pid) if include_done or not t["done"]]
        with self.cond:
            out += [{k: v for k, v in t.items() if k != "session"} for t in self.tasks.values()
                    if self.in_scope(t["project"], scope) and self.visible_project(t["project"]) and (include_done or not t["done"])]
        for t in out:
            t["project_name"] = self.projects.name(t["project"])
        return out

    def _file_todos(self, pid: str) -> list[dict]:
        stamp = []
        for _, path in self.projects.todo_files(pid):
            try:
                st = path.stat()
                stamp.append((str(path), st.st_mtime_ns, st.st_size))
            except OSError:
                stamp.append((str(path), 0, 0))
        hit = self._todo_cache.get(pid)
        if hit and hit[0] == tuple(stamp):
            return [dict(t) for t in hit[1]]
        todos = self.projects.todos(pid)
        self._todo_cache[pid] = (tuple(stamp), todos)
        return [dict(t) for t in todos]

    def mark_todo(self, todo_id: str) -> dict | None:
        """Flip one `- [ ]` line in a configured file. None if no configured file has that line unchanged."""
        if not re.fullmatch(r"[0-9a-f]{20}", todo_id):
            return None
        for pid in self.projects.projects:
            todo = next((t for t in self._file_todos(pid) if t["id"] == todo_id and not t["done"]), None)
            if todo and self.projects.mark_done(pid, todo_id) == "done":
                self._todo_cache.pop(pid, None)
                self.emit("todo", todo=todo_id, project=pid, text=todo["text"][:TARGET_MAX], source=todo["source"], status="done")
                return {**todo, "done": True, "project_name": self.projects.name(pid)}
        return None

    def project_list(self) -> list[dict]:
        git = self.projects.git_all()
        with self.cond:
            ids = list(self.projects.projects)
            if self.projects.unmapped != "hide" and any(s["project"] == OTHER for s in self.sessions.values()):
                ids.append(OTHER)
            return [self._project(pid, git.get(pid)) for pid in ids]

    def project_detail(self, pid: str) -> dict | None:
        if pid not in self.projects.projects and not (pid == OTHER and self.projects.unmapped != "hide"):
            return None
        git = self.projects.git(pid)
        with self.cond:
            return {**self._project(pid, git), "commits": git["commits"] if git else []}

    def _project(self, pid: str, git: dict | None) -> dict:
        sessions = [s for s in self.sessions.values() if s["project"] == pid]
        return {"id": pid, "name": self.projects.name(pid), "branch": (git or {}).get("branch", ""),
                "dirty": (git or {}).get("dirty", 0), "pinned": bool(self.focus and pid in self.focus["projects"]),
                "counts": {"sessions": sum(s["state"] != "ended" for s in sessions),
                           "running": sum(r["project"] == pid and r["status"] in ("running", "waiting") for r in self.runs.values()),
                           "waiting": sum(s["state"] == "waiting" for s in sessions) + sum(g["project"] == pid and g["session"] is None for g in self.gate.values()),
                           "todos_open": len(self.todo_list(pid)) if pid != OTHER else sum(not t["done"] and t["project"] == pid for t in self.tasks.values())}}

    def deck(self, scope: str) -> dict:
        """The root screen's counts."""
        needs = self.needs(scope)
        runs = self.run_list(scope, active=True)
        sessions = self.session_list(scope)
        with self.cond:
            outside = None
            if self.focus and scope == "focus":
                rest = [s for s in self.sessions.values() if not self.in_scope(s["project"], "focus") and s["state"] != "ended"
                        and self.visible_project(s["project"])]
                outside = {"active": len(rest), "waiting": sum(s["state"] == "waiting" for s in rest)
                           + sum(not self.in_scope(g["project"], "focus") for g in self.gate.values())}
            errors = [e for e in self.errors if self.in_scope(e["project"], scope)]
            return {"scope": scope, "needs_you": len(needs), "sessions": sum(s["state"] != "ended" for s in sessions),
                    "waiting": sum(s["state"] == "waiting" for s in sessions), "running": len(runs),
                    "longest_s": max((r["elapsed_s"] for r in runs), default=0),
                    "projects": len(self.projects.projects) + (1 if self.projects.unmapped != "hide" and any(s["project"] == OTHER for s in self.sessions.values()) else 0),
                    "todos_open": len(self.todo_list(scope)), "focus": self.public_focus(), "outside_focus": outside,
                    "errors": errors[:5], "cursor": self.seq}
