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
