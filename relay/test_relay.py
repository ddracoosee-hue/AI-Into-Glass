"""Relay tests. Run: python -m unittest discover -s relay"""
import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import relay as R

DEVICE_TOKEN = "agt_test-device-token"


def request_body(**over):
    body = {"session": {"agent": "claude-code", "name": "hub", "cwd": "~/dev/hub"},
            "tool": "Bash", "summary": "git push origin main", "detail": "git push origin main --tags", "risk": "high"}
    body.update(over)
    return body


class RelayTest(unittest.TestCase):
    expiry = R.EXPIRY

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        R.save_devices(self.home, {"dev_abc123": {"label": "test", "sha256": R.sha256(DEVICE_TOKEN), "created_at": R.now_iso()}})
        self.httpd, self.relay = R.make_server(self.home, port=0, expiry=self.expiry)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        self.hook_key = self.relay.hook_key
        R.set_armed(self.home, True, by="test")

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def call(self, method, path, token=None, body=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                text = resp.read()
                return resp.status, (json.loads(text) if text else None), resp.headers
        except urllib.error.HTTPError as err:
            text = err.read()
            return err.code, (json.loads(text) if text else None), err.headers

    def create(self, **over):
        status, body, _ = self.call("POST", "/requests", self.hook_key, request_body(**over))
        self.assertEqual(status, 201, body)
        return body["id"]

    def wait_async(self, rid):
        result = {}
        t = threading.Thread(target=lambda: result.update(r=self.call("GET", f"/requests/{rid}/wait", self.hook_key)))
        t.start()
        return t, result

    def log_events(self):
        return [json.loads(line) for line in (self.home / "gate.log").read_text(encoding="utf-8").splitlines()]


class RoundTrip(RelayTest):
    def test_approve_round_trip(self):
        rid = self.create()
        status, pending, headers = self.call("GET", "/pending", DEVICE_TOKEN)
        self.assertEqual(status, 200)
        self.assertEqual(pending["id"], rid)
        self.assertEqual(headers["X-Gate-Armed"], "1")
        self.assertEqual(headers["X-Gate-Waiting"], "1")
        self.assertEqual(headers["X-Gate-Session"], "hub")
        self.assertLessEqual(pending["expires_in"], 45)  # high risk
        t, result = self.wait_async(rid)
        status, decided, _ = self.call("POST", f"/requests/{rid}/decide", DEVICE_TOKEN,
                                       {"decision": "approve", "nonce": pending["nonce"]})
        self.assertEqual(status, 200)
        t.join(5)
        status, final, _ = result["r"]
        self.assertEqual((final["status"], final["decision"], final["decided_by"]), ("approved", "approve", "dev_abc123"))
        self.assertNotIn("nonce", final)
        self.assertEqual(self.call("GET", "/pending", DEVICE_TOKEN)[0], 204)
        log = (self.home / "gate.log").read_text(encoding="utf-8")
        self.assertNotIn(DEVICE_TOKEN, log)
        self.assertEqual([e["event"] for e in self.log_events() if e.get("id") == rid], ["request", "approved"])

    def test_deny(self):
        rid = self.create(risk="low")
        _, pending, _ = self.call("GET", "/pending", DEVICE_TOKEN)
        self.assertGreater(pending["expires_in"], 45)  # low risk gets 90 s
        t, result = self.wait_async(rid)
        self.call("POST", f"/requests/{rid}/decide", DEVICE_TOKEN, {"decision": "deny", "nonce": pending["nonce"]})
        t.join(5)
        self.assertEqual(result["r"][1]["status"], "denied")

    def test_oldest_first(self):
        first, second = self.create(summary="first"), self.create(summary="second")
        _, pending, headers = self.call("GET", "/pending", DEVICE_TOKEN)
        self.assertEqual(pending["id"], first)
        self.assertEqual(headers["X-Gate-Waiting"], "2")
        self.call("POST", f"/requests/{first}/decide", DEVICE_TOKEN, {"decision": "deny", "nonce": pending["nonce"]})
        self.assertEqual(self.call("GET", "/pending", DEVICE_TOKEN)[1]["id"], second)


class OneDecision(RelayTest):
    def test_wrong_nonce_and_replay(self):
        rid = self.create()
        _, pending, _ = self.call("GET", "/pending", DEVICE_TOKEN)
        self.assertEqual(self.call("POST", f"/requests/{rid}/decide", DEVICE_TOKEN, {"decision": "approve", "nonce": "guess"})[0], 409)
        good = {"decision": "approve", "nonce": pending["nonce"]}
        self.assertEqual(self.call("POST", f"/requests/{rid}/decide", DEVICE_TOKEN, good)[0], 200)
        status, body, _ = self.call("POST", f"/requests/{rid}/decide", DEVICE_TOKEN, {"decision": "deny", "nonce": pending["nonce"]})
        self.assertEqual((status, body["status"]), (409, "approved"))

    def test_only_approve_or_deny(self):
        rid = self.create()
        _, pending, _ = self.call("GET", "/pending", DEVICE_TOKEN)
        for decision in ("allow", "APPROVE", None, 1):
            status, _, _ = self.call("POST", f"/requests/{rid}/decide", DEVICE_TOKEN, {"decision": decision, "nonce": pending["nonce"]})
            self.assertEqual(status, 400, decision)

    def test_unknown_id(self):
        status, _, _ = self.call("POST", f"/requests/req_{R.ulid()}/decide", DEVICE_TOKEN, {"decision": "approve", "nonce": "x"})
        self.assertEqual(status, 404)
        self.assertEqual(self.call("GET", f"/requests/req_{R.ulid()}/wait", self.hook_key)[0], 404)


class Expiry(RelayTest):
    expiry = {"low": 1, "medium": 1, "high": 1}

    def test_timeout_then_late_decision(self):
        rid = self.create()
        _, pending, _ = self.call("GET", "/pending", DEVICE_TOKEN)
        status, final, _ = self.call("GET", f"/requests/{rid}/wait", self.hook_key)
        self.assertEqual((final["status"], final["decision"]), ("timeout", None))
        status, body, _ = self.call("POST", f"/requests/{rid}/decide", DEVICE_TOKEN, {"decision": "approve", "nonce": pending["nonce"]})
        self.assertEqual((status, body["status"]), (409, "timeout"))
        self.assertEqual(self.call("GET", "/pending", DEVICE_TOKEN)[0], 204)


class Arming(RelayTest):
    def test_disarmed_refuses_new_requests(self):
        R.set_armed(self.home, False, by="test")
        status, body, _ = self.call("POST", "/requests", self.hook_key, request_body())
        self.assertEqual((status, body["error"]), (409, "disarmed"))

    def test_disarm_cancels_waiting_hooks(self):
        rid = self.create()
        t, result = self.wait_async(rid)
        time.sleep(0.3)
        status, body, _ = self.call("POST", "/disarm", DEVICE_TOKEN, {})
        self.assertEqual((status, body["armed"]), (200, False))
        t.join(5)
        self.assertEqual(result["r"][1]["status"], "cancelled")
        self.assertFalse((self.home / "armed").exists())
        self.assertEqual(self.call("GET", "/pending", DEVICE_TOKEN)[2]["X-Gate-Armed"], "0")
        self.assertEqual(self.call("POST", "/arm", DEVICE_TOKEN, {})[1]["armed"], True)

    def test_vanished_waiter_cancels(self):
        rid = self.create()
        sock = socket.create_connection(("127.0.0.1", self.httpd.server_port))
        sock.sendall(f"GET /requests/{rid}/wait HTTP/1.0\r\nAuthorization: Bearer {self.hook_key}\r\n\r\n".encode())
        time.sleep(0.3)
        sock.close()
        deadline = time.time() + 3
        while time.time() < deadline and self.relay.requests[rid]["status"] == "pending":
            time.sleep(0.1)
        self.assertEqual(self.relay.requests[rid]["status"], "cancelled")
        self.assertEqual(self.call("GET", "/pending", DEVICE_TOKEN)[0], 204)


class Auth(RelayTest):
    def test_each_side_needs_its_own_credential(self):
        self.assertEqual(self.call("GET", "/pending")[0], 401)
        self.assertEqual(self.call("GET", "/pending", self.hook_key)[0], 401)
        self.assertEqual(self.call("POST", "/requests", DEVICE_TOKEN, request_body())[0], 401)
        rid = self.create()
        self.assertEqual(self.call("GET", f"/requests/{rid}/wait", DEVICE_TOKEN)[0], 401)
        self.assertEqual(self.call("POST", f"/requests/{rid}/decide", self.hook_key, {"decision": "approve", "nonce": "x"})[0], 401)
        self.assertEqual(self.call("POST", "/arm", self.hook_key, {})[0], 401)

    def test_revoked_device(self):
        R.save_devices(self.home, {})
        self.assertEqual(self.call("GET", "/pending", DEVICE_TOKEN)[0], 401)

    def test_health_reports_who_you_are(self):
        self.assertEqual(self.call("GET", "/health")[1]["auth"], None)
        self.assertEqual(self.call("GET", "/health", self.hook_key)[1]["auth"], "hook")
        _, body, _ = self.call("GET", "/health", DEVICE_TOKEN)
        self.assertEqual((body["auth"], body["armed"], body["version"]), ("device", True, R.VERSION))


class Validation(RelayTest):
    def test_rejects_bad_requests(self):
        cases = [request_body(summary="x" * 61), request_body(summary=""), request_body(risk="extreme"),
                 request_body(detail="x" * 201), request_body(tool=7), {"tool": "Bash"}]
        for body in cases:
            self.assertEqual(self.call("POST", "/requests", self.hook_key, body)[0], 400, body)
        self.assertEqual(self.call("POST", "/requests", self.hook_key, raw=b"{not json")[0], 400)
        self.assertEqual(self.call("POST", "/requests", self.hook_key, raw=b"[]")[0], 400)

    def test_ignores_extra_fields(self):
        rid = self.create(status="approved", decision="approve")
        _, pending, _ = self.call("GET", "/pending", DEVICE_TOKEN)
        self.assertEqual((pending["id"], pending["status"], pending["decision"]), (rid, "pending", None))

    def test_cors_preflight(self):
        status, _, headers = self.call("OPTIONS", "/pending")
        self.assertEqual(status, 204)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "*")
        self.assertIn("Authorization", headers["Access-Control-Allow-Headers"])
        self.assertIn("X-Gate-Armed", headers["Access-Control-Expose-Headers"])


if __name__ == "__main__":
    unittest.main()
