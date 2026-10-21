#!/usr/bin/env python3
"""Drive the Even Hub simulator's automation API, for tests and the demo recording.

Start the simulator with `--automation-port 9898` (`npm run simulate` in plugin/ does), then:

    python scripts/sim.py click            # tap: approve / arm
    python scripts/sim.py double           # double-tap: deny / disarm
    python scripts/sim.py down | up        # swipe: full command / summary
    python scripts/sim.py shot out.png     # glasses framebuffer as PNG
    python scripts/sim.py console          # the plugin's console output
"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:9898"
ACTIONS = {"click": "click", "double": "double_click", "down": "down", "up": "up"}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in (*ACTIONS, "shot", "console"):
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd in ACTIONS:
        body = json.dumps({"action": ACTIONS[cmd]}).encode()
        req = urllib.request.Request(f"{BASE}/api/input", data=body, headers={"Content-Type": "application/json"})
        print(urllib.request.urlopen(req).read().decode())
    elif cmd == "shot":
        out = sys.argv[2] if len(sys.argv) > 2 else "glasses.png"
        with open(out, "wb") as f:
            f.write(urllib.request.urlopen(f"{BASE}/api/screenshot/glasses").read())
        print(out)
    else:
        for entry in json.load(urllib.request.urlopen(f"{BASE}/api/console"))["entries"]:
            print(entry["level"], entry["message"])


if __name__ == "__main__":
    main()
