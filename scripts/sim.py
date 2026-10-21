#!/usr/bin/env python3
"""Drive the Even Hub simulator's automation API, for tests and the demo recording.

Start the simulator with `--automation-port 9898` (`npm run simulate` in plugin/ does), then:

    python scripts/sim.py click            # tap: approve / arm
    python scripts/sim.py double           # double-tap: deny / disarm
    python scripts/sim.py down | up        # swipe: full command / summary
    python scripts/sim.py shot out.png     # glasses framebuffer as PNG
    python scripts/sim.py console          # the plugin's console output

scripts/demo.py imports this module and uses the same calls.
"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:9898"
ACTIONS = {"click": "click", "double": "double_click", "down": "down", "up": "up"}


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


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in (*ACTIONS, "shot", "console"):
        sys.exit(__doc__)
    cmd = sys.argv[1]
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
