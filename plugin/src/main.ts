import {
  waitForEvenAppBridge,
  TextContainerProperty,
  CreateStartUpPageContainer,
  TextContainerUpgrade,
} from '@evenrealities/even_hub_sdk'
import { AuthError, RelayClient, type PendingRequest, type Poll } from './relay'
import { createInput, type Screen } from './input'
import { mountPhone, type Frame } from './phone'

const POLL_MS = 2000
const RESULT_MS = 1500
const KEY_RELAY = 'gate.relay'
const KEY_TOKEN = 'gate.token'

type View =
  | { kind: 'unpaired' }
  | { kind: 'offline' }
  | { kind: 'idle'; armed: boolean; waiting: number; session: string }
  | { kind: 'prompt'; req: PendingRequest; deadline: number; shownAt: number; detail: boolean; waiting: number; sending: boolean }
  | { kind: 'result'; text: string }

type Slot = keyof Frame
const LAYOUT: Record<Slot, { id: number; x: number; y: number; w: number; h: number; border?: boolean; capture?: boolean }> = {
  head: { id: 1, x: 0, y: 0, w: 470, h: 36 },
  clock: { id: 2, x: 470, y: 0, w: 106, h: 36 },
  body: { id: 3, x: 0, y: 40, w: 576, h: 162, border: true, capture: true },
  risk: { id: 4, x: 0, y: 206, w: 576, h: 36 },
  hint: { id: 5, x: 0, y: 248, w: 576, h: 36 },
}
const SLOTS = Object.keys(LAYOUT) as Slot[]
// Text brightness, 0..4. The body and a high-risk warning get the brightest level.
const BRIGHTNESS: Record<Slot, number> = { head: 3, clock: 4, body: 4, risk: 4, hint: 2 }

const bridge = await waitForEvenAppBridge()
const now = () => performance.now()

// ---- storage ---------------------------------------------------------------

async function load(key: string): Promise<string> {
  try {
    return (await bridge.getLocalStorage(key)) || ''
  } catch {
    return localStorage.getItem(key) ?? ''
  }
}

async function store(key: string, value: string): Promise<void> {
  try {
    await bridge.setLocalStorage(key, value)
  } catch {
    if (value) localStorage.setItem(key, value)
    else localStorage.removeItem(key)
  }
}

// A pairing link (`gatectl.py pair`) carries ?relay=…&token=…; keep it, then drop it from the address.
const params = new URLSearchParams(location.search)
if (params.get('relay') && params.get('token')?.startsWith('agt_')) {
  await store(KEY_RELAY, params.get('relay')!)
  await store(KEY_TOKEN, params.get('token')!)
  history.replaceState(null, '', location.pathname)
}
let client: RelayClient | null = null
{
  const [relay, token] = [await load(KEY_RELAY), await load(KEY_TOKEN)]
  if (relay && token) client = new RelayClient(relay, token)
}

// ---- rendering -------------------------------------------------------------

let view: View = client ? { kind: 'offline' } : { kind: 'unpaired' }

function frameFor(v: View): Frame {
  const blank = { head: 'APPROVAL GATE', clock: '', body: '', risk: '', hint: '' }
  switch (v.kind) {
    case 'unpaired':
      return { ...blank, body: 'NOT PAIRED\nRun gatectl.py pair on the PC\nand open the link on your phone.', hint: 'double-tap: exit' }
    case 'offline':
      return { ...blank, body: 'RELAY OFFLINE\nRetrying every 2 s.', hint: 'tap: retry  ·  double-tap: exit' }
    case 'idle':
      return v.armed
        ? { ...blank, head: v.session || 'APPROVAL GATE', body: `GATE ARMED · ${v.waiting} waiting`, hint: 'double-tap: disarm' }
        : { ...blank, body: 'GATE OFF\nPrompts stay in the terminal.', hint: 'tap: arm  ·  double-tap: exit' }
    case 'prompt': {
      const secs = Math.max(0, Math.ceil((v.deadline - now()) / 1000))
      const more = v.waiting > 1 ? `  (+${v.waiting - 1})` : ''
      // Two agents can share a folder, so the header names which one is asking.
      const agent = ({ 'claude-code': 'Claude', codex: 'Codex' } as Record<string, string>)[v.req.session.agent] ?? v.req.session.agent
      return {
        head: `${v.req.session.name} · ${agent} · ${v.req.tool}${more}`,
        clock: v.sending ? '…' : `${secs}s`,
        // TODO(hardware): check how the G2 firmware wraps this. evenhub-simulator 0.9.5 wraps by
        // character, so `rm -r build` broke as "rm -" / "r build", and the simulator says its font
        // rendering may not match the glasses. If the firmware also wraps mid-token, pre-wrap at
        // spaces with measureTextWrap from @evenrealities/pretext (inner width 568 px). Then retune
        // the hook's SUMMARY_MAX (60) to what reads at a glance. Unverified: needs a G2.
        body: v.detail ? v.req.detail : v.req.summary,
        risk: v.req.risk === 'high' ? 'HIGH RISK' : v.req.risk,
        hint: v.sending ? 'sending…' : `tap: approve  ·  double-tap: deny  ·  swipe ${v.detail ? 'up: summary' : 'down: full'}`,
      }
    }
    case 'result':
      return { ...blank, body: v.text }
  }
}

const painted: Partial<Record<Slot, string>> = {}
let writes: Promise<unknown> = Promise.resolve()
let mirror: (frame: Frame) => void = () => {}

// Serialized and deduplicated: only containers whose text changed are written.
function render(): void {
  const frame = frameFor(view)
  mirror(frame)
  for (const slot of SLOTS) {
    const content = frame[slot] || ' '
    if (painted[slot] === content) continue
    painted[slot] = content
    const { id } = LAYOUT[slot]
    const brightness = slot === 'risk' && view.kind === 'prompt' && view.req.risk !== 'high' ? 2 : BRIGHTNESS[slot]
    writes = writes
      .then(() => bridge.textContainerUpgrade(new TextContainerUpgrade({ containerID: id, containerName: slot, content, textColor: brightness })))
      .catch(err => console.error('textContainerUpgrade failed', err))
  }
}

function setView(next: View): void {
  if (next.kind !== 'prompt') input.cancelTap()
  view = next
  render()
}

{
  const first = frameFor(view)
  const textObject = SLOTS.map(slot => {
    const l = LAYOUT[slot]
    painted[slot] = first[slot] || ' '
    return new TextContainerProperty({
      xPosition: l.x, yPosition: l.y, width: l.w, height: l.h,
      borderWidth: l.border ? 1 : 0, borderColor: l.border ? 6 : 0, borderRadius: l.border ? 6 : 0,
      paddingLength: 4, containerID: l.id, containerName: slot,
      content: painted[slot], textColor: BRIGHTNESS[slot], isEventCapture: l.capture ? 1 : 0,
    })
  })
  const created = await bridge.createStartUpPageContainer(new CreateStartUpPageContainer({ containerTotalNum: SLOTS.length, textObject }))
  if (created !== 0) console.error('createStartUpPageContainer failed:', created)
}

// ---- relay sync --------------------------------------------------------------

let pollTimer: number | undefined
let polling = false
let pollAgain = false
let foreground = true
let resultUntil = 0
let latest: Poll | null = null

async function poll(): Promise<void> {
  if (polling) {
    pollAgain = true
    return
  }
  polling = true
  window.clearTimeout(pollTimer)
  try {
    if (!client) return setView({ kind: 'unpaired' })
    latest = await client.poll()
    apply(latest)
  } catch (err) {
    if (err instanceof AuthError) setView({ kind: 'unpaired' })
    else if (view.kind !== 'result') setView({ kind: 'offline' })
  } finally {
    polling = false
    if (pollAgain) {
      pollAgain = false
      void poll()
    } else if (foreground) {
      pollTimer = window.setTimeout(() => void poll(), POLL_MS)
    }
  }
}

// The relay is the source of truth: whatever it says replaces local state.
function apply(p: Poll): void {
  if (view.kind === 'result' && now() < resultUntil) return // finish showing the result first
  if (view.kind === 'prompt' && view.sending) return
  const req = p.request
  if (req && view.kind === 'prompt' && view.req.id === req.id) {
    const deadline = now() + req.expires_in * 1000
    // Follow the relay's clock, but don't let a ±1 s poll jitter shake the countdown.
    view = { ...view, waiting: p.waiting, deadline: Math.abs(deadline - view.deadline) > 1500 ? deadline : view.deadline }
    return render()
  }
  if (req) {
    return setView({ kind: 'prompt', req, deadline: now() + req.expires_in * 1000, shownAt: now(), detail: false, waiting: p.waiting, sending: false })
  }
  if (view.kind === 'prompt') return showResult(view.deadline <= now() + 1000 ? 'EXPIRED' : 'WITHDRAWN\nanswered elsewhere or cancelled')
  setView({ kind: 'idle', armed: p.armed, waiting: p.waiting, session: p.session })
}

function showResult(text: string): void {
  resultUntil = now() + RESULT_MS
  setView({ kind: 'result', text })
  window.setTimeout(() => {
    if (view.kind !== 'result') return
    setView(latest && !latest.request ? { kind: 'idle', armed: latest.armed, waiting: latest.waiting, session: latest.session } : { kind: 'offline' })
    void poll()
  }, RESULT_MS)
}

async function send(decision: 'approve' | 'deny'): Promise<void> {
  if (view.kind !== 'prompt' || view.sending || !client) return
  const { req } = view
  setView({ ...view, sending: true })
  try {
    const outcome = await client.decide(req.id, decision, req.nonce)
    showResult(outcome === 'gone' ? 'TOO LATE\nalready expired or answered' : decision === 'approve' ? 'APPROVED' : 'DENIED')
  } catch (err) {
    showResult(err instanceof AuthError ? 'NOT PAIRED' : 'NOT SENT\ncheck the relay')
  }
}

// Never leave the gate armed behind us, or the hook asks the relay on every prompt until the
// relay's 60 s backstop disarms it. keepalive lets the request finish while the page is torn down.
function disarmOnExit(reason: string): void {
  if (client && latest?.armed !== false) client.setArmed(false, 'glasses', reason, true).catch(() => {})
}

async function setArmed(on: boolean, source: 'glasses' | 'phone'): Promise<void> {
  try {
    await client?.setArmed(on, source)
  } catch (err) {
    console.error('arm/disarm failed', err)
  }
  void poll()
}

window.setInterval(() => {
  if (view.kind === 'prompt' && !view.sending) render() // countdown
}, 1000)

// ---- input -------------------------------------------------------------------

function screen(): Screen {
  switch (view.kind) {
    case 'prompt':
      return view.sending ? { kind: 'busy' } : { kind: 'prompt', id: view.req.id, shownAt: view.shownAt }
    case 'idle':
      return { kind: view.armed ? 'armed' : 'off' }
    case 'result':
      return { kind: 'busy' }
    default:
      return { kind: view.kind }
  }
}

const input = createInput(screen, {
  approve: id => {
    if (view.kind === 'prompt' && view.req.id === id) void send('approve')
  },
  deny: () => void send('deny'),
  arm: () => void setArmed(true, 'glasses'),
  disarm: () => void setArmed(false, 'glasses'),
  exit: () => {
    disarmOnExit('plugin exit')
    void bridge.shutDownPageContainer(1)
  },
  retry: () => void poll(),
  swipe: down => {
    if (view.kind === 'prompt' && !view.sending && view.detail !== down) setView({ ...view, detail: down })
  },
  foreground: entered => {
    foreground = entered
    // Back in front: anything shown before is stale. Ask the relay again now.
    // TODO(hardware): confirm FOREGROUND_ENTER_EVENT arrives, including after a Bluetooth
    // reconnect. evenhub-simulator 0.9.5 never emits lifecycle events ("Status Events: Not
    // emitted"), so this path has only been checked by the unit test in test/input.test.ts. If
    // it doesn't fire, polling stays stopped after FOREGROUND_EXIT_EVENT: the relay then sends
    // prompts to the terminal (nothing polling for 10 s) and disarms after 60 s. That is safe, but
    // the glasses keep showing stale state. Unverified: needs a G2.
    if (entered) void poll()
    else window.clearTimeout(pollTimer)
  },
  systemExit: abnormal => {
    disarmOnExit(abnormal ? 'plugin exited abnormally' : 'plugin exit')
    unsubscribe()
  },
}, {
  now,
  setTimeout: (fn, ms) => window.setTimeout(fn, ms),
  clearTimeout: handle => window.clearTimeout(handle as number),
})

const unsubscribe = bridge.onEvenHubEvent(event => input.handle(event))

// ---- phone page ----------------------------------------------------------------

mirror = mountPhone(document.querySelector<HTMLDivElement>('#app')!, {
  async pair(relay, token) {
    if (!/^https?:\/\//.test(relay) || !token.startsWith('agt_')) return 'Need a relay URL (http/https) and an agt_ token.'
    const candidate = new RelayClient(relay, token)
    try {
      const health = await candidate.health()
      if (health.auth !== 'device') return 'Relay reached, but it does not know this token.'
      await store(KEY_RELAY, relay)
      await store(KEY_TOKEN, token)
      client = candidate
      void poll()
      return `Paired. Relay ${health.version}, gate ${health.armed ? 'armed' : 'off'}.`
    } catch (err) {
      return `Could not reach the relay: ${(err as Error).message}`
    }
  },
  async unpair() {
    await store(KEY_TOKEN, '')
    client = null
    void poll()
  },
  async disarm() {
    await setArmed(false, 'phone')
  },
}, await load(KEY_RELAY))
render()
void poll()
