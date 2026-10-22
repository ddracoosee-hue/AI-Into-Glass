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

## Phase C: Sessions, Needs you, Running

| # | The plan said | The code does | Why |
|---|---|---|---|
| C1 | Writes are a closed list: approve or deny, arm or disarm, focus, to-do done | The list gains mute: `POST /sessions/{id}/mute {muted}`. A muted session is hidden from the glasses, and its gate requests get `409 muted`, so they go straight to the terminal | "Muting is local … its approvals still go to the terminal". Only the relay can route a request, so the mute has to live there. Muting never approves anything |
| C2 | The spotlight row is pinned "until opened" | The relay keeps the newest five failures; the plugin remembers which ones it opened | Opening is about what this pair of glasses has seen. The desktop page shows the failures regardless |
| C3 | (Cursor) | The cursor follows its row's key, not its index | When a failure pins a row above, or a session starts waiting and sorts to the top, the highlight stays on the row you were on |
| C4 | Since-you-looked cursor in `localStorage` | Bridge storage, with the `deck.*` keys also mirrored to the WebView's own `localStorage` | evenhub-simulator keeps bridge storage in memory, so it is empty after every launch. Its WebView profile persists. The pairing token is not mirrored. HARDWARE #9 |
| C5 | Running: every open tool call and subagent | Also runs waiting on a permission prompt | They are open runs, and the longest wait is often the one that matters |
| C6 | Session menu: "Jump to its running tool" | Opens that run's detail page (`session.current.run`), or the Running list when it has none | The relay now names the run in `current` |
| C7 | Phase C acceptance with Codex live | `sim.py run running` runs Codex with `-s danger-full-access` | Codex's sandbox can't start while the Codex desktop app is open (A7). The prompt is fixed and the repo is throwaway |

Simulator runs added: `sim.py run running` (live agents), `run tick` and `run since`.

## Phase D: projects and to-dos

| # | The plan said | The code does | Why |
|---|---|---|---|
| D1 | To-do menu: "Move to top" | Reorders the list on the glasses only, kept in plugin storage. Files never change | The closed write list has exactly one to-do write, done |
| D2 | `todo.id` is a hash of the file and the line text | A hash of project, file, line text and occurrence number | Two identical lines in one file would otherwise share an id, and "done" has to flip exactly one |
| D3 | (Tap, tap) | The to-do screen keeps the last copy it read, even after the line changes on disk. The second tap still goes to the relay, which answers 409, and the screen says CHANGED ON DISK | The glasses never decide on their own that a file changed. The relay's hash check is the only judge |
| D4 | (Zero console errors) | Simulator runs fail on any console error except the ones a run provokes on purpose, each listed in the run by its exact message | evenhub-simulator logs every non-2xx fetch as an error, including the 409 that Phase D's acceptance asks for |

## Phase E: focus mode

| # | The plan said | The code does | Why |
|---|---|---|---|
| E1 | Long-press a project, then Focus on this | Starts a 25-minute focus on that one project, replacing any focus already on | One long-press should be enough to start. The picker is where you pin two projects, or pick 50 minutes or no timer |
| E2 | Focus picker: tap pins or unpins (max 2) | The picker shows ○ and ● rows for projects. Once one is pinned, `Start … 25 min`, `50 min` and `no timer` rows appear, plus `End focus` while focus is on | A tap must start focus somewhere, and the plan named no gesture for picking the length |
| E3 | The summary shows for 4 s when focus ends | It also shows for ends the glasses didn't cause (the timer, a disarm on the PC). The plugin notices focus vanish between polls and reads `GET /focus`'s `last_summary` | The summary is the closing beat however the block ends |
| E4 | (Menu input) | Swipes and taps while the menu is open are dropped by the page | evenhub-simulator hands menu swipes to the page underneath as well. Its menu opens on its first item and wraps around at the ends |
| E5 | A list's long-press menu | Fixed per row type (sessions, runs, projects, to-dos), not built from the rows | The menu is declared when the page is built, usually before its rows arrive. A data-built menu fell back to one item and stayed that way |
| E6 | "A git push from a hub session falls back in under 0.5 s" | `sim.py run focus` times the real gate hook process (`python -I hook/approval_gate_hook.py`) on a PermissionRequest from hub: 0.16 s, no output | That is the program Claude Code runs. Timing it directly measures the fallback, not the model's thinking time |

## Phase F: desktop page and phone page

| # | The plan said | The code does | Why |
|---|---|---|---|
| F1 | One web UI, two hosts | One plain ES module, `web/deck-ui.js`, with no build step. The relay serves it on 127.0.0.1:8799; the plugin imports the same file for its phone page | The relay stays stdlib Python with nothing to compile, and the phone page can't drift from the desktop page |
| F2 | Desktop page on 8799, loopback only | Read-only: `/api/*` reads, no writes and no token. A `Host` header that isn't 127.0.0.1 or localhost gets 403, there are no CORS headers, and a strict CSP applies | A web page in your browser can't read it, not even through DNS rebinding. Writes stay on the glasses |
| F3 | (Presence) | The desktop page's `/events` long-poll never counts as a device watching | Otherwise an open desktop tab would send prompts to glasses nobody is wearing |
| F4 | `/present` "follows" the glasses (beat sheet) | The plugin reports the page it shows with `POST /view` (device token). The relay keeps it in memory as a `view` event, never written to `deck.log`, and `/present` follows it | Nothing else tells the relay what the glasses show. It is display state and changes nothing an agent does |
| F5 | Phone page "behind its flag" | `VITE_DECK_PHONE=1` at build time; off by default. The public build contains none of the deck UI code (checked). The devstack turns it on | "Phone page off by default in public builds" |
| F6 | Full paths hidden until clicked | Every target on the desktop and phone pages (commands, files, patterns) shows a "show" button until clicked, plus a "Show all paths" toggle. `/present` shows them, since it has no clicks and the demo repos are throwaway | The phone page must hide them; the desktop does the same so the two read alike |
| F7 | Lighthouse accessibility ≥ 90 | 100 on the Deck, the Sessions view and `/present` (Lighthouse 12 via npx, approved Oct 22) | Landmarks, a skip link, real headings, focus moved to the new heading after each transition, one polite live region for approvals and failures, and contrast that passes on the dark theme |
| F8 | (Tooling) | `scripts/cdp.mjs` drives headless Chrome over the DevTools protocol with Node's built-in WebSocket: screenshots, evals, and sampling the live page every 250 ms | `--headless --screenshot` never finishes on a page that long-polls, and there was nothing to install |

## Phase G: the demo package

| # | The plan said | The code does | Why |
|---|---|---|---|
| G1 | Capture each window with ffmpeg `gdigrab`, then compose with `hstack`/`vstack` | Nothing is captured from the screen. The glasses pane is the simulator's own framebuffer (`/api/screenshot/glasses`, about 25 Hz). The right-hand panes are headless Chrome screencasts (`scripts/cdp.mjs record`) of `/present`, a live view of `TODO.md`, and a terminal pane that prints each agent's own output stream. ffmpeg composes the 1920×1080 frame from those | It can't miss a frame to a moved window, the 150% display scaling, a notification or the mouse cursor, and it doesn't take over your screen. `demo_stage.py --reset` then `demo_run.py --record` rebuilds the take exactly. The pixels are the simulator's own, without its window's glow |
| G2 | `claude -p` for the push, the delete and the out-of-focus write | Two long-lived Claude Code sessions, one per repo, use streaming input (`--input-format stream-json`), so each prompt goes out on cue, and `--permission-prompt-tool stdio`, so this side plays the terminal | With `-p`, sessions end as soon as they answer, so the Deck couldn't show three live sessions. A `-p` session can't wait at a prompt either. With stdio, an out-of-focus write really waits in its own terminal, and the Deck counts it as waiting, as the beat sheet says |
| G3 | Beat order: push, deny, then focus, then the to-do and the summary | Focus comes first (beat 5), then the push (6), the deny (7) and the to-do (8). The summary still closes the video | The summary counts what happened during focus. To show "1 approved, 1 denied, 1 to-do done", as the beat sheet asks, the push and the deny have to fall inside the focus block |
| G4 | Every beat is a fixed window of the timeline | Each beat is one or more windows of the raw take, anchored on marks the driver logs (a prompt appearing, a tap landing). Beat 5 skips the seconds the hub session spends thinking | Agent latency varies from run to run. Anchoring on what happened, rather than on the clock, keeps every caption on its moment. The cut is reproducible from the take's `take.json` |
| G5 | Codex runs the tests "without a prompt" | `codex exec` (approval: never) with `-s danger-full-access` | Its Windows sandbox can't start while the Codex desktop app holds one of its runtime files (A7). The prompt only runs the test suite. The terminal pane names the run `codex · demo-hub` and doesn't show the flags |
| G6 | The failing test | A real bug: `backoff()` in demo-hub is off by one and leaks its cap by two seconds, so `test_backoff_caps_at_30s` sees 32.0 | Background activity comes from real tasks, never replay |
| G7 | (Claude Code's permission rules) | The demo sessions get their allow list on the command line (`--allowedTools`), in both the `Bash(…)` and `PowerShell(…)` forms | Claude Code 2.1.306 didn't apply the project settings' `permissions` in this mode, though project hooks ran. On Windows it sometimes picks its PowerShell tool for the same command |
| G8 | (The terminal pane) | While a prompt is up on the glasses, the terminal pane shows Claude Code asking there too. When the glasses answer, it shows "Answered on the glasses." | Claude Code runs the PermissionRequest hook alongside its own prompt, and the first answer wins. The terminal really is asking at that moment |
| G9 | (Where the raw take goes) | The raw frames go to `C:/deck-sandbox/demo/take`, and only the cut goes to `demo/`. If the glasses screen changes during a gap in the capture, that change reaches the video late. If it is more than 1 s late inside the beats, the take fails and the log says when | One take's frames went to the exFAT H: drive, and every pane froze for six seconds, the glasses capture and a Chrome screencast together, while the relay logged the focus end on time. The summary beat came out showing the Deck instead of the summary. Now a stall like that fails the take instead of reaching the cut |
| G10 | Error ids `err_<seq>` | Error ids carry the relay's clock as well (`err_<hex ms><hex seq>`) | `seq` restarts with the relay. After a restart, a new failure reused an old id, and the glasses treated it as already opened, so the FAILED row never pinned. Rehearsals restart the relay every take, which is how this surfaced |
| G11 | `/present` is a fixed 1920×1080 page | It fills its window (`100vw` × `100vh`) and is laid out for 1920×1080. In the demo it fills the 1536×1080 screencast | Session rows give the place column at most 36–40% of the row and let the command wrap (one line, with an ellipsis, in the presenter's previews). The previews stack, and each shows its top row. A focus block's end reads "ended from the Deck: 1 approval, 1 denied…" rather than the raw reason. At 1536 px the fixed-width columns had pushed the command off the edge, one letter per line |
