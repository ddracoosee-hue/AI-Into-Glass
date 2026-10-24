# Using Command Deck

The G2 shows the glance and the decision. The desktop page and the phone page carry the detail.

**Gestures**, the same on every screen. The hint line names what tap does on each one.

| Gesture | Lists | Detail screens | A prompt |
|---|---|---|---|
| Swipe up / down | Move the highlight (›) | Scroll | Full command / summary |
| Tap | Open the row | The one action on the hint line | Approve, once the prompt has been up 0.7 s |
| Double-tap | Back; at the Deck, exit (disarming first) | Back | Deny |
| Long-press | Actions for the highlighted row | Actions for this item | Off |

Every tap waits 0.35 s in case it is half of a double-tap. Every screen ignores taps for its first
0.7 s.

**Screens:**

- **Deck.** Needs you, Sessions, Running, Projects, To-dos and Focus, with live counts. The status
  bar shows where you are, ARMED or OFF, the focus block, and the clock. A failed tool call or an
  API error pins a FAILED row to the top until you open it.
- **Needs you.** Live prompts first, then sessions waiting in their own terminal, then cards.
- **Sessions.** One row per agent session (`CC` Claude Code, `CDX` Codex), waiting first. A session
  opens to its state, its current tool and its counts.
- **Running.** Every open tool call and subagent, oldest first, in whole minutes. A run opens to a
  clock that ticks every second.
- **Projects.** Each project opens as a Deck of its own, with its branch, dirty files and recent
  commits.
- **To-dos.** The `- [ ]` lines of each project's files, plus Claude Code's own task list. Open one
  and tap again to mark it done. Exactly that one line becomes `- [x]`. If the line changed since it
  was read, nothing is written.
- **Focus.** Pin one or two projects for 25 min, 50 min or no timer. Everything outside focus
  leaves the glasses: its prompts go straight to their terminals, and its sessions show only as a
  dim count. When focus ends you get a summary (minutes, approvals, runs finished, to-dos done).
- **Since you looked.** When you open the plugin, one screen shows what finished, failed or fell
  back to the terminal while it was closed.

**Prompts outrank everything.** A permission prompt takes the screen from any depth. After you
answer, you are back on the same row. The prompt shows the session, the tool, a countdown, the
hook's one-line summary and `HIGH RISK` when it applies. A deny tells the agent why and asks it not
to retry.

**The desktop page** at `http://127.0.0.1:8799/` has the same sections, plus a timeline for each
session and a live ticker. It is read-only, and paths stay hidden until you click them.
**`/present`** is the Deck at 1920×1080 with large type, and it follows whatever page the glasses
show. **Cards:** any script on the PC can post one to Needs you:

```powershell
$key = Get-Content "$env:APPDATA\approval-gate\ingest.key"
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8798/ingest?agent=claude-code" `
  -Headers @{ Authorization = "Bearer $key" } -ContentType application/json `
  -Body '{"type":"card","title":"Nightly build failed","project":"glasses","level":"error"}'
```

`python relay/gatectl.py log` shows recent gate outcomes. `status` shows the armed state, and
`arm` and `disarm` work from the PC. Records are kept in `gate.log` (the gate) and `deck.log`
(sessions, runs, focus).
