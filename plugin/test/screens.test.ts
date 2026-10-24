// Screen and gesture tests. Run: npm test
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createGestures, DOUBLE_WINDOW_MS, GRACE_MS, type HubEvent, type View } from '../src/gestures.ts'
import {
  barText,
  DECK_MENU,
  listScreen,
  detailScreen,
  MAX_ROWS,
  ROW_W,
  rowText,
  screenFor,
  VISIBLE_ROWS,
  windowFor,
  type Data,
  type Page,
} from '../src/screens.ts'
import { ago, bar, fit, width } from '../src/text.ts'
import type { DeckCounts, Run, Session, Todo } from '../src/deck-api.ts'

const page = (kind: Page['kind'], scope = 'all', id?: string, label?: string): Page => ({
  kind,
  scope,
  id,
  label,
  cursor: 0,
  top: 0,
})
const LONG = 'a-very-long-project-name-that-goes-on-and-on'

function session(i: number, over: Partial<Session> = {}): Session {
  return {
    id: `cc:${i}`,
    agent: i % 2 ? 'codex' : 'claude-code',
    title: '',
    project: LONG,
    project_name: LONG,
    folder: LONG,
    tag: '',
    model: '',
    state: 'waiting',
    waiting_for: 'approval',
    state_for_s: 42 + i,
    counts: { tools: 1, approved: 0, denied: 0, failed: 0 },
    current: { tool: 'Bash', target: 'npm run build -- --verbose --watch --and-a-lot-more-flags', since: '' },
    error: false,
    muted: false,
    ...over,
  }
}

function run(i: number): Run {
  return {
    id: `toolu_${i}`,
    session: 'cc:1',
    agent: 'codex',
    project: LONG,
    project_name: LONG,
    kind: 'tool',
    tool: 'Bash',
    target: 'pytest -q tests/integration/test_everything_that_could_possibly_break.py',
    parent: null,
    started_at: new Date().toISOString(),
    ended_at: null,
    status: 'running',
    elapsed_s: 754 + i,
  }
}

const deck: DeckCounts = {
  scope: 'all',
  armed: true,
  needs_you: 2,
  sessions: 4,
  waiting: 1,
  running: 3,
  longest_s: 720,
  projects: 6,
  todos_open: 11,
  focus: { projects: ['glasses', 'hub'], minutes: 50, started_at: '', ends_at: '', left_s: 1860 },
  outside_focus: { active: 3, waiting: 1 },
  errors: [],
  cursor: 0,
}

const data = (over: Partial<Data> = {}): Data => ({ armed: true, clock: '14:02', rootDeck: deck, ...over })

test('every row and status bar fits the glasses line, whatever the names', () => {
  const sessions = { items: Array.from({ length: 25 }, (_, i) => session(i)), more: 7 }
  const runs = { items: Array.from({ length: 25 }, (_, i) => run(i)), more: 0 }
  const todos = {
    items: [
      {
        id: 't',
        project: LONG,
        project_name: LONG,
        text: 'x'.repeat(200),
        source: 'todo-file',
        file: 'TODO.md',
        done: false,
      } as Todo,
    ],
    more: 0,
  }
  for (const [kind, d] of [
    ['sessions', { sessions }],
    ['running', { runs }],
    ['todos', { todos }],
    ['deck', { deck }],
  ] as const) {
    const stack = [page('deck', 'focus', undefined, 'DECK'), page(kind, 'all', undefined, kind.toUpperCase())]
    const screen = listScreen(stack, data(d as Partial<Data>))
    assert.ok(screen.rows.length <= MAX_ROWS, `${kind}: ${screen.rows.length} rows`)
    for (const row of screen.rows) {
      for (const selected of [true, false])
        assert.ok(width(rowText(row, selected)) <= ROW_W, `${kind}: ${rowText(row, selected)}`)
    }
    assert.ok(width(barText(stack, data(d as Partial<Data>))) <= ROW_W, `${kind} bar`)
  }
})

test('a list over 20 rows ends in a row that sends the rest to the phone', () => {
  const sessions = { items: Array.from({ length: 25 }, (_, i) => session(i)), more: 7 }
  const screen = listScreen([page('sessions')], data({ sessions }))
  assert.equal(screen.rows.length, MAX_ROWS)
  assert.match(rowText(screen.rows[MAX_ROWS - 1], false), /13 more sessions on phone/)
})

test('waiting sessions show seconds, others whole minutes; badges name the agent', () => {
  const screen = listScreen(
    [page('sessions')],
    data({
      sessions: {
        items: [
          session(0, { project_name: 'glasses' }),
          session(1, { project_name: 'hub', state: 'working', state_for_s: 185 }),
        ],
        more: 0,
      },
    }),
  )
  assert.match(rowText(screen.rows[0], false), /^\s+CC\s+glasses\s+waiting · Bash\s+0:42$/)
  assert.match(rowText(screen.rows[1], false), /^\s+CDX\s+hub\s+working · Bash\s+3m$/)
})

test('menus stay inside the SDK limits on every screen', () => {
  const kinds: Page['kind'][] = [
    'deck',
    'needs',
    'sessions',
    'running',
    'projects',
    'todos',
    'focus',
    'project',
    'session',
    'run',
    'todo',
    'card',
    'commits',
    'error',
  ]
  for (const kind of kinds) {
    const s = screenFor(
      [page('deck', 'focus', undefined, 'DECK'), page(kind, 'glasses', 'x')],
      data({ sessions: { items: [session(0)], more: 0 } }),
    )
    assert.ok(s.menu.length <= 10, kind)
    assert.equal(new Set(s.menu.map(m => m.id)).size, s.menu.length, `${kind} ids unique`)
    for (const m of s.menu) {
      assert.ok(m.id > 0, kind)
      assert.ok(new TextEncoder().encode(m.name).length <= 32, `${kind}: ${m.name}`)
    }
  }
  assert.deepEqual(
    DECK_MENU.map(m => m.name),
    ['Arm / Disarm', 'Start focus', 'End focus', 'Transitions on / off'],
  )
})

test('a list declares the same menu before and after its rows arrive', () => {
  // The menu is fixed when the page is built, which is usually before the data comes back.
  for (const kind of ['sessions', 'running', 'projects', 'todos', 'needs'] as const) {
    const stack = [page('deck', 'focus', undefined, 'DECK'), page(kind)]
    const before = listScreen(stack, data()).menu.map(m => `${m.id}:${m.name}`)
    const after = listScreen(
      stack,
      data({
        sessions: { items: [session(0)], more: 0 },
        runs: { items: [run(0)], more: 0 },
        projects: {
          items: [
            {
              id: 'p',
              name: 'p',
              branch: '',
              dirty: 0,
              pinned: false,
              counts: { sessions: 0, running: 0, waiting: 0, todos_open: 0 },
            },
          ],
          more: 0,
        },
        todos: {
          items: [
            { id: 't', project: 'p', project_name: 'p', text: 't', source: 'todo-file', file: 'TODO.md', done: false },
          ],
          more: 0,
        },
        needs: { items: [], more: 0 },
      }),
    ).menu.map(m => `${m.id}:${m.name}`)
    assert.deepEqual(after, before, kind)
    assert.ok(before.length > 1, `${kind} has its row type's actions`)
  }
})

test('the deck shows focus in the status bar and the outside-focus line', () => {
  const s = listScreen([page('deck', 'focus', undefined, 'DECK')], data({ deck }))
  assert.equal(s.note, 'outside focus: 3 active, 1 waiting')
  assert.match(
    barText([page('deck', 'focus', undefined, 'DECK')], data({ deck })),
    /^DECK\s+ARMED · FOCUS glasses\+hub · 31m · 14:02$/,
  )
})

test('run detail ticks ELAPSED from the relay clock plus the time since it was fetched', () => {
  const r = run(0)
  const s = detailScreen([page('running'), page('run', 'all', r.id)], data({ run: r, runAge: 4.6 }))
  assert.equal(s.headline, 'ELAPSED 12:38')
})

test('windowFor keeps the cursor on screen', () => {
  assert.equal(windowFor(0, 0, 12), 0)
  assert.equal(windowFor(VISIBLE_ROWS, 0, 12), 1)
  assert.equal(windowFor(2, 4, 12), 2)
  assert.equal(windowFor(11, 7, 12), 7)
})

test('a short breadcrumb is never cut to make room for a short status', () => {
  for (const [left, right] of [
    ['TO-DOS › TO-DO', 'OFF · 11:56'],
    ['glasses › SESSIONS', 'OFF · 11:23'],
    ['DECK', 'ARMED · 14:02'],
  ]) {
    const line = bar(left, right, ROW_W)
    assert.ok(line.startsWith(`${left} `) && line.endsWith(` ${right}`), line)
    assert.ok(width(line) <= ROW_W, line)
  }
})

test('fit cuts by pixels and ago formats durations', () => {
  assert.equal(fit('short', 200), 'short')
  assert.ok(width(fit('W'.repeat(80), 100)) <= 100)
  assert.ok(fit('W'.repeat(80), 100).endsWith('…'))
  assert.deepEqual(
    [ago(42), ago(185), ago(7200), ago(42, true), ago(761, true), ago(3725, true)],
    ['0m', '3m', '2h', '0:42', '12:41', '1:02:05'],
  )
})

// ---- gestures -------------------------------------------------------------------

const TAP = { sysEvent: { eventSource: 1 } }
const DOUBLE = { sysEvent: { eventType: 3, eventSource: 1 } }
const MENU_OPEN = { sysEvent: { eventType: 4 } }
const MENU_CLOSE = { sysEvent: { eventType: 5 } }
const MENU_PICK = { menuItemClickEvent: { itemID: 12 } }

function rig(start: View) {
  let t = 100_000
  let current = start
  const timers = new Map<number, { at: number; fn: () => void }>()
  let next = 1
  const calls: string[] = []
  const g = createGestures(
    () => current,
    {
      tap: id => calls.push(`tap:${id}`),
      double: () => calls.push('double'),
      swipe: d => calls.push(`swipe:${d}`),
      menu: (id, v) => calls.push(`menu:${id}:${v}`),
      foreground: e => calls.push(`fg:${e}`),
      systemExit: a => calls.push(`exit:${a}`),
    },
    {
      now: () => t,
      setTimeout: (fn, ms) => (timers.set(next, { at: t + ms, fn }), next++),
      clearTimeout: h => void timers.delete(h as number),
    },
  )
  return {
    calls,
    send: (e: object) => g.handle(e as HubEvent),
    wait(ms: number) {
      t += ms
      for (const [id, timer] of [...timers]) if (timer.at <= t && timers.delete(id)) timer.fn()
    },
    show(v: View) {
      current = v
    },
  }
}

test('a menu opening and closing in the simulator is not going to the background', () => {
  const r = rig({ id: 'sessions', shownAt: 0 })
  r.send(MENU_OPEN)
  r.send(MENU_PICK)
  r.send(MENU_CLOSE)
  assert.deepEqual(r.calls, ['fg:true', 'menu:12:sessions'])
  r.send(MENU_CLOSE) // a real exit now
  assert.deepEqual(r.calls.slice(-1), ['fg:false'])
  r.send(TAP) // input means we're in front again
  assert.deepEqual(r.calls.slice(-1), ['fg:true'])
})

test('every screen has the tap lockout, and a tap waits out the double-tap window', () => {
  const r = rig({ id: 'deck', shownAt: 100_000 })
  r.send(TAP)
  r.wait(2_000)
  assert.deepEqual(r.calls, [])
  r.send(TAP)
  r.wait(DOUBLE_WINDOW_MS - 1)
  assert.deepEqual(r.calls, [])
  r.wait(1)
  assert.deepEqual(r.calls, ['tap:deck'])
  r.show({ id: 'projects', shownAt: 102_400 })
  r.wait(GRACE_MS - 10)
  r.send(TAP)
  r.send(DOUBLE)
  r.wait(2_000)
  assert.deepEqual(r.calls, ['tap:deck', 'double'])
})
