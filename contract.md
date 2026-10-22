# Approval Gate contract

One JSON shape travels the whole loop: hook → relay → plugin → relay → hook.
The hook, the relay and the plugin share no code. They share only this file.

```json
{
  "id": "req_01HX6Q4V8E9M2K7T3N5P0R1S2W",
  "created_at": "2026-10-21T15:50:00-06:00",
  "expires_at": "2026-10-21T15:51:30-06:00",
  "session": { "agent": "claude-code", "name": "hub-orchestrator", "cwd": "~/dev/hub" },
  "tool": "Bash",
  "summary": "git push origin main",
  "detail": "git push origin main --tags",
  "risk": "high",
  "status": "pending",
  "decision": null,
  "decided_by": null,
  "decided_at": null
}
```

## Fields

| Field | Set by | Rule |
|---|---|---|
| `id` | relay | `req_` + a 26-character ULID. Assigned on `POST /requests`. |
| `created_at` | relay | ISO 8601 with the PC's local offset. |
| `expires_at` | relay | `created_at` + 90 s for `low`/`medium`, + 45 s for `high`. |
| `session.agent` | hook | `claude-code` or `codex`, from the hook's `--agent` flag. The glasses show it next to the session name. |
| `session.name` | hook | Basename of the session's working directory, or `APPROVAL_GATE_SESSION`. Max 40 chars. |
| `session.cwd` | hook | Working directory with the home directory replaced by `~`. |
| `tool` | hook | The agent's tool name: `Bash`, `Write`, `Edit` from Claude Code; `Bash`, `apply_patch` from Codex. |
| `summary` | hook | What the glasses show. Max 60 characters. The hook truncates, never the plugin. Built from `tool_input`, never from text the agent wrote about itself. |
| `detail` | hook | The full command or file path. Shown on swipe-down only. Max 200 characters. A command that does not fit is never sent: it goes to the terminal. |
| `risk` | hook | `low` / `medium` / `high`, from the hook's local rule table. |
| `status` | relay | `pending` → `approved` / `denied` / `timeout` / `cancelled`. Only the relay changes it. |
| `decision` | plugin | `approve` / `deny`. The relay rejects anything else. |
| `decided_by` | relay | The device ID of the paired token that answered (`dev_…`), never the token itself. |
| `decided_at` | relay | ISO 8601 with offset. |

`cancelled` means the hook stopped waiting before anyone decided. This happens when
Claude Code is interrupted or the session ends. The request leaves the glasses at once.

## What never enters the contract

- File contents. `Write` and `Edit` send a path and a line count only. A Codex `apply_patch` sends file names
  and `+added -removed` counts, never the patch.
- Environment variables, and secret-shaped strings in commands (`sk-…`, `ghp_…`, `AKIA…`, `TOKEN=…`).
  The hook replaces these with `[secret]`.
- Absolute paths under the home directory. These become `~/…`, or a path relative to `cwd`.

## Endpoints

The relay listens on `127.0.0.1:8797`. The phone reaches it through `tailscale serve`. Every response
carries CORS headers; credentials are bearer tokens, never cookies.

| Endpoint | Caller and credential | Body | Answer |
|---|---|---|---|
| `POST /requests` | hook, `hook.key` | `session`, `tool`, `summary`, `detail`, `risk` | `201 {id, expires_at}`. `409` if disarmed or no device polling, `400` if malformed, `429` past 20 pending |
| `GET /requests/{id}/wait` | hook, `hook.key` | — | Long-polls until the request leaves `pending` (at most its expiry), then the full request |
| `GET /pending` | plugin, device token | — | `200` oldest live request plus `nonce` and `expires_in`, or `204` |
| `POST /requests/{id}/decide` | plugin, device token | `{decision, nonce}` | `200` with the request; `409` if decided, expired, cancelled or the nonce is wrong; `404` if unknown |
| `POST /arm`, `POST /disarm` | plugin, device token | `{source: "glasses" \| "phone", reason?}` | `200 {armed}`. `400` without a valid `source` |
| `GET /health` | anyone | — | `{ok, version, armed, auth}`, where `auth` is `hook`, `device` or `null` for the credential presented |

## On the wire (not stored)

`GET /pending` adds two fields to the request it returns:

| Field | Meaning |
|---|---|
| `nonce` | One-time value. `POST /requests/{id}/decide` must echo it. |
| `expires_in` | Whole seconds left, measured by the relay's clock. The plugin counts down from this and ignores its own clock. |

Every `/pending` response, `200` or `204`, carries `X-Gate-Armed: 1|0`, `X-Gate-Waiting: <n>`
and `X-Gate-Session: <last session name>`.

## Armed state

The gate is armed while `%APPDATA%\approval-gate\armed` exists. The hook only checks that file;
the relay and `gatectl.py` create and remove it. Every change is a `gate.log` line with its `source`:

| `source` | Who |
|---|---|
| `glasses` | The plugin: tap to arm, double-tap to disarm, or disarm on exit (`reason: "plugin exit"`) |
| `phone` | The Disarm button on the plugin's phone page |
| `hotkey`, `cli` | `gatectl.py arm` or `disarm` on the PC, with `--source hotkey` or `--source cli` |
| `relay` | The relay itself: on stop, and after 60 s with no device polling |

`by` carries the device id when a device made the change.

## Hook output to the agent

Claude Code and Codex both have a `PermissionRequest` event and accept the same decision object.
The hook answers with one of exactly two JSON objects, and only
when the relay returned a decision for the hook's own request `id`:

```json
{"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}}
```

```json
{"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "deny",
  "message": "The user denied this from Approval Gate on their glasses (dev_…). Do not retry it; continue without it or ask the user."}}}
```

In every other case it prints nothing and exits 0, and the agent shows its own approval prompt.

## Decision rules

1. Only `approved` with `decision: "approve"` and a matching `id` makes the hook allow.
2. `denied` makes the hook deny and tell Claude why.
3. Everything else gives the terminal prompt: `timeout`, `cancelled`, unknown `id`, malformed body,
   relay down or restarted, gate disarmed. These never become `allow`.
4. The relay accepts a request only if a paired device polled `GET /pending` in the last 10 s. If polling
   stops, its pending requests are `cancelled`, and a decision that arrives afterwards gets `409`.
   Nobody watching means the terminal prompt, not a 90 s stall.
5. After 60 s with no device polling, the relay disarms the gate (`source: relay`). The 60 s count from
   the later of the last poll and the moment of arming, so arming on the PC before opening the plugin works.
