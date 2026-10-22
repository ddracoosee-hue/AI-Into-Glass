// Every glasses screen as data: which template, what each container says, what tap opens and what
// the long-press menu offers. Pure, so test/screens.test.ts can check rows and limits without a bridge.
import type { DeckCounts, DeckError, DeckEvent, Need, Project, Rows, Run, Session, SessionDetail, Todo } from './deck-api.ts'
import { ago, bar, columns, fit, SCREEN_W } from './text.ts'

export type Template = 'list' | 'detail' | 'prompt'
export type ListKind = 'deck' | 'needs' | 'sessions' | 'running' | 'projects' | 'project' | 'todos' | 'focus'
export type DetailKind = 'session' | 'run' | 'todo' | 'card' | 'commits' | 'error' | 'since'
export type PageKind = ListKind | DetailKind

/** One level of the stack. `scope` is all, focus or a project id; `cursor` and `top` belong to lists. */
export interface Page {
  kind: PageKind
  scope: string
  id?: string
  label?: string
  cursor: number
  top: number
  /** The highlighted row's key, so the cursor stays on it when rows are added above or re-sorted. */
  cursorKey?: string
  /** The focus picker's pinned projects, before focus starts. */
  picked?: string[]
}

export type Action =
  | { do: 'open'; page: Omit<Page, 'cursor' | 'top'> }
  | { do: 'prompt'; request: string }
  | { do: 'todo-done'; id: string }
  | { do: 'arm-toggle' } | { do: 'focus-start' } | { do: 'focus-end' } | { do: 'transitions' }
  | { do: 'focus-project'; project: string } | { do: 'mute'; session: string } | { do: 'phone'; page: Omit<Page, 'cursor' | 'top'> }
  | { do: 'todo-top'; id: string } | { do: 'pick'; project: string } | { do: 'focus-go'; minutes: 25 | 50 | null }
  | { do: 'back' } | { do: 'none' }

export interface Row {
  key: string
  cols: { text: string; x: number }[]
  right?: string
  open?: Action
  /** Long-press menu for this row; the menu acts on the highlighted row. */
  menu?: Menu
}

export interface MenuItem {
  id: number
  name: string // at most 32 UTF-8 bytes (SDK)
  action: Action
}
export type Menu = MenuItem[]

export interface ListScreen {
  template: 'list'
  key: string
  crumb: string
  status: string
  rows: Row[]
  note: string
  hint: string
  menu: Menu
  empty: string
}

export interface DetailScreen {
  template: 'detail'
  key: string
  crumb: string
  status: string
  headline: string
  body: string
  hint: string
  menu: Menu
  primary?: Action
}

export type Screen = ListScreen | DetailScreen

export const VISIBLE_ROWS = 5
export const ROW_W = SCREEN_W - 12 // inner width 572, less a margin: a line that wraps scrolls
export const GUTTER = 16 // the › marker, then the row
export const MAX_ROWS = 20 // the glasses list limit; the rest live on the phone

export const AGENT_BADGE: Record<string, string> = { 'claude-code': 'CC', codex: 'CDX' }
export const AGENT_NAME: Record<string, string> = { 'claude-code': 'Claude Code', codex: 'Codex' }

/** Data the screens read. Every field is optional: a screen draws before its data arrives. */
export interface Data {
  deck?: DeckCounts
  rootDeck?: DeckCounts
  needs?: Rows<Need>
  sessions?: Rows<Session>
  runs?: Rows<Run>
  projects?: Rows<Project>
  project?: Project
  todos?: Rows<Todo>
  session?: SessionDetail
  run?: Run
  todo?: Todo
  need?: Need
  error?: DeckError
  armed: boolean
  clock: string
  /** Seconds since `run` was fetched, so ELAPSED can tick between polls. */
  runAge?: number
  /** The newest failure not yet opened on the glasses; it pins a row to the top of the Deck. */
  spotlight?: DeckError
  /** What happened while nobody looked: the relay's events since the last view. */
  since?: { events: DeckEvent[]; away_s: number }
  /** The relay's answer to the last "done" on this to-do. */
  todoOutcome?: 'done' | 'changed' | 'failed'
  /** To-do ids moved to the top on the glasses, newest first. */
  todoTop?: string[]
}

const open = (kind: PageKind, scope: string, id?: string, label?: string): Action => ({ do: 'open', page: { kind, scope, id, label } })
const phone = (kind: PageKind, scope: string, id?: string): Action => ({ do: 'phone', page: { kind, scope, id } })
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`
/** Why an approval went to the terminal, in the words the glasses use. */
const WHY: Record<string, string> = { 'no device listening': 'nobody watching', 'outside focus': 'outside focus', muted: 'muted',
  timeout: 'timed out', cancelled: 'cancelled' }
/** A label and its value, the value at a fixed x so detail bodies line up in a proportional font. */
const kv = (label: string, value: string) => columns([{ text: label, x: 0 }, { text: value, x: 76 }], ROW_W)

// ---- menus (ids are stable per object type; labels never change without a rebuild) --------------

export const DECK_MENU: Menu = [
  { id: 1, name: 'Arm / Disarm', action: { do: 'arm-toggle' } },
  { id: 2, name: 'Start focus', action: { do: 'focus-start' } },
  { id: 3, name: 'End focus', action: { do: 'focus-end' } },
  { id: 4, name: 'Transitions on / off', action: { do: 'transitions' } },
]

export function sessionMenu(s: { id: string; project: string; current?: { run?: string; tool: string; target: string } | null }): Menu {
  const run = s.current?.run
  return [
    { id: 10, name: 'Open on phone', action: phone('session', s.project, s.id) },
    { id: 11, name: 'Focus on its project', action: { do: 'focus-project', project: s.project } },
    { id: 12, name: 'Mute this session', action: { do: 'mute', session: s.id } },
    { id: 13, name: 'Jump to its running tool', action: run ? open('run', s.project, run, s.current?.target || s.current?.tool) : open('running', s.project, undefined, 'RUNNING') },
  ]
}

export function runMenu(r: { session: string; project: string; id: string }): Menu {
  return [
    { id: 20, name: 'Open session', action: open('session', r.project, r.session) },
    { id: 21, name: 'Open on phone', action: phone('run', r.project, r.id) },
  ]
}

export function projectMenu(p: { id: string }): Menu {
  return [
    { id: 30, name: 'Focus on this', action: { do: 'focus-project', project: p.id } },
    { id: 31, name: 'Recent commits', action: open('commits', p.id, p.id) },
    { id: 32, name: 'Open on phone', action: phone('project', p.id, p.id) },
  ]
}

export function todoMenu(t: { id: string; project: string }): Menu {
  return [
    { id: 40, name: 'Mark done', action: { do: 'todo-done', id: t.id } },
    { id: 41, name: 'Move to top', action: { do: 'todo-top', id: t.id } },
    { id: 42, name: 'Open on phone', action: phone('todo', t.project, t.id) },
  ]
}

/**
 * The menu a list page declares. A page's menu is fixed when the page is built, often before its
 * rows arrive, so it is the row type's menu with the labels only. A selection acts on the
 * highlighted row's own copy of the item (main.ts, menuAction), which knows the row.
 */
function listMenu(rowMenu: Menu): Menu {
  return rowMenu.map(item => ({ ...item, action: { do: 'none' } as Action }))
}
const SESSION_ROWS = listMenu(sessionMenu({ id: '', project: '' }))
const RUN_ROWS = listMenu(runMenu({ id: '', session: '', project: '' }))
const PROJECT_ROWS = listMenu(projectMenu({ id: '' }))
const TODO_ROWS = listMenu(todoMenu({ id: '', project: '' }))

// ---- status bar ---------------------------------------------------------------------------------

export function statusText(d: Data, page: Page): string {
  const parts = [d.armed ? 'ARMED' : 'OFF']
  const focus = (d.rootDeck ?? d.deck)?.focus
  if (focus) parts.push(`FOCUS ${focus.projects.join('+')}${focus.left_s != null ? ` · ${ago(focus.left_s)}` : ''}`)
  else if (page.kind === 'deck' && page.scope !== 'all') parts.push('FOCUS off')
  if (page.kind === 'project' && d.project) {
    return [d.project.branch, d.project.dirty ? `${d.project.dirty} dirty` : 'clean', d.clock].filter(Boolean).join(' · ')
  }
  parts.push(d.clock)
  return parts.join(' · ')
}

export function crumb(stack: Page[]): string {
  const labels = stack.map(p => p.label ?? p.kind.toUpperCase())
  return labels.length === 1 ? labels[0] : `${labels[labels.length - 2]} › ${labels[labels.length - 1]}`
}

export function barText(stack: Page[], d: Data): string {
  return bar(crumb(stack), statusText(d, stack[stack.length - 1]), ROW_W)
}

// ---- lists --------------------------------------------------------------------------------------

function deckRows(c: DeckCounts | undefined, scope: string, project?: Project): Row[] {
  const n = (v: number | undefined) => (c ? String(v ?? 0) : '…')
  const rows: Row[] = [
    { key: 'needs', cols: [{ text: 'Needs you', x: 0 }, { text: n(c?.needs_you), x: 170 }], open: open('needs', scope, undefined, 'NEEDS YOU') },
    { key: 'sessions', cols: [{ text: 'Sessions', x: 0 }, { text: c ? (project ? n(c.sessions) : `${c.sessions} active`) : '…', x: 170 }], open: open('sessions', scope, undefined, 'SESSIONS') },
    { key: 'running', cols: [{ text: 'Running', x: 0 }, { text: c ? (c.running ? `${c.running} · longest ${ago(c.longest_s)}` : '0') : '…', x: 170 }], open: open('running', scope, undefined, 'RUNNING') },
  ]
  if (!project) rows.push({ key: 'projects', cols: [{ text: 'Projects', x: 0 }, { text: n(c?.projects), x: 170 }], open: open('projects', 'all', undefined, 'PROJECTS') })
  rows.push({ key: 'todos', cols: [{ text: 'To-dos', x: 0 }, { text: c ? `${c.todos_open} open` : '…', x: 170 }], open: open('todos', scope, undefined, 'TO-DOS') })
  if (project) {
    rows.push({ key: 'commits', cols: [{ text: 'Recent commits', x: 0 }, { text: String(project.commits?.length ?? '…'), x: 170 }],
      open: open('commits', project.id, project.id, 'COMMITS') })
  } else {
    const f = c?.focus
    rows.push({ key: 'focus', cols: [{ text: 'Focus', x: 0 }, { text: f ? `${f.projects.join('+')}${f.left_s != null ? ` · ${ago(f.left_s)}` : ''}` : 'off', x: 170 }],
      open: open('focus', 'all', undefined, 'FOCUS') })
  }
  return rows
}

function sessionRow(s: Session): Row {
  const what = s.state === 'waiting' ? (s.waiting_for === 'input' ? 'waiting · input' : `waiting · ${s.current?.tool ?? ''}`)
    : s.state === 'working' && s.current ? `working · ${s.current.tool}` : s.state
  const age = s.state === 'waiting' && s.state_for_s < 3600 ? ago(s.state_for_s, true) : ago(s.state_for_s)
  const where = s.tag ? `${s.project_name}:${s.tag}` : s.project === '(other)' ? s.folder : s.project_name
  return { key: s.id, cols: [{ text: AGENT_BADGE[s.agent] ?? s.agent, x: 0 }, { text: where, x: 50 }, { text: what, x: 158 }], right: age,
    open: open('session', s.project, s.id, `${where} · ${AGENT_NAME[s.agent] ?? s.agent}`), menu: sessionMenu(s) }
}

function runRow(r: Run): Row {
  return { key: r.id, cols: [{ text: ago(r.elapsed_s), x: 0 }, { text: AGENT_BADGE[r.agent] ?? r.agent, x: 46 }, { text: r.project_name, x: 100 },
    { text: r.target || r.tool, x: 208 }], open: open('run', r.project, r.id, r.target || r.tool), menu: runMenu(r) }
}

function needRow(n: Need): Row {
  const age = n.for_s < 3600 ? ago(n.for_s, true) : ago(n.for_s)
  const badge = n.kind === 'card' ? (n.level === 'error' ? '!' : '·') : AGENT_BADGE[n.agent ?? ''] ?? '·'
  const action: Action = n.kind === 'approval' ? { do: 'prompt', request: n.id }
    : n.kind === 'session' ? open('session', n.project, n.id, `${n.project_name} · ${AGENT_NAME[n.agent ?? ''] ?? ''}`)
      : open('card', n.project, n.id, 'CARD')
  const what = n.kind === 'approval' ? `${n.tool} ${n.title}` : n.title
  return { key: `${n.kind}:${n.id}`, cols: [{ text: badge, x: 0 }, { text: n.project_name, x: 50 }, { text: what, x: 158 }], right: age, open: action,
    menu: n.session ? sessionMenu({ id: n.session, project: n.project }) : undefined }
}

function projectRow(p: Project): Row {
  const c = p.counts
  const busy = [c.waiting && `${c.waiting} waiting`, c.running && `${c.running} running`, `${plural(c.sessions, 'session')}`].filter(Boolean).join(' · ')
  return { key: p.id, cols: [{ text: p.pinned ? '●' : '', x: 0 }, { text: p.name, x: 18 }, { text: busy, x: 150 }],
    open: open('project', p.id, p.id, p.name), menu: projectMenu(p) }
}

function todoRow(t: Todo): Row {
  return { key: t.id, cols: [{ text: t.project_name, x: 0 }, { text: t.text, x: 110 }], open: open('todo', t.project, t.id, 'TO-DO'), menu: todoMenu(t) }
}

function focusRows(projects: Project[], picked: string[], focus: DeckCounts['focus'] | undefined): Row[] {
  const rows: Row[] = projects.map(p => ({ key: p.id, cols: [{ text: picked.includes(p.id) ? '●' : '○', x: 0 }, { text: p.name, x: 22 }],
    open: { do: 'pick', project: p.id } }))
  if (picked.length) {
    const what = picked.map(id => projects.find(p => p.id === id)?.name ?? id).join('+')
    for (const minutes of [25, 50, null] as const) {
      rows.push({ key: `go:${minutes}`, cols: [{ text: `Start ${what}`, x: 0 }, { text: minutes ? `${minutes} min` : 'no timer', x: 300 }],
        open: { do: 'focus-go', minutes } })
    }
  }
  if (focus) rows.push({ key: 'end', cols: [{ text: `End focus on ${focus.projects.join('+')}`, x: 0 }], open: { do: 'focus-end' } })
  return rows
}

function capped(rows: Row[], more: number, what: string): Row[] {
  const shown = rows.slice(0, MAX_ROWS - (more || rows.length > MAX_ROWS ? 1 : 0))
  const hidden = more + rows.length - shown.length
  return hidden > 0 ? [...shown, { key: 'more', cols: [{ text: `› ${hidden} more ${what} on phone`, x: 0 }], open: { do: 'none' } }] : shown
}

export function listScreen(stack: Page[], d: Data): ListScreen {
  const page = stack[stack.length - 1]
  const base = { template: 'list' as const, key: `${page.kind}:${page.scope}:${page.id ?? ''}`, crumb: crumb(stack), status: statusText(d, page), note: '' }
  switch (page.kind as ListKind) {
    case 'deck': {
      const c = d.deck
      const note = c?.outside_focus ? `outside focus: ${c.outside_focus.active} active, ${c.outside_focus.waiting} waiting` : ''
      const rows = deckRows(c, page.scope)
      const e = d.spotlight
      if (e) {
        // Pinned until opened: one row, above everything, at full brightness when highlighted.
        rows.unshift({ key: `error:${e.error ?? e.id}`, cols: [{ text: 'FAILED', x: 0 }, { text: AGENT_BADGE[e.agent] ?? '', x: 70 },
          { text: `${e.target || e.tool || e.project} · ${e.detail}`, x: 118 }], open: open('error', e.project, e.error ?? e.id, 'FAILED'),
          menu: sessionMenu({ id: e.session, project: e.project }) })
      }
      return { ...base, rows, note, hint: stack.length === 1 ? 'tap open · 2× exit · hold actions' : 'tap open · 2× back · hold actions',
        menu: DECK_MENU, empty: '' }
    }
    case 'project': {
      const rows = deckRows(d.deck, page.scope, d.project ?? { id: page.scope, name: page.label ?? page.scope, branch: '', dirty: 0, pinned: false, counts: { sessions: 0, running: 0, waiting: 0, todos_open: 0 } })
      return { ...base, rows, hint: 'tap open · 2× back · hold focus on this', menu: projectMenu({ id: page.scope }), empty: '' }
    }
    case 'needs': {
      const rows = capped((d.needs?.items ?? []).map(needRow), d.needs?.more ?? 0, 'items')
      return { ...base, rows, hint: 'tap open · 2× back · hold actions', menu: SESSION_ROWS,
        empty: d.needs ? 'Nothing needs you.' : 'Loading…' }
    }
    case 'sessions': {
      const rows = capped((d.sessions?.items ?? []).filter(s => !s.muted).map(sessionRow), d.sessions?.more ?? 0, 'sessions')
      return { ...base, rows, hint: 'tap open · 2× back · hold actions', menu: SESSION_ROWS,
        empty: d.sessions ? 'No sessions.' : 'Loading…' }
    }
    case 'running': {
      const rows = capped((d.runs?.items ?? []).map(runRow), d.runs?.more ?? 0, 'runs')
      const count = d.runs ? d.runs.items.length + d.runs.more : 0
      const labelled = { ...base, crumb: crumb([...stack.slice(0, -1), { ...page, label: `RUNNING · ${count}` }]) }
      return { ...labelled, rows, hint: 'oldest first · tap open · 2× back', menu: RUN_ROWS,
        empty: d.runs ? 'Nothing running.' : 'Loading…' }
    }
    case 'projects': {
      const rows = (d.projects?.items ?? []).map(projectRow)
      return { ...base, rows, hint: 'tap open · 2× back · hold actions', menu: PROJECT_ROWS,
        empty: d.projects ? 'No projects configured.' : 'Loading…' }
    }
    case 'todos': {
      const top = d.todoTop ?? []
      const rank = (t: Todo) => (top.includes(t.id) ? top.indexOf(t.id) : top.length)
      const items = [...(d.todos?.items ?? [])].sort((a, b) => rank(a) - rank(b)) // stable: file order otherwise
      const rows = capped(items.map(todoRow), d.todos?.more ?? 0, 'to-dos')
      const open_ = d.todos ? d.todos.items.length + d.todos.more : 0
      const labelled = { ...base, crumb: crumb([...stack.slice(0, -1), { ...page, label: `TO-DOS · ${open_} open` }]) }
      return { ...labelled, rows, hint: 'tap open · 2× back · hold actions', menu: TODO_ROWS,
        empty: d.todos ? 'No open to-dos.' : 'Loading…' }
    }
    case 'focus': {
      const projects = (d.projects?.items ?? []).filter(p => p.id !== '(other)')
      const picked = page.picked ?? (d.rootDeck?.focus?.projects ?? [])
      const rows = d.projects ? focusRows(projects, picked, d.rootDeck?.focus) : []
      const note = picked.length >= 2 ? 'two pinned: unpin one to pick another' : ''
      // Long-press is disabled here: an empty menu still claims it from the OS.
      return { ...base, rows, note, hint: 'tap pin (max 2) · 2× back', menu: [], empty: d.projects ? 'No projects configured.' : 'Loading…' }
    }
  }
}

// ---- details ------------------------------------------------------------------------------------

export function detailScreen(stack: Page[], d: Data): DetailScreen {
  const page = stack[stack.length - 1]
  const base = { template: 'detail' as const, key: `${page.kind}:${page.scope}:${page.id ?? ''}`, crumb: crumb(stack), status: statusText(d, page) }
  switch (page.kind as DetailKind) {
    case 'session': {
      const s = d.session
      if (!s) return { ...base, headline: 'Loading…', body: '', hint: '2× back', menu: sessionMenu({ id: page.id ?? '', project: page.scope }) }
      const head = s.state === 'waiting'
        ? `${s.waiting_for === 'input' ? 'WAITING FOR INPUT' : 'WAITING ON YOU'} · ${ago(s.state_for_s, true)}`
        : `${s.state.toUpperCase()} · ${ago(s.state_for_s)}`
      const current = s.current ? `${s.current.tool}  ${s.current.target}` : ''
      const lines = [
        s.title && kv('Title', s.title),
        kv('Model', [s.model || '—', `${s.counts.tools} tools`, `${s.counts.approved} approved`, s.counts.failed ? `${s.counts.failed} failed` : ''].filter(Boolean).join(' · ')),
        lastRun(s),
      ].filter(Boolean) as string[]
      return { ...base, headline: `${head}\n${current}`.trim(), body: lines.join('\n'),
        hint: s.pending_request ? 'tap open prompt · 2× back · hold actions' : '2× back · hold actions',
        menu: sessionMenu(s), primary: s.pending_request ? { do: 'prompt', request: s.pending_request } : undefined }
    }
    case 'run': {
      const r = d.run
      if (!r) return { ...base, headline: 'Loading…', body: '', hint: '2× back', menu: runMenu({ id: page.id ?? '', session: '', project: page.scope }) }
      const live = r.status === 'running' || r.status === 'waiting'
      const elapsed = r.elapsed_s + (live ? Math.floor(d.runAge ?? 0) : 0)
      const started = new Date(r.started_at)
      return { ...base, headline: live ? `ELAPSED ${ago(elapsed, true)}` : `${r.status.toUpperCase()} after ${ago(elapsed, true)}`,
        body: [kv('Agent', `${AGENT_NAME[r.agent] ?? r.agent} · ${r.project_name}`), kv('Tool', `${r.tool}${r.kind === 'subagent' ? ' (subagent)' : ''}`),
          r.target && kv('Target', r.target), kv('Started', `${String(started.getHours()).padStart(2, '0')}:${String(started.getMinutes()).padStart(2, '0')}`)].filter(Boolean).join('\n'),
        hint: 'tap open session · 2× back', menu: runMenu(r), primary: open('session', r.project, r.session, `${r.project_name} · ${AGENT_NAME[r.agent] ?? ''}`) }
    }
    case 'todo': {
      const t = d.todo
      if (!t) return { ...base, headline: 'Loading…', body: '', hint: '2× back', menu: [] }
      const writable = t.source !== 'claude-task'
      if (d.todoOutcome === 'changed' || d.todoOutcome === 'failed') {
        return { ...base, headline: d.todoOutcome === 'changed' ? 'CHANGED ON DISK · not marked' : 'NOT SENT · check the relay',
          body: [kv('Line', t.text), kv('File', t.file ?? ''), d.todoOutcome === 'changed' ? 'The line was edited or moved since it was read.' : ''].filter(Boolean).join('\n'),
          hint: '2× back', menu: todoMenu(t) }
      }
      return { ...base, headline: t.done ? `DONE · ${t.text}` : t.text, body: [kv('Project', t.project_name), kv('From', t.source === 'claude-task' ? 'Claude Code task list' : t.file ?? '')].join('\n'),
        hint: t.done ? '2× back' : writable ? 'tap done · 2× back · hold actions' : 'done in Claude Code · 2× back',
        menu: todoMenu(t), primary: !t.done && writable ? { do: 'todo-done', id: t.id } : undefined }
    }
    case 'card': {
      const n = d.need
      return { ...base, headline: n ? n.title : 'Gone', body: n ? [kv('Project', n.project_name), kv('Level', n.level ?? 'info'), kv('Posted', `${ago(n.for_s)} ago`)].join('\n') : 'This card expired or was cleared.',
        hint: '2× back', menu: [{ id: 50, name: 'Open on phone', action: phone('card', page.scope, page.id) }] }
    }
    case 'commits': {
      const p = d.project
      return { ...base, headline: p ? `${p.name} · ${p.branch || 'no branch'}` : 'Loading…', body: (p?.commits ?? []).map(c => `· ${c}`).join('\n') || (p ? 'No commits.' : ''),
        hint: '2× back', menu: projectMenu({ id: page.scope }) }
    }
    case 'since': {
      const ev = d.since?.events ?? []
      const runs = ev.filter(e => e.type === 'run' && e.status === 'ok').length
      const failed = ev.filter(e => e.type === 'error')
      const fell = ev.filter(e => e.type === 'approval' && ['refused', 'timeout', 'cancelled'].includes(String(e.status)))
      const decided = ev.filter(e => e.type === 'approval' && (e.status === 'approved' || e.status === 'denied')).length
      const ended = ev.filter(e => e.type === 'session' && e.state === 'ended').length
      const lines = [
        kv('Finished', `${plural(runs, 'run')}${ended ? ` · ${plural(ended, 'session')} ended` : ''}${decided ? ` · ${decided} decided` : ''}`),
        ...failed.slice(0, 2).map(e => kv('Failed', `${e.target || e.tool} · ${e.detail} (${e.project})`)),
        ...fell.slice(0, 2).map(e => kv('Terminal', `${e.summary || e.tool} (${e.project}, ${WHY[String(e.status === 'refused' ? e.reason : e.status)] ?? e.status})`)),
      ]
      const more = failed.length + fell.length - Math.min(failed.length, 2) - Math.min(fell.length, 2)
      if (more > 0) lines.push(`… ${more} more on phone`)
      return { ...base, headline: `SINCE YOU LOOKED · ${ago(d.since?.away_s ?? 0)}\n${plural(failed.length, 'failure')} · ${fell.length} fell back to the terminal`,
        body: lines.map(l => fit(l, ROW_W)).join('\n'), hint: 'tap or 2× to the Deck', menu: [], primary: { do: 'back' } }
    }
    case 'error': {
      const e = d.error
      return { ...base, headline: e ? `FAILED · ${e.detail}` : 'Gone', body: e ? [kv('Agent', `${AGENT_NAME[e.agent] ?? e.agent} · ${e.project}`), e.tool && kv('Tool', e.tool), e.target && kv('Target', e.target)].filter(Boolean).join('\n') : '',
        hint: e ? 'tap open session · 2× back' : '2× back', menu: e ? sessionMenu({ id: e.session, project: e.project }) : [],
        primary: e ? open('session', e.project, e.session, `${e.project} · ${AGENT_NAME[e.agent] ?? ''}`) : undefined }
    }
  }
}

function lastRun(s: SessionDetail): string {
  const run = [...s.events].reverse().find(e => e.type === 'run' && e.status !== 'running')
  if (!run) return ''
  const age = Math.max(0, (Date.now() - Date.parse(String(run.ts))) / 1000)
  return kv('Last', [run.target ? `${run.target}  ${run.tool}` : String(run.tool), ago(age)].join(' · '))
}

export function screenFor(stack: Page[], d: Data): Screen {
  const kind = stack[stack.length - 1].kind
  return ['session', 'run', 'todo', 'card', 'commits', 'error', 'since'].includes(kind) ? detailScreen(stack, d) : listScreen(stack, d)
}

/** A row as it appears on the glasses: the › marker on the highlighted one, columns at pixel x. */
export function rowText(row: Row, selected: boolean): string {
  return columns([{ text: selected ? '›' : '', x: 0 }, ...row.cols.map(c => ({ text: c.text, x: c.x + GUTTER }))], ROW_W,
    row.right ? { text: row.right, edge: ROW_W - 4 } : undefined)
}

/** The window of rows to show so that the cursor is visible. */
export function windowFor(cursor: number, top: number, count: number): number {
  if (cursor < top) return cursor
  if (cursor >= top + VISIBLE_ROWS) return cursor - VISIBLE_ROWS + 1
  return Math.max(0, Math.min(top, Math.max(0, count - VISIBLE_ROWS)))
}
