"""Command Deck HTTP: v2 reads on the phone API (8797) and the loopback-only ingest listener (8798).

The phone API answers only paired devices. Ingest answers only the ingest key, and only on a port
`tailscale serve` never exposes. Stdlib only. Endpoints: contract.md, "Contract v2".
"""

from __future__ import annotations

import hmac
import json
import re
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from deck import OTHER, Deck

MAX_INGEST = 64 * 1024  # parsed in full up to this size
MAX_STREAM = 64 * 1024 * 1024  # larger bodies are read, skimmed and dropped; past this the connection closes
HEAD, TAIL = 16 * 1024, 4 * 1024
LIST_ROWS = 20  # the glasses list limit; `limit` raises it for the desktop page
EVENTS_WAIT = 25
SKIM = (
    "session_id",
    "hook_event_name",
    "cwd",
    "tool_name",
    "agent_id",
    "agent_type",
    "notification_type",
    "model",
    "session_title",
)
SKIM_INPUT = ("command", "file_path", "notebook_path", "pattern", "subagent_type")


def skim(head: bytes, tail: bytes) -> dict:
    """The few fields the registry needs from a body too big to parse. Claude Code puts tool_use_id
    after tool_response, so it comes from the tail."""
    h, t = head.decode("utf-8", "replace"), tail.decode("utf-8", "replace")

    def find(text: str, field: str, last: bool = False):
        hits = re.findall(rf'"{field}"\s*:\s*("(?:[^"\\]|\\.)*"|true|false)', text)
        if not hits:
            return None
        try:
            return json.loads(hits[-1] if last else hits[0])
        except ValueError:
            return None

    event = {k: v for k in SKIM if (v := find(h, k)) is not None}
    tool_input = {k: v for k in SKIM_INPUT if (v := find(h, k)) is not None}
    if tool_input:
        event["tool_input"] = tool_input
    if (tuid := find(t, "tool_use_id", last=True) or find(h, "tool_use_id")) is not None:
        event["tool_use_id"] = tuid
    if (interrupt := find(t, "is_interrupt", last=True)) is not None:
        event["is_interrupt"] = interrupt
    return event


def rows(items: list, query: dict) -> dict:
    try:
        limit = max(1, min(200, int(query.get("limit", [LIST_ROWS])[0])))
    except ValueError:
        limit = LIST_ROWS
    return {"items": items[:limit], "more": max(0, len(items) - limit)}


def scope_of(deck: Deck, query: dict) -> str | None:
    scope = query.get("scope", ["all"])[0]
    if scope in ("all", "focus", OTHER) or scope in deck.projects.projects:
        return scope
    return None


def handle(h, method: str, path: str, query: str, desktop: bool = False) -> bool:
    """Serve a v2 endpoint. False if `path` isn't one, so the caller answers 404.

    On the phone API a paired device token is required. `desktop` is the loopback desktop page:
    no token, reads only, and waiting on /events never counts as a device watching, so an open
    desktop tab can't make the gate send prompts to glasses nobody is wearing."""
    deck: Deck = h.relay.deck
    q = parse_qs(query)
    parts = [unquote(p) for p in path.strip("/").split("/")]
    if not parts or parts[0] not in (
        "deck",
        "needs",
        "sessions",
        "runs",
        "projects",
        "todos",
        "focus",
        "events",
        "view",
    ):
        return False
    if desktop and method != "GET":
        h.reply(405, {"error": "the desktop page is read-only"})
        return True
    if not desktop and not h.device():
        h.reply(401, {"error": "unauthorized"})
        return True
    route = (method, parts[0], len(parts))
    if parts[0] in ("deck", "needs", "sessions", "runs", "todos") and len(parts) == 1 and method == "GET":
        scope = scope_of(deck, q)
        if scope is None:
            h.reply(400, {"error": "scope must be all, focus or a project id"})
            return True
        if parts[0] == "deck":
            h.reply(200, {**deck.deck(scope), "armed": h.relay.armed, "server_time": int(time.time())})
        elif parts[0] == "needs":
            h.reply(200, rows(deck.needs(scope), q))
        elif parts[0] == "sessions":
            h.reply(200, rows(deck.session_list(scope), q))
        elif parts[0] == "runs":
            h.reply(200, rows(deck.run_list(scope, active=q.get("active", ["0"])[0] == "1"), q))
        else:
            h.reply(200, rows(deck.todo_list(scope), q))
    elif route == ("GET", "sessions", 2):
        found = deck.session_detail(parts[1])
        h.reply(200, found) if found else h.reply(404, {"error": "unknown session"})
    elif route == ("POST", "sessions", 3) and parts[2] == "mute":
        body = h.read_json() or {}
        if not isinstance(body.get("muted"), bool):
            h.reply(400, {"error": "muted must be true or false"})
            return True
        found = deck.mute(parts[1], body["muted"])
        h.reply(200, found) if found else h.reply(404, {"error": "unknown session"})
    elif route == ("GET", "runs", 2):
        found = deck.run_detail(parts[1])
        h.reply(200, found) if found else h.reply(404, {"error": "unknown run"})
    elif route == ("GET", "projects", 1):
        h.reply(200, {"items": deck.project_list(), "more": 0})
    elif route == ("GET", "projects", 2):
        found = deck.project_detail(parts[1])
        h.reply(200, found) if found else h.reply(404, {"error": "unknown project"})
    elif route == ("POST", "todos", 3) and parts[2] == "done":
        done = deck.mark_todo(parts[1])
        h.reply(200, done) if done else h.reply(
            409, {"error": "not an open line in a configured to-do file, or it changed"}
        )
    elif route == ("POST", "view", 1):
        shown = deck.view((h.read_json() or {}).get("route"))
        h.reply(200, {"route": shown}) if shown else h.reply(400, {"error": "route must name a deck view"})
    elif route == ("GET", "focus", 1):
        h.reply(200, {"focus": deck.public_focus(), "last_summary": deck.last_summary})
    elif route == ("PUT", "focus", 1):
        body = h.read_json() or {}
        result = deck.set_focus(body.get("projects"), body.get("minutes"))
        h.reply(200, {"focus": result}) if isinstance(result, dict) else h.reply(400, {"error": result})
    elif route == ("DELETE", "focus", 1):
        h.reply(200, {"focus": None, "last_summary": deck.end_focus("ended")})
    elif route == ("GET", "events", 1):
        try:
            since = int(q.get("since", ["0"])[0])
            wait = max(0.0, min(float(q.get("wait", ["0"])[0]), EVENTS_WAIT))
        except ValueError:
            h.reply(400, {"error": "since and wait must be numbers"})
            return True

        def alive() -> bool:
            if not desktop:
                h.relay.touch()  # a device waiting on events is a device watching
            return not h.client_gone()

        alive()
        events, cursor, reset = deck.events_since(since, wait, alive)
        if events or reset:
            h.reply(200, {"events": events, "cursor": cursor, "reset": reset})
        else:
            h.reply(204, None, {"X-Deck-Cursor": str(cursor)})
    else:
        h.reply(405, {"error": "method not allowed"})
    return True


WEB = Path(__file__).resolve().parent.parent / "web"
PAGES = {"/": "index.html", "/present": "present.html"}
STATIC = {
    "deck-ui.js": "text/javascript; charset=utf-8",
    "deck-ui.css": "text/css; charset=utf-8",
    "desktop.js": "text/javascript; charset=utf-8",
    "present.js": "text/javascript; charset=utf-8",
    "favicon.svg": "image/svg+xml",
}
DESKTOP_HOSTS = ("127.0.0.1", "localhost")
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class DesktopHandler(BaseHTTPRequestHandler):
    """The desktop page on 127.0.0.1:8799: the web UI plus /api/* reads, no token.

    It is loopback-only and read-only. A Host header that isn't this machine's loopback name is
    refused, so a web page can't reach it through DNS rebinding, and there are no CORS headers, so
    another origin can't read its answers."""

    relay = None  # set by relay.make_desktop_server
    server_version = "CommandDeckDesktop/2"

    def log_message(self, *args) -> None:
        pass

    def reply(self, code: int, body: dict | None = None, headers: dict | None = None) -> None:
        self.send_bytes(
            code,
            b"" if body is None else json.dumps(body, ensure_ascii=False).encode(),
            "application/json; charset=utf-8",
            headers,
        )

    def send_bytes(self, code: int, data: bytes, ctype: str, headers: dict | None = None) -> None:
        try:
            self.send_response(code)
            for k, v in {**SECURITY_HEADERS, **(headers or {})}.items():
                self.send_header(k, v)
            if code != 204:
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass

    def client_gone(self) -> bool:
        import select
        import socket

        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            return bool(readable) and self.connection.recv(1, socket.MSG_PEEK) == b""
        except OSError:
            return True

    def do_GET(self) -> None:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in DESKTOP_HOSTS:
            return self.reply(403, {"error": "open the desktop page at http://127.0.0.1:8799/"})
        url = urlparse(self.path)
        if url.path.startswith("/api/") and handle(self, "GET", url.path[4:], url.query, desktop=True):
            return None
        name = PAGES.get(url.path) or (url.path[1:] if url.path[1:] in STATIC else None)
        if name is None:
            return self.reply(404, {"error": "not found"})
        try:
            data = (WEB / name).read_bytes()
        except OSError:
            return self.reply(404, {"error": "not found"})
        self.send_bytes(200, data, "text/html; charset=utf-8" if name.endswith(".html") else STATIC[name])

    def do_POST(self) -> None:
        self.reply(405, {"error": "the desktop page is read-only"})

    do_PUT = do_DELETE = do_POST


class IngestHandler(BaseHTTPRequestHandler):
    """POST /ingest?agent=claude-code|codex. Always an empty 200 for the ingest key: a Claude Code
    http hook reads a JSON body as hook output, and telemetry must never decide anything."""

    deck: Deck
    key: str
    server_version = "CommandDeckIngest/2"

    def log_message(self, *args) -> None:
        pass

    def answer(self, code: int) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
        except OSError:
            pass

    def do_GET(self) -> None:
        self.answer(404)

    do_PUT = do_DELETE = do_OPTIONS = do_GET

    def do_POST(self) -> None:
        url = urlparse(self.path)
        if url.path != "/ingest":
            return self.answer(404)
        auth = self.headers.get("Authorization", "")
        token = auth[7:].strip() if auth.startswith("Bearer ") else ""
        if not hmac.compare_digest(token.encode(), self.key.encode()):
            return self.answer(401)
        agent = parse_qs(url.query).get("agent", [""])[0]
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        event = None
        if 0 < length <= MAX_INGEST:
            try:
                parsed = json.loads(self.rfile.read(length))
                event = parsed if isinstance(parsed, dict) else None
            except (ValueError, OSError):
                event = None
        elif MAX_INGEST < length <= MAX_STREAM:
            event = self.skim_body(length)
        else:
            self.close_connection = True
        try:  # in-memory work, well under a millisecond, so the answer can wait for it
            self.deck.ingest(agent, event) if event is not None else self.deck.log_error(
                "unparseable or oversized ingest body"
            )
        except Exception as err:  # a malformed event must never take the listener down
            self.deck.log_error(f"ingest failed: {type(err).__name__}")
        self.answer(200)

    def skim_body(self, length: int) -> dict | None:
        head, tail, left = b"", b"", length
        try:
            while left > 0:
                chunk = self.rfile.read(min(65536, left))
                if not chunk:
                    break
                left -= len(chunk)
                if len(head) < HEAD:
                    head += chunk[: HEAD - len(head)]
                tail = (tail + chunk)[-TAIL:]
        except OSError:
            return None
        event = skim(head, tail)
        return event if event.get("hook_event_name") else None
