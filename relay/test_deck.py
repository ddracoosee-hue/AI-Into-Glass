"""Command Deck registry and ingest tests. Run: python -m unittest discover -s relay"""

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import deck as D
import projects as P
import relay as R

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "hooks"
sys.path.insert(0, str(ROOT / "hook"))
import deck_ingest  # noqa: E402  (tests only: the relay never imports hook code)

DEVICE_TOKEN = "agt_deck-test-token"
FAKE_HOME = r"C:\Users\demo"
REC = r"C:\deck-sandbox\rec"
CC_SESSION = "cc:d37e1171-106b-4c78-963e-9b61024a4ca9"
# Anything that looks like an absolute path or the home directory. None may reach the phone.
ABSOLUTE = re.compile(r"[A-Za-z]:[\\/]|(^|[\s'\"=])/[A-Za-z]/|\\\\|users[\\/]demo|\bdemo[\\/]", re.I)


def fixtures(agent: str, rebase: tuple[str, str] | None = None) -> list[dict]:
    events = []
    for path in sorted((FIXTURES / agent).glob("*.json")):
        text = path.read_text(encoding="utf-8")
        if rebase:
            old, new = (json.dumps(x)[1:-1] for x in rebase)
            text = text.replace(old, new).replace(rebase[0].replace("\\", "/"), rebase[1].replace("\\", "/"))
        events.append(json.loads(text))
    return events


def synthetic(name: str) -> dict:
    return json.loads((FIXTURES / "synthetic" / name).read_text(encoding="utf-8"))


def codex_session_id() -> str:
    return "cdx:" + fixtures("codex")[0]["session_id"]


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


def write_config(home: Path, root: str = REC, **extra) -> Path:
    path = home / "deck.projects.json"
    path.write_text(
        json.dumps(
            {
                "projects": [
                    {"id": "rec", "name": "rec", "root": root, "todos": ["TODO.md"]},
                    {"id": "hub", "name": "hub-orch", "root": "C:/deck-sandbox/hub"},
                ],
                **extra,
            }
        )
    )
    return path


class DeckTest(unittest.TestCase):
    root = REC

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.clock = Clock()
        self.deck = D.Deck(self.home, P.Projects(write_config(self.home, self.root)), FAKE_HOME, self.clock)

    def tearDown(self):
        self.tmp.cleanup()

    def replay(self, agent: str, events: list[dict], step: float = 1.0) -> None:
        for event in events:
            if agent == "codex":
                event = deck_ingest.slim(event, "codex")  # what the forwarder actually posts
            self.clock.t += step
            self.deck.ingest(agent, event)


class Replay(DeckTest):
    def test_claude_code_session_snapshot(self):
        self.replay("claude-code", fixtures("claude-code"))
        sess = self.deck.sessions[CC_SESSION]
        self.assertEqual(
            (sess["agent"], sess["project"], sess["folder"], sess["state"]), ("claude-code", "rec", "rec", "ended")
        )
        self.assertEqual(sess["counts"], {"tools": 10, "approved": 0, "denied": 0, "failed": 1})
        runs = [(r["tool"], r["target"], r["status"], r["kind"]) for r in self.deck.runs.values()]
        self.assertEqual(
            runs,
            [
                ("ToolSearch", "", "ok", "tool"),
                ("TaskCreate", "", "ok", "tool"),
                ("TaskCreate", "", "ok", "tool"),
                ("Bash", "git status --short", "ok", "tool"),
                ("Bash", 'python -c "import sys; sys.exit(3)"', "failed", "tool"),
                ("Edit", "src/calc.py", "ok", "tool"),
                ("TaskUpdate", "", "ok", "tool"),
                ("Bash", "git push origin main", "ended", "tool"),  # denied by -p in the terminal, inferred
                ("Agent", "Explore", "ok", "subagent"),
                ("Glob", "src/**", "ok", "tool"),
            ],
        )
        glob = list(self.deck.runs.values())[-1]
        self.assertEqual(glob["parent"], list(self.deck.runs)[-2])  # the Explore subagent ran it
        states = [e["state"] for e in self.deck.events if e["type"] == "session" and e["session"] == CC_SESSION]
        self.assertEqual(states, ["idle", "working", "waiting", "working", "idle", "ended"])
        tasks = sorted((t["text"], t["done"], t["source"]) for t in self.deck.tasks.values())
        self.assertEqual(tasks, [("check status", True, "claude-task"), ("push branch", False, "claude-task")])
        self.assertEqual(self.deck.errors[0]["detail"], "exit 3")

    def test_codex_session_snapshot(self):
        self.replay("codex", fixtures("codex"))
        sess = self.deck.sessions[codex_session_id()]
        self.assertEqual(
            (sess["agent"], sess["project"], sess["model"], sess["state"]), ("codex", "rec", "gpt-6-astra", "ended")
        )
        runs = [(r["tool"], r["target"], r["status"]) for r in self.deck.runs.values()]
        self.assertEqual(
            runs,
            [
                ("Bash", "git status --short", "ok"),
                ("Bash", 'python -c "import sys; sys.exit(3)"', "ok"),
                ("apply_patch", "TODO.md", "ok"),
                ("Bash", "git push origin main", "ok"),
                ("Bash", 'python -c "import time; time.sleep(4)"', "ok"),
            ],
        )

    def test_states_idle_working_waiting_idle_ended(self):
        events = fixtures("claude-code")
        seen = []
        for event in events:
            self.clock.t += 1
            self.deck.ingest("claude-code", event)
            state = self.deck.sessions[CC_SESSION]["state"]
            if not seen or seen[-1] != state:
                seen.append(state)
        self.assertEqual(seen, ["idle", "working", "waiting", "working", "idle", "ended"])

    def test_codex_permission_request_waits_on_the_open_run(self):
        events = fixtures("codex")
        pre_push = next(
            i for i, e in enumerate(events) if e.get("tool_input", {}).get("command") == "git push origin main"
        )
        self.replay("codex", events[: pre_push + 1])
        self.replay("codex", [synthetic("codex-PermissionRequest.json")])
        sess = self.deck.sessions[codex_session_id()]
        self.assertEqual(
            (sess["state"], sess["waiting_for"], sess["current"]["target"]),
            ("waiting", "approval", "git push origin main"),
        )
        self.assertEqual([r["status"] for r in self.deck.runs.values()][-1], "waiting")
        self.replay("codex", events[pre_push + 1 : pre_push + 2])  # its PostToolUse: approved after all
        self.assertEqual([r["status"] for r in self.deck.runs.values()][-1], "ok")
        self.assertEqual(self.deck.sessions[codex_session_id()]["state"], "working")

    def test_post_before_pre_is_one_closed_run(self):
        events = fixtures("codex")
        self.replay("codex", events[:2] + [events[3], events[2]])  # Post lands first
        self.assertEqual([(r["tool"], r["status"]) for r in self.deck.runs.values()], [("Bash", "ok")])

    def test_idle_prompt_stop_failure_cwd_changed(self):
        self.replay("claude-code", fixtures("claude-code")[:2])
        self.replay("claude-code", [synthetic("cc-Notification-idle_prompt.json")])
        self.assertEqual(
            (self.deck.sessions[CC_SESSION]["state"], self.deck.sessions[CC_SESSION]["waiting_for"]),
            ("waiting", "input"),
        )
        self.assertEqual(self.deck.needs("all")[0]["title"], "waiting for input")
        self.replay("claude-code", [synthetic("cc-StopFailure.json")])
        self.assertEqual(
            (self.deck.sessions[CC_SESSION]["state"], self.deck.errors[0]["detail"]), ("idle", "rate_limit")
        )
        self.replay("claude-code", [synthetic("cc-CwdChanged.json")])
        self.assertEqual(self.deck.sessions[CC_SESSION]["project"], "rec")


class Scrubbing(DeckTest):
    root = r"C:\Users\demo\dev\rec"

    def test_home_paths_in_a_command(self):
        self.replay("claude-code", fixtures("claude-code", (REC, self.root))[:2])
        self.replay(
            "claude-code",
            [
                json.loads(
                    json.dumps(synthetic("cc-home-paths-PreToolUse.json")).replace(
                        json.dumps(REC)[1:-1], json.dumps(self.root)[1:-1]
                    )
                )
            ],
        )
        target = list(self.deck.runs.values())[-1]["target"]
        self.assertEqual(target, "type notes.txt && ls src x.log y TOKEN=[secret]")

    def test_unmapped_session_shows_only_its_folder(self):
        deck = D.Deck(self.home, P.Projects(None), FAKE_HOME, self.clock)
        for event in fixtures("claude-code", (REC, self.root)):
            deck.ingest("claude-code", event)
        sess = deck.sessions[CC_SESSION]
        self.assertEqual((sess["project"], sess["folder"]), ("(other)", "rec"))
        self.assertEqual([r["target"] for r in deck.runs.values() if r["tool"] == "Edit"], ["src/calc.py"])

    def test_paths_under_another_project_and_nowhere(self):
        place = self.deck.projects.place(self.root)
        self.assertEqual(
            self.deck.scrub.text(r"cp C:\deck-sandbox\hub\a.txt C:\Windows\x.dll .", place), "cp hub-orch/a.txt x.dll ."
        )
        self.assertEqual(
            self.deck.scrub.text("curl https://example.com/home/x https://u:pw@h.io/p", place),
            "curl https://example.com/home/x https://u:[secret]@h.io/p",
        )
        self.assertEqual(self.deck.scrub.text("sed s/a/b/ f && echo a/b/c", place), "sed s/a/b/ f && echo a/b/c")


class HttpBase(unittest.TestCase):
    """The relay as it runs: phone API, ingest listener, fixtures posted over HTTP."""

    root = r"C:\Users\demo\dev\rec"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        R.save_devices(
            self.home, {"dev_deck01": {"label": "t", "sha256": R.sha256(DEVICE_TOKEN), "created_at": R.now_iso()}}
        )
        self.httpd, self.relay = R.make_server(
            self.home, port=0, projects=write_config(self.home, self.root), user_home=FAKE_HOME
        )
        self.ingest = R.make_ingest_server(self.relay, port=0)
        for server in (self.httpd, self.ingest):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        self.ingest_url = f"http://127.0.0.1:{self.ingest.server_port}/ingest"

    def tearDown(self):
        for server in (self.httpd, self.ingest):
            server.shutdown()
            server.server_close()
        self.tmp.cleanup()

    def call(self, method, url, token=None, body=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(url, data=data, method=method)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as err:
            return err.code, err.read()

    def post(self, agent, event, token=None):
        return self.call("POST", f"{self.ingest_url}?agent={agent}", token or self.relay.ingest_key, event)

    def get(self, path, token=DEVICE_TOKEN):
        status, body = self.call("GET", self.base + path, token)
        return status, (json.loads(body) if body else None)


class Http(HttpBase):
    def test_ingest_always_answers_an_empty_200(self):
        started = time.monotonic()
        for event in fixtures("claude-code", (REC, self.root)):
            status, body = self.post("claude-code", event)
            self.assertEqual((status, body), (200, b""))  # a JSON body would be read as hook output
        self.assertLess((time.monotonic() - started) / 29, 0.05)
        for raw in (b"{not json", b"[]", b""):
            self.assertEqual(
                self.call("POST", f"{self.ingest_url}?agent=claude-code", self.relay.ingest_key, raw=raw), (200, b"")
            )
        self.assertEqual(self.post("nobody", {"hook_event_name": "Stop", "session_id": "x"})[0], 200)
        self.assertEqual(self.get("/sessions")[1]["items"][0]["id"], CC_SESSION)

    def test_only_the_ingest_key_can_post(self):
        for token in (DEVICE_TOKEN, self.relay.hook_key, "dik_wrong"):
            self.assertEqual(self.post("claude-code", fixtures("claude-code")[0], token), (401, b""))
        self.assertEqual(self.get("/sessions")[1]["items"], [])

    def test_phone_port_has_no_ingest(self):
        for method in ("GET", "POST"):
            for token in (self.relay.ingest_key, self.relay.hook_key, DEVICE_TOKEN):
                status, _ = self.call(
                    method, self.base + "/ingest?agent=claude-code", token, fixtures("claude-code")[0]
                )
                self.assertEqual(status, 404)

    def test_ingest_and_hook_keys_cannot_read_the_deck(self):
        for path in ("/deck", "/sessions", "/runs?active=1", "/projects", "/todos", "/focus", "/needs", "/events"):
            for token in (None, self.relay.hook_key, self.relay.ingest_key):
                self.assertEqual(self.get(path, token)[0], 401, (path, token))
            self.assertEqual(self.get(path)[0], 204 if path == "/events" else 200, path)  # no events yet

    def test_oversized_post_is_skimmed_not_parsed(self):
        events = fixtures("claude-code", (REC, self.root))
        for event in events[:2]:
            self.post("claude-code", event)
        pre = next(e for e in events if e["hook_event_name"] == "PreToolUse" and e["tool_name"] == "Edit")
        post = next(e for e in events if e["hook_event_name"] == "PostToolUse" and e["tool_name"] == "Edit")
        self.post("claude-code", pre)
        big = dict(post, tool_response={"originalFile": "x" * 500_000, "filePath": post["tool_input"]["file_path"]})
        big = {k: big[k] for k in list(post)}  # keep Claude Code's field order: tool_use_id after tool_response
        self.assertEqual(self.post("claude-code", big), (200, b""))
        _, runs = self.get("/runs")
        self.assertEqual([(r["tool"], r["status"]) for r in runs["items"]], [("Edit", "ok")])
        try:  # past MAX_STREAM the listener stops reading and hangs up; it must survive that
            self.call(
                "POST", f"{self.ingest_url}?agent=claude-code", self.relay.ingest_key, raw=b"y" * (70 * 1024 * 1024)
            )
        except OSError:
            pass
        self.assertEqual(self.post("claude-code", events[-1]), (200, b""))

    def test_no_absolute_path_reaches_any_phone_endpoint(self):
        for event in fixtures("claude-code", (REC, self.root)) + [synthetic("cc-Notification-idle_prompt.json")]:
            self.post(
                "claude-code", json.loads(json.dumps(event).replace(json.dumps(REC)[1:-1], json.dumps(self.root)[1:-1]))
            )
        home_cmd = json.loads(
            json.dumps(synthetic("cc-home-paths-PreToolUse.json")).replace(
                json.dumps(REC)[1:-1], json.dumps(self.root)[1:-1]
            )
        )
        self.post("claude-code", home_cmd)
        for event in fixtures("codex", (REC, self.root)):
            self.post("codex", deck_ingest.slim(event, "codex"))
        self.post(
            "claude-code",
            {"type": "card", "title": r"Build done: C:\Users\demo\dev\rec\dist\app.zip", "project": "rec"},
        )
        R.set_armed(self.home, True, "cli")
        self.get("/deck")  # nothing else is polling; make the device count as watching
        self.call("GET", self.base + "/pending", DEVICE_TOKEN)
        self.call(
            "POST",
            self.base + "/requests",
            self.relay.hook_key,
            {
                "session": {"agent": "claude-code", "name": "rec", "cwd": "~/dev/rec"},
                "tool": "Bash",
                "summary": "git push origin main",
                "detail": "git push origin main",
                "risk": "high",
            },
        )
        bodies = {}
        paths = [
            "/deck",
            "/deck?scope=rec",
            "/needs",
            "/sessions",
            f"/sessions/{CC_SESSION}",
            "/runs",
            "/runs?active=1",
            "/projects",
            "/projects/rec",
            "/todos",
            "/focus",
            "/events?since=0",
            "/health",
        ]
        _, runs = self.get("/runs")
        paths += [f"/runs/{r['id']}" for r in runs["items"]]
        for path in paths:
            status, body = self.get(path)
            self.assertEqual(status, 200, path)
            bodies[path] = body
        strings = []

        def walk(value):
            if isinstance(value, str):
                strings.append(value)
            elif isinstance(value, dict):
                [walk(v) for v in value.values()]
            elif isinstance(value, list):
                [walk(v) for v in value]

        walk(bodies)
        leaks = [s for s in strings if ABSOLUTE.search(s)]
        self.assertEqual(leaks, [])
        self.assertIn("Build done: dist/app.zip", json.dumps(bodies["/needs"]))
        self.assertIn("type notes.txt && ls src x.log y TOKEN=[secret]", json.dumps(bodies["/runs"]))
        self.assertEqual(bodies["/needs"]["items"][0]["kind"], "approval")


class Mute(HttpBase):
    def test_a_muted_session_is_hidden_and_its_approvals_go_to_the_terminal(self):
        events = fixtures("claude-code", (REC, self.root))
        perm = next(i for i, e in enumerate(events) if e["hook_event_name"] == "PermissionRequest")
        for event in events[: perm + 1]:
            self.post("claude-code", event)
        self.assertEqual(self.get("/needs")[1]["items"][0]["kind"], "session")
        status, body = self.call("POST", f"{self.base}/sessions/{CC_SESSION}/mute", DEVICE_TOKEN, {"muted": True})
        self.assertEqual((status, json.loads(body)["muted"]), (200, True))
        self.assertEqual(self.get("/needs")[1]["items"], [])
        self.assertEqual(self.get("/runs?active=1")[1]["items"], [])
        R.set_armed(self.home, True, "cli")
        self.call("GET", self.base + "/pending", DEVICE_TOKEN)
        status, body = self.call(
            "POST",
            self.base + "/requests",
            self.relay.hook_key,
            {
                "session": {"agent": "claude-code", "name": "rec", "cwd": "~/dev/rec"},
                "tool": "Bash",
                "summary": "git push origin main",
                "detail": "git push origin main",
                "risk": "high",
            },
        )
        self.assertEqual((status, json.loads(body)["error"]), (409, "muted"))
        self.assertEqual(
            self.call("POST", f"{self.base}/sessions/{CC_SESSION}/mute", self.relay.hook_key, {"muted": False})[0], 401
        )
        self.assertEqual(
            self.call("POST", f"{self.base}/sessions/{CC_SESSION}/mute", DEVICE_TOKEN, {"muted": "no"})[0], 400
        )
        self.call("POST", f"{self.base}/sessions/{CC_SESSION}/mute", DEVICE_TOKEN, {"muted": False})
        self.assertEqual(self.get("/needs")[1]["items"][0]["kind"], "session")

    def test_current_names_its_run(self):
        events = fixtures("claude-code", (REC, self.root))
        perm = next(i for i, e in enumerate(events) if e["hook_event_name"] == "PermissionRequest")
        for event in events[: perm + 1]:
            self.post("claude-code", event)
        _, sess = self.get(f"/sessions/{CC_SESSION}")
        _, run = self.get(f"/runs/{sess['current']['run']}")
        self.assertEqual((run["target"], run["status"]), ("git push origin main", "waiting"))


class Desktop(HttpBase):
    """127.0.0.1:8799: the web UI and read-only /api, for this machine's browser only."""

    def setUp(self):
        super().setUp()
        self.desktop = R.make_desktop_server(self.relay, port=0)
        threading.Thread(target=self.desktop.serve_forever, daemon=True).start()
        self.dbase = f"http://127.0.0.1:{self.desktop.server_port}"

    def tearDown(self):
        self.desktop.shutdown()
        self.desktop.server_close()
        super().tearDown()

    def test_pages_and_reads_without_a_token(self):
        for path, kind in (
            ("/", "text/html"),
            ("/present", "text/html"),
            ("/deck-ui.js", "text/javascript"),
            ("/deck-ui.css", "text/css"),
        ):
            with urllib.request.urlopen(self.dbase + path, timeout=5) as resp:
                self.assertEqual((resp.status, resp.headers["Content-Type"].split(";")[0]), (200, kind), path)
                self.assertIn("script-src 'self'", resp.headers["Content-Security-Policy"])
                self.assertIsNone(resp.headers.get("Access-Control-Allow-Origin"))
        for event in fixtures("claude-code", (REC, self.root))[:4]:
            self.post("claude-code", event)
        status, body = self.call("GET", self.dbase + "/api/sessions")
        self.assertEqual((status, json.loads(body)["items"][0]["id"]), (200, CC_SESSION))

    def test_read_only_and_loopback_names_only(self):
        for method in ("POST", "PUT", "DELETE"):
            self.assertEqual(
                self.call(method, self.dbase + "/api/focus", body={"projects": ["rec"], "minutes": 25})[0], 405
            )
        self.assertEqual(self.call("POST", f"{self.dbase}/api/todos/{'0' * 20}/done")[0], 405)
        req = urllib.request.Request(self.dbase + "/api/deck", headers={"Host": "attacker.example:8799"})
        with self.assertRaises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(err.exception.code, 403)  # DNS rebinding: a foreign Host is refused
        for path in ("/../relay/relay.py", "/relay.py", "/web/deck-ui.js", "/%2e%2e/contract.md"):
            self.assertEqual(self.call("GET", self.dbase + path)[0], 404, path)

    def test_a_desktop_tab_never_counts_as_watching(self):
        self.relay.last_poll = float("-inf")
        self.call("GET", self.dbase + "/api/events?since=0&wait=1")
        self.assertFalse(self.relay.watched)
        self.get("/events?since=0&wait=0")  # a paired device does count
        self.assertTrue(self.relay.watched)

    def test_view_is_device_only_and_never_logged(self):
        route = {"view": "sessions", "scope": "rec"}
        self.assertEqual(self.call("POST", self.base + "/view", self.relay.hook_key, {"route": route})[0], 401)
        self.assertEqual(self.call("POST", self.base + "/view", DEVICE_TOKEN, {"route": {"view": "rm -rf"}})[0], 400)
        self.assertEqual(self.call("POST", self.base + "/view", DEVICE_TOKEN, {"route": route})[0], 200)
        self.assertEqual(
            self.call("POST", self.base + "/view", DEVICE_TOKEN, {"route": route})[0], 200
        )  # same page: one event
        views = [e for e in self.get("/events?since=0")[1]["events"] if e["type"] == "view"]
        self.assertEqual([v["route"] for v in views], [route])
        self.assertNotIn(
            '"view"', (self.home / "deck.log").read_text(encoding="utf-8") if (self.home / "deck.log").exists() else ""
        )


class Upkeep(DeckTest):
    def test_quiet_after_15_minutes_working(self):
        self.replay("claude-code", fixtures("claude-code")[:4])
        self.assertEqual(self.deck.sessions[CC_SESSION]["state"], "working")
        self.clock.t += D.QUIET_AFTER - 1
        self.deck.sweep()
        self.assertEqual(self.deck.sessions[CC_SESSION]["state"], "working")
        self.clock.t += 2
        self.deck.sweep()
        self.assertEqual(self.deck.sessions[CC_SESSION]["state"], "quiet")
        self.replay("claude-code", fixtures("claude-code")[4:5])
        self.assertEqual(self.deck.sessions[CC_SESSION]["state"], "working")

    def test_ended_sessions_drop_after_10_minutes(self):
        self.replay("claude-code", fixtures("claude-code"))
        self.clock.t += D.ENDED_KEEP + 1
        self.deck.sweep()
        self.assertNotIn(CC_SESSION, self.deck.sessions)
        self.assertEqual(len(self.deck.runs), 0)

    def test_caps(self):
        for i in range(D.MAX_SESSIONS + 15):
            self.clock.t += 1
            self.deck.ingest("codex", {"hook_event_name": "SessionStart", "session_id": f"s{i}", "cwd": REC})
        self.assertEqual(len(self.deck.sessions), D.MAX_SESSIONS)
        self.assertNotIn("cdx:s0", self.deck.sessions)
        for i in range(D.MAX_RUNS + 30):
            self.deck.ingest(
                "codex",
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "s214",
                    "cwd": REC,
                    "tool_name": "Bash",
                    "tool_use_id": f"t{i}",
                    "tool_input": {"command": "true"},
                },
            )
        self.assertEqual(len(self.deck.runs), D.MAX_RUNS)

    def test_event_log_rotates_and_reloads(self):
        old = D.LOG_ROTATE
        D.LOG_ROTATE = 4096
        try:
            self.replay("claude-code", fixtures("claude-code") * 3)
        finally:
            D.LOG_ROTATE = old
        self.assertTrue((self.home / "deck.log.1").exists())
        self.assertLess((self.home / "deck.log").stat().st_size, 4096 + 2048)
        again = D.Deck(self.home, self.deck.projects, FAKE_HOME, self.clock)
        self.assertEqual(again.seq, self.deck.seq)
        self.assertEqual(again.events_since(self.deck.seq - 2)[0][-1]["id"], self.deck.seq)

    def test_events_long_poll(self):
        cursor = self.deck.seq
        threading.Timer(0.3, lambda: self.deck.ingest("claude-code", fixtures("claude-code")[0])).start()
        started = time.monotonic()
        events, new_cursor, _ = self.deck.events_since(cursor, wait=5)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual((events[0]["type"], new_cursor), ("session", cursor + 1))
        self.assertEqual(self.deck.events_since(new_cursor, wait=0.2)[0], [])


class Cards(DeckTest):
    def test_a_card_shows_in_needs_you_until_cleared_or_expired(self):
        self.deck.ingest(
            "claude-code",
            {"type": "card", "title": "Nightly build failed", "project": "rec", "level": "error", "id": "nightly"},
        )
        self.deck.ingest("claude-code", {"type": "card", "title": "Deploy done", "project": "nope", "ttl": 60})
        needs = self.deck.needs("all")
        self.assertEqual(
            [(n["kind"], n["title"], n["project"]) for n in needs],
            [("card", "Nightly build failed", "rec"), ("card", "Deploy done", "(other)")],
        )
        self.assertEqual([n["title"] for n in self.deck.needs("rec")], ["Nightly build failed"])
        self.deck.ingest("claude-code", {"type": "card", "id": "nightly", "clear": True})
        self.clock.t += 61
        self.deck.sweep()
        self.assertEqual(self.deck.needs("all"), [])


class Placement(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_worktrees_map_to_their_main_project(self):
        main, tree = self.dir / "glasses", self.dir / "elsewhere" / "glasses-wt"
        (main / ".git" / "worktrees" / "glasses-wt").mkdir(parents=True)
        (tree / "src").mkdir(parents=True)
        (tree / ".git").write_text(f"gitdir: {(main / '.git' / 'worktrees' / 'glasses-wt').as_posix()}\n")
        config = self.dir / "deck.projects.json"
        config.write_text(json.dumps({"projects": [{"id": "glasses", "name": "glasses", "root": main.as_posix()}]}))
        projects = P.Projects(config)
        place = projects.place(str(tree / "src"))
        self.assertEqual((place.project, place.tag, place.folder), ("glasses", "glasses-wt", "glasses-wt"))
        self.assertEqual(projects.display(str(tree / "src" / "a.ts"), place.base, place.project), "src/a.ts")
        inner = projects.place(str(main / ".claude" / "worktrees" / "fix-1" / "lib"))
        self.assertEqual((inner.project, inner.tag), ("glasses", "fix-1"))
        self.assertEqual(projects.place(str(self.dir / "other" / "thing")).project, "(other)")

    def test_bad_config_is_reported_not_fatal(self):
        config = self.dir / "deck.projects.json"
        config.write_text(json.dumps({"projects": [{"id": "Bad Id", "root": "relative/path"}]}))
        projects = P.Projects(config)
        self.assertEqual(projects.projects, {})
        self.assertIn("deck.projects.json", projects.error)

    def test_unmapped_hide(self):
        home = self.dir / "state"
        home.mkdir()
        deck = D.Deck(home, P.Projects(write_config(home, unmapped="hide")), FAKE_HOME)
        deck.ingest("codex", {"hook_event_name": "SessionStart", "session_id": "x", "cwd": "C:/somewhere/else"})
        self.assertEqual((deck.session_list("all"), deck.deck("all")["projects"]), ([], 2))


class Todos(unittest.TestCase):
    """To-dos from configured files: read, then flip exactly one line, or refuse and change nothing."""

    BODY = "# TODO\r\n\r\n- [ ] README threat model\r\n- [ ] record demo\r\n- [x] ship v1\r\n  * [ ] nested item\r\n- [ ] record demo\r\nnot a task\r\n"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.root, self.vault = self.dir / "glasses", self.dir / "vault"
        (self.vault / "Projects").mkdir(parents=True)
        self.root.mkdir()
        (self.root / "TODO.md").write_bytes(self.BODY.encode())
        (self.vault / "Projects" / "Glasses.md").write_text("- [ ] from obsidian\n")
        (self.dir / "secret.md").write_text("- [ ] outside the project\n")
        config = self.dir / "deck.projects.json"
        config.write_text(
            json.dumps(
                {
                    "obsidian_vault": self.vault.as_posix(),
                    "projects": [
                        {
                            "id": "glasses",
                            "name": "glasses",
                            "root": self.root.as_posix(),
                            "todos": ["TODO.md", "obsidian:Projects/Glasses.md", "../secret.md"],
                        }
                    ],
                }
            )
        )
        self.deck = D.Deck(self.dir, P.Projects(config), FAKE_HOME)

    def tearDown(self):
        self.tmp.cleanup()

    def open_todos(self):
        return {t["text"]: t for t in self.deck.todo_list("all")}

    def test_reads_open_lines_from_configured_files_only(self):
        todos = self.deck.todo_list("all")
        self.assertEqual(
            [(t["text"], t["source"], t["file"]) for t in todos],
            [
                ("README threat model", "todo-file", "TODO.md"),
                ("record demo", "todo-file", "TODO.md"),
                ("nested item", "todo-file", "TODO.md"),
                ("record demo", "todo-file", "TODO.md"),
                ("from obsidian", "obsidian", "Projects/Glasses.md"),
            ],
        )
        self.assertEqual(len({t["id"] for t in todos}), 5)  # the duplicate line gets its own id
        self.assertNotIn("outside the project", json.dumps(todos))

    def test_done_flips_exactly_one_line_atomically(self):
        target = self.open_todos()["README threat model"]
        done = self.deck.mark_todo(target["id"])
        self.assertEqual((done["text"], done["done"]), ("README threat model", True))
        after = (self.root / "TODO.md").read_bytes().decode()
        self.assertEqual(after, self.BODY.replace("- [ ] README threat model", "- [x] README threat model"))
        self.assertEqual([p.name for p in self.root.iterdir()], ["TODO.md"])  # no temp file left behind
        self.assertIsNone(self.deck.mark_todo(target["id"]))  # already done: 409
        self.assertEqual([e["status"] for e in self.deck.events if e["type"] == "todo"], ["done"])

    def test_the_second_duplicate_is_its_own_line(self):
        second = [t for t in self.deck.todo_list("all") if t["text"] == "record demo"][1]
        self.deck.mark_todo(second["id"])
        lines = (self.root / "TODO.md").read_text().splitlines()
        self.assertEqual([line for line in lines if "record demo" in line], ["- [ ] record demo", "- [x] record demo"])

    def test_an_edited_line_is_refused_and_nothing_changes(self):
        target = self.open_todos()["README threat model"]
        edited = self.BODY.replace("README threat model", "README threat model, v2")
        (self.root / "TODO.md").write_bytes(edited.encode())
        self.assertIsNone(self.deck.mark_todo(target["id"]))
        self.assertEqual((self.root / "TODO.md").read_bytes().decode(), edited)

    def test_obsidian_notes_and_claude_tasks(self):
        note = self.open_todos()["from obsidian"]
        self.assertTrue(self.deck.mark_todo(note["id"]))
        self.assertEqual((self.vault / "Projects" / "Glasses.md").read_text(), "- [x] from obsidian\n")
        self.deck.ingest(
            "claude-code",
            {
                "hook_event_name": "TaskCreated",
                "session_id": "s1",
                "cwd": self.root.as_posix(),
                "task_id": "1",
                "task_subject": "push branch",
            },
        )
        task = self.open_todos()["push branch"]
        self.assertEqual(task["source"], "claude-task")
        self.assertIsNone(self.deck.mark_todo(task["id"]))  # only files can be written

    def test_garbage_ids_are_refused(self):
        for bad in ("", "../TODO.md", "x" * 20, "0" * 40):
            self.assertIsNone(self.deck.mark_todo(bad))
        self.assertEqual((self.root / "TODO.md").read_bytes().decode(), self.BODY)

    def test_over_http_done_is_200_then_409(self):
        R.save_devices(
            self.dir, {"dev_todo01": {"label": "t", "sha256": R.sha256(DEVICE_TOKEN), "created_at": R.now_iso()}}
        )
        httpd, relay = R.make_server(self.dir, port=0, projects=self.dir / "deck.projects.json")
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_port}"
        try:

            def call(method, path, token=DEVICE_TOKEN):
                req = urllib.request.Request(base + path, method=method, headers={"Authorization": f"Bearer {token}"})
                try:
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        return resp.status, json.load(resp)
                except urllib.error.HTTPError as err:
                    return err.code, None

            _, todos = call("GET", "/todos?scope=glasses")
            tid = todos["items"][0]["id"]
            self.assertEqual(call("POST", f"/todos/{tid}/done", relay.hook_key)[0], 401)
            self.assertEqual(call("POST", f"/todos/{tid}/done")[0], 200)
            self.assertEqual(call("POST", f"/todos/{tid}/done")[0], 409)
            self.assertEqual(call("GET", "/todos?scope=glasses")[1]["items"][0]["text"], "record demo")
        finally:
            httpd.shutdown()
            httpd.server_close()


class Focus(DeckTest):
    def test_rules_for_starting_focus(self):
        self.assertIn("1 to 2", self.deck.set_focus([], 25))
        self.assertIn("1 to 2", self.deck.set_focus(["rec", "hub", "rec"], 25))
        self.assertIn("1 to 2", self.deck.set_focus(["rec", "rec"], 25))
        self.assertEqual(self.deck.set_focus(["nope"], 25), "unknown project")
        self.assertIn("25, 50", self.deck.set_focus(["rec"], 10))
        focus = self.deck.set_focus(["rec", "hub"], None)
        self.assertEqual((focus["projects"], focus["ends_at"], focus["left_s"]), (["rec", "hub"], None, None))

    def test_focus_survives_a_relay_restart(self):
        self.deck.set_focus(["rec"], 50)
        again = D.Deck(self.home, self.deck.projects, FAKE_HOME, self.clock)
        self.assertEqual((again.public_focus()["projects"], again.public_focus()["left_s"]), (["rec"], 3000))

    def test_the_timer_ends_focus_with_a_summary(self):
        self.deck.set_focus(["rec"], 25)
        self.clock.t += 25 * 60 - 1
        self.deck.sweep()
        self.assertIsNotNone(self.deck.focus)
        self.clock.t += 2
        self.deck.sweep()
        self.assertIsNone(self.deck.focus)
        self.assertEqual((self.deck.last_summary["reason"], self.deck.last_summary["minutes"]), ("timer", 25))
        self.assertFalse((self.home / "focus.json").exists())

    def test_disarming_ends_focus(self):
        self.deck.sweep(armed=True)
        self.deck.set_focus(["rec"], None)
        self.deck.sweep(armed=False)
        self.assertEqual(self.deck.last_summary["reason"], "disarmed")

    def test_scope_and_routing(self):
        self.replay("claude-code", fixtures("claude-code")[:4])  # a rec session, working
        self.deck.ingest("codex", {"hook_event_name": "SessionStart", "session_id": "h1", "cwd": "C:/deck-sandbox/hub"})
        self.deck.ingest(
            "codex", {"hook_event_name": "UserPromptSubmit", "session_id": "h1", "cwd": "C:/deck-sandbox/hub"}
        )
        self.deck.set_focus(["rec"], 25)
        self.assertEqual([s["project"] for s in self.deck.session_list("focus")], ["rec"])
        self.assertEqual(len(self.deck.session_list("all")), 2)
        deck = self.deck.deck("focus")
        self.assertEqual((deck["sessions"], deck["outside_focus"]), (1, {"active": 1, "waiting": 0}))
        self.assertIsNone(self.deck.deck("all")["outside_focus"])
        hub = {"agent": "codex", "name": "hub", "cwd": "C:/deck-sandbox/hub"}
        rec = {"agent": "claude-code", "name": "rec", "cwd": "C:/deck-sandbox/rec"}
        self.assertEqual(self.deck.gate_route(hub, "Bash"), "outside focus")
        self.assertIsNone(self.deck.gate_route(rec, "Bash"))
        self.assertEqual([p["pinned"] for p in self.deck.project_list()], [True, False])

    def test_summary_counts_come_from_the_event_log(self):
        self.deck.set_focus(["rec"], 25)
        for status in ("approved", "denied", "approved"):
            self.deck.emit("approval", request="r", project="rec", status=status)
        self.deck.emit("approval", request="r", project="hub", status="approved")  # outside focus: not counted
        self.deck.emit("run", run="x", project="rec", status="ok")
        self.deck.emit("run", run="y", project="rec", status="running")
        self.deck.emit("todo", todo="t", project="rec", status="done")
        self.clock.t += 31 * 60
        summary = self.deck.end_focus("ended")
        self.assertEqual(
            {k: summary[k] for k in ("minutes", "approved", "denied", "runs", "todos")},
            {"minutes": 31, "approved": 2, "denied": 1, "runs": 1, "todos": 1},
        )
        logged = [json.loads(line) for line in (self.home / "deck.log").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(logged[-1]["type"], "focus_end")
        self.assertEqual(
            logged[-1]["summary"], {k: summary[k] for k in ("minutes", "approved", "denied", "runs", "todos")}
        )


class Forwarder(unittest.TestCase):
    def test_slim_drops_text_and_patch_bodies(self):
        events = fixtures("codex")
        patch = next(e for e in events if e.get("tool_name") == "apply_patch" and e["hook_event_name"] == "PreToolUse")
        slim = deck_ingest.slim(patch, "codex")
        self.assertEqual(slim["tool_input"]["command"], "*** Update File: C:/deck-sandbox/rec/TODO.md")
        self.assertNotIn("transcript_path", slim)
        stop = deck_ingest.slim(dict(events[-2], last_assistant_message="secret plan"), "codex")
        self.assertNotIn("secret plan", json.dumps(stop))
        prompt = deck_ingest.slim(dict(events[1], prompt="my prompt"), "codex")
        self.assertNotIn("my prompt", json.dumps(prompt))

    def test_codex_test_failures_are_read_from_the_output_tail_only(self):
        post = next(e for e in fixtures("codex") if e["hook_event_name"] == "PostToolUse")
        for output in (
            "===== 1 failed, 11 passed in 20.4s =====\n",
            "FAILED (failures=1)\n",
            "# fail 2\n",
            "Tests:       1 failed, 4 passed, 5 total\n",
            "test result: FAILED. 3 passed; 1 failed\n",
        ):
            slim = deck_ingest.slim(dict(post, tool_response="lots of output\n" + output), "codex")
            self.assertEqual(slim.get("deck_outcome"), "failed", output)
            self.assertNotIn("tool_response", slim)
        for output in ("===== 12 passed in 20.4s =====\n", "# fail 0\n", "OK\n", ""):
            self.assertNotIn("deck_outcome", deck_ingest.slim(dict(post, tool_response=output), "codex"), output)
        self.assertNotIn("deck_outcome", deck_ingest.slim(dict(post, tool_response="1 failed"), "claude-code"))

    def test_the_real_forwarder_posts_to_a_live_relay(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            R.save_devices(
                home, {"dev_fw0001": {"label": "t", "sha256": R.sha256(DEVICE_TOKEN), "created_at": R.now_iso()}}
            )
            httpd, relay = R.make_server(home, port=0, projects=write_config(home))
            ingest = R.make_ingest_server(relay, port=0)
            for server in (httpd, ingest):
                threading.Thread(target=server.serve_forever, daemon=True).start()
            (home / "relay.json").write_text(
                json.dumps(
                    {
                        "url": f"http://127.0.0.1:{httpd.server_port}",
                        "ingest_url": f"http://127.0.0.1:{ingest.server_port}/ingest",
                    }
                )
            )
            env = {**os.environ, "APPROVAL_GATE_HOME": str(home)}
            started = time.monotonic()
            for event in fixtures("codex")[:4]:
                out = subprocess.run(
                    [sys.executable, "-I", str(ROOT / "hook" / "deck_ingest.py"), "--agent", "codex"],
                    input=json.dumps(event).encode(),
                    capture_output=True,
                    env=env,
                    timeout=10,
                )
                self.assertEqual((out.returncode, out.stdout), (0, b""))
            self.assertLess(time.monotonic() - started, 8)
            self.assertEqual([(r["tool"], r["status"]) for r in relay.deck.runs.values()], [("Bash", "ok")])
            httpd.shutdown(), ingest.shutdown()
            httpd.server_close(), ingest.server_close()
            out = subprocess.run(
                [sys.executable, "-I", str(ROOT / "hook" / "deck_ingest.py"), "--agent", "codex"],
                input=b"{}",
                capture_output=True,
                env=env,
                timeout=10,
            )
            self.assertEqual((out.returncode, out.stdout), (0, b""))  # relay gone: silent


if __name__ == "__main__":
    unittest.main()
