# Approval Gate

Claude Code permission prompts on Even Realities G2 glasses. Tap approves, double-tap denies.
If nobody answers, the normal terminal prompt appears.

Even Hub plugins run only in the foreground, so the gate is a mode you turn on, not a background
notifier. While it is armed and the plugin is open, prompts go to the glasses. Otherwise everything
behaves as if the gate were not installed.

```
Claude Code ──PermissionRequest──▶ hook ──POST /requests──▶ relay ◀──GET /pending── plugin (phone) ──▶ glasses
            ◀──allow / deny / nothing── hook ◀──GET …/wait──  relay ◀──POST …/decide── plugin ◀── tap / double-tap
```

The hook, the relay and the plugin share no code, only [contract.md](contract.md).

| Piece | Where | What it is |
|---|---|---|
| `hook/approval_gate_hook.py` | PC | Claude Code `PermissionRequest` hook. Stdlib Python, run with `-I`. |
| `relay/relay.py` | PC | The only thing the phone talks to. Stdlib `http.server`, in-memory queue, JSONL log. |
| `relay/gatectl.py` | PC | Pair and revoke devices, arm and disarm from the PC, status. |
| `plugin/` | phone + glasses | Even Hub plugin (Vite + TypeScript + `@evenrealities/even_hub_sdk`). |

## Run it in the simulator

Needs Python 3.10+, Node 20.17+ and Claude Code 2.1.306+ (`args` in hook config).

```powershell
# 1. Relay (keep this terminal open). State lives in %APPDATA%\approval-gate.
python relay/relay.py                       # http://127.0.0.1:8797

# 2. Pair the simulator. Prints a link carrying the relay URL and a one-time-shown token.
python relay/gatectl.py pair --label simulator

# 3. Plugin dev server, then the simulator on the pairing link
cd plugin
npm install
npm run dev                                 # http://127.0.0.1:5180
npx evenhub-simulator "<link from step 2>" --automation-port 9898

# 4. Register the hook for every project (keeps a .approval-gate.bak copy of settings.json)
python scripts/install_hook.py
```

Tap the simulated glasses (or run `python scripts/sim.py click`) to arm. From then on, Claude Code
prompts for `Bash`, `Write` and `Edit` appear on the glasses. `scripts/sim.py` drives the simulator
(`click`, `double`, `down`, `up`, `shot`) for recording the demo.

To try the hook in one project first, use
`python scripts/install_hook.py --settings <project>/.claude/settings.local.json`. To remove it, run
`python scripts/install_hook.py --uninstall`.

## Gestures

| Screen | Tap | Double-tap | Swipe down / up |
|---|---|---|---|
| Gate off | Arm | Exit plugin | — |
| Armed, idle | — | Disarm | — |
| Prompt | Approve | Deny | Full command / summary |
| Approved / Denied | — | — | — |

A tap approves only after the prompt has been on screen for 0.7 s. The approval is then held for
0.35 s, so if the touchpad reports a double-tap as tap + double-tap, the deny wins.

The phone page mirrors the glasses and has a Disarm button, but it cannot approve. Approval needs the
glasses.

## On the phone (Phase 4)

The phone has to reach the relay over TLS, from anywhere. With Tailscale on the PC and the phone:

```powershell
tailscale serve --bg --https=443 http://127.0.0.1:8797   # needs MagicDNS + HTTPS certs on the tailnet
python relay/gatectl.py pair --label phone --relay-url https://<pc>.<tailnet>.ts.net `
    --plugin-url http://<tailnet-ip>:5180
$env:GATE_DEV_HOST = "<tailnet-ip>"; npm run dev          # in plugin/
npx evenhub qr --url "<link from pair>"                   # scan with the Even app
```

The relay keeps listening on loopback only. `tailscale serve` is what exposes it, on the tailnet only,
with a real certificate. That gives TLS without managing certificates, and there is no listener on
any other interface. Add `https://<pc>.<tailnet>.ts.net` to the `network` whitelist in
`plugin/app.json` before you pack.

## Security

This is a remote control for commands on your PC. These rules ship in v1:

- **Silence means terminal.** Timeout, malformed input, unknown id, relay down or restarted, gate
  disarmed, nobody watching: the hook prints nothing, and Claude Code shows its own prompt. No error
  path prints `allow`.
- **The glasses can never approve these.** The hook sends them straight to the terminal:
  - `rm -rf` and its Windows equivalents, `git push --force`, `git reset --hard`, `git clean -f`
  - `sudo`/`runas`, `curl | sh`, `iex`, `eval`, `base64 -d`
  - anything touching `.ssh`, `.env`, `settings.json`, `.claude/`, `.git/hooks`, or the gate's own files
  - commands with invisible or bidi characters
  - commands too long to show in full (over 200 characters)
- **The glasses show the hook's summary.** The hook builds it from `tool_input`, never from the
  agent's description. In a long chain, the risky part leads (`rm -r build …+1`).
- **Each side has its own credential.** The hook uses `hook.key`. Each phone uses a device token
  minted by `gatectl.py pair`, stored on the PC as a SHA-256 hash, and revocable. Logs carry the
  device id, never the token.
- **One decision per request.** A nonce from `/pending` must be echoed back. A replayed, late or
  second decision gets `409`.
- **Short expiry.** 90 s, or 45 s for high risk. If no device has polled for 10 s, the relay refuses
  new requests and cancels pending ones, so a sleeping phone costs nothing.
- **Arming is explicit.** Arm from the glasses or with `gatectl.py arm`. Disarm with a double-tap,
  the phone button, or `gatectl.py disarm` (bind it to a hotkey). Stopping the relay disarms.
- **Nothing secret leaves the PC.** No file contents (Write and Edit send a path and a line count),
  home paths become `~`, and secret-shaped strings become `[secret]`.
- **Everything is logged.** Every request, decision, arm and disarm goes to
  `%APPDATA%\approval-gate\gate.log` (JSONL).

What this does **not** protect against:

- **Someone with your unlocked phone and your glasses while the gate is armed.** They can approve
  whatever comes in. Disarm when you put them down.
- An agent that writes a harmless-looking command which does harm through files it wrote earlier.
  The glasses show the command, not what the scripts it calls will do.
- The denylist is pattern-based. It is a backstop, not a sandbox. The real check is you reading the
  summary.

## Tests

```powershell
python -m unittest discover -s relay     # 18 relay tests
python -m unittest discover -s hook      # 22 hook tests, incl. the real hook run with python -I against a live relay
cd plugin; npx tsc --noEmit; npx vite build
```
