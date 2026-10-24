#!/usr/bin/env python3
"""A short, slow cut of the live demo: the Deck, Running, and a git push prompt cutting in over it,
approved with one tap. About 14 s. Windows.

    python scripts/demo_stage.py --reset && python scripts/demo_short.py   # record and cut
    python scripts/demo_short.py --cut                                     # re-cut the last short take

The same rig as demo_run.py (live Claude Code and Codex sessions, real hooks, relay and plugin, the
simulator's own framebuffer and Chrome screencasts), with every screen held long enough to read.
Its raw take goes to C:/deck-sandbox/demo/take-short, so the full demo's take stays re-cuttable.
Writes demo/command-deck-short.mp4.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import demo_run as R  # must come first: sets APPROVAL_GATE_HOME before sim loads
from demo_run import DEMO, FPS, GLASSES, HUB, OUT, sim
from demo_stage import git

TAKE = DEMO / "take-short"
FINAL = OUT / "command-deck-short.mp4"
CAPTIONS = [
    "Three AI agents. Two projects. One glance.",
    "What's running, where, for how long.",
    "Prompts cut through any screen. One tap.",
]
DECK_HOLD = 3.0  # on the Deck before the cursor moves
CURSOR_HOLD = 1.0  # on the Running row before the tap
ASK_AFTER = 1.2  # on Running before the hub session is asked: it can ask back in 2 s
PROMPT_HOLD = 2.6  # the prompt, long enough to read, before the tap
RUNNING_MAX = 3.4  # Running in the cut: past this, the wait for Claude to ask is cut out
RUNNING_MIN = 2.5  # every caption stays up this long or more
LEAD = 0.6  # Running again, just before the prompt, after that cut
TAIL = 2.6  # back on Running after the approval, as long as the clip fits
TAIL_MIN = 1.6
LONGEST = 15.0


def pieces(m: dict[str, float]) -> list[tuple[float, float, int]]:
    """Windows of the take as (start, end, caption), in seconds into it. To keep the clip within
    LONGEST, the last screen's hold shrinks first, then Running's (when its wait is cut anyway)."""
    cut_wait = m["prompt"] - m["running"] > RUNNING_MAX  # Claude took a while to ask: skip the middle
    running = RUNNING_MAX if cut_wait else m["prompt"] - m["running"]
    start = m["prompt"] - LEAD if cut_wait else m["prompt"]
    tail = min(TAIL, m["end"] - m["restored"])
    over = m["running"] - m["deck"] + running + m["restored"] - start + tail - LONGEST
    if over > 0:
        cut = min(over, max(0.0, tail - TAIL_MIN))
        tail, over = tail - cut, over - cut
    if over > 0 and cut_wait:
        running -= min(over, running - RUNNING_MIN)
    out = [(m["deck"], m["running"], 0), (m["running"], m["running"] + running, 1), (start, m["restored"] + tail, 2)]
    return [(round(s, 3), round(e, 3), c) for s, e, c in out]


def stage_push() -> None:
    """One hub commit waits to be pushed, for the clip's approval to send. demo_stage.py --reset takes it out."""
    (HUB / "CHANGELOG.md").write_text("# Changelog\n\nRetries back off, capped at 30 s.\n", encoding="utf-8")
    git("add", "CHANGELOG.md", cwd=HUB)
    git("commit", "-qm", "Add the changelog", cwd=HUB)


def pushed() -> bool:
    head = lambda repo: subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "main"], capture_output=True, text=True
    ).stdout.strip()
    subject = subprocess.run(
        ["git", "-C", str(HUB), "log", "-1", "--format=%s"], capture_output=True, text=True
    ).stdout.strip()
    return subject == "Add the changelog" and head(DEMO / "demo-hub-origin.git") == head(HUB)


def drive() -> dict:
    marks: dict[str, float] = {}
    mark = lambda name: marks.__setitem__(name, time.time())
    term = R.Terminal()
    server = R.pane_server(term)
    run = sim.Run("short")
    sim.at_root(run)
    stage_push()
    capture = R.Capture(TAKE)
    mark("start")
    hub = R.Claude("hub", HUB, term, marks)
    glasses = R.Claude(
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
    codex = R.Codex(
        HUB,
        "Run the test suite right away, before anything else: python -m unittest discover -s tests. "
        "Don't change any files and don't try to fix anything. Then reply with one short sentence.",
        term,
        marks,
    )
    term.show("codex")
    try:
        # The Deck: three sessions, two running (the render and the suite), the hub session idle.
        R.wait_for(
            lambda: R.counts() == (3, 2) and marks.get("hub-result-1"), 90, "3 sessions, 2 running, the hub idle", run
        )
        mark("deck")
        time.sleep(DECK_HOLD)
        # One row down to Running, a beat on it, then in.
        run.goto("running")
        time.sleep(CURSOR_HOLD)
        run.do("click", r"page running:focus: depth 2", pause=0)
        mark("running")
        # The hub session pushes; its prompt cuts in over Running. Its terminal asks too.
        term.show("hub")
        time.sleep(ASK_AFTER)
        hub.send("Run exactly one Bash command: git push origin main. Then reply in one short sentence.")
        run.expect(r"\[deck\] prompt req_\w+ over running:focus:", 60)
        mark("prompt")
        time.sleep(PROMPT_HOLD)
        run.do("click", r"rebuild result:APPROVED", pause=0)
        mark("approved")
        run.expect(r"restored running:focus:", 6)
        mark("restored")
        R.wait_for(lambda: marks.get("hub-result-2"), 40, "Claude's reply after the push", run)
        time.sleep(max(1.0, marks["restored"] + TAIL + 0.5 - time.time()))
        mark("end")
    except RuntimeError as err:
        print(f"Stopped: {err}")
    finally:
        TAKE.mkdir(parents=True, exist_ok=True)
        for name in term.lines:
            (TAKE / f"transcript-{name}.txt").write_text(
                "\n".join(entry["t"] for entry in term.lines[name]), encoding="utf-8"
            )
        for session in (hub, glasses):
            session.close()
        if codex.proc.poll() is None:
            codex.proc.kill()
        capture.finish()
        server.shutdown()
        sim.gate("disarm")
    start = marks["start"]
    late = [
        (round(a - start, 2), round(b - start, 2))
        for a, b in capture.late
        if b > start and a < marks.get("end", time.time())
    ]
    worst = max((b - a for a, b in late), default=0.0)
    checks = {
        "every step happened, no console errors": not run.failures,
        "the push reached demo-hub's origin": pushed(),
        f"every glasses change captured within 1 s (worst {worst:.1f} s)": worst <= 1.0,
    }
    marks = {k: round(v - start, 3) for k, v in marks.items()}
    return {"marks": marks, "start_ms": int(start * 1000), "checks": checks, "failures": run.failures, "late": late}


def cut(take: dict) -> float:
    marks, t0 = take["marks"], int(take["start_ms"])
    work = TAKE / "work"
    work.mkdir(exist_ok=True)
    end = t0 + int((marks["end"] + 1) * 1000)
    for name, size, flags in (
        ("glasses", "1152:576", "neighbor"),
        ("terminal", "768:540", "lanczos"),
        ("present", "768:540", "lanczos"),
    ):
        R.frames_to_video(TAKE / name, t0, end, work / f"{name}.mp4", size, flags)
    parts = []
    for i, (s, e, c) in enumerate(pieces(marks)):
        caption = work / f"caption-{c}.txt"
        caption.write_text(CAPTIONS[c], encoding="utf-8")
        out = work / f"piece-{i}.mp4"
        R.compose(work, "present", s, e, caption, out)
        parts.append(out)
    listing = work / "pieces.txt"
    listing.write_text("\n".join(f"file '{p.as_posix()}'" for p in parts))
    total = round(sum(e - s for s, e, _ in pieces(marks)), 2)
    R.ff(
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(listing),
        "-t",
        f"{total:.3f}",
        "-vf",
        f"fade=t=in:st=0:d=0.4,fade=t=out:st={total - 0.6:.3f}:d=0.6",
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(FPS),
        "-movflags",
        "+faststart",
        str(FINAL),
    )
    print(f"Cut {FINAL} ({total:.1f} s)")
    at = 0.0
    for s, e, c in pieces(marks):
        print(f"  {at:5.1f}-{at + e - s:5.1f}  {CAPTIONS[c]}")
        at += e - s
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Record and cut the short Command Deck demo.")
    parser.add_argument(
        "--cut", action="store_true", help="re-cut the last short take (C:/deck-sandbox/demo/take-short)"
    )
    args = parser.parse_args()
    if args.cut:
        cut(json.loads((TAKE / "take.json").read_text()))
        return
    OUT.mkdir(exist_ok=True)
    result = drive()
    (TAKE / "take.json").write_text(json.dumps(result, indent=2))
    try:
        parts = pieces(result["marks"])
        total = round(sum(e - s for s, e, _ in parts), 2)
        shortest = min(e - s for s, e, _ in parts)
        result["checks"][f"the cut runs 10-{LONGEST:.0f} s ({total:.1f} s)"] = 10.0 <= total <= LONGEST
        result["checks"][f"every caption stays up {RUNNING_MIN} s or more"] = shortest >= RUNNING_MIN
    except KeyError as err:
        result["checks"][f"no cut plan: mark {err} missing"] = False
    for name, ok in result["checks"].items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    for failure in result["failures"]:
        print(f"    - {failure}")
    ok = all(result["checks"].values())
    if ok:
        cut(result)
    else:
        print("Not cut: the take failed a check. Reset and record again.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
