# Hook fixtures

Real hook payloads, recorded on Oct 22, 2026 with `scripts/record_hooks.py` and scrubbed with its
`scrub` command. The home directory became `C:\Users\demo`, the user name became `demo`, and
prompt, assistant and notification text became `<field omitted>`. Field names, ids, order and path
shapes are as the agents sent them.

| Folder | Source |
|---|---|
| `claude-code/` | `claude -p` (Claude Code 2.1.306) in a scratch repo, recorded at `H:\deck-sandbox\rec` and rebased to `C:\deck-sandbox\rec`. Covers SessionStart, UserPromptSubmit, Pre/PostToolUse, PostToolUseFailure, PermissionRequest (auto-denied by `-p`), TaskCreated/TaskCompleted, SubagentStart/Stop, Stop and SessionEnd. |
| `codex/` | `codex exec` (Codex 0.162.1) in `C:\deck-sandbox\rec` with `-s danger-full-access`. Codex's Windows sandbox could not start on this PC while the Codex desktop app held one of its runtime files. |
| `synthetic/` | Written by hand from the documented field lists, for events the recorded runs could not produce. |

The synthetic fixtures:

| File | Why it is synthetic |
|---|---|
| `codex-PermissionRequest.json` | `codex exec` runs with `approval: never`, so headless Codex never asks. The fields follow the Codex hooks docs; the session and turn ids come from the Codex recording. |
| `cc-Notification-idle_prompt.json` | Sent only in interactive sessions, 60 s after Claude stops. |
| `cc-StopFailure.json` | Needs an API error. |
| `cc-CwdChanged.json` | The recorded prompt didn't change directory. |
| `cc-home-paths-PreToolUse.json` | A command full of home-directory paths and a secret, for the scrubbing tests. |

Two facts the recordings established, which the relay relies on:

- Claude Code's `-p` sessions send no `model` on SessionStart. Codex sends `model` on every event.
- Codex's PostToolUse for `Bash` carries the command's stdout and no exit code: `sys.exit(3)` looks
  exactly like success. So `hook/deck_ingest.py` reads the end of the output for a test runner's
  failure summary, and nothing else.

Re-record with `python -I scripts/record_hooks.py record --out <dir>` registered for every event in a
scratch repo, then `python scripts/record_hooks.py scrub <dir> tests/fixtures/hooks/<agent>`.
