#!/usr/bin/env python3
"""Approval Gate admin commands. They work whether or not the relay is running.

    python relay/gatectl.py pair --label pixel   # mint a device token for the plugin
    python relay/gatectl.py devices              # list paired devices
    python relay/gatectl.py revoke dev_1a2b3c    # unpair one
    python relay/gatectl.py arm | disarm         # PC-side switch; bind disarm to a hotkey
    python relay/gatectl.py status               # armed state, relay URL, last log lines
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from urllib.parse import quote

import relay as R


def pair(home, args) -> None:
    devices = R.load_devices(home)
    token, dev_id = f"agt_{secrets.token_urlsafe(32)}", f"dev_{secrets.token_hex(3)}"
    devices[dev_id] = {"label": args.label, "sha256": R.sha256(token), "created_at": R.now_iso()}
    R.save_devices(home, devices)
    R.append_log(home, "pair", device=dev_id, label=args.label)
    try:
        relay_url = args.relay_url or json.loads((home / "relay.json").read_text())["url"]
    except (OSError, ValueError, KeyError):
        relay_url = "http://127.0.0.1:8797"
    link = f"{args.plugin_url.rstrip('/')}/?relay={quote(relay_url, safe='')}&token={token}"
    print(f"Paired {dev_id} ({args.label}). The token is shown once. Revoke with: gatectl.py revoke {dev_id}\n")
    print(f"  relay  {relay_url}\n  token  {token}\n")
    print(f"Simulator:\n  npx evenhub-simulator \"{link}\"\n")
    print(f"Phone (dev sideload; scan with the Even app):\n  npx evenhub qr --url \"{link}\"")


def main() -> None:
    parser = argparse.ArgumentParser(description="Approval Gate admin")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pair", help="mint a device token for the glasses plugin")
    p.add_argument("--label", default="phone")
    p.add_argument("--relay-url", help="URL the phone uses to reach the relay (default: the running relay's)")
    p.add_argument("--plugin-url", default="http://127.0.0.1:5180", help="where the plugin is served")
    sub.add_parser("devices", help="list paired devices")
    p = sub.add_parser("revoke", help="unpair a device")
    p.add_argument("device_id")
    sub.add_parser("arm", help="arm the gate from the PC")
    sub.add_parser("disarm", help="disarm the gate from the PC")
    sub.add_parser("status", help="armed state, relay and the last log lines")
    args = parser.parse_args()

    home = R.state_dir()
    home.mkdir(parents=True, exist_ok=True)
    if args.cmd == "pair":
        pair(home, args)
    elif args.cmd == "devices":
        for dev_id, dev in R.load_devices(home).items():
            print(f"{dev_id}  {dev['label']:<16} paired {dev['created_at']}")
    elif args.cmd == "revoke":
        devices = R.load_devices(home)
        if devices.pop(args.device_id, None) is None:
            sys.exit(f"No device {args.device_id}")
        R.save_devices(home, devices)
        R.append_log(home, "revoke", device=args.device_id)
        print(f"Revoked {args.device_id}")
    elif args.cmd in ("arm", "disarm"):
        R.set_armed(home, args.cmd == "arm", by="pc")
        print("Gate ARMED" if args.cmd == "arm" else "Gate disarmed")
    elif args.cmd == "status":
        relay_file = home / "relay.json"
        print(f"armed    {(home / 'armed').exists()}")
        print(f"relay    {json.loads(relay_file.read_text())['url'] if relay_file.exists() else 'not running'}")
        print(f"devices  {len(R.load_devices(home))}")
        print(f"state    {home}")
        log = home / "gate.log"
        for line in log.read_text(encoding="utf-8").splitlines()[-5:] if log.exists() else []:
            print(f"  {line}")


if __name__ == "__main__":
    main()
