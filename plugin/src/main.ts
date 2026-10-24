import { waitForEvenAppBridge } from '@evenrealities/even_hub_sdk'
import { AuthError, type PendingRequest, type Poll } from './relay.ts'
import { DeckClient, type DeckCounts, type FocusSummary } from './deck-api.ts'
import { createGestures } from './gestures.ts'
import { Glasses, type Paint } from './glasses.ts'
import {
  barText,
  rowText,
  screenFor,
  VISIBLE_ROWS,
  windowFor,
  type Action,
  type Data,
  type Menu,
  type Page,
  type Screen,
} from './screens.ts'
import { mountPhone, type Frame } from './phone.ts'
import { bar, columns, fit } from './text.ts'
import type { DeckHandle, Route } from '../../web/deck-ui.js'

// The Command Deck phone page is off unless the build turns it on (VITE_DECK_PHONE=1): the phone
// becomes a readable map of your work, so public builds leave it out.
const PHONE_DECK = import.meta.env.VITE_DECK_PHONE === '1'
let phoneDeck: DeckHandle | null = null

const POLL_MS = 2000
const RESULT_MS = 1500
const SUMMARY_MS = 4000
const KEY_RELAY = 'gate.relay'
const KEY_TOKEN = 'gate.token'
const KEY_TRANSITIONS = 'deck.transitions'
const KEY_SEEN = 'deck.seen-errors' // failures already opened on the glasses
const KEY_LOOKED = 'deck.looked' // {cursor, at}: the event log position at the last view
const KEY_TODO_TOP = 'deck.todo-top' // "Move to top": to-do ids, newest first. Order only; files never change

const bridge = await waitForEvenAppBridge()
const now = () => performance.now()
const glasses = new Glasses(bridge)

// ---- storage ---------------------------------------------------------------

// HARDWARE: evenhub-simulator keeps bridge storage in memory, so it is empty after every launch,
// while its WebView profile persists. The deck.* keys (no secrets) are therefore mirrored into the
// WebView's localStorage. The pairing token stays in bridge storage only, as in v1. Whether the
// Even app keeps bridge storage across launches is unverified: needs a G2.
const MIRRORED = (key: string) => key.startsWith('deck.')

async function load(key: string): Promise<string> {
  let value = ''
  try {
    value = (await bridge.getLocalStorage(key)) || ''
  } catch {
    return localStorage.getItem(key) ?? ''
  }
  return value || (MIRRORED(key) ? (localStorage.getItem(key) ?? '') : '')
}

async function store(key: string, value: string): Promise<void> {
  const local = () => (value ? localStorage.setItem(key, value) : localStorage.removeItem(key))
  try {
    await bridge.setLocalStorage(key, value)
    if (MIRRORED(key)) local()
  } catch {
    local()
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
let seenErrors: string[] = []
try {
  seenErrors = JSON.parse((await load(KEY_SEEN)) || '[]')
} catch {
  seenErrors = []
}
let looked: { cursor: number; at: number } | null = null
try {
  looked = JSON.parse((await load(KEY_LOOKED)) || 'null')
} catch {
  looked = null
}
let todoTop: string[] = []
try {
  todoTop = JSON.parse((await load(KEY_TODO_TOP)) || '[]')
} catch {
  todoTop = []
}
let sinceShown = false
console.debug('[deck] looked', looked ? looked.cursor : 'never')

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

/** A glasses page as a route of the shared web UI (web/deck-ui.js). */
function routeOf(p: Pick<Page, 'kind' | 'scope' | 'id'>): Route {
  if (p.kind === 'project') return { view: 'project', id: p.scope, scope: p.scope }
  if (p.kind === 'since') return { view: 'deck', scope: 'focus' }
  return { view: p.kind, scope: p.scope, id: p.id }
}
let stack: Page[] = [root()]
let message: 'unpaired' | 'offline' | null = client ? null : 'unpaired'
let prompt: Prompt | null = null
let result: { text: string; until: number } | null = null
let summary: { s: FocusSummary; until: number } | null = null
let focusSince: string | null = null // started_at of the focus block the last poll saw
let lastCounts = ''
let lastRows = ''
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
  const spotlight =
    page.kind === 'deck'
      ? (own?.deck ?? rootDeck)?.errors.find(e => !seenErrors.includes(e.error ?? e.id ?? ''))
      : undefined
  const spotId = spotlight ? (spotlight.error ?? spotlight.id) : undefined
  if (spotlight && spotId !== lastSpotlight) console.log('[deck] spotlight', (lastSpotlight = spotId), spotlight.detail)
  return {
    armed: latest?.armed ?? false,
    clock: clock(),
    rootDeck,
    ...own,
    spotlight,
    todoTop,
    runAge: own ? (now() - own.at) / 1000 : 0,
  }
}

let lastSpotlight: string | undefined

function markSeen(errorId: string): void {
  if (seenErrors.includes(errorId)) return
  seenErrors = [errorId, ...seenErrors].slice(0, 30)
  void store(KEY_SEEN, JSON.stringify(seenErrors))
}

// Since you looked: on open, one screen of what finished, failed or fell back to the terminal while
// the plugin was closed. The cursor is the relay's event id at the last poll.
async function sinceYouLooked(cursor: number): Promise<void> {
  if (sinceShown || !client) return
  sinceShown = true
  if (!looked || looked.cursor >= cursor) return
  const { events, reset } = await client.events(looked.cursor).catch(() => ({ events: [], reset: false, cursor }))
  if (reset) return
  const notable = events.filter(
    e =>
      (e.type === 'run' && e.status === 'ok') ||
      e.type === 'error' ||
      (e.type === 'approval' && ['refused', 'timeout', 'cancelled'].includes(String(e.status))),
  )
  if (!notable.length || stack.length > 1) return
  const page: Page = { kind: 'since', scope: 'all', label: 'SINCE YOU LOOKED', cursor: 0, top: 0 }
  cache.set(pageKey(page), {
    since: { events: notable, away_s: Math.max(0, (Date.now() - looked.at) / 1000) },
    at: now(),
  })
  console.log('[deck] since you looked', notable.length, 'events')
  stack = [...stack, page]
}

let lookedSaved = 0
function saveLooked(cursor: number): void {
  if (!sinceShown || now() - lookedSaved < 10_000) return
  lookedSaved = now()
  looked = { cursor, at: Date.now() }
  void store(KEY_LOOKED, JSON.stringify(looked))
}

// ---- painting ------------------------------------------------------------------

function promptPaint(p: Prompt): Paint {
  const secs = Math.max(0, Math.ceil((p.deadline - now()) / 1000))
  const more = p.waiting > 1 ? `  (+${p.waiting - 1})` : ''
  const agent =
    ({ 'claude-code': 'Claude', codex: 'Codex' } as Record<string, string>)[p.req.session.agent] ?? p.req.session.agent
  return {
    template: 'prompt',
    key: `prompt:${p.req.id}`,
    menu: [], // long-press is disabled on a prompt
    slots: {
      head: { text: fit(`${p.req.session.name} · ${agent} · ${p.req.tool}${more}`, 460), color: 3 },
      clock: { text: p.sending ? '…' : `${secs}s`, color: 4 },
      source: {
        text: fit(
          `from ${p.req.session.name}${
            stack.length > 1
              ? ` · back to ${stack
                  .map(s => s.label ?? s.kind)
                  .slice(-2)
                  .join(' › ')}`
              : ''
          }`,
          568,
        ),
        color: 2,
      },
      // HARDWARE: how the G2 firmware wraps this. evenhub-simulator wraps by word, matching pretext's
      // measureTextWrap. The hook still caps the summary at 60 characters; retune on a G2.
      body: { text: p.detail ? p.req.detail : p.req.summary, color: 4 },
      risk: { text: p.req.risk === 'high' ? 'HIGH RISK' : p.req.risk, color: p.req.risk === 'high' ? 4 : 2 },
      hint: {
        text: p.sending
          ? 'sending…'
          : `tap: approve  ·  double-tap: deny  ·  swipe ${p.detail ? 'up: summary' : 'down: full'}`,
        color: 2,
      },
    },
  }
}

function resultPaint(text: string): Paint {
  return {
    template: 'prompt',
    key: `result:${text}`,
    menu: [],
    slots: {
      head: { text: 'APPROVAL', color: 3 },
      clock: { text: '', color: 4 },
      source: { text: '', color: 2 },
      body: { text, color: 4 },
      risk: { text: '', color: 2 },
      hint: { text: '', color: 2 },
    },
  }
}

// The closing beat of a focus block: four seconds of what it got done, then the Deck.
function summaryPaint(s: FocusSummary): Paint {
  const pair = (a: string, b: string) =>
    columns(
      [
        { text: a, x: 0 },
        { text: b, x: 100 },
      ],
      564,
    )
  return {
    template: 'detail',
    key: `summary:${s.reason}`,
    menu: [],
    slots: {
      bar: { text: bar(`FOCUS · ${s.projects.join('+')}`, clock(), 564), color: 3 },
      headline: {
        text: `FOCUS DONE · ${s.minutes} min\n${s.reason === 'timer' ? 'the timer ran out' : s.reason === 'disarmed' ? 'the gate was disarmed' : 'ended from the Deck'}`,
        color: 4,
      },
      body: {
        text: [
          pair('Approved', String(s.approved)),
          pair('Denied', String(s.denied)),
          pair('Runs', `${s.runs} finished`),
          pair('To-dos', `${s.todos} done`),
        ].join('\n'),
        color: 3,
      },
      hint: { text: 'back to the Deck in a moment', color: 2 },
    },
  }
}

function showSummary(s: FocusSummary): void {
  if (summary && now() < summary.until) return
  summary = { s, until: now() + SUMMARY_MS }
  focusSince = null
  console.log(
    '[deck] focus summary',
    JSON.stringify({ minutes: s.minutes, approved: s.approved, denied: s.denied, runs: s.runs, todos: s.todos }),
  )
  render()
  window.setTimeout(() => {
    summary = null
    render()
  }, SUMMARY_MS)
}

function messagePaint(kind: 'unpaired' | 'offline'): Paint {
  const body =
    kind === 'unpaired' ? 'Run gatectl.py pair on the PC\nand open the link on your phone.' : 'Retrying every 2 s.'
  return {
    template: 'detail',
    key: `message:${kind}`,
    menu: [],
    slots: {
      bar: { text: bar('COMMAND DECK', clock(), 568), color: 3 },
      headline: { text: kind === 'unpaired' ? 'NOT PAIRED' : 'RELAY OFFLINE', color: 4 },
      body: { text: body, color: 3 },
      hint: { text: kind === 'unpaired' ? '2× exit' : 'tap retry · 2× exit', color: 2 },
    },
  }
}

function pagePaint(s: Screen, page: Page): Paint {
  const barSlot = { text: barText(stack, dataFor(page)), color: 3 }
  if (s.template === 'detail') {
    return {
      template: 'detail',
      key: s.key,
      menu: s.menu,
      slots: {
        bar: barSlot,
        headline: { text: s.headline, color: 4 },
        body: { text: s.body, color: 3 },
        hint: { text: s.hint, color: 2 },
      },
    }
  }
  const kept = page.cursorKey ? s.rows.findIndex(r => r.key === page.cursorKey) : -1
  page.cursor = kept >= 0 ? kept : Math.max(0, Math.min(page.cursor, s.rows.length - 1))
  page.cursorKey = s.rows[page.cursor]?.key
  page.top = windowFor(page.cursor, page.top, s.rows.length)
  const rowsLine = `${s.key} ${page.cursor} ${s.rows.map(r => r.key).join(',') || '-'}`
  if (rowsLine !== lastRows) console.debug('[deck] rows', (lastRows = rowsLine)) // lets sim runs move straight to a row
  const slots: Paint['slots'] = { bar: barSlot, note: { text: s.note, color: 1 }, hint: { text: s.hint, color: 2 } }
  for (let i = 0; i < VISIBLE_ROWS; i++) {
    const index = page.top + i
    const row = s.rows[index]
    slots[`r${i}`] = row
      ? { text: rowText(row, index === page.cursor), color: index === page.cursor ? 4 : 2 }
      : { text: i === 0 && !s.rows.length ? `  ${s.empty}` : '', color: 2 }
  }
  // More rows below or above than fit: say so on the note line when it is free.
  if (!s.note && s.rows.length > VISIBLE_ROWS)
    slots.note = { text: `  ${page.cursor + 1} of ${s.rows.length}`, color: 1 }
  return { template: 'list', key: s.key, menu: s.menu, slots }
}

let mirror: (frame: Frame) => void = () => {}

function render(): void {
  let paint: Paint
  if (message) paint = messagePaint(message)
  else if (result && now() < result.until) paint = resultPaint(result.text)
  else if (prompt) paint = promptPaint(prompt)
  else if (summary && now() < summary.until) paint = summaryPaint(summary.s)
  else {
    screen = screenFor(stack, dataFor(top()))
    paint = pagePaint(screen, top())
  }
  const id = /^(prompt|result|message|summary):/.test(paint.key) ? paint.key : `${stack.length}:${paint.key}`
  if (id !== view.id) {
    view = { id, shownAt: now() } // a new screen: taps are locked out for GRACE_MS
    gestures.cancelTap()
    // The presenter view (/present) follows the glasses page by page. Overlays don't move it.
    if (!/^(prompt|result|message|summary):/.test(paint.key)) client?.view(routeOf(top())).catch(() => {})
  }
  glasses.show(paint)
  const s = paint.slots
  mirror({
    head: (s.bar ?? s.head)?.text ?? '',
    clock: s.clock?.text ?? '',
    body:
      paint.template === 'list'
        ? ['r0', 'r1', 'r2', 'r3', 'r4'].map(r => s[r]?.text ?? '').join('\n')
        : `${(s.headline ?? s.body)?.text ?? ''}\n\n${paint.template === 'detail' ? s.body.text : ''}`,
    risk: (s.note ?? s.risk)?.text ?? '',
    hint: s.hint?.text ?? '',
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
    case 'deck':
      got = { deck: await c.deck(page.scope) }
      break
    case 'project': {
      const [deck, project] = await Promise.all([c.deck(page.scope), c.project(page.scope)])
      got = { deck, project }
      break
    }
    case 'needs':
      got = { needs: await c.needs(page.scope) }
      break
    case 'sessions':
      got = { sessions: await c.sessions(page.scope) }
      break
    case 'running': {
      got = { runs: await c.runs(page.scope) }
      console.debug('[deck] running', got.runs!.items.map(r => r.id).join(',') || '-')
      break
    }
    case 'projects':
    case 'focus':
      got = { projects: await c.projects() }
      break
    case 'todos':
      got = { todos: await c.todos(page.scope) }
      break
    case 'session':
      got = { session: await c.session(id) }
      break
    case 'run':
      got = { run: await c.run(id) }
      break
    case 'todo': {
      // Keep the last copy when the line changes on disk: the tap then still asks the relay, which
      // answers 409, and the screen says so. The glasses never guess that a file changed.
      const fresh = (await c.todos(page.scope)).items.find(t => t.id === id)
      const prev = cache.get(key)
      got = { todo: fresh ?? prev?.todo, todoOutcome: prev?.todoOutcome }
      break
    }
    case 'card':
      got = { need: (await c.needs('all')).items.find(n => n.kind === 'card' && n.id === id) }
      break
    case 'commits':
      got = { project: await c.project(page.scope) }
      break
    case 'error': {
      got = { error: (rootDeck ?? (await c.deck('all'))).errors.find(e => (e.error ?? e.id) === id) }
      markSeen(id) // opened: the spotlight lets go of it
      break
    }
    case 'since':
      return // filled once, at open
    default:
      got = {}
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
    rootDeck = await client.deck('focus') // the status bar's focus, on every page
    // Focus that ended without us (the timer, a disarm elsewhere) still gets its summary screen.
    if (focusSince && !rootDeck.focus) {
      const { last_summary } = await client.focus()
      if (last_summary) showSummary(last_summary)
    }
    focusSince = rootDeck.focus?.started_at ?? null
    const counts = JSON.stringify({
      needs: rootDeck.needs_you,
      sessions: rootDeck.sessions,
      running: rootDeck.running,
      projects: rootDeck.projects,
      todos: rootDeck.todos_open,
    })
    if (counts !== lastCounts) console.debug('[deck] counts', (lastCounts = counts)) // the sim runs compare these with the desktop page
    await sinceYouLooked(rootDeck.cursor)
    saveLooked(rootDeck.cursor)
    const current = top()
    if (current.kind === 'deck' && current.scope === 'focus') cache.set(pageKey(current), { deck: rootDeck, at: now() })
    else await fetchPage(current)
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
    prompt = {
      ...prompt,
      waiting: p.waiting,
      deadline: Math.abs(deadline - prompt.deadline) > 1500 ? deadline : prompt.deadline,
    }
    return
  }
  if (req) {
    prompt = {
      req,
      deadline: now() + req.expires_in * 1000,
      shownAt: now(),
      detail: false,
      waiting: p.waiting,
      sending: false,
    }
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
    showResult(
      outcome === 'gone' ? 'TOO LATE\nalready expired or answered' : decision === 'approve' ? 'APPROVED' : 'DENIED',
    )
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
    case 'open':
      return push(action.page)
    case 'prompt':
      return void poll() // a live approval takes the screen by itself
    case 'arm-toggle':
      return void setArmed(!latest?.armed, 'glasses')
    case 'transitions':
      glasses.fadeIn = !glasses.fadeIn
      return void store(KEY_TRANSITIONS, glasses.fadeIn ? 'on' : 'off')
    case 'todo-done': {
      const outcome = await client.todoDone(action.id).catch(() => 'failed' as const)
      console.log('[deck] todo', action.id, outcome)
      const page = top()
      const entry = cache.get(pageKey(page))
      if (page.kind === 'todo' && entry?.todo) {
        cache.set(pageKey(page), {
          ...entry,
          todo: { ...entry.todo, done: outcome === 'done' || entry.todo.done },
          todoOutcome: outcome,
        })
        render()
        if (outcome === 'done') window.setTimeout(() => top() === page && pop(), 1200) // DONE, then back to the list
        return
      }
      return void poll()
    }
    case 'focus-start':
      return push({ kind: 'focus', scope: 'all', label: 'FOCUS' })
    case 'focus-end': {
      const ended = await client.endFocus().catch(() => null)
      if (ended) showSummary(ended)
      if (top().kind === 'focus') pop()
      return void poll()
    }
    case 'focus-project': {
      await client.setFocus([action.project], 25).catch(err => console.warn('[deck] focus failed', String(err)))
      console.log('[deck] focus on', action.project)
      return void poll()
    }
    case 'pick': {
      const page = top()
      const picked = page.picked ?? [...(rootDeck?.focus?.projects ?? [])]
      page.picked = picked.includes(action.project)
        ? picked.filter(p => p !== action.project)
        : picked.length < 2
          ? [...picked, action.project]
          : picked
      return render()
    }
    case 'focus-go': {
      const picked = top().picked ?? rootDeck?.focus?.projects ?? []
      if (!picked.length) return
      await client.setFocus(picked, action.minutes).catch(err => console.warn('[deck] focus failed', String(err)))
      console.log('[deck] focus on', picked.join('+'), action.minutes ?? 'no timer')
      return pop()
    }
    case 'mute':
      await client.mute(action.session, true).catch(err => console.warn('[deck] mute failed', String(err)))
      console.log('[deck] muted', action.session)
      if (top().kind === 'session' && top().id === action.session) return pop()
      return void poll()
    case 'back':
      return pop()
    case 'todo-top':
      todoTop = [action.id, ...todoTop.filter(t => t !== action.id)].slice(0, 50)
      void store(KEY_TODO_TOP, JSON.stringify(todoTop))
      return render()
    case 'phone': {
      // The phone page shares this WebView, so "Open on phone" just routes it.
      const route = routeOf(action.page)
      if (!phoneDeck) return console.log('[deck] phone page is off in this build')
      console.log('[deck] phone route', JSON.stringify(route))
      return void phoneDeck.go(route)
    }
    case 'none':
      return
  }
}

function move(down: boolean): void {
  if (!screen || screen.template !== 'list' || !screen.rows.length) return
  const page = top()
  const next = Math.max(0, Math.min(screen.rows.length - 1, page.cursor + (down ? 1 : -1)))
  console.debug('[deck] cursor', screen.rows[next].key) // every swipe, even against the end: the sim runs count on it
  if (next === page.cursor) return
  page.cursor = next
  page.cursorKey = screen.rows[next].key
  render()
}

function menuAction(itemID: number): Action | undefined {
  if (!screen) return undefined
  const menu: Menu = screen.template === 'list' ? (screen.rows[top().cursor]?.menu ?? screen.menu) : screen.menu
  return (menu.find(m => m.id === itemID) ?? screen.menu.find(m => m.id === itemID))?.action
}

window.setInterval(() => render(), 1000) // the clock, the prompt countdown and ELAPSED

// ---- input -----------------------------------------------------------------------

const gestures = createGestures(
  () => view,
  {
    tap(id) {
      if (id !== view.id) return
      if (message === 'offline') return void poll()
      if (message || result) return
      if (prompt) return void send('approve')
      if (summary && now() < summary.until) {
        summary = null
        return render()
      }
      if (!screen) return
      if (screen.template === 'list') return void act(screen.rows[top().cursor]?.open)
      return void act(screen.primary)
    },
    double() {
      if (prompt) return void send('deny')
      if (result) return
      if (message) return exit()
      if (summary && now() < summary.until) {
        summary = null
        return render()
      }
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
      phoneDeck?.setBlank(!entered)
      // Back in front: anything shown before is stale. Ask the relay again now.
      // HARDWARE: FOREGROUND_ENTER after a real background is unverified (see gestures.ts).
      if (entered) void poll()
      else window.clearTimeout(pollTimer)
    },
    systemExit(abnormal) {
      disarmOnExit(abnormal ? 'plugin exited abnormally' : 'plugin exit')
      unsubscribe()
    },
  },
  {
    now,
    setTimeout: (fn, ms) => window.setTimeout(fn, ms),
    clearTimeout: handle => window.clearTimeout(handle as number),
  },
)

const unsubscribe = bridge.onEvenHubEvent(event => gestures.handle(event))

// ---- phone page ----------------------------------------------------------------

mirror = mountPhone(
  document.querySelector<HTMLDivElement>('#app')!,
  {
    async pair(relay, token) {
      if (!/^https?:\/\//.test(relay) || !token.startsWith('agt_'))
        return 'Need a relay URL (http/https) and an agt_ token.'
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
  },
  await load(KEY_RELAY),
)

if (PHONE_DECK) {
  const { mountDeck } = await import('../../web/deck-ui.js')
  await import('../../web/deck-ui.css')
  const slot = document.querySelector<HTMLElement>('#deckPhone')!
  slot.hidden = false
  phoneDeck = mountDeck(
    slot,
    {
      get: path => (client ? client.get(path) : Promise.reject(new Error('not paired'))),
      events: (since, wait) => (client ? client.events(since, wait) : Promise.reject(new Error('not paired'))),
    },
    { hidePaths: true, title: 'Command Deck' },
  )
  // Blank whenever the page is out of sight, so a glance at an unlocked phone shows nothing.
  document.addEventListener('visibilitychange', () => phoneDeck?.setBlank(document.hidden))
}

render()
void poll()
