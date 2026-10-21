#!/usr/bin/env python3
"""Approval Gate relay.

The only process the phone talks to and the only thing that changes a request's
status. Pending requests live in memory: a restart drops them, and every hook
waiting on one falls back to the terminal prompt. Stdlib only.

    python relay/relay.py                        # http://127.0.0.1:8797
    python relay/gatectl.py pair --label pixel   # mint a device token
    python relay/gatectl.py disarm               # PC-side kill switch
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import select
import socket
import sys
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse

VERSION = "0.1.0"
EXPIRY = {"low": 90, "medium": 90, "high": 45}  # seconds
LIMITS = {"tool": 40, "summary": 60, "detail": 200, "agent": 40, "name": 40, "cwd": 200}
MAX_BODY = 16 * 1024
MAX_PENDING = 20
KEEP_FINISHED = 600  # seconds a decided request stays answerable to a late /wait
PRESENCE = 10  # seconds since a device last polled /pending before the gate stops accepting requests
WAIT_RE = re.compile(r"/requests/(req_[0-9A-Z]{26})/wait")
DECIDE_RE = re.compile(r"/requests/(req_[0-9A-Z]{26})/decide")
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CORS = {
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Expose-Headers": "X-Gate-Armed, X-Gate-Waiting, X-Gate-Session",
    "Access-Control-Allow-Private-Network": "true",
    "Access-Control-Max-Age": "600",
}


def state_dir() -> Path:
    if os.environ.get("APPROVAL_GATE_HOME"):
        return Path(os.environ["APPROVAL_GATE_HOME"])
    return Path(os.environ.get("APPDATA") or Path.home() / ".config") / "approval-gate"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def ulid() -> str:
    n = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    return "".join(CROCKFORD[(n >> s) & 31] for s in range(125, -1, -5))


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def append_log(home: Path, event: str, **fields) -> None:
    line = json.dumps({"ts": now_iso(), "event": event, **fields}, ensure_ascii=False)
    with open(home / "gate.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def set_armed(home: Path, on: bool, by: str) -> None:
    flag = home / "armed"
    if on == flag.exists():
        return
    if on:
        flag.write_text(now_iso())
    else:
        flag.unlink(missing_ok=True)
    append_log(home, "arm" if on else "disarm", by=by)


def load_devices(home: Path) -> dict:
    try:
        return json.loads((home / "devices.json").read_text())
    except (OSError, ValueError):
        return {}


def save_devices(home: Path, devices: dict) -> None:
    tmp = home / "devices.json.tmp"
    tmp.write_text(json.dumps(devices, indent=2))
    tmp.replace(home / "devices.json")


class Relay:
    def __init__(self, home: Path, expiry: dict[str, int] = EXPIRY, presence: float = PRESENCE):
        self.home, self.expiry, self.presence = home, expiry, presence
        home.mkdir(parents=True, exist_ok=True)
        key_file = home / "hook.key"
        if not key_file.exists():
            key_file.write_text(secrets.token_urlsafe(32))
        self.hook_key = key_file.read_text().strip()
        self.cond = threading.Condition()
        self.requests: dict[str, dict] = {}  # id -> contract shape, insertion = age order
        self.private: dict[str, dict] = {}   # id -> nonce, deadline, finished (never sent to the hook)
        self.last_session = ""
        self.last_poll = float("-inf")  # monotonic time a device last polled /pending

    @property
    def armed(self) -> bool:
        return (self.home / "armed").exists()

    @property
    def watched(self) -> bool:
        """Is a device polling right now? Nobody watching means the terminal prompt, not a 90 s stall."""
        return time.monotonic() - self.last_poll <= self.presence

    def device_for(self, token: str) -> str | None:
        digest = sha256(token)
        for dev_id, dev in load_devices(self.home).items():
            if hmac.compare_digest(dev.get("sha256", ""), digest):
                return dev_id
        return None

    def log(self, event: str, req: dict) -> None:
        append_log(self.home, event, id=req["id"], session=req["session"]["name"], tool=req["tool"],
                   summary=req["summary"], risk=req["risk"], status=req["status"], decided_by=req["decided_by"])

    def create(self, body: dict) -> dict | str:
        """Store a hook's request. Returns the stored request, or an error string."""
        try:
            s = body["session"]
            fields = {k: body[k] for k in ("tool", "summary", "detail", "risk")}
            session = {k: s[k] for k in ("agent", "name", "cwd")}
        except (KeyError, TypeError):
            return "missing field"
        merged = {**fields, **session}
        if not all(isinstance(v, str) for v in merged.values()):
            return "fields must be strings"
        if any(len(merged[k]) > n for k, n in LIMITS.items() if k in merged) or not fields["summary"]:
            return "field too long or summary empty"
        if fields["risk"] not in self.expiry:
            return "risk must be low, medium or high"
        with self.cond:
            if not self.watched:
                return "no device listening"
            if sum(r["status"] == "pending" for r in self.requests.values()) >= MAX_PENDING:
                return "too many pending requests"
            ttl = self.expiry[fields["risk"]]
            created = datetime.now().astimezone()
            req = {"id": f"req_{ulid()}", "created_at": created.isoformat(timespec="seconds"),
                   "expires_at": (created + timedelta(seconds=ttl)).isoformat(timespec="seconds"),
                   "session": session, **fields, "status": "pending", "decision": None, "decided_by": None, "decided_at": None}
            self.requests[req["id"]] = req
            self.private[req["id"]] = {"nonce": secrets.token_urlsafe(16), "deadline": time.monotonic() + ttl, "finished": 0.0}
            self.last_session = session["name"]
            self.log("request", req)
            self.cond.notify_all()
            return req

    def _finish(self, rid: str, status: str, decision: str | None = None, device: str | None = None) -> None:
        req = self.requests[rid]
        req.update(status=status, decision=decision, decided_by=device, decided_at=now_iso() if decision else None)
        self.private[rid]["finished"] = time.monotonic()
        self.log(status, req)
        self.cond.notify_all()

    def wait(self, rid: str, client_gone) -> dict | None:
        """Block until the request leaves `pending`. A vanished waiter cancels it."""
        with self.cond:
            while True:
                req = self.requests.get(rid)
                if req is None or req["status"] != "pending":
                    return req
                left = self.private[rid]["deadline"] - time.monotonic()
                if left <= 0:
                    self._finish(rid, "timeout")
                elif client_gone():
                    self._finish(rid, "cancelled")
                else:
                    self.cond.wait(min(left, 0.5))

    def pending(self) -> tuple[dict | None, int]:
        """Oldest live request plus its nonce, and how many are live."""
        with self.cond:
            now = self.last_poll = time.monotonic()
            live = [r for r in self.requests.values()
                    if r["status"] == "pending" and self.private[r["id"]]["deadline"] > now]
            if not live or not self.armed:
                return None, 0
            p = self.private[live[0]["id"]]
            return {**live[0], "nonce": p["nonce"], "expires_in": int(p["deadline"] - now)}, len(live)

    def decide(self, rid: str, decision, nonce, device: str) -> tuple[int, dict]:
        if decision not in ("approve", "deny"):
            return 400, {"error": "decision must be approve or deny"}
        with self.cond:
            req = self.requests.get(rid)
            if req is None:
                return 404, {"error": "unknown request"}
            if not (isinstance(nonce, str) and nonce.isascii() and hmac.compare_digest(nonce, self.private[rid]["nonce"])):
                return 409, {"error": "nonce mismatch"}
            if req["status"] == "pending" and (time.monotonic() >= self.private[rid]["deadline"] or not self.armed):
                self._finish(rid, "timeout" if self.armed else "cancelled")
            if req["status"] != "pending":
                return 409, {"error": f"already {req['status']}", "status": req["status"]}
            self._finish(rid, "approved" if decision == "approve" else "denied", decision, device)
            return 200, req

    def sweep(self) -> None:
        """Expire stale requests, cancel everything on disarm or when no device is polling, forget old ones."""
        with self.cond:
            now, armed, watched = time.monotonic(), self.armed, self.watched
            for rid, req in list(self.requests.items()):
                p = self.private[rid]
                if req["status"] == "pending" and not (armed and watched):
                    self._finish(rid, "cancelled")
                elif req["status"] == "pending" and now >= p["deadline"]:
                    self._finish(rid, "timeout")
                elif req["status"] != "pending" and now - p["finished"] > KEEP_FINISHED:
                    del self.requests[rid], self.private[rid]


class Handler(BaseHTTPRequestHandler):
    relay: Relay
    cors_origin = "*"
    server_version = f"ApprovalGate/{VERSION}"

    def log_message(self, *args) -> None:
        pass  # gate.log is the record; per-request access lines would just be noise

    def bearer(self) -> str:
        auth = self.headers.get("Authorization", "")
        return auth[7:].strip() if auth.startswith("Bearer ") else ""

    def is_hook(self) -> bool:
        return hmac.compare_digest(self.bearer().encode(), self.relay.hook_key.encode())

    def device(self) -> str | None:
        token = self.bearer()
        return self.relay.device_for(token) if token.startswith("agt_") else None

    def client_gone(self) -> bool:
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            return bool(readable) and self.connection.recv(1, socket.MSG_PEEK) == b""
        except OSError:
            return True

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                return None
            body = json.loads(self.rfile.read(length))
            return body if isinstance(body, dict) else None
        except (ValueError, OSError):
            return None

    def reply(self, code: int, body: dict | None = None, headers: dict | None = None) -> None:
        data = b"" if body is None else json.dumps(body, ensure_ascii=False).encode()
        try:
            self.send_response(code)
            for k, v in {**CORS, "Access-Control-Allow-Origin": self.cors_origin, "Cache-Control": "no-store", **(headers or {})}.items():
                self.send_header(k, v)
            if code != 204:
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass  # client hung up; nothing to tell it

    def do_OPTIONS(self) -> None:
        self.reply(204)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            who = "hook" if self.is_hook() else "device" if self.device() else None
            return self.reply(200, {"ok": True, "version": VERSION, "armed": self.relay.armed, "auth": who})
        if path == "/pending":
            if not self.device():
                return self.reply(401, {"error": "unauthorized"})
            req, waiting = self.relay.pending()
            headers = {"X-Gate-Armed": "1" if self.relay.armed else "0", "X-Gate-Waiting": str(waiting),
                       "X-Gate-Session": quote(self.relay.last_session)}
            return self.reply(200, req, headers) if req else self.reply(204, None, headers)
        if m := WAIT_RE.fullmatch(path):
            if not self.is_hook():
                return self.reply(401, {"error": "unauthorized"})
            req = self.relay.wait(m[1], self.client_gone)
            return self.reply(200, req) if req else self.reply(404, {"error": "unknown request"})
        self.reply(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self.read_json()
        if path == "/requests":
            if not self.is_hook():
                return self.reply(401, {"error": "unauthorized"})
            if body is None:
                return self.reply(400, {"error": "malformed body"})
            if not self.relay.armed:
                return self.reply(409, {"error": "disarmed"})
            req = self.relay.create(body)
            if isinstance(req, str):
                code = 429 if req.startswith("too many") else 409 if req.startswith("no device") else 400
                return self.reply(code, {"error": req})
            return self.reply(201, {"id": req["id"], "expires_at": req["expires_at"]})
        device = self.device()
        if m := DECIDE_RE.fullmatch(path):
            if not device:
                return self.reply(401, {"error": "unauthorized"})
            if body is None:
                return self.reply(400, {"error": "malformed body"})
            return self.reply(*self.relay.decide(m[1], body.get("decision"), body.get("nonce"), device))
        if path in ("/arm", "/disarm"):
            if not device:
                return self.reply(401, {"error": "unauthorized"})
            set_armed(self.relay.home, path == "/arm", by=device)
            self.relay.sweep()  # disarm sends every waiting hook back to the terminal now
            return self.reply(200, {"armed": self.relay.armed})
        self.reply(404, {"error": "not found"})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second process bind the same port and steal requests.
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(home: Path, host: str = "127.0.0.1", port: int = 8797, cors_origin: str = "*",
                expiry: dict[str, int] = EXPIRY, presence: float = PRESENCE) -> tuple[Server, Relay]:
    relay = Relay(home, expiry, presence)
    handler = type("BoundHandler", (Handler,), {"relay": relay, "cors_origin": cors_origin})
    return Server((host, port), handler), relay


def serve(args) -> None:
    if args.host in ("0.0.0.0", "::", ""):
        sys.exit("Refusing to bind every interface. Use 127.0.0.1 (with `tailscale serve`) or your tailnet IP.")
    home = state_dir()
    httpd, relay = make_server(home, args.host, args.port, args.cors_origin)
    url = f"http://{args.host}:{httpd.server_port}"
    (home / "relay.json").write_text(json.dumps({"url": url, "pid": os.getpid(), "version": VERSION}))

    def sweep_forever() -> None:
        while True:
            time.sleep(1)
            relay.sweep()

    threading.Thread(target=sweep_forever, daemon=True).start()
    append_log(home, "relay_start", url=url, version=VERSION)
    print(f"Approval Gate relay {VERSION} on {url}  (state: {home})", flush=True)
    print(f"Gate is {'ARMED' if relay.armed else 'disarmed'}. Ctrl+C stops the relay and disarms the gate.", flush=True)
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        set_armed(home, False, by="relay_stop")
        (home / "relay.json").unlink(missing_ok=True)
        append_log(home, "relay_stop")


def main() -> None:
    parser = argparse.ArgumentParser(description="Approval Gate relay. Admin commands live in gatectl.py.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8797)
    parser.add_argument("--cors-origin", default="*", help="Access-Control-Allow-Origin (auth is a bearer token, never a cookie)")
    serve(parser.parse_args())


if __name__ == "__main__":
    main()
