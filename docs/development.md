# Development

The devstack runs the relay, Vite and the simulator against a throwaway state directory
(`C:/deck-sandbox/state`), so your own pairing and logs are untouched:

```powershell
python scripts/devstack.py up            # relay, Vite (phone page on), simulator, paired
python scripts/devstack.py seed          # live sessions in two scratch projects, from real hook fixtures
python scripts/sim.py run walk           # also: interrupt, running, tick, since, todo, focus, desktop
python scripts/devstack.py down
```

Each `sim.py run` drives the simulator through its automation API and fails on any console error.
`running` and the demo use real Claude Code and Codex sessions.

Tests:

```powershell
python -m unittest discover -s relay     # gate, registry, scrubbing, ingest, focus, to-dos, desktop page
python -m unittest discover -s hook      # rules and redaction, plus the real hook against a live relay
python -m unittest discover -s scripts   # demo cut, hardware checklist
cd plugin; npm test; npx tsc --noEmit    # gestures, screens, pixel fitting, types
```

Style is enforced in CI. Python uses ruff, and TypeScript, JavaScript and CSS use prettier; settings live in
`pyproject.toml` and `.prettierrc.json`:

```powershell
ruff check .; ruff format .              # lint, then format Python
npx prettier --write .                   # format TS, JS and CSS
```

The hook fixtures in `tests/fixtures/hooks/` are real Claude Code and Codex payloads with the home
directory scrubbed (see the README there). Everything was built against `evenhub-simulator` 0.9.5.
What only a G2 can confirm is listed in [docs/hardware-checklist.md](hardware-checklist.md),
and marked `HARDWARE:` in the code.

## Reproducing the demo

The demo is reproducible from a cold start, and every action in it is live:

```powershell
python scripts/demo_stage.py              # two throwaway repos in C:/deck-sandbox/demo, project-level hooks, the stack
python scripts/demo_run.py                # rehearse: drive every beat and check the outcomes
python scripts/demo_stage.py --reset      # back to the start, in about 15 s
python scripts/demo_run.py --record       # record, then cut into demo/
python scripts/demo_run.py --cut          # re-cut after editing demo/captions.srt
python scripts/demo_stage.py --reset; python scripts/demo_short.py   # the short, slow clip: Deck, Running, one approval
python scripts/demo_stage.py --teardown
```

`demo/` holds the video, `captions.srt`, six stills, `deck-loop.gif` and `command-deck-short.mp4`
(about 14 s, each screen held long enough to read). The glasses pane is the
simulator's own framebuffer. The right-hand panes are Chrome screencasts of `/present`, the
`TODO.md` file, and each agent's output stream. Nothing is captured from your screen. The raw take
stays in `C:/deck-sandbox/demo/take`, and a take whose capture stalls fails its checks and is not cut.
