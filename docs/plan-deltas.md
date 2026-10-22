# Plan deltas

Where the code differs from the build plan ("Approval Gate — Build Plan", Oct 21, 2026), and why. All
of these were accepted on Oct 21. [contract.md](../contract.md) is the source of truth for the wire
format; this file explains how the code got there.

## The nine changes from the first build

| # | The plan said | The code does | Why |
|---|---|---|---|
| 1 | The hook returns `{"hookSpecificOutput":{"hookEventName":"PermissionRequest","behavior":"allow"}}` | It returns `{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}`. A deny adds `decision.message` with the reason. | That is Claude Code's documented `PermissionRequest` schema. Verified against Claude Code 2.1.306: approve ran the command, and deny reached Claude with its reason. |
| 2 | Bind the relay to the tailnet address | The relay binds `127.0.0.1:8797`. `tailscale serve --bg --https=443 http://127.0.0.1:8797` publishes it to the tailnet at `https://<pc>.<tailnet>.ts.net`. The relay refuses to bind `0.0.0.0`. | The plan requires TLS. `tailscale serve` provides a real certificate with no certificate management, and there is no listener on any other interface. A plain-HTTP tailnet address may also be blocked by the phone's WebView. |
| 3 | Nothing; whether the WebView stays alive was an open question | The relay accepts a request only if a paired device polled `GET /pending` in the last 10 s. Pending requests are `cancelled` once polling stops, and a later decision gets `409`. | If the phone sleeps, Claude Code falls back to the terminal at once instead of stalling for up to 90 s. Tested by `hook/test_hook.py::PresenceGap`. |
| 4 | Line 2: `summary` in large text | The summary sits in a bordered box at full brightness (`textColor` 4), with `HIGH RISK` on its own line and gesture hints dimmed (2). | `@evenrealities/even_hub_sdk` 0.0.16 has one font size. Text containers only have brightness 0–4. |
| 5 | A press approves | Three safeguards: a tap counts only after the prompt has been on screen for **0.7 s**; the approve then waits **0.35 s** in case the tap is half of a double-tap; the phone page mirrors the glasses but cannot approve. | A stray tap must not approve a prompt that just arrived. A double-tap reported as tap + double-tap must deny. A stolen unlocked phone alone must not be enough to approve. Tested by `plugin/test/input.test.ts` and replayed in the simulator. |
| 6 | Idle: press does nothing, double-press disarms. No way to arm from the glasses. | Gate off: **tap arms**, double-tap exits the plugin. Armed and idle: double-tap disarms. | The plan's "arm press" had no gesture. Exiting needs a gesture, and the templates use double-tap for it. |
| 7 | Port not specified | Relay on **8797**, plugin dev server on **5180** (`strictPort`, bound to `127.0.0.1` unless `GATE_DEV_HOST` is set). | On the development PC, 8787 is the Orion MCP host and 5173 was already taken. |
| 8 | Demo: "A `rm` of a build dir" | The demo's deny step is `rm -r build`. | `rm -rf` is on the hook's denylist, so it would never reach the glasses. |
| 9 | Keep the relay under 300 lines | `relay/relay.py` is about 400 lines. Admin commands live in `relay/gatectl.py`. | The extra lines are separate hook and device credentials, cancel-on-disconnect for waiters, the nobody-watching rule and its backstop, and a Windows guard against another process sharing the port. Kept as is (Oct 21). |

## Follow-ups (Oct 21)

| Change | Detail |
|---|---|
| Disarm on exit | The plugin disarms (`source: glasses`, `reason: plugin exit`) on its double-tap exit and on `SYSTEM_EXIT_EVENT` / `ABNORMAL_EXIT_EVENT`. If the plugin dies without saying so, the relay disarms after **60 s** with no device polling (`source: relay`), counted from the later of the last poll and the arm. |
| Source on every arm and disarm | `gate.log` records `glasses`, `phone`, `hotkey`, `cli` or `relay`, plus the device id. The relay rejects a device's arm or disarm without a valid `source`. |
| Tap then double-tap | `plugin/test/input.test.ts` replays the SDK envelopes (a tap with no `eventType`, then `eventType: 3`) and asserts deny. |

## Smaller differences

- **A separate hook credential.** The hook authenticates with `hook.key`; phones use device tokens. A device token cannot create requests, and the hook key cannot decide them.
- **Device tokens are stored as SHA-256 hashes.** `decided_by` and the log carry the device id (`dev_…`), never the token.
- **`cancelled` is a status.** The relay sets it when the hook stops waiting (Claude Code interrupted), on disarm, or when nobody is polling.
- **More on the denylist** than the plan named:
  - `git reset --hard`, `git clean -f`, `git push --mirror` and `+refspec`
  - `Remove-Item -Recurse -Force`, `rd /s`
  - `eval`, `base64 -d`, `iex`
  - `.claude/`, `.git/hooks`, credential files, and the gate's own files
  - commands with invisible or bidi characters
  - commands longer than the 200-character `detail`, which can't be shown in full
- **Pairing uses a link, not a QR of our own.** `gatectl.py pair` prints a plugin URL carrying the relay URL and token, and `npx evenhub qr --url` turns it into the QR the Even app scans.
- **Codex is supported now, through `PermissionRequest`, not `PreToolUse`.** The plan expected Codex to need a
  `PreToolUse` hook with `permissionDecision`. Codex 0.162 has a `PermissionRequest` event with the same decision
  object as Claude Code, so one hook serves both (`--agent codex`). `apply_patch` edits are summarized as file
  names and line counts. The glasses header names the agent, because two agents can share a folder.
- **PowerShell.** The hook handles the `PowerShell` tool's commands, but the matcher stays `Bash|Write|Edit` as the plan says. Adding `PowerShell` is a one-word change in `scripts/install_hook.py --matcher`.
- **The hook is not installed globally.** `python scripts/install_hook.py` does that, when you choose.

# Command Deck (v2)

Where the code differs from "Command Deck — Build Plan" (Oct 22, 2026), and why. The v1 rows above
still hold. [contract.md](../contract.md), "Contract v2", is the source of truth for the wire format.

## Before building (Oct 22, decided with Caleb)

| The plan said | The code does | Why |
|---|---|---|
| Truncate with `even-toolkit` | Uses `@evenrealities/pretext` | It's the measurement engine `even-toolkit` wraps. It comes from Even Realities and has no dependencies. `even-toolkit` is a third-party React component library that pulls in recharts. |
| Scratch and demo repos anywhere outside `H:\glasses` | `C:\deck-sandbox\` | `H:` is an exFAT drive. Codex's Windows sandbox needs NTFS ACLs, and git needs a `safe.directory` workaround for every repo there. |
| Read `docs/approval-gate-plan.md` first | It doesn't exist | The v1 plan was never committed. contract.md, plan-deltas.md and the README carry it. |
| "All 40 v1 tests" | 70: relay 23, hook 33, demo 6, plugin 8 | The count grew with the Codex support. All 70 must pass after every commit. |

## Phase A: registry and ingest

| # | The plan said | The code does | Why |
|---|---|---|---|
| A1 | The ingest token is the hook token | A third credential, `ingest.key` (`dik_…`), that can only post to ingest | Claude Code's `http` hooks take header values only from environment variables, and Claude Code exports those to the agent's own shell. With the hook token there, the agent could create gate requests. With `ingest.key` it can only post telemetry. |
| A2 | `POST /ingest` answers 200 always | An empty 200 for the ingest key, including unparseable bodies; 401 without the key | A wrong key should be visible, not silently dropped. The body is always empty, because Claude Code reads a 2xx JSON body from an `http` hook as hook output. |
| A3 | Ingest bodies max 64 KB | Up to 64 KB parsed; up to 64 MB read and skimmed for the event name, ids and target; past that the connection closes | Claude Code's PostToolUse carries the tool's output (`Edit` sends the whole original file), so real bodies often pass 64 KB. Dropping them would leave runs open forever. Nothing past 64 KB is ever parsed or kept. |
| A4 | `http` hooks for every Claude Code event | Every event except SessionStart, which uses `hook/deck_ingest.py` (async, 2 s) | Claude Code doesn't run `http` hooks on SessionStart. |
| A5 | Codex PostToolUse closes a run `ok` or `failed` | `ok`, or `failed` when the forwarder finds a test runner's failure summary in the output's last 4 KB | Codex's Bash PostToolUse carries stdout and no exit code; the fixtures show `sys.exit(3)` as `''`. The forwarder reads the output in memory and sends one flag. The output never leaves the hook. |
| A6 | Record fixtures from one Claude Code and one Codex session | Done, plus five synthetic fixtures, labeled in `tests/fixtures/hooks/README.md` | `codex exec` runs with `approval: never` and never raises PermissionRequest. `idle_prompt` needs an interactive session, StopFailure needs an API error, and the recorded prompt never changed directory. |
| A7 | (Codex run in a scratch repo) | The Codex fixtures were recorded with `-s danger-full-access` | The Codex Windows sandbox failed to start (`setup refresh had errors`): the running Codex desktop app holds `node_repl.exe`, which the sandbox setup tries to re-ACL. This matters for Phase G, where Codex runs the test suite. |
| A8 | Agent identity not specified | `?agent=claude-code` or `?agent=codex` on the ingest URL | Neither agent names itself in its hook payloads. |
| A9 | Endpoints for Deck, Sessions, Running, Projects, To-dos | Adds `GET /needs?scope=` | The Needs-you section needs a list, which the endpoint table had no row for. |
| A10 | (Run lifecycle) | A permission still `waiting` when the same thread asks for another tool closes as `ended` | No hook reports a deny given in the terminal. An approved tool posts PostToolUse before its thread asks for anything else, so a new PreToolUse means the prompt was answered. A late PostToolUse corrects the inference. |
| A11 | Nothing absolute leaves the PC | True for every v2 endpoint. v1's `/pending` still carries `session.cwd` as the hook sends it: home becomes `~`, but other drives stay absolute (`H:/glasses`) | v1 is frozen, and the plugin never displays `cwd`. |

## Phase B: navigation engine

| # | The plan said | The code does | Why |
|---|---|---|---|
| B1 | List template: a firmware list container, "swipe moves the highlight (firmware-native)" | Lists are five text rows. The plugin owns the cursor, marks the highlighted row with › at full brightness, and dims the rest. A swipe is two `textContainerUpgrade` calls | A probe showed that a firmware list sends no event when the highlight moves, and resets to row 0 on every rebuild, with no API to set it. The plugin couldn't know the highlighted row, so it couldn't return to it after an interrupt, or point a long-press menu at it. Both are acceptance criteria. HARDWARE #3 |
| B2 | "List screens: long-press opens the actions menu for the highlighted item" | Each list page declares the union of its row menus. A selection acts on the row under the plugin's cursor | Menus change only on rebuild, and a cursor move isn't a rebuild |
| B3 | ▸ marks the highlighted row | › | The firmware font has no ▸; it renders blank. ● ○ › · … × → all render |
| B4 | Status bar: one container | One container, breadcrumb left and status right, aligned by measured spaces (5 px each) | It keeps the List template at 8 text containers: bar, five rows, the dim note line, hint |
| B5 | The "v1 approval layout plus a source line" | The source line reads `from <session> · back to <where you were>` | It names who's asking and where tap or double-tap returns you |
| B6 | Gate off: tap arms (v1) | The Deck is the root. Arm and disarm are in its long-press menu, and the status bar shows ARMED or OFF | On the Deck, tap opens the highlighted section, as on every list |
| B7 | (Tap timing) | Every tap waits out the 0.35 s double-tap window, and every screen has the 0.7 s lockout | A double-tap that the touchpad reports as tap + double must go back, not open and then go back |
| B8 | (Simulator lifecycle) | A FOREGROUND_ENTER while already in front counts as "menu opened", and the EXIT after it as "menu closed" | evenhub-simulator sends 4 and 5 when the contextual menu opens and closes. Taken literally, closing a menu would stop polling. HARDWARE #5 |
| B9 | `scripts/sim.py run` | `python scripts/sim.py run walk` and `run interrupt`, on the stack from `scripts/devstack.py up && seed` | devstack keeps its state in `C:/deck-sandbox/state`, so test runs never touch your paired devices or `gate.log` |
