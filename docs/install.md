# Installing Command Deck

## Requirements

- A Windows PC with Python 3.12+, Node 20.17+, Claude Code (tested with 2.1.306), and optionally
  Codex (tested with 0.162.1).
- Tailscale on the PC and the phone, with MagicDNS and HTTPS certificates enabled on the tailnet.
- An Even Realities G2 with the Even app 2.2.10+. Or no glasses: `evenhub-simulator` runs everything
  on the PC.

## Install

### 1. Run the relay

```powershell
python relay/relay.py      # gate and phone API on 127.0.0.1:8797, ingest on 8798, desktop page on 8799
```

State lives in `%APPDATA%\approval-gate`. Every listener is loopback only. The relay refuses to bind
`0.0.0.0`, and disarms the gate when it stops.

### 2. Tell it about your projects

Create `%APPDATA%\approval-gate\deck.projects.json` (or pass `--projects <file>`):

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

A session in a folder under a `root` belongs to that project, and so does a git worktree of it.
Sessions anywhere else are grouped as `(other)` and show only their folder name, or are hidden with
`"unmapped": "hide"`. `todos` lists the files whose `- [ ]` lines become to-dos. These are the only
files the glasses can ever change. This file stays on the PC.

### 3. Let the phone reach it

```powershell
tailscale serve --bg --https=443 http://127.0.0.1:8797
```

This publishes port 8797, and only 8797, to your tailnet at `https://<pc>.<tailnet>.ts.net`, with a
real certificate. Never serve 8798 (ingest) or 8799 (desktop page).

### 4. Pair the phone

```powershell
python relay/gatectl.py pair --label phone --relay-url https://<pc>.<tailnet>.ts.net `
    --plugin-url http://<tailnet-ip>:5180
```

This prints the device token once, plus a link that carries the relay URL and the token. The PC
stores only a hash of the token. `gatectl.py devices` lists paired devices, and
`gatectl.py revoke dev_…` unpairs one.

### 5. Install the plugin

The plugin's `network` whitelist is fixed into the package when it is built, and your relay's
address is yours alone, so every user builds their own. There are two ways to do it.

**A. Sideload the dev build.** This is the quickest; the PC serves the plugin:

```powershell
cd plugin
npm install
$env:GATE_DEV_HOST = "<tailnet-ip>"; npm run dev    # serve the plugin on the tailnet only
npx evenhub qr --url "<link from step 4>"           # scan with the Even app
```

**B. Pack your own `.ehpk`**, with your relay in the whitelist:

```powershell
cd plugin
npm install
npm run pack -- --relay https://<pc>.<tailnet>.ts.net --id com.<you>.commanddeck
```

This writes `app.local.json` (gitignored), whitelists only your relay, builds, and runs
`evenhub pack`. To install the package, use your own Even Hub developer account:

1. `evenhub login`
2. `evenhub create`
3. `evenhub promote <version> --to beta`
4. `evenhub testers add <your email>`
5. Install the beta from the Even app.
6. Open the plugin's phone page and paste the relay URL and token under **Pairing**.

The phone page is the gate's mirror and pairing form. The full Command Deck phone page (sections,
timelines, "Open on phone") is behind a build flag and off by default:
`$env:VITE_DECK_PHONE = "1"` before `npm run dev` or `npm run pack`. See the threat model.

Whether Even will list one shared build is an open question in
[docs/discord-questions.md](discord-questions.md).

### 6. Register the hooks

```powershell
python scripts/install_hook.py --deck --settings <project>/.claude/settings.json          # one project
python scripts/install_hook.py --deck --codex --settings <project>/.codex/hooks.json      # Codex, same project
python scripts/install_hook.py --deck                                                     # every project: ~/.claude/settings.json
python scripts/install_hook.py --uninstall --settings <file>
```

The gate hook goes on `PermissionRequest` (matcher `Bash|Write|Edit`, or `Bash|apply_patch` for
Codex, timeout 100). `--deck` adds the telemetry:

- **Claude Code** gets `http` hooks that post every event to the ingest port with a 2 s timeout.
  SessionStart, which Claude Code can't send over `http`, goes through `hook/deck_ingest.py`.
- **Codex** gets `hook/deck_ingest.py` with `async: true`.

The ingest key reaches Claude Code as `DECK_INGEST_TOKEN` in the `env` of `settings.local.json`,
never in a committed `settings.json`. A dead relay costs an agent nothing. The installer keeps a
`.approval-gate.bak` copy and leaves your other hooks alone.

For Codex, open `/hooks` in the session and trust the new hooks. Codex skips new or changed hooks
until you do, and loads a project's `.codex/` only in a trusted project. If your config uses
automatic review, start Codex with `-c approvals_reviewer=user` so approvals come to you.

### 7. Optional: a disarm hotkey

```powershell
$s = (New-Object -ComObject WScript.Shell).CreateShortcut(
    "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Command Deck - disarm.lnk")
$s.TargetPath = (Get-Command pythonw).Source
$s.Arguments = '"<repo>\relay\gatectl.py" disarm --source hotkey'
$s.Hotkey = "CTRL+ALT+D"
$s.Save()
```

## Configuration

| Port | What | Exposed |
|---|---|---|
| 8797 | Gate and phone API (`relay.py --port`) | To your tailnet, through `tailscale serve` |
| 8798 | Ingest for hook telemetry (`--ingest-port`) | Never: 127.0.0.1 only |
| 8799 | Desktop page and `/present` (`--desktop-port`) | Never: 127.0.0.1 only |
| 5180 | Plugin dev server (Vite, `strictPort`; `GATE_DEV_HOST` to move off loopback) | Only if you set `GATE_DEV_HOST` |
| 9898 | Simulator automation API | Development only |

8787 and 5173, the obvious picks, were taken on the development PC. The hooks find the relay
through `relay.json`, so a port change needs no other edits. These files live in
`%APPDATA%\approval-gate` (or `APPROVAL_GATE_HOME`):

| File | What |
|---|---|
| `armed` | Present while the gate is armed. The hook checks only this. |
| `hook.key`, `ingest.key` | The gate hook's and the telemetry's credentials, created on first start. |
| `devices.json` | Paired devices: id, label, token hash. |
| `relay.json` | The running relay's URLs and pid. |
| `deck.projects.json` | Your projects. Never sent anywhere. |
| `gate.log` | JSONL: requests, decisions, arm and disarm. |
| `deck.log` | JSONL: sessions, runs, approvals, focus blocks, to-dos done. Rotates at 10 MB. |
| `focus.json` | The focus block in progress, so it survives a restart. |
