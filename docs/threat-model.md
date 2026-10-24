# Threat model

Command Deck is a remote control for commands on your PC, and a live map of your work. Read this
before arming it.

**What it protects.** Your shell and files, which the agents act on. The credentials that can
approve: device tokens on the phone, and `hook.key` on the PC. And the shape of your work (project
names, paths, commands), which the deck makes visible.

**Who it assumes might misbehave:**

- **The agent.** A model can be wrong or prompt-injected. It can try to label a command as
  something else, hide it in a long chain, or reach for credentials.
- **Other devices on your tailnet.** They can reach the relay, but they hold no token.
- **Web pages in your browser.** They can send requests to localhost.
- **Time.** A stale prompt, a replayed decision, or a phone that went to sleep mid-prompt.

**What the gate does** (unchanged from Approval Gate):

- **Silence means terminal.** Timeout, malformed input, unknown id, relay down or restarted, gate
  disarmed, nobody watching, outside focus, a muted session: in every one of these cases the hook
  prints nothing, and the agent shows its own prompt. No error path prints `allow`.
- **The glasses can never approve these.** The hook sends them straight to the terminal:
  - `rm -rf` and its Windows equivalents, `git push --force`, `git reset --hard`, `git clean -f`
  - `sudo`/`runas`, `curl | sh`, `iex`, `eval`, `base64 -d`
  - anything touching `.ssh`, `.env`, `settings.json`, `.claude/`, `.git/hooks`, credential files,
    or the gate's own files
  - commands with invisible or bidi characters
  - commands too long to show in full (over 200 characters)
- **The glasses show the hook's summary.** The hook builds it from `tool_input`, never from the
  agent's description. In a long chain, the risky part leads (`rm -r build …+1`).
- **One decision per request.** The decision must echo a nonce, and expires after 90 s (45 s for
  high risk). A replayed, late or second decision gets `409`.
- **Nobody watching means terminal.** If no paired device has polled for 10 s, the relay refuses
  new requests and cancels pending ones. The relay disarms on stop, on plugin exit, and after 60 s
  with nobody polling.
- **No auto-approval, ever.** There is no "approve similar commands for N minutes", and muting a
  session sends its prompts to its terminal, never to an automatic yes.

**What the deck adds:**

- **Three credentials, each for one job.** `hook.key` creates gate requests. Device tokens decide
  them and read the deck. `ingest.key` can only post telemetry. Each gets `401` everywhere else.
- **Ingest is never on the tailnet.** It has its own port, 8798, on 127.0.0.1, which
  `tailscale serve` never publishes. A test checks that the phone port answers `404` to `/ingest`.
- **Nothing absolute leaves the PC.** The relay turns every path into a project-relative one at
  ingest. A path in no project becomes its last component, and the home directory never appears.
  Secret-shaped strings become `[secret]`. A test feeds hook fixtures full of `C:/Users/…` paths
  through every phone endpoint.
- **No agent text is stored.** Prompts, assistant messages, tool output and patch bodies are
  dropped at ingest. Codex's test output is read in its hook process, for a pass/fail flag, and
  never sent.
- **Writes are a closed list.** Approve or deny, arm or disarm, focus, to-do done, and mute (which
  only hides a session and routes its prompts to the terminal). The to-do write touches only files
  in `deck.projects.json`. It replaces exactly the one line whose hash matches, by temp file and
  rename. Nothing on the glasses, phone or desktop page runs a command.
- **The desktop page is loopback and read-only.** It refuses any `Host` but 127.0.0.1 or localhost
  (DNS rebinding), sends no CORS headers, has a strict CSP, and its event stream never counts as a
  device watching.
- **The phone page is off by default.** Public builds don't contain it. When it is on, paths are
  hidden until tapped, and the page blanks whenever the WebView goes to the background.
- **Caps.** Ingest parses at most 64 KB of a body. The registry keeps at most 200 sessions and
  2,000 runs, and `deck.log` rotates at 10 MB.

**What it does not protect against:**

- **Someone with your unlocked phone while the plugin is open.** They can read your project map,
  and with your glasses they can approve whatever comes in. So can anyone who pulls the device
  token out of the app. Disarm when you put the phone down, and revoke the device
  (`gatectl.py revoke`) if you lose it.
- **An agent posting telemetry.** Claude Code exports `DECK_INGEST_TOKEN` to the agent's own shell.
  With it, an agent can post fake session states or cards. It can't create or decide a prompt.
- **Harm done through files.** An agent can run a harmless-looking command that does damage through
  a script it wrote earlier. The glasses show the command, not what the script will do.
- **Clever disguises.** The denylist is pattern-based: a backstop, not a sandbox. A disguised `rm`
  reaches the glasses as what it literally says. The real check is you reading the summary.
- **A compromised PC, phone or Even app.** Whoever controls those controls the gate.
