#!/usr/bin/env python3
"""Record the 60-second Approval Gate demo, reproducibly. Windows only.

Left half of the capture region: Claude Code in Windows Terminal, working in a throwaway demo repo.
Right half: the Even Hub simulator. scripts/sim.py does the tapping, gate.log says when each beat
happens, and ffmpeg (gdigrab) records the region. `cut` then trims the raw take to the beat sheet
and burns in one caption per beat.

    python scripts/demo.py prepare             # demo repo + bare origin + hook (project-local only)
    python scripts/demo.py check               # prerequisites, without changing anything
    python scripts/demo.py record              # drive the take -> demo-take/raw.mp4 + beats.json
    python scripts/demo.py record --no-capture # rehearse: same driving, no ffmpeg
    python scripts/demo.py plan [--example]    # print the cut from beats.json (or nominal timings)
    python scripts/demo.py cut                 # -> demo-take/approval-gate-demo.mp4

Before `record`: relay running, plugin dev server running, simulator open with
`--automation-port 9898` and paired, ffmpeg on PATH (`winget install Gyan.FFmpeg.Essentials`).
Don't touch the keyboard or mouse during the take: the script types into the terminal.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import sim

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "demo-take"
MARKER = ".approval-gate-demo"
TITLE = "hub-orchestrator"
PROMPT = ("Release chore for this repo. Do exactly these three steps, one tool call each, in this order, "
          "with no other tool calls. Step 1, Bash: git push origin main. "
          "Step 2, Write: a file named .env containing the single line DEMO_MODE=1. "
          "Step 3, Bash: rm -r build. "
          "If a step is denied or rejected, do not retry it, just go on to the next step. "
          "When all three are done, reply with one short sentence.")
FOLLOW_UP = "Skip that step and go on."
LOG_COMMAND = f"!python {(ROOT / 'relay' / 'gatectl.py').as_posix()} log -n 3"

# The beat sheet. Each beat's footage is a window of the raw take anchored on a moment the driver
# marks: (key, seconds, caption, anchor mark, offset from the mark).
BEATS = [
    ("away", 8, "I'm away from the desk.", "push", -8),
    ("prompt", 7, "The permission prompt came to me.", "push", 0),
    ("detail", 5, "Full command on demand.", "swipe", 0),
    ("approve", 5, "One tap.", "tap", 0),
    ("continue", 10, "Claude never stopped.", "tap", 5),
    ("risky", 10, "Risky things never reach the glasses.", "env", -1),
    ("deny", 10, "Two taps to say no.", "rm", -1),
    ("log", 5, "Logged, open-source, built on Even Hub.", "log", 0.5),
]
# When the driver acts, relative to its marks. Chosen so each beat's window contains its action.
PROMPT_HOLD = 7     # swipe down this long after the request reaches the relay
DETAIL_HOLD = 5     # tap this long after the swipe
DIALOG_HOLD = 9     # answer the terminal's own .env dialog (Esc) this long after it appears
DENY_AFTER = 4      # double-tap this long after the rm request reaches the relay
FINISH_WAIT = 10    # let Claude report the deny before typing the log command
LOG_HOLD = 6
CAPTURE_LATENCY = 0.25  # gdigrab's first frame lands roughly this long after launch
EXAMPLE_MARKS = {"push": 12.0, "swipe": 19.0, "tap": 24.0, "env": 48.0, "rm": 70.0, "log": 95.0}
CAPTION_STYLE = ("FontName=Segoe UI,FontSize=26,PrimaryColour=&H00FFFFFF,OutlineColour=&H99000000,"
                 "BorderStyle=3,Outline=8,Shadow=0,Alignment=2,MarginV=36")


# ---- the cut ----------------------------------------------------------------------

def plan_cut(marks: dict[str, float]) -> list[dict]:
    """Beat sheet + marks -> source window and output time for every beat."""
    cut, out = [], 0.0
    for key, seconds, caption, anchor, offset in BEATS:
        start = marks[anchor] + offset
        end = start + seconds
        start = max(0.0, start)  # a fast start can leave less than 8 s of lead-in
        cut.append({"beat": key, "caption": caption, "src": (round(start, 3), round(end, 3)),
                    "out": (round(out, 3), round(out + end - start, 3))})
        out += end - start
    return cut


def segments(cut: list[dict]) -> list[tuple[float, float]]:
    """Merge beats whose footage is contiguous, so the video only cuts where the story jumps."""
    merged: list[list[float]] = []
    for beat in cut:
        start, end = beat["src"]
        if merged and abs(merged[-1][1] - start) < 0.05:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def srt(cut: list[dict]) -> str:
    def ts(t: float) -> str:
        ms = round(t * 1000)
        return f"{ms // 3_600_000:02}:{ms // 60_000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"
    return "\n".join(f"{i}\n{ts(b['out'][0])} --> {ts(b['out'][1])}\n{b['caption']}\n" for i, b in enumerate(cut, 1))


def cut_command(ffmpeg: str, cut: list[dict], raw: str = "raw.mp4", captions: str = "captions.srt",
                out: str = "approval-gate-demo.mp4") -> list[str]:
    """ffmpeg argv, to run with cwd = the take folder (keeps the subtitles path free of escaping)."""
    parts = [f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[s{i}]" for i, (s, e) in enumerate(segments(cut))]
    labels = "".join(f"[s{i}]" for i in range(len(parts)))
    graph = ";".join(parts) + f";{labels}concat=n={len(parts)}:v=1:a=0[cat];[cat]scale=1920:-2:flags=lanczos,subtitles={captions}:force_style='{CAPTION_STYLE}'[v]"
    return [ffmpeg, "-hide_banner", "-y", "-i", raw, "-filter_complex", graph, "-map", "[v]",
            "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]


def print_plan(cut: list[dict]) -> None:
    print(f"{'out':>11}  {'source (raw take)':>17}  {'beat':<9} caption")
    for b in cut:
        (o1, o2), (s1, s2) = b["out"], b["src"]
        print(f"{o1:5.1f}–{o2:5.1f}  {s1:7.1f}–{s2:7.1f}  {b['beat']:<9} {b['caption']}")
    overlaps = [(a["beat"], b["beat"]) for a, b in zip(cut, cut[1:]) if b["src"][0] < a["src"][1] - 0.05]
    total = cut[-1]["out"][1]
    print(f"\n{len(segments(cut))} source segments, {total:.1f} s total"
          + (f". Overlapping footage: {overlaps}" if overlaps else ""))


# ---- the demo repo -------------------------------------------------------------------

def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def remove_tree(path: Path) -> None:
    def make_writable(func, p, _exc):  # git marks object files read-only
        os.chmod(p, stat.S_IWRITE)
        func(p)
    shutil.rmtree(path, onexc=make_writable)


def prepare(repo: Path) -> None:
    """A fresh repo with one unpushed commit, a build/ dir to delete, and the hook in its local settings."""
    origin = repo.with_name(repo.name + "-origin.git")
    for path in (repo, origin):
        if path.exists():
            if not (path / MARKER).exists():
                sys.exit(f"{path} exists and was not made by this script; refusing to delete it")
            remove_tree(path)
    origin.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    (origin / MARKER).write_text("made by scripts/demo.py\n")
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Demo")
    git(repo, "config", "user.email", "demo@example.invalid")
    (repo / MARKER).write_text("made by scripts/demo.py\n")
    (repo / ".git" / "info" / "exclude").write_text(f"{MARKER}\n.claude/\n")
    (repo / ".gitignore").write_text("build/\n.env\n")
    (repo / "README.md").write_text("# hub-orchestrator\n\nRoutes jobs between local agents.\n")
    (repo / "VERSION").write_text("1.4.1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Initial commit")
    git(repo, "remote", "add", "origin", origin.as_posix())
    git(repo, "push", "-q", "-u", "origin", "main")
    (repo / "VERSION").write_text("1.4.2\n")
    git(repo, "commit", "-q", "-am", "Release 1.4.2")
    (repo / "build").mkdir()
    (repo / "build" / "hub-1.4.2.whl").write_text("demo artifact\n")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "install_hook.py"),
                    "--settings", str(repo / ".claude" / "settings.local.json")], check=True, capture_output=True)


# ---- windows, keys and the screen ---------------------------------------------------

class INPUT_KEY(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT_MOUSE(ctypes.Structure):  # only here so the union has the size SendInput expects
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", INPUT_KEY), ("mi", INPUT_MOUSE)]
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


VK_RETURN, VK_ESCAPE, VK_MENU = 0x0D, 0x1B, 0x12
KEYUP, UNICODE = 0x2, 0x4
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class Desktop:
    def __init__(self) -> None:
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # physical pixels, like the capture
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND] + [ctypes.c_int] * 4 + [wintypes.UINT]
        self.user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]

    def windows(self) -> list[tuple[int, str, str]]:
        """Visible top-level windows as (hwnd, title, exe name)."""
        found = []

        def visit(hwnd, _):
            if self.user32.IsWindowVisible(hwnd):
                title = ctypes.create_unicode_buffer(512)
                self.user32.GetWindowTextW(hwnd, title, 512)
                pid = wintypes.DWORD()
                self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                found.append((hwnd, title.value, self.exe_of(pid.value)))
            return True

        self.user32.EnumWindows(WNDENUMPROC(visit), 0)
        return found

    def exe_of(self, pid: int) -> str:
        handle = self.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            size, name = wintypes.DWORD(1024), ctypes.create_unicode_buffer(1024)
            return Path(name.value).name.lower() if self.kernel32.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)) else ""
        finally:
            self.kernel32.CloseHandle(handle)

    def find(self, exe: str, title: str | None = None, timeout: float = 20) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for hwnd, text, name in self.windows():
                if name == exe and text and (title is None or text == title):
                    return hwnd
            time.sleep(0.25)
        raise RuntimeError(f"no window from {exe}" + (f" titled {title!r}" if title else ""))

    def place(self, hwnd: int, x: int, y: int, w: int, h: int) -> None:
        self.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        self.user32.SetWindowPos(hwnd, None, x, y, w, h, 0x0040)  # SWP_SHOWWINDOW

    def focus(self, hwnd: int) -> None:
        self.keys([(VK_MENU, 0), (VK_MENU, KEYUP)])  # a key event lets this process take the foreground
        self.user32.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        if self.user32.GetForegroundWindow() != hwnd:
            raise RuntimeError("could not bring the terminal to the foreground; is someone using the keyboard?")

    def keys(self, strokes: list[tuple[int, int]], scan: bool = False) -> None:
        events = (INPUT * len(strokes))()
        for event, (code, flags) in zip(events, strokes):
            event.type = 1  # INPUT_KEYBOARD
            event.u.ki = INPUT_KEY(0 if scan else code, code if scan else 0, flags, 0, 0)
        self.user32.SendInput(len(strokes), events, ctypes.sizeof(INPUT))

    def type(self, hwnd: int, text: str) -> None:
        self.focus(hwnd)
        for ch in text:
            if ch == "\n":
                self.keys([(VK_RETURN, 0), (VK_RETURN, KEYUP)])
            else:
                self.keys([(ord(ch), UNICODE), (ord(ch), UNICODE | KEYUP)], scan=True)
            time.sleep(0.03)

    def press(self, hwnd: int, vk: int) -> None:
        self.focus(hwnd)
        self.keys([(vk, 0), (vk, KEYUP)])


def primary_region() -> tuple[int, int, int, int]:
    user32 = ctypes.windll.user32
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    return 0, 0, min(width, 2560), min(height, 1440)  # the cut scales this to 1920x1080


# ---- the take --------------------------------------------------------------------------

def state_dir() -> Path:
    if os.environ.get("APPROVAL_GATE_HOME"):
        return Path(os.environ["APPROVAL_GATE_HOME"])
    return Path(os.environ.get("APPDATA") or Path.home() / ".config") / "approval-gate"


def relay_health() -> dict:
    url = json.loads((state_dir() / "relay.json").read_text())["url"]
    return json.load(urllib.request.urlopen(f"{url}/health", timeout=3))


class LogTail:
    """New gate.log events since the take started."""

    def __init__(self, path: Path) -> None:
        self.path, self.offset = path, path.stat().st_size if path.exists() else 0

    def wait(self, what: str, match, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                for raw in f.read().splitlines():
                    self.offset += len(raw) + 1
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    if match(event):
                        return event
            time.sleep(0.1)
        raise TimeoutError(f"gave up waiting for {what}")


def check(args) -> bool:
    ok = True

    def report(good: bool, label: str, detail: str = "", fix: str = "") -> None:
        nonlocal ok
        ok &= good
        note = detail if good or not fix else fix
        print(f"  {'ok ' if good else 'NO '} {label}{f'  ({note})' if note else ''}")

    try:
        health = relay_health()
        report(True, "relay", f"armed={health['armed']}")
    except (OSError, ValueError, KeyError) as err:
        report(False, "relay running", str(err))
    report(sim.ping(), "simulator automation API on :9898")
    desk = Desktop()
    names = {name for _, _, name in desk.windows()}
    report("evenhub-simulator.exe" in names, "simulator window")
    report(shutil.which("wt") is not None, "Windows Terminal (wt.exe)")
    report(shutil.which("claude") is not None, "claude on PATH")
    report((args.repo / MARKER).exists(), f"demo repo at {args.repo}", fix="run `demo.py prepare`")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        filters = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        report(" subtitles " in filters, "ffmpeg with the subtitles filter (libass)", ffmpeg)
    else:
        report(args.no_capture, "ffmpeg", fix="winget install Gyan.FFmpeg.Essentials")
    dpi = ctypes.windll.user32.GetDpiForSystem()
    report(dpi == 96, "display scaling 100%",
           fix=f"{dpi * 100 // 96}%: gdigrab may capture a shifted region. Set 100% for the take, or check a test clip")
    width, height = ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
    print(f"  capture region {args.region} of a {width}x{height} screen: terminal left half, simulator right half")
    return ok


def record(args) -> None:
    if not check(args):
        sys.exit("Fix the items marked NO first.")
    prepare(args.repo)  # every take starts from the same repo state
    OUT.mkdir(exist_ok=True)
    x, y, w, h = args.region
    desk = Desktop()
    desk.place(desk.find("evenhub-simulator.exe"), x + w // 2, y, w - w // 2, h)
    if not relay_health()["armed"]:
        sim.send("click")  # gate off: a tap arms it
        time.sleep(2.5)
    if not relay_health()["armed"]:
        sys.exit("The gate did not arm. Is the plugin paired and showing GATE OFF?")

    take = OUT / "take.cmd"  # cmd keeps the prompt's quoting intact through wt
    take.write_text(f'@echo off\nclaude --permission-mode default "{PROMPT}"\n')  # text mode writes CRLF
    tail = LogTail(state_dir() / "gate.log")
    subprocess.Popen(["wt", "-w", "new", "new-tab", "--title", TITLE, "--suppressApplicationTitle",
                      "-d", str(args.repo), "cmd", "/c", str(take)])
    terminal = desk.find("windowsterminal.exe", TITLE)
    desk.place(terminal, x, y, w // 2, h)

    capture = None
    if not args.no_capture:
        capture = subprocess.Popen([shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                                    "-f", "gdigrab", "-framerate", "30", "-draw_mouse", "0",
                                    "-offset_x", str(x), "-offset_y", str(y), "-video_size", f"{w}x{h}", "-i", "desktop",
                                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                                    str(OUT / "raw.mp4")], stdin=subprocess.PIPE)
    t0 = time.monotonic() + (CAPTURE_LATENCY if capture else 0)
    marks: dict[str, float] = {}

    def mark(name: str) -> None:
        marks[name] = round(time.monotonic() - t0, 3)
        print(f"  {marks[name]:6.1f}s  {name}", flush=True)

    def at(name: str, delay: float) -> None:
        time.sleep(max(0.0, t0 + marks[name] + delay - time.monotonic()))

    try:
        tail.wait("the git push prompt", lambda e: e["event"] == "request" and e["summary"].startswith("git push"), 180)
        mark("push")
        at("push", PROMPT_HOLD)
        sim.send("down")
        mark("swipe")
        at("swipe", DETAIL_HOLD)
        sim.send("click")
        mark("tap")
        tail.wait("the approval", lambda e: e["event"] == "approved", 10)
        tail.wait("the .env write", lambda e: e["event"] == "bypassed" and e.get("tool") == "Write", 180)
        mark("env")
        at("env", DIALOG_HOLD)
        desk.press(terminal, VK_ESCAPE)  # "No" on Claude Code's own dialog
        time.sleep(1.5)
        desk.type(terminal, FOLLOW_UP + "\n")
        tail.wait("the rm prompt", lambda e: e["event"] == "request" and e["summary"].startswith("rm "), 180)
        mark("rm")
        at("rm", DENY_AFTER)
        sim.send("double")
        tail.wait("the deny", lambda e: e["event"] == "denied", 10)
        time.sleep(FINISH_WAIT)
        desk.type(terminal, LOG_COMMAND + "\n")
        mark("log")
        time.sleep(LOG_HOLD)
    finally:
        if capture:
            capture.communicate(b"q", timeout=30)  # ffmpeg finalizes the file on q
        (OUT / "beats.json").write_text(json.dumps({"recorded_at": datetime.now().astimezone().isoformat(),
                                                    "region": args.region, "marks": marks, "prompt": PROMPT}, indent=2))
    print_plan(plan_cut(marks))
    print(f"\nRaw take: {OUT / 'raw.mp4'}. Next: python scripts/demo.py cut")


def cut(args) -> None:
    marks = json.loads((OUT / "beats.json").read_text())["marks"]
    beats = plan_cut(marks)
    (OUT / "captions.srt").write_text(srt(beats), encoding="utf-8")
    command = cut_command(shutil.which("ffmpeg") or "ffmpeg", beats)
    print_plan(beats)
    subprocess.run(command, cwd=OUT, check=True)
    print(f"\n{OUT / 'approval-gate-demo.mp4'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("prepare", "check", "record", "plan", "cut"))
    parser.add_argument("--repo", type=Path, default=Path(ROOT.anchor) / "demo" / TITLE,
                        help="demo repo, recreated on every take (default: <drive>/demo/hub-orchestrator)")
    parser.add_argument("--region", type=lambda s: tuple(int(v) for v in s.split(",")), default=None,
                        help="capture region x,y,w,h in physical pixels (default: top-left of the primary screen, at most 2560x1440)")
    parser.add_argument("--no-capture", action="store_true", help="drive the take without recording")
    parser.add_argument("--example", action="store_true", help="plan: use nominal timings instead of beats.json")
    args = parser.parse_args()
    args.region = args.region or primary_region()
    sys.stdout.reconfigure(encoding="utf-8")

    if args.command == "prepare":
        prepare(args.repo)
        print(f"Demo repo ready: {args.repo}\nOne time only: run `claude` there, accept the trust prompt, then /exit.")
    elif args.command == "check":
        sys.exit(0 if check(args) else 1)
    elif args.command == "record":
        record(args)
    elif args.command == "plan":
        marks = EXAMPLE_MARKS if args.example else json.loads((OUT / "beats.json").read_text())["marks"]
        print_plan(plan_cut(marks))
        print("\n" + " ".join(f'"{a}"' if " " in a or ";" in a else a for a in cut_command("ffmpeg", plan_cut(marks))))
    else:
        cut(args)


if __name__ == "__main__":
    main()
