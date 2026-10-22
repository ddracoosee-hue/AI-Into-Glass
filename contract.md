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

---

# Contract v2: Command Deck

Everything above is v1 and unchanged. v2 adds a registry that the relay builds from hook telemetry,
read by the plugin and the desktop page. The plugin never derives state on its own.

## Objects

```json
{
  "session": {
    "id": "cc:4f1a…", "agent": "claude-code", "title": "approval gate docs pass",
    "project": "glasses", "project_name": "glasses", "folder": "glasses", "tag": "",
    "model": "opus", "state": "waiting", "waiting_for": "approval", "state_for_s": 42,
    "started_at": "…", "last_event_at": "…",
    "current": { "tool": "Bash", "target": "git push origin main", "since": "…" },
    "counts": { "tools": 37, "approved": 2, "denied": 0, "failed": 1 },
    "error": true, "muted": false
  },
  "run": {
    "id": "toolu_01ABC…", "session": "cc:4f1a…", "agent": "claude-code", "project": "glasses",
    "kind": "tool", "tool": "Bash", "target": "npm run build", "parent": null,
    "started_at": "…", "ended_at": null, "status": "running", "elapsed_s": 754
  },
  "project": {
    "id": "glasses", "name": "glasses", "branch": "main", "dirty": 3, "pinned": false,
    "counts": { "sessions": 2, "running": 1, "waiting": 1, "todos_open": 4 }
  },
  "todo": {
    "id": "9f2c…", "project": "glasses", "text": "README threat model",
    "source": "todo-file", "file": "TODO.md", "done": false
  },
  "focus": { "projects": ["glasses", "hub"], "minutes": 50, "started_at": "…", "ends_at": "…", "left_s": 1860 }
}
```

| Field | Rule |
|---|---|
| `session.id` | `cc:` or `cdx:` plus the agent's `session_id`. |
| `session.state` | `idle`, `working`, `waiting`, `quiet` or `ended`. `quiet` means 15 min with no event while `working`, because a killed terminal sends no SessionEnd. `ended` sessions leave every list after 10 min. |
| `session.waiting_for` | `approval` or `input` while `waiting`, else `null`. |
| `session.title` | Claude Code's `session_title`, which it sends only when one is set (`--name`, `/rename`). Often empty. |
| `session.model` | From SessionStart (interactive Claude Code only) or from any Codex event. Often empty for `claude -p`. |
| `session.folder`, `tag` | The folder name, and the worktree name for a session in a git worktree. A session under no configured root has project `(other)` and shows only its folder. |
| `run.kind` | `tool` or `subagent`. A subagent's own tool calls have `parent` set to its run. |
| `run.status` | `running`, `waiting` (on a permission prompt), `ok`, `failed`, `denied`, `interrupted`, or `ended` (closed by Stop or SessionEnd, or a permission answered in the terminal). |
| `run.target` | A project-relative path, a command, a pattern or a host, scrubbed, at most 64 characters. Never file contents. |
| `todo.id` | SHA-1 of the project, file, line text and occurrence, 20 hex characters. `source` is `todo-file`, `obsidian` or `claude-task`. |
| `*_s` | Whole seconds by the relay's clock, so the plugin never compares clocks. |

## Scrubbing

These rules apply at ingest, before anything is stored:

- An absolute path under the session's project root becomes relative to it (`src/calc.py`). Under
  another configured project it becomes `<name>/<path>`. Anywhere else it becomes its last
  component. `~`, `$HOME` and `%USERPROFILE%` count as absolute. The home directory never appears.
- Secret-shaped strings become `[secret]`, using the patterns the gate hook uses.
- The relay drops `prompt`, `last_assistant_message`, `tool_response`, `transcript_path` and patch
  bodies unread or right after reading. Project roots and `deck.projects.json` never leave the relay.

## Where the registry comes from

| Hook event | Agents | Effect |
|---|---|---|
| `SessionStart` | CC, CDX | Create the session as `idle`; set `title`, `model`, and the project from `cwd`. |
| `UserPromptSubmit` | CC, CDX | `working`. |
| `PreToolUse` | CC, CDX | Open a run keyed by `tool_use_id`, set `current`, `working`. A permission still waiting in the same thread closes as `ended`, because it was answered in the terminal. |
| `PermissionRequest` | CC, CDX | The open run for that tool becomes `waiting`, and the session waits for `approval`. The v1 gate flow runs unchanged. |
| `PermissionDenied` | CC | Close the run as `denied` (auto mode's classifier). |
| `PostToolUse` | CC, CDX | Close the run as `ok`, or as `failed` when the Codex forwarder saw a test runner's failure summary. |
| `PostToolUseFailure` | CC | Close the run as `failed` (`interrupted` on Esc), raise the error flag, add an error row. |
| `Notification` | CC | `idle_prompt`: waiting for `input`. `permission_prompt`: waiting for `approval`. |
| `SubagentStart` / `SubagentStop` | CC, CDX | The `Agent` tool run that launched the subagent becomes `kind: subagent`, and closes on stop. |
| `TaskCreated` / `TaskCompleted` | CC | A `claude-task` to-do, open or done. |
| `Stop` / `StopFailure` | CC, CDX / CC | `idle`; open runs close as `ended`. StopFailure also raises the error flag. |
| `Interrupt` | CDX | `idle`; open runs close as `interrupted`. |
| `CwdChanged` | CC | Re-map the project. |
| `SessionEnd` | CC, CDX | `ended`. |

The relay's own gate decisions also feed the registry. A new gate request links to the session of
the same agent and project that raised a PermissionRequest in the last 30 s. The decision counts as
`approved` or `denied` there, and a deny closes the waiting run as `denied`.

Known limits:

- Codex reports no exit code for shell commands.
- Background Bash (`run_in_background`) reports done at launch, so its run closes early.

## Ingest (port 8798, loopback only)

`POST /ingest?agent=claude-code|codex` with `Authorization: Bearer <ingest.key>`. The body is a
hook event or a card.

- The answer is always an empty `200` for the ingest key, and `401` without it. It is never a JSON
  body, because a Claude Code `http` hook would read one as hook output.
- Bodies up to 64 KB are parsed in full. Larger ones, up to 64 MB, are read and skimmed for the
  event name, the ids and the target only; Claude Code puts `tool_use_id` after `tool_response`.
  Past 64 MB, the connection closes.
- Problems go to `deck.log` as a count, never with the payload.

```json
{"type": "card", "title": "Nightly build failed", "project": "glasses", "level": "error", "id": "nightly", "ttl": 3600}
```

A card shows in Needs you until its `ttl` runs out (60 s to 24 h, default 30 min), or until the
same `id` is posted with `"clear": true`.

`ingest.key` (`dik_…`) is a third credential. It can post to ingest and nothing else. The hook key
and device tokens get `401` on ingest. Claude Code's `http` hooks read the key from
`DECK_INGEST_TOKEN`, which `install_hook.py --deck` puts in the `env` of `settings.local.json`.

## Phone API additions (port 8797, device token)

| Endpoint | Answer |
|---|---|
| `GET /deck?scope=` | Counts for the root screen: `needs_you`, `sessions`, `waiting`, `running`, `longest_s`, `projects`, `todos_open`, `focus`, `outside_focus`, `errors` (newest five), `armed`, `cursor`. |
| `GET /needs?scope=` | Live gate approvals first, then sessions waiting in the terminal, then cards. |
| `GET /sessions?scope=` · `GET /sessions/{id}` | Waiting sessions first. The detail adds the last 20 events and `pending_request`. |
| `GET /runs?scope=&active=1` · `GET /runs/{id}` | Oldest first. |
| `GET /projects` · `GET /projects/{id}` | Counts, branch, and dirty count. The detail adds the last 5 commit subjects, from read-only `git` with a 2 s timeout, cached 15 s. |
| `GET /todos?scope=` · `POST /todos/{id}/done` | Open to-dos. Done flips exactly one `- [ ]` line in a configured file, by temp file and rename. `409` if no configured file still has that line unchanged. |
| `GET /focus` · `PUT /focus` · `DELETE /focus` | `PUT {projects: [1 or 2 ids], minutes: 25 \| 50 \| null}`. The answer to `DELETE`, and `last_summary` in `GET`, carry the summary. |
| `GET /events?since=&wait=` | Events after the cursor, waiting up to 25 s. `204` with `X-Deck-Cursor` on timeout. A device waiting here counts as watching. |
| `GET /health` | Adds `contract: 2`. |

Lists answer `{items, more}`, with 20 rows (the glasses list limit) unless `limit` asks for up to 200.
`scope` is `all`, `focus` or a project id. With focus on, a new gate request from a project outside
it gets `409 outside focus`, and the hook falls back to the terminal at once. A request from a
muted session gets `409 muted`.

## Event log

The event log is `deck.log` in the state dir: JSONL, rotated to `deck.log.1` at 10 MB. Each event
has `id` (the cursor), `ts` and `type`:

| `type` | Fields |
|---|---|
| `session` | `session`, `agent`, `project`, `state`, `waiting_for` |
| `run` | `run`, `session`, `agent`, `project`, `kind`, `tool`, `target`, `status`, and `elapsed_s` when it ends |
| `approval` | `request`, `session`, `agent`, `project`, `tool`, `summary`, `status` (`pending`, `approved`, `denied`, `timeout`, `cancelled`, `refused`), `reason`, `by` |
| `error` | `error`, `session`, `project`, `tool`, `target`, `kind` (`tool` or `stop`), `detail` (`exit 3`, `tests failed`, `rate_limit`) |
| `card` | `card`, `title`, `project`, `level`, `status` (`posted` or `cleared`) |
| `todo` | `todo`, `project`, `text`, `source`, `status` (`added` or `done`) |
| `focus_start` / `focus_end` | `projects`, `minutes`, `ends_at` / `reason`, `summary` (`minutes`, `approved`, `denied`, `runs`, `todos`) |
| `armed` | `armed` |

## deck.projects.json

The file lives in the state dir, or wherever `relay.py --projects <file>` points. It is never sent
to the phone.

```json
{
  "projects": [
    { "id": "glasses", "name": "glasses", "root": "H:/glasses", "todos": ["TODO.md"] },
    { "id": "hub", "name": "hub-orch", "root": "C:/dev/hub", "todos": ["obsidian:Projects/Hub.md"] }
  ],
  "obsidian_vault": "C:/vault",
  "unmapped": "other"
}
```

- `id` is lowercase, at most 32 characters.
- `unmapped` is `other` (group unmatched sessions as `(other)`) or `hide`.
- `todos` entries are paths relative to the root, or `obsidian:` plus a path in the vault. Only
  these files can be written, and only by `POST /todos/{id}/done`.
