"""Command Deck projects: deck.projects.json, cwd -> project mapping, read-only git, to-do files.

Project roots, absolute paths and file contents stay in this process. Everything returned to a
caller that serves the phone is a project id, a project-relative path or a count. Stdlib only.

    {
      "projects": [
        {"id": "glasses", "name": "glasses", "root": "H:/glasses", "todos": ["TODO.md"]},
        {"id": "hub", "name": "hub-orch", "root": "C:/dev/hub", "todos": ["obsidian:Projects/Hub.md"]}
      ],
      "obsidian_vault": "C:/vault",
      "unmapped": "other"
    }
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

OTHER = "(other)"
ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
TODO_LINE = re.compile(r"^(\s*[-*+]\s+\[)([ xX])(\]\s+)(.*?)\s*$")
GIT_TIMEOUT = 2.0
GIT_CACHE = 15.0
TODO_TEXT_MAX = 200
MAX_TODO_FILE = 1024 * 1024
WORKTREE_DIRS = (".claude/worktrees", ".worktrees", "worktrees")


def norm(path: str | os.PathLike) -> str:
    """Forward slashes, no trailing slash, Git Bash /c/x as c:/x. Case is kept; compare with key()."""
    text = str(path).replace("\\", "/")
    if m := re.match(r"^/([a-zA-Z])(?=/|$)", text):
        text = f"{m[1]}:{text[2:]}"
    text = text.rstrip("/")
    return text + "/" if re.fullmatch(r"[a-zA-Z]:", text) else text or "/"


def key(path: str | os.PathLike) -> str:
    """Comparison form. Windows paths compare case-insensitively."""
    return norm(path).lower() if os.name == "nt" else norm(path)


def under(path: str, root: str) -> str | None:
    """`path` relative to `root` ("" for the root itself), or None when it isn't inside."""
    p, r = key(path), key(root)
    if p == r:
        return ""
    prefix = r if r.endswith("/") else r + "/"
    return norm(path)[len(prefix):] if p.startswith(prefix) else None


@dataclass
class Project:
    id: str
    name: str
    root: str  # normalized, never sent anywhere
    todos: list[str] = field(default_factory=list)
    show_summary: bool = False


@dataclass
class Placement:
    """Where a working directory sits. `base` is the directory paths are made relative to."""
    project: str
    base: str
    folder: str
    tag: str = ""


class Projects:
    def __init__(self, config: Path | None = None):
        self.path = config
        self.projects: dict[str, Project] = {}
        self.vault = ""
        self.unmapped = "other"
        self.error = ""
        self.lock = threading.Lock()
        self._git: dict[str, tuple[float, dict]] = {}
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="deck-git")
        self.load()

    # ---- config -----------------------------------------------------------------

    def load(self) -> None:
        self.projects, self.error = {}, ""
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.vault = norm(data.get("obsidian_vault") or "") if data.get("obsidian_vault") else ""
            self.unmapped = "hide" if data.get("unmapped") == "hide" else "other"
            for p in data.get("projects", []):
                pid, root = str(p.get("id", "")), p.get("root")
                if not ID_RE.fullmatch(pid) or not isinstance(root, str) or not os.path.isabs(root.replace("/", os.sep)):
                    raise ValueError(f"project needs an id like 'glasses' and an absolute root: {pid!r}")
                todos = [t for t in p.get("todos", []) if isinstance(t, str)]
                name = str(p.get("name") or pid)[:24]
                self.projects[pid] = Project(pid, name, norm(root), todos, p.get("show_summary") is True)
        except (OSError, ValueError, TypeError, AttributeError) as err:
            self.projects, self.error = {}, f"{self.path.name}: {err}"

    def name(self, pid: str) -> str:
        return self.projects[pid].name if pid in self.projects else pid

    # ---- placement --------------------------------------------------------------

    def place(self, cwd: str) -> Placement:
        """Map a working directory to a project. Git worktrees map to their main project."""
        cwd = norm(cwd)
        best: Project | None = None
        for p in self.projects.values():
            if under(cwd, p.root) is not None and (best is None or len(p.root) > len(best.root)):
                best = p
        if best:
            rel = under(cwd, best.root) or ""
            for wt in WORKTREE_DIRS:  # <root>/.claude/worktrees/<name>/...
                if rel.lower().startswith(wt + "/"):
                    tag = rel[len(wt) + 1:].split("/")[0]
                    return Placement(best.id, f"{best.root.rstrip('/')}/{wt}/{tag}", tag, tag)
            return Placement(best.id, best.root, Path(best.root).name)
        main, tree = worktree_main(cwd)
        if main:
            for p in self.projects.values():
                if under(main, p.root) is not None:
                    return Placement(p.id, tree, Path(tree).name, Path(tree).name)
        return Placement(OTHER, cwd, Path(cwd).name or cwd)

    def display(self, path: str, base: str, own: str = "") -> str:
        """An absolute path as the phone may see it: relative to `base`, `<project>/rel` for another
        project, otherwise the last path component only."""
        path = norm(path)
        rel = under(path, base)
        if rel is not None:
            return rel or "."
        for p in self.projects.values():
            if p.id != own and (rel := under(path, p.root)) is not None:
                return f"{p.name}/{rel}" if rel else p.name
        return Path(path).name or "."

    # ---- git (read-only) --------------------------------------------------------

    def git(self, pid: str) -> dict:
        """Branch, dirty file count and the last five commit subjects. Cached, 2 s per call."""
        p = self.projects.get(pid)
        if not p:
            return {"branch": "", "dirty": 0, "commits": []}
        with self.lock:
            hit = self._git.get(pid)
        if hit and time.monotonic() - hit[0] < GIT_CACHE:
            return hit[1]
        info = git_info(p.root)
        with self.lock:
            self._git[pid] = (time.monotonic(), info)
        return info

    def git_all(self) -> dict[str, dict]:
        ids = list(self.projects)
        return dict(zip(ids, self._pool.map(self.git, ids)))

    # ---- to-do files ------------------------------------------------------------

    def todo_files(self, pid: str) -> list[tuple[str, Path]]:
        """(source label, file) for each to-do file a project lists. Only these can be written."""
        p = self.projects.get(pid)
        out = []
        for entry in p.todos if p else []:
            if entry.startswith("obsidian:"):
                if self.vault:
                    out.append((entry, Path(self.vault) / entry[len("obsidian:"):]))
            else:
                out.append((entry, Path(p.root) / entry))
        return [(label, f) for label, f in out if contained(f, Path(self.vault if label.startswith("obsidian:") else p.root))]

    def todos(self, pid: str) -> list[dict]:
        items = []
        for label, path in self.todo_files(pid):
            for todo_id, done, text in scan(path, pid, label):
                items.append({"id": todo_id, "project": pid, "text": text, "source": "obsidian" if label.startswith("obsidian:") else "todo-file",
                              "file": label.split(":", 1)[1] if label.startswith("obsidian:") else label, "done": done})
        return items

    def mark_done(self, pid: str, todo_id: str) -> str:
        """'done', or why not: 'unknown' (no such open line, or it changed since it was read)."""
        for label, path in self.todo_files(pid):
            if mark(path, pid, label, todo_id):
                return "done"
        return "unknown"


def contained(path: Path, root: Path) -> bool:
    try:
        return under(os.path.realpath(path), os.path.realpath(root)) is not None
    except (OSError, ValueError):
        return False


def worktree_main(cwd: str) -> tuple[str, str]:
    """(main repo root, worktree root) when cwd is inside a linked git worktree, else ("", "")."""
    d = Path(cwd)
    for candidate in (d, *d.parents):
        dot_git = candidate / ".git"
        try:
            if dot_git.is_dir():
                return "", ""
            if dot_git.is_file():
                m = re.match(r"gitdir:\s*(.+?)\s*$", dot_git.read_text(encoding="utf-8", errors="replace"))
                gitdir = norm(m[1]) if m else ""
                if (i := gitdir.lower().rfind("/.git/worktrees/")) > 0:
                    return gitdir[:i], norm(candidate)
                return "", ""
        except OSError:
            return "", ""
    return "", ""


def git_info(root: str) -> dict:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}

    def run(*args: str) -> str | None:
        try:
            out = subprocess.run(["git", "-c", f"safe.directory={norm(root)}", "-C", root, *args], capture_output=True,
                                 timeout=GIT_TIMEOUT, env=env, stdin=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return out.stdout.decode("utf-8", "replace") if out.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    branch = (run("rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    if branch == "HEAD":
        branch = (run("rev-parse", "--short", "HEAD") or "").strip()
    status = run("status", "--porcelain=v1", "-z")
    log = run("log", "-5", "--format=%s")
    return {"branch": branch[:40], "dirty": len([e for e in (status or "").split("\0") if e[:2].strip()]),
            "commits": [s[:64] for s in (log or "").splitlines() if s.strip()][:5]}


def todo_id(pid: str, label: str, text: str, nth: int) -> str:
    return hashlib.sha1(f"{pid}\0{label}\0{text}\0{nth}".encode()).hexdigest()[:20]


def read_lines(path: Path) -> list[str] | None:
    try:
        if path.stat().st_size > MAX_TODO_FILE:
            return None
        return path.read_bytes().decode("utf-8", "replace").splitlines(keepends=True)
    except OSError:
        return None


def scan(path: Path, pid: str, label: str) -> list[tuple[str, bool, str]]:
    """(id, done, text) for every `- [ ]` / `- [x]` line. Identical lines get distinct ids by position."""
    seen: dict[str, int] = {}
    out = []
    for line in read_lines(path) or []:
        if m := TODO_LINE.match(line.rstrip("\r\n")):
            raw = line.rstrip("\r\n")
            nth = seen[raw] = seen.get(raw, -1) + 1
            out.append((todo_id(pid, label, raw, nth), m[2] != " ", m[4][:TODO_TEXT_MAX]))
    return out


def mark(path: Path, pid: str, label: str, wanted: str) -> bool:
    """Flip exactly the one open line whose id matches to `[x]`, by temp file + rename."""
    lines = read_lines(path)
    if lines is None:
        return False
    seen: dict[str, int] = {}
    for i, line in enumerate(lines):
        raw = line.rstrip("\r\n")
        m = TODO_LINE.match(raw)
        if not m:
            continue
        nth = seen[raw] = seen.get(raw, -1) + 1
        if m[2] == " " and todo_id(pid, label, raw, nth) == wanted:
            lines[i] = m[1] + "x" + line[len(m[1]) + 1:]
            tmp = path.with_name(f".{path.name}.deck-{os.getpid()}.tmp")
            try:
                tmp.write_bytes("".join(lines).encode("utf-8"))
                os.replace(tmp, path)
            except OSError:
                tmp.unlink(missing_ok=True)
                return False
            return True
    return False
