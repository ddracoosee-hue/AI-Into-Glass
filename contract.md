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
| `session.agent` | hook | `claude-code` for now. |
| `session.name` | hook | Basename of the session's working directory, or `APPROVAL_GATE_SESSION`. Max 40 chars. |
| `session.cwd` | hook | Working directory with the home directory replaced by `~`. |
| `tool` | hook | Claude Code tool name: `Bash`, `Write`, `Edit`, … |
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

- File contents. `Write` and `Edit` send a path and a line count only.
- Environment variables, and secret-shaped strings in commands (`sk-…`, `ghp_…`, `AKIA…`, `TOKEN=…`).
  The hook replaces these with `[secret]`.
- Absolute paths under the home directory. These become `~/…`, or a path relative to `cwd`.

## On the wire (not stored)

`GET /pending` adds two fields to the request it returns:

| Field | Meaning |
|---|---|
| `nonce` | One-time value. `POST /requests/{id}/decide` must echo it. |
| `expires_in` | Whole seconds left, measured by the relay's clock. The plugin counts down from this and ignores its own clock. |

Every `/pending` response, `200` or `204`, carries `X-Gate-Armed: 1|0`, `X-Gate-Waiting: <n>`
and `X-Gate-Session: <last session name>`.

## Decision rules

1. Only `approved` with `decision: "approve"` and a matching `id` makes the hook allow.
2. `denied` makes the hook deny and tell Claude why.
3. Everything else gives the terminal prompt: `timeout`, `cancelled`, unknown `id`, malformed body,
   relay down or restarted, gate disarmed. These never become `allow`.
