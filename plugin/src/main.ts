import { waitForEvenAppBridge } from '@evenrealities/even_hub_sdk'
import { AuthError, type PendingRequest, type Poll } from './relay.ts'
import { DeckClient, type DeckCounts } from './deck-api.ts'
import { createGestures } from './gestures.ts'
import { Glasses, type Paint } from './glasses.ts'
import {
  barText, rowText, screenFor, VISIBLE_ROWS, windowFor,
  type Action, type Data, type Menu, type Page, type Screen,
} from './screens.ts'
import { mountPhone, type Frame } from './phone.ts'
import { bar, fit } from './text.ts'

const POLL_MS = 2000
const RESULT_MS = 1500
const KEY_RELAY = 'gate.relay'
const KEY_TOKEN = 'gate.token'
const KEY_TRANSITIONS = 'deck.transitions'

const bridge = await waitForEvenAppBridge()
const now = () => performance.now()
const glasses = new Glasses(bridge)

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
let client: DeckClient | null = null
{
  const [relay, token] = [await load(KEY_RELAY), await load(KEY_TOKEN)]
  if (relay && token) client = new DeckClient(relay, token)
}
glasses.fadeIn = (await load(KEY_TRANSITIONS)) === 'on'

// ---- state -------------------------------------------------------------------

interface Prompt {
  req: PendingRequest
  deadline: number
  shownAt: number
  detail: boolean
  waiting: number
  sending: boolean
}

const root = (): Page => ({ kind: 'deck', scope: 'focus', label: 'DECK', cursor: 0, top: 0 })
let stack: Page[] = [root()]
let message: 'unpaired' | 'offline' | null = client ? null : 'unpaired'
let prompt: Prompt | null = null
let result: { text: string; until: number } | null = null
let latest: Poll | null = null
let rootDeck: DeckCounts | undefined
const cache = new Map<string, Partial<Data> & { at: number }>()
let view = { id: '', shownAt: 0 }
let screen: Screen | null = null

const top = () => stack[stack.length - 1]
const pageKey = (p: Page) => `${p.kind}:${p.scope}:${p.id ?? ''}`
const clock = () => new Date().toTimeString().slice(0, 5)

function dataFor(page: Page): Data {
  const own = cache.get(pageKey(page))
  return { armed: latest?.armed ?? false, clock: clock(), rootDeck, ...own, runAge: own ? (now() - own.at) / 1000 : 0 }
}

// ---- painting ------------------------------------------------------------------

function promptPaint(p: Prompt): Paint {
  const secs = Math.max(0, Math.ceil((p.deadline - now()) / 1000))
  const more = p.waiting > 1 ? `  (+${p.waiting - 1})` : ''
  const agent = ({ 'claude-code': 'Claude', codex: 'Codex' } as Record<string, string>)[p.req.session.agent] ?? p.req.session.agent
  return {
    template: 'prompt', key: `prompt:${p.req.id}`, menu: [], // long-press is disabled on a prompt
    slots: {
      head: { text: fit(`${p.req.session.name} · ${agent} · ${p.req.tool}${more}`, 460), color: 3 },
      clock: { text: p.sending ? '…' : `${secs}s`, color: 4 },
      source: { text: fit(`from ${p.req.session.name}${stack.length > 1 ? ` · back to ${stack.map(s => s.label ?? s.kind).slice(-2).join(' › ')}` : ''}`, 568), color: 2 },
      // HARDWARE: how the G2 firmware wraps this. evenhub-simulator wraps by word, matching pretext's
      // measureTextWrap. The hook still caps the summary at 60 characters; retune on a G2.
      body: { text: p.detail ? p.req.detail : p.req.summary, color: 4 },
      risk: { text: p.req.risk === 'high' ? 'HIGH RISK' : p.req.risk, color: p.req.risk === 'high' ? 4 : 2 },
      hint: { text: p.sending ? 'sending…' : `tap: approve  ·  double-tap: deny  ·  swipe ${p.detail ? 'up: summary' : 'down: full'}`, color: 2 },
    },
  }
}

function resultPaint(text: string): Paint {
  return { template: 'prompt', key: `result:${text}`, menu: [],
    slots: { head: { text: 'APPROVAL', color: 3 }, clock: { text: '', color: 4 }, source: { text: '', color: 2 }, body: { text, color: 4 }, risk: { text: '', color: 2 }, hint: { text: '', color: 2 } } }
}

function messagePaint(kind: 'unpaired' | 'offline'): Paint {
  const body = kind === 'unpaired' ? 'Run gatectl.py pair on the PC\nand open the link on your phone.' : 'Retrying every 2 s.'
  return { template: 'detail', key: `message:${kind}`, menu: [],
    slots: { bar: { text: bar('COMMAND DECK', clock(), 568), color: 3 }, headline: { text: kind === 'unpaired' ? 'NOT PAIRED' : 'RELAY OFFLINE', color: 4 },
      body: { text: body, color: 3 }, hint: { text: kind === 'unpaired' ? '2× exit' : 'tap retry · 2× exit', color: 2 } } }
}

function pagePaint(s: Screen, page: Page): Paint {
  const barSlot = { text: barText(stack, dataFor(page)), color: 3 }
  if (s.template === 'detail') {
    return { template: 'detail', key: s.key, menu: s.menu, slots: {
      bar: barSlot, headline: { text: s.headline, color: 4 }, body: { text: s.body, color: 3 }, hint: { text: s.hint, color: 2 } } }
  }
  page.cursor = Math.max(0, Math.min(page.cursor, s.rows.length - 1))
  page.top = windowFor(page.cursor, page.top, s.rows.length)
  const slots: Paint['slots'] = { bar: barSlot, note: { text: s.note, color: 1 }, hint: { text: s.hint, color: 2 } }
  for (let i = 0; i < VISIBLE_ROWS; i++) {
    const index = page.top + i
    const row = s.rows[index]
    slots[`r${i}`] = row ? { text: rowText(row, index === page.cursor), color: index === page.cursor ? 4 : 2 }
      : { text: i === 0 && !s.rows.length ? `  ${s.empty}` : '', color: 2 }
  }
  // More rows below or above than fit: say so on the note line when it is free.
  if (!s.note && s.rows.length > VISIBLE_ROWS) slots.note = { text: `  ${page.cursor + 1} of ${s.rows.length}`, color: 1 }
  return { template: 'list', key: s.key, menu: s.menu, slots }
}

let mirror: (frame: Frame) => void = () => {}

function render(): void {
  let paint: Paint
  if (message) paint = messagePaint(message)
  else if (result && now() < result.until) paint = resultPaint(result.text)
  else if (prompt) paint = promptPaint(prompt)
  else {
    screen = screenFor(stack, dataFor(top()))
    paint = pagePaint(screen, top())
  }
  const id = paint.key.startsWith('prompt:') || paint.key.startsWith('result:') || paint.key.startsWith('message:') ? paint.key : `${stack.length}:${paint.key}`
  if (id !== view.id) {
    view = { id, shownAt: now() } // a new screen: taps are locked out for GRACE_MS
    gestures.cancelTap()
  }
  glasses.show(paint)
  const s = paint.slots
  mirror({
    head: (s.bar ?? s.head)?.text ?? '', clock: s.clock?.text ?? '',
    body: paint.template === 'list' ? ['r0', 'r1', 'r2', 'r3', 'r4'].map(r => s[r]?.text ?? '').join('\n') : `${(s.headline ?? s.body)?.text ?? ''}\n\n${paint.template === 'detail' ? s.body.text : ''}`,
    risk: (s.note ?? s.risk)?.text ?? '', hint: s.hint?.text ?? '',
  })
}

// ---- relay sync ----------------------------------------------------------------

let pollTimer: number | undefined
let polling = false
let pollAgain = false
let foreground = true

async function fetchPage(page: Page): Promise<void> {
  if (!client) return
  const c = client
  const key = pageKey(page)
  const id = page.id ?? ''
  let got: Partial<Data>
  switch (page.kind) {
    case 'deck': got = { deck: await c.deck(page.scope) }; break
    case 'project': {
      const [deck, project] = await Promise.all([c.deck(page.scope), c.project(page.scope)])
      got = { deck, project }
      break
    }
    case 'needs': got = { needs: await c.needs(page.scope) }; break
    case 'sessions': got = { sessions: await c.sessions(page.scope) }; break
    case 'running': got = { runs: await c.runs(page.scope) }; break
    case 'projects': case 'focus': got = { projects: await c.projects() }; break
    case 'todos': got = { todos: await c.todos(page.scope) }; break
    case 'session': got = { session: await c.session(id) }; break
    case 'run': got = { run: await c.run(id) }; break
    case 'todo': got = { todo: (await c.todos(page.scope)).items.find(t => t.id === id) }; break
    case 'card': got = { need: (await c.needs('all')).items.find(n => n.kind === 'card' && n.id === id) }; break
    case 'commits': got = { project: await c.project(page.scope) }; break
    case 'error': got = { error: (rootDeck ?? await c.deck('all')).errors.find(e => (e.error ?? e.id) === id) }; break
    default: got = {}
  }
  cache.set(key, { ...got, at: now() })
}

async function poll(): Promise<void> {
  if (polling) {
    pollAgain = true
    return
  }
  polling = true
  window.clearTimeout(pollTimer)
  try {
    if (!client) {
      message = 'unpaired'
      return render()
    }
    latest = await client.poll()
    message = null
    applyGate(latest)
    const page = top()
    rootDeck = await client.deck('focus') // the status bar's focus, on every page
    if (page.kind === 'deck' && page.scope === 'focus') cache.set(pageKey(page), { deck: rootDeck, at: now() })
    else await fetchPage(page)
    render()
  } catch (err) {
    if (err instanceof AuthError) message = 'unpaired'
    else if (!result) message = 'offline'
    if (!(err instanceof AuthError) && !(err instanceof TypeError)) console.warn('[deck] poll failed', String(err))
    render()
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

// Approvals outrank everything: a pending request takes the screen from any depth, and when it is
// answered the page underneath comes back with its cursor where it was.
function applyGate(p: Poll): void {
  if (result && now() < result.until) return // finish showing the result first
  if (prompt?.sending) return
  const req = p.request
  if (req && prompt && prompt.req.id === req.id) {
    const deadline = now() + req.expires_in * 1000
    // Follow the relay's clock, but don't let a ±1 s poll jitter shake the countdown.
    prompt = { ...prompt, waiting: p.waiting, deadline: Math.abs(deadline - prompt.deadline) > 1500 ? deadline : prompt.deadline }
    return
  }
  if (req) {
    prompt = { req, deadline: now() + req.expires_in * 1000, shownAt: now(), detail: false, waiting: p.waiting, sending: false }
    console.log('[deck] prompt', req.id, 'over', pageKey(top()), 'cursor', top().cursor)
    return
  }
  if (prompt) showResult(prompt.deadline <= now() + 1000 ? 'EXPIRED' : 'WITHDRAWN\nanswered elsewhere or cancelled')
}

function showResult(text: string): void {
  prompt = null
  result = { text, until: now() + RESULT_MS }
  render()
  window.setTimeout(() => {
    result = null
    console.log('[deck] restored', pageKey(top()), 'cursor', top().cursor)
    void poll()
  }, RESULT_MS)
}

async function send(decision: 'approve' | 'deny'): Promise<void> {
  if (!prompt || prompt.sending || !client) return
  const { req } = prompt
  prompt = { ...prompt, sending: true }
  render()
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
    console.error('[deck] arm/disarm failed', err)
  }
  void poll()
}

function exit(): void {
  disarmOnExit('plugin exit')
  void bridge.shutDownPageContainer(1)
}

// ---- navigation ----------------------------------------------------------------

function push(page: Omit<Page, 'cursor' | 'top'>): void {
  if (stack.length >= 8) return
  stack = [...stack, { ...page, cursor: 0, top: 0 }]
  console.log('[deck] page', pageKey(top()), 'depth', stack.length)
  render()
  void poll()
}

function pop(): void {
  if (stack.length === 1) return exit()
  stack = stack.slice(0, -1)
  console.log('[deck] page', pageKey(top()), 'depth', stack.length, 'cursor', top().cursor)
  render()
  void poll()
}

async function act(action: Action | undefined): Promise<void> {
  if (!action || !client) return
  switch (action.do) {
    case 'open': return push(action.page)
    case 'prompt': return void poll() // a live approval takes the screen by itself
    case 'arm-toggle': return void setArmed(!latest?.armed, 'glasses')
    case 'transitions':
      glasses.fadeIn = !glasses.fadeIn
      return void store(KEY_TRANSITIONS, glasses.fadeIn ? 'on' : 'off')
    case 'todo-done': {
      const outcome = await client.todoDone(action.id).catch(() => 'failed' as const)
      console.log('[deck] todo', action.id, outcome)
      cache.delete(pageKey(top()))
      return void poll()
    }
    case 'focus-start': return push({ kind: 'focus', scope: 'all', label: 'FOCUS' })
    case 'focus-end': await client.endFocus().catch(() => null); return void poll()
    case 'focus-project': await client.setFocus([action.project], 25).catch(() => null); return void poll()
    case 'mute': case 'phone': case 'none':
      console.log('[deck] action not built yet:', action.do)
  }
}

function move(down: boolean): void {
  if (!screen || screen.template !== 'list' || !screen.rows.length) return
  const page = top()
  const next = Math.max(0, Math.min(screen.rows.length - 1, page.cursor + (down ? 1 : -1)))
  if (next === page.cursor) return
  page.cursor = next
  render()
}

function menuAction(itemID: number): Action | undefined {
  if (!screen) return undefined
  const menu: Menu = screen.template === 'list' ? (screen.rows[top().cursor]?.menu ?? screen.menu) : screen.menu
  return (menu.find(m => m.id === itemID) ?? screen.menu.find(m => m.id === itemID))?.action
}

window.setInterval(() => render(), 1000) // the clock, the prompt countdown and ELAPSED

// ---- input -----------------------------------------------------------------------

const gestures = createGestures(() => view, {
  tap(id) {
    if (id !== view.id) return
    if (message === 'offline') return void poll()
    if (message || result) return
    if (prompt) return void send('approve')
    if (!screen) return
    if (screen.template === 'list') return void act(screen.rows[top().cursor]?.open)
    return void act(screen.primary)
  },
  double() {
    if (prompt) return void send('deny')
    if (result) return
    if (message) return exit()
    pop()
  },
  swipe(down) {
    if (prompt && !prompt.sending && !result) {
      if (prompt.detail !== down) {
        prompt = { ...prompt, detail: down }
        render()
      }
      return
    }
    if (!message && !result) move(down)
  },
  menu(itemID, id) {
    if (id !== view.id || prompt || message) return
    console.log('[deck] menu', itemID, 'on', pageKey(top()), 'row', top().cursor)
    void act(menuAction(itemID))
  },
  foreground(entered) {
    foreground = entered
    // Back in front: anything shown before is stale. Ask the relay again now.
    // HARDWARE: FOREGROUND_ENTER after a real background is unverified (see gestures.ts).
    if (entered) void poll()
    else window.clearTimeout(pollTimer)
  },
  systemExit(abnormal) {
    disarmOnExit(abnormal ? 'plugin exited abnormally' : 'plugin exit')
    unsubscribe()
  },
}, {
  now,
  setTimeout: (fn, ms) => window.setTimeout(fn, ms),
  clearTimeout: handle => window.clearTimeout(handle as number),
})

const unsubscribe = bridge.onEvenHubEvent(event => gestures.handle(event))

// ---- phone page ----------------------------------------------------------------

mirror = mountPhone(document.querySelector<HTMLDivElement>('#app')!, {
  async pair(relay, token) {
    if (!/^https?:\/\//.test(relay) || !token.startsWith('agt_')) return 'Need a relay URL (http/https) and an agt_ token.'
    const candidate = new DeckClient(relay, token)
    try {
      const health = await candidate.health()
      if (health.auth !== 'device') return 'Relay reached, but it does not know this token.'
      await store(KEY_RELAY, relay)
      await store(KEY_TOKEN, token)
      client = candidate
      message = null
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
