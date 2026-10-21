# Approval Gate

Claude Code permission prompts on your Even Realities G2 glasses. Tap to approve, double-tap to
deny. If nobody answers, the normal terminal prompt appears.

Even Hub plugins run only in the foreground, so the gate is a mode you turn on, not a background
notifier. While it is armed and the plugin is open, prompts go to the glasses. Otherwise Claude Code
behaves as if the gate were not installed.

```
Claude Code ──PermissionRequest──▶ hook ──POST /requests──▶ relay ◀──GET /pending── plugin (phone) ──▶ glasses
            ◀──allow / deny / nothing── hook ◀──GET …/wait──  relay ◀──POST …/decide── plugin ◀── tap / double-tap
```

| Piece | Runs on | What it is |
|---|---|---|
| `hook/approval_gate_hook.py` | PC | Claude Code `PermissionRequest` hook. Stdlib Python, run with `-I`. |
| `relay/relay.py` | PC | The only thing the phone talks to. Stdlib `http.server`, in-memory queue, JSONL log. |
| `relay/gatectl.py` | PC | Pair and revoke devices, arm and disarm, status, log. |
| `plugin/` | phone + glasses | Even Hub plugin: Vite, TypeScript, `@evenrealities/even_hub_sdk`. |

The three pieces share no code, only [contract.md](contract.md). Where the code differs from the
original build plan, and why, is in [docs/plan-deltas.md](docs/plan-deltas.md).

## Requirements

- Windows PC with Python 3.10+ (3.12+ for the demo recorder), Node 20.17+, and Claude Code (tested
  with 2.1.306).
- Tailscale on the PC and the phone, with MagicDNS and HTTPS certificates enabled on the tailnet.
- An Even Realities G2 with the Even app 2.2.10+. Or no glasses: `evenhub-simulator` runs the whole
  flow on the PC.

## Install

### 1. Run the relay

```powershell
python relay/relay.py                         # 127.0.0.1:8797. Keep it running.
```

State lives in `%APPDATA%\approval-gate`. The relay listens on loopback only, refuses to bind
`0.0.0.0`, and disarms the gate when it stops.

### 2. Let the phone reach it

```powershell
tailscale serve --bg --https=443 http://127.0.0.1:8797
```

This publishes the relay to your tailnet only, at `https://<pc>.<tailnet>.ts.net`, with a real
certificate. There is no port forwarding, no public IP, and no certificate to manage.

### 3. Pair the phone

```powershell
python relay/gatectl.py pair --label phone --relay-url https://<pc>.<tailnet>.ts.net `
    --plugin-url http://<tailnet-ip>:5180
```

This prints the device token once, plus a link carrying the relay URL and the token. The PC stores
only a hash of the token. `gatectl.py devices` lists paired devices, and `gatectl.py revoke dev_…`
unpairs one.

### 4. Install the plugin

The plugin's `network` whitelist is fixed into the package when it is built, and your relay's
address is yours alone. So every user builds their own. Two ways:

**A. Sideload the dev build** (quickest; the PC serves the plugin):

```powershell
cd plugin
npm install
$env:GATE_DEV_HOST = "<tailnet-ip>"; npm run dev    # serve the plugin on the tailnet only
npx evenhub qr --url "<link from step 3>"           # scan with the Even app
```

**B. Pack your own `.ehpk`** with your relay in the whitelist:

```powershell
cd plugin
npm install
npm run pack -- --relay https://<pc>.<tailnet>.ts.net --id com.<you>.approvalgate
```

This writes `app.local.json` (gitignored) from `app.json`, whitelists only your relay, builds, and
runs `evenhub pack`. To install it, use your own Even Hub developer account: `evenhub login`,
`evenhub create approval-gate.ehpk --name "Approval Gate" --tagline "Claude Code prompts on G2"`,
`evenhub promote 0.1.0 --to beta`, then `evenhub testers add <your email>`. After that, install the
beta from the Even app. Open the plugin's phone page once, and paste the relay URL and token under
**Pairing**.

Whether Even will list one shared build is an open question in
[docs/discord-questions.md](docs/discord-questions.md).

### 5. Register the hook

```powershell
python scripts/install_hook.py                # every project: ~/.claude/settings.json
python scripts/install_hook.py --settings <project>/.claude/settings.local.json   # or one project
python scripts/install_hook.py --uninstall
```

The installer keeps a `.approval-gate.bak` copy of the settings file and leaves your other hooks
alone. It registers `PermissionRequest` with matcher `Bash|Write|Edit` and `timeout: 100`. While the
gate is disarmed, the hook exits in about 0.1 s and prints nothing.

### 6. Optional: a disarm hotkey

```powershell
$s = (New-Object -ComObject WScript.Shell).CreateShortcut(
    "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Approval Gate - disarm.lnk")
$s.TargetPath = (Get-Command pythonw).Source
$s.Arguments = '"<repo>\relay\gatectl.py" disarm --source hotkey'
$s.Hotkey = "CTRL+ALT+D"
$s.Save()
```

## Use

Open the plugin on the glasses and tap to arm. From then on, Claude Code's prompts for `Bash`,
`Write` and `Edit` come to the glasses.

| Screen | Tap | Double-tap | Swipe down / up |
|---|---|---|---|
| Gate off | Arm | Exit (disarming first) | — |
| Armed, idle | — | Disarm | — |
| Prompt | Approve | Deny | Full command / summary |
| Approved / Denied | — | — | — |

A prompt shows the session, the tool, a countdown, the hook's one-line summary and `HIGH RISK` when
it applies. A tap approves only after the prompt has been on screen for 0.7 s, and the approval
then waits 0.35 s, so a double-tap that the touchpad reports as tap + double-tap still denies. A
deny tells Claude why and asks it not to retry.

The phone page mirrors the glasses and has a Disarm button. It cannot approve.

`python relay/gatectl.py log` shows recent outcomes. `status` shows the armed state and the relay
URL. `arm` and `disarm` work from the PC. Everything is also in `%APPDATA%\approval-gate\gate.log`.

## Threat model

Approval Gate is a remote control for commands on your PC. Read this before arming it.

**What it protects.** Your shell and files, which Claude Code acts on. Also the credentials that
can approve: device tokens on the phone, and `hook.key` on the PC.

**Who it assumes might misbehave:**

- **The agent.** A model can be wrong or prompt-injected. It can try to label a command as
  something else, hide it in a long chain, or reach for credentials.
- **Other devices on your tailnet.** They can reach the relay, but they hold no token.
- **Time.** A stale prompt, a replayed decision, or a phone that went to sleep mid-prompt.

**What ships in v1:**

- **Silence means terminal.** Timeout, malformed input, unknown id, relay down or restarted, gate
  disarmed, nobody watching: the hook prints nothing, and Claude Code shows its own prompt. No error
  path prints `allow`.
- **The glasses can never approve these.** The hook sends them straight to the terminal:
  - `rm -rf` and its Windows equivalents, `git push --force`, `git reset --hard`, `git clean -f`
  - `sudo`/`runas`, `curl | sh`, `iex`, `eval`, `base64 -d`
  - anything touching `.ssh`, `.env`, `settings.json`, `.claude/`, `.git/hooks`, credential files,
    or the gate's own files
  - commands with invisible or bidi characters
  - commands too long to show in full (over 200 characters)
- **The glasses show the hook's summary.** The hook builds it from `tool_input`, never from the
  agent's description. In a long chain, the risky part leads (`rm -r build …+1`).
- **Separate credentials.** The hook uses `hook.key`, which can create requests but not decide them.
  Each phone has its own device token, which can decide but not create, is stored on the PC as a
  hash, and can be revoked. Logs name the device id, never the token.
- **One decision per request.** The decision must echo the nonce from `/pending`. A replayed, late
  or second decision gets `409`.
- **Short expiry.** 90 s, or 45 s for high risk.
- **Nobody watching means terminal.** If no device has polled for 10 s, the relay refuses new
  requests and cancels pending ones. A phone that sleeps mid-prompt hands the prompt back to the
  terminal at once, and its late tap gets `409`.
- **Arming is explicit and visible.** The idle screen always shows the armed state. You can disarm
  with a double-tap, the phone button, the hotkey, or `gatectl.py disarm`. The relay disarms on
  stop, on plugin exit, and after 60 s with no device polling. `gate.log` records the source of
  every switch.
- **Nothing secret leaves the PC.** Write and Edit send a path and a line count, never contents.
  Home paths become `~`, and secret-shaped strings become `[secret]`.
- **Everything is logged.** Every request, decision, arm and disarm goes to `gate.log` (JSONL),
  with timestamps and the deciding device.

**What it does not protect against:**

- **Someone with your unlocked phone while the gate is armed.** The phone page can't approve, but
  with your glasses they can approve whatever comes in. So can anyone who pulls the device token out
  of the app. Disarm when you put the phone down, and revoke the device (`gatectl.py revoke`) if you
  lose it.
- **Harm done through files.** An agent can run a harmless-looking command that does damage through
  a script it wrote earlier. The glasses show the command, not what the script will do.
- **Clever disguises.** The denylist is pattern-based, so it is a backstop, not a sandbox. A
  disguised `rm` reaches the glasses as what it literally says, and the real check is you reading
  the summary.
- **A compromised PC, phone or Even app.** Whoever controls those controls the gate.

## Configuration

| Port | What | Why this one |
|---|---|---|
| 8797 | Relay, `127.0.0.1` only (`relay.py --port`) | 8787, the obvious pick, is the Orion MCP host on the development PC |
| 5180 | Plugin dev server (Vite, `strictPort`, `GATE_DEV_HOST` to move off loopback) | 5173, Vite's default, was already in use |
| 9898 | Simulator automation API | The simulator README's own example |

The hook finds the relay through `relay.json`, so changing the port needs no other edits. Files in
`%APPDATA%\approval-gate` (or `APPROVAL_GATE_HOME`):

| File | What |
|---|---|
| `armed` | Present while the gate is armed. The hook checks only this. |
| `hook.key` | The hook's credential, created by the relay on first start. |
| `devices.json` | Paired devices: id, label, token hash. |
| `relay.json` | The running relay's URL and pid. |
| `gate.log` | JSONL record of requests, decisions, arm and disarm. |

## Develop

Without glasses, the simulator runs the whole loop:

```powershell
python relay/relay.py
python relay/gatectl.py pair --label simulator           # prints the simulator command with the link
cd plugin; npm install; npm run dev
npx evenhub-simulator "<link>" --automation-port 9898
python scripts/sim.py click | double | down | up | shot glasses.png
```

Tests:

```powershell
python -m unittest discover -s relay     # round trip, nonce, expiry, presence, backstop, auth, sources
python -m unittest discover -s hook      # rules and redaction, plus the real hook run with python -I against a live relay
python -m unittest discover -s scripts   # demo cut planning
cd plugin; npm test; npx tsc --noEmit    # gestures (tap then double-tap denies), types
```

The 60-second demo is scripted. `python scripts/demo.py check`, then `record`, then `cut`. It needs
ffmpeg (`winget install Gyan.FFmpeg.Essentials`). See the docstring at the top of `scripts/demo.py`.

**Unverified until it runs on hardware:** whether the re-poll on `FOREGROUND_ENTER_EVENT` fires, and
how the G2 firmware wraps the summary. Both are marked `TODO(hardware)` in the code. Two open
questions from the plan also need hardware: whether the WebView survives the phone's screen turning
off (the nobody-watching rule covers the case where it doesn't), and whether a 45 s countdown is
readable at a glance.

## License

MIT. See [LICENSE](LICENSE).
