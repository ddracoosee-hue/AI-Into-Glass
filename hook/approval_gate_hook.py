#!/usr/bin/env python3
"""Approval Gate hook for the PermissionRequest event of Claude Code and Codex.

Both agents send the same event name and accept the same decision object. `--agent codex`
marks requests from Codex, whose shell commands and apply_patch edits arrive in their own shape.

Silence means terminal. Unless the relay returns an explicit decision for this
request's own id, the hook prints nothing and exits 0, and Claude Code shows its
usual permission prompt. No error path prints "allow".

Stdlib only; run with `python -I`. Registered by scripts/install_hook.py:

    "PermissionRequest": [{"matcher": "Bash|Write|Edit", "hooks": [{"type": "command",
        "command": "<python.exe>", "args": ["-I", "<repo>/hook/approval_gate_hook.py"], "timeout": 100}]}]
"""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from pathlib import Path

SUMMARY_MAX = 60
DETAIL_MAX = 200
WAIT_TIMEOUT = 95  # relay answers within 90 s; Claude Code kills the hook at 100 s
ID_RE = re.compile(r"req_[0-9A-Z]{26}")
AGENTS = ("claude-code", "codex")
PATCH_FILE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+?)\s*$")

# Never sent to the glasses: these always get the terminal prompt.
DENY_COMMAND = [
    (r"\brm\s+(\S+\s+)*-[a-z]*(r[a-z]*f|f[a-z]*r)", "rm -rf"),
    (r"\brm\s+(\S+\s+)*(-[a-z]*r[a-z]*|--recursive)\s+(\S+\s+)*(-[a-z]*f[a-z]*|--force)\b", "rm -rf"),
    (r"\brm\s+(\S+\s+)*(-[a-z]*f[a-z]*|--force)\s+(\S+\s+)*(-[a-z]*r[a-z]*|--recursive)\b", "rm -rf"),
    (r"\bremove-item\b.*-r(ecurse)?\b.*-fo(rce)?\b|\bremove-item\b.*-fo(rce)?\b.*-r(ecurse)?\b", "Remove-Item -Recurse -Force"),
    (r"\b(rd|rmdir)\s+/s\b|\b(del|erase)\s+(\S+\s+)*/s\b", "recursive delete"),
    (r"\bgit\s+push\b.*(\s--force\b|\s-f\b|\s--force-with-lease\b|\s--mirror\b|\s\+\S)", "git push --force"),
    (r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f)", "discards work"),
    (r"(^|[\s;&|(`])(sudo|doas|runas|su)\b|\bstart-process\b.*-verb\s+runas", "privilege escalation"),
    (r"\b(curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod)\b.*\|\s*(sudo\s+)?(ba|z|da|k|fi)?sh\b", "curl | sh"),
    (r"\|\s*(iex|invoke-expression)\b|\b(iex|invoke-expression)\s*[(\"'$]", "pipe to iex"),
    (r"base64\s+(-d|--decode|-D)\b", "decoded payload"),
    (r"(^|[\s;&|(])eval\b", "eval"),
    (r"\bmkfs\b|\bdd\s+if=|\bformat(-volume)?\s+[a-z]:|\bdiskpart\b", "disk"),
]
SENSITIVE_PATH = re.compile(
    r"\.ssh\b|(^|[\\/\s'\"=:])\.env(rc)?(\.[\w.-]+)?($|[\s'\"\\/;|&)])|settings(\.local)?\.json|"
    r"\.claude[\\/]|approval[-_]gate|\.git[\\/](hooks|config)|\.aws\b|\.gnupg\b|\.npmrc|\.pypirc|\.netrc|"
    r"\bid_(rsa|ed25519|ecdsa)\b|credentials", re.I)

# Risk for everything that is allowed through. Any high segment makes the whole command high.
HIGH = re.compile(
    r"\bgit\s+(push|rebase|reset|restore|checkout\s+--|branch\s+-D|tag\s+-d)\b|"
    r"\b(rm|del|erase|rmdir|rd|unlink|shred|remove-item|ri)\b|"
    r"\b(curl|wget|ssh|scp|sftp|rsync|nc|ncat|telnet|ftp|invoke-webrequest|invoke-restmethod|iwr|irm)\b|"
    r"\b(npm|pnpm|yarn)\s+publish\b|\b(twine\s+upload|cargo\s+publish|docker\s+push|gh\s+release|gh\s+pr\s+merge)\b|"
    r"\b(kill|pkill|taskkill|stop-process|shutdown|reboot|chmod|chown|icacls|takeown|reg\s+(add|delete))\b|"
    r"\b(terraform\s+(apply|destroy)|kubectl\s+(apply|delete)|docker\s+(rm|rmi|system\s+prune))\b", re.I)
READ_ONLY = re.compile(
    r"^(git\s+(status|diff|log|show|branch|remote\s+-v|rev-parse|ls-files|blame)\b|"
    r"(ls|dir|cat|type|head|tail|wc|grep|rg|pwd|which|where|echo|tree|stat|file|du|df|whoami|date|get-childitem|"
    r"get-content|get-location|select-string)\b|"
    r"(npm|pnpm|yarn)\s+(test|run\s+test|run\s+lint|ls|list|outdated)\b|(pytest|python\s+-m\s+(pytest|unittest))\b|"
    r"(go|cargo)\s+(test|check|vet)\b|tsc\s+--noEmit\b)", re.I)
SECRETS = [
    (r"\bsk-(ant-)?[A-Za-z0-9_-]{16,}", "[secret]"),
    (r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}|AKIA[0-9A-Z]{16}|xox[abpr]-[A-Za-z0-9-]{10,}|AIza[\w-]{30,})", "[secret]"),
    (r"\beyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]+", "[secret]"),
    (r"(?i)\b(\w*(token|secret|passw(or)?d|api_?key|auth)\w*)\s*=\s*('[^']*'|\"[^\"]*\"|\S+)", r"\1=[secret]"),
    (r"(?i)(authorization:\s*(bearer|basic|token)\s+)[^\s'\"]+", r"\1[secret]"),
    (r"(?i)(--?(password|passwd|token|api-key|secret)[= ])\S+", r"\1[secret]"),
    (r"(?i)(://[^/\s:@]+:)[^@\s/]+@", r"\1[secret]@"),
]


def state_dir() -> Path:
    if os.environ.get("APPROVAL_GATE_HOME"):
        return Path(os.environ["APPROVAL_GATE_HOME"])
    return Path(os.environ.get("APPDATA") or Path.home() / ".config") / "approval-gate"


def has_hidden_chars(text: str) -> bool:
    """Control, format and bidi characters can make what the glasses show differ from what runs."""
    return any(unicodedata.category(c) in ("Cc", "Cf", "Co", "Cs") and c not in "\n\t" for c in text)


def home_variants() -> list[str]:
    home = str(Path.home())
    forward = home.replace("\\", "/")
    variants = {home, forward}
    if re.match(r"^[A-Za-z]:/", forward):
        variants.add(f"/{forward[0].lower()}{forward[2:]}")  # Git Bash: /c/Users/name
    return sorted(variants, key=len, reverse=True)


def redact(text: str) -> str:
    for pattern, repl in SECRETS:
        text = re.sub(pattern, repl, text)
    for home in home_variants():
        text = re.sub(re.escape(home) + r"(?=$|[\\/\s'\"])", "~", text, flags=re.I)
    return text


def clip(text: str, limit: int, keep_tail: bool = False) -> str:
    if len(text) <= limit:
        return text
    return "…" + text[-(limit - 1):] if keep_tail else text[:limit - 1] + "…"


def display_path(path: str, cwd: str) -> str:
    """Relative to cwd when inside it, otherwise home-redacted."""
    try:
        rel = os.path.relpath(path, cwd)
        if not rel.startswith(".."):
            return rel.replace("\\", "/")
    except ValueError:
        pass  # different drive
    return redact(path).replace("\\", "/")


def inside(path: str, cwd: str) -> bool:
    try:
        return not os.path.relpath(path, cwd).startswith("..")
    except ValueError:
        return False


def describe_command(command: str) -> tuple[dict | None, str]:
    """(summary, detail, risk) for a shell command, or None and the reason it goes to the terminal."""
    if not command.strip():
        return None, "empty command"
    if has_hidden_chars(command):
        return None, "hidden characters"
    for pattern, reason in DENY_COMMAND:
        if re.search(pattern, command, re.I):
            return None, reason
    if SENSITIVE_PATH.search(command):
        return None, "sensitive path"
    shown = redact(command.strip())
    if len(shown) > DETAIL_MAX:
        return None, "too long to show in full"
    segments = [s for s in re.split(r"\s*(?:&&|\|\||;|\||\n)\s*", shown) if s.strip()]
    risky = [s for s in segments if HIGH.search(s)]
    writes = re.search(r"(?<![<>&0-9])>{1,2}(?!&)|\|\s*tee\b|\b(tee|set-content|out-file|add-content)\b", shown, re.I)
    risk = "high" if risky else "low" if not writes and all(READ_ONLY.search(s) for s in segments) else "medium"
    one_line = " ; ".join(line.strip() for line in shown.splitlines() if line.strip())
    if len(one_line) <= SUMMARY_MAX:
        summary = one_line
    elif len(segments) > 1:
        lead = (risky or segments)[0]
        suffix = f" …+{len(segments) - 1}"
        summary = clip(lead, SUMMARY_MAX - len(suffix)) + suffix
    else:
        summary = clip(one_line, SUMMARY_MAX)
    return {"summary": summary, "detail": shown, "risk": risk}, ""


def shape(value: dict) -> str:
    """Field names and types only, for logging an input this hook can't read. Never values."""
    return "keys " + ", ".join(f"{k}:{type(v).__name__}" for k, v in sorted(value.items()))[:150]


def shell_command(value) -> str | None:
    """Codex may send argv instead of a string. For `bash -lc <script>` the script is what runs."""
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value and all(isinstance(a, str) for a in value):
        if len(value) == 3 and value[1] in ("-c", "-lc", "-Command", "/c"):
            return value[2]
        return " ".join(value)
    return None


def patch_files(patch: str) -> list[list]:
    """[op, path, added, removed] for each file in a Codex apply_patch body. Contents stay in here."""
    files: list[list] = []
    for line in patch.splitlines():
        if m := PATCH_FILE.match(line):
            files.append([m[1], m[2], 0, 0])
        elif line.startswith("*** Move to: ") and files:
            files.append(["Move", line[len("*** Move to: "):].strip(), 0, 0])
        elif files and line.startswith("+") and not line.startswith("+++"):
            files[-1][2] += 1
        elif files and line.startswith("-") and not line.startswith("---"):
            files[-1][3] += 1
    return files


def describe_patch(tool_input: dict, cwd: str) -> tuple[dict | None, str]:
    """A Codex apply_patch: file names and line counts only, never the patch text."""
    patch = tool_input.get("command")
    if not isinstance(patch, str):
        return None, f"unreadable patch ({shape(tool_input)})"
    files = patch_files(patch)
    if not files:
        return None, "no files in patch"
    risk, shown = "medium", []
    for op, path, added, removed in files:
        if has_hidden_chars(path):
            return None, "hidden characters"
        full = os.path.normpath(os.path.join(cwd, path))
        if SENSITIVE_PATH.search(full):
            return None, "sensitive path"
        if op in ("Delete", "Move") or not inside(full, cwd):
            risk = "high"
        shown.append((op, display_path(full, cwd), added, removed))
    detail = "; ".join(f"{op.lower()} {path} (+{a} -{r})" for op, path, a, r in shown)
    if len(detail) > DETAIL_MAX:
        return None, "too many files to show in full"
    prefix, suffix = (f"{shown[0][0]} ", "") if len(shown) == 1 else ("Edit ", f" +{len(shown) - 1} more")
    summary = prefix + clip(shown[0][1], SUMMARY_MAX - len(prefix) - len(suffix), keep_tail=True) + suffix
    return {"summary": summary, "detail": detail, "risk": risk}, ""


def describe_file(tool: str, tool_input: dict, cwd: str) -> tuple[dict | None, str]:
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(path, str) or not path:
        return None, "no path"
    if has_hidden_chars(path):
        return None, "hidden characters"
    path = os.path.normpath(os.path.join(cwd, path))
    if SENSITIVE_PATH.search(path):
        return None, "sensitive path"
    shown = display_path(path, cwd)
    lines = lambda s: (s or "").count("\n") + 1 if s else 0
    if tool == "Write":
        what = f"{'overwrite' if os.path.exists(path) else 'new file'}, {lines(tool_input.get('content'))} lines"
    elif tool == "Edit":
        what = f"replace {lines(tool_input.get('old_string'))} lines with {lines(tool_input.get('new_string'))}"
        what += " (every match)" if tool_input.get("replace_all") else ""
    elif tool == "MultiEdit":
        what = f"{len(tool_input.get('edits') or [])} edits"
    else:
        what = f"notebook cell {tool_input.get('edit_mode') or 'replace'}"
    return {"summary": clip(f"{tool} {shown}", SUMMARY_MAX, keep_tail=True),
            "detail": clip(f"{what} · {shown}", DETAIL_MAX, keep_tail=True),
            "risk": "medium" if inside(path, cwd) else "high"}, ""


def build_request(event: dict, agent: str = "claude-code") -> tuple[dict | None, str]:
    tool, tool_input = event.get("tool_name"), event.get("tool_input")
    cwd = event.get("cwd") or os.getcwd()
    if not isinstance(tool, str) or not isinstance(tool_input, dict) or not isinstance(cwd, str):
        return None, "malformed event"
    if tool in ("Bash", "PowerShell"):
        command = shell_command(tool_input.get("command"))
        described, reason = (describe_command(command) if command is not None
                             else (None, f"unreadable command ({shape(tool_input)})"))
    elif tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        described, reason = describe_file(tool, tool_input, cwd)
    elif tool == "apply_patch":
        described, reason = describe_patch(tool_input, cwd)
    else:
        return None, "tool not handled"
    if described is None:
        return None, reason
    name = os.environ.get("APPROVAL_GATE_SESSION") or os.path.basename(cwd.rstrip("\\/")) or "session"
    session = {"agent": agent, "name": clip(redact(name), 40), "cwd": clip(redact(cwd).replace("\\", "/"), 200, keep_tail=True)}
    return {"session": session, "tool": tool, **described}, ""


def log_summary(event: dict) -> str:
    """Redacted one-liner for the local log, for requests that never reach the relay."""
    tool, tool_input, cwd = event.get("tool_name"), event.get("tool_input"), event.get("cwd") or os.getcwd()
    text = str(tool)
    if isinstance(tool_input, dict) and isinstance(cwd, str):
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        if tool == "apply_patch":  # the command is the patch itself: log file names, never contents
            patch = tool_input.get("command")
            files = patch_files(patch) if isinstance(patch, str) else []
            more = f" +{len(files) - 1} more" if len(files) > 1 else ""
            first = display_path(os.path.normpath(os.path.join(cwd, files[0][1])), cwd) if files else ""
            text = f"apply_patch {first}{more}" if files else "apply_patch"
        elif shell_command(tool_input.get("command")) is not None:
            text = shell_command(tool_input["command"])
        elif isinstance(path, str):
            text = f"{tool} {display_path(os.path.normpath(os.path.join(cwd, path)), cwd)}"
    text = " ".join(redact(text).split())
    return clip("".join(c if c.isprintable() else "?" for c in text), SUMMARY_MAX)


def append_log(home: Path, **fields) -> None:
    from datetime import datetime
    line = json.dumps({"ts": datetime.now().astimezone().isoformat(timespec="seconds"), **fields}, ensure_ascii=False)
    with open(home / "gate.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def ask_relay(home: Path, request: dict) -> dict | None:
    """POST the request, long-poll for its outcome. Any failure returns None."""
    import urllib.request
    url = json.loads((home / "relay.json").read_text())["url"].rstrip("/")
    auth = {"Authorization": f"Bearer {(home / 'hook.key').read_text().strip()}"}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never route the gate through a proxy
    post = urllib.request.Request(f"{url}/requests", data=json.dumps(request).encode(), method="POST",
                                  headers={**auth, "Content-Type": "application/json"})
    with opener.open(post, timeout=5) as resp:
        rid = json.load(resp).get("id")
    if not isinstance(rid, str) or not ID_RE.fullmatch(rid):
        return None
    with opener.open(urllib.request.Request(f"{url}/requests/{rid}/wait", headers=auth), timeout=WAIT_TIMEOUT) as resp:
        final = json.load(resp)
    return final if isinstance(final, dict) and final.get("id") == rid else None


def decide(event: dict, home: Path, agent: str = "claude-code") -> dict | None:
    """The hook's whole policy. Returns the agent's decision object, or None for its own approval prompt."""
    if event.get("hook_event_name") != "PermissionRequest" or not (home / "armed").exists():
        return None
    request, reason = build_request(event, agent)
    if request is None:
        append_log(home, event="bypassed", tool=event.get("tool_name"), summary=log_summary(event), reason=reason)
        return None
    try:
        final = ask_relay(home, request)
    except Exception:  # relay down, restarted, slow or malformed: terminal prompt
        return None
    if final is None:
        return None
    if final.get("status") == "approved" and final.get("decision") == "approve":
        return {"behavior": "allow"}
    if final.get("status") == "denied" and final.get("decision") == "deny":
        device = final.get("decided_by") if isinstance(final.get("decided_by"), str) else "a paired device"
        return {"behavior": "deny", "message": f"The user denied this from Approval Gate on their glasses ({device}). "
                                               "Do not retry it; continue without it or ask the user."}
    return None


def agent_from_argv(argv: list[str]) -> str:
    if "--agent" in argv and argv.index("--agent") + 1 < len(argv) and argv[argv.index("--agent") + 1] in AGENTS:
        return argv[argv.index("--agent") + 1]
    return "claude-code"


def main() -> None:
    try:
        event = json.loads(sys.stdin.buffer.read() or b"{}")
        decision = decide(event, state_dir(), agent_from_argv(sys.argv)) if isinstance(event, dict) else None
    except Exception:
        return
    if decision:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}))


if __name__ == "__main__":
    main()
