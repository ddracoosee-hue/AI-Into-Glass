// Command Deck web UI: one module, three hosts. The desktop page (127.0.0.1:8799), the presenter
// view (/present), and the plugin's phone page all mount it with their own way to reach the relay.
// Read-only: it shows what the relay built and never writes. Plain ES module, no build step.

/**
 * @typedef {{ get: (path: string) => Promise<any>, events: (since: number, wait: number) => Promise<{ events: any[], cursor: number, reset?: boolean } | null> }} DeckApi
 * @typedef {{ view: string, scope?: string, id?: string }} Route
 * @typedef {{ hidePaths?: boolean, present?: boolean, hash?: boolean, title?: string, onRoute?: (r: Route) => void }} DeckOptions
 */

const AGENT = { 'claude-code': 'Claude Code', codex: 'Codex' }
const BADGE = { 'claude-code': 'CC', codex: 'CDX' }
const DEPTH = { deck: 0, needs: 1, sessions: 1, running: 1, projects: 1, todos: 1, focus: 1, project: 1, session: 2, run: 2, todo: 2, commits: 2, card: 2, error: 2 }
const TITLES = { deck: 'Deck', needs: 'Needs you', sessions: 'Sessions', running: 'Running', projects: 'Projects', todos: 'To-dos',
  focus: 'Focus', project: 'Project', session: 'Session', run: 'Run', todo: 'To-do', commits: 'Recent commits', card: 'Card', error: 'Failure' }
const SECTIONS = ['deck', 'needs', 'sessions', 'running', 'projects', 'todos', 'focus']
const ENDED = { ended: 'from the Deck', timer: 'when its timer ran out', disarmed: 'when the gate was disarmed', replaced: 'for a new focus' }
const WHY = { 'no device listening': 'nobody watching', 'outside focus': 'outside focus', muted: 'muted', timeout: 'timed out', cancelled: 'cancelled' }

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
const num = n => (Number.isFinite(Number(n)) ? Number(n) : 0) // numbers from the relay are still data, never markup
const plural = (n, one, many = `${one}s`) => `${num(n)} ${num(n) === 1 ? one : many}`

/** 0m, 12m, 3h, 2d; or, `precise`, 0:42 and 1:02:05 */
export function ago(seconds, precise = false) {
  const s = Math.max(0, Math.floor(seconds))
  const two = n => String(n).padStart(2, '0')
  if (precise) return s < 3600 ? `${Math.floor(s / 60)}:${two(s % 60)}` : `${Math.floor(s / 3600)}:${two(Math.floor(s / 60) % 60)}:${two(s % 60)}`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  return s < 86400 ? `${Math.floor(s / 3600)}h` : `${Math.floor(s / 86400)}d`
}

export function routeToHash(r) {
  const q = r.scope && r.scope !== 'all' ? `?scope=${encodeURIComponent(r.scope)}` : ''
  return `#/${r.view === 'deck' ? '' : r.view}${r.id ? `/${encodeURIComponent(r.id)}` : ''}${q}`
}

export function hashToRoute(hash) {
  const m = /^#\/([a-z]*)(?:\/([^?]*))?(?:\?scope=([^&]*))?/.exec(hash || '')
  if (!m) return { view: 'deck', scope: 'all' }
  const view = m[1] && DEPTH[m[1]] !== undefined ? m[1] : 'deck'
  return { view, id: m[2] ? decodeURIComponent(m[2]) : undefined, scope: m[3] ? decodeURIComponent(m[3]) : view === 'project' ? decodeURIComponent(m[2] ?? '') : 'all' }
}

const depthOf = r => (DEPTH[r.view] ?? 0) + (r.scope && !['all', 'focus'].includes(r.scope) && DEPTH[r.view] === 1 && r.view !== 'project' ? 1 : 0)
const link = (r, text, cls = '') => `<a href="${routeToHash(r)}"${cls ? ` class="${cls}"` : ''}>${text}</a>`

// ---- formatting ---------------------------------------------------------------------------------

function stateText(s) {
  if (s.state === 'waiting') return s.waiting_for === 'input' ? 'waiting for input' : `waiting · ${esc(s.current?.tool ?? '')}`
  if (s.state === 'working' && s.current) return `working · ${esc(s.current.tool)}`
  return esc(s.state)
}

function eventLine(e) {
  const where = esc(e.project ?? '')
  const who = BADGE[e.agent] ? `${BADGE[e.agent]} ` : ''
  switch (e.type) {
    case 'session': return e.muted !== undefined ? `${who}${where} muted` : `${who}${where} ${esc(e.state)}${e.waiting_for ? ` for ${esc(e.waiting_for)}` : ''}`
    case 'run': return `${who}${where} ${esc(e.target || e.tool)} · ${e.status === 'running' ? 'started' : `${esc(e.status)}${e.elapsed_s != null ? ` after ${ago(e.elapsed_s, true)}` : ''}`}`
    case 'approval': return `${who}${where} ${esc(e.summary || e.tool)} · ${e.status === 'refused' ? `to the terminal (${esc(WHY[e.reason] ?? e.reason)})` : e.status === 'pending' ? 'asks for approval' : esc(e.status)}`
    case 'error': return `${who}${where} ${esc(e.target || e.tool || '')} · failed (${esc(e.detail)})`
    case 'card': return `${where} ${esc(e.title ?? e.card)} · ${esc(e.status)}`
    case 'todo': return `${where} to-do ${e.status === 'done' ? 'done' : 'added'}: ${esc(e.text)}`
    case 'focus_start': return `focus on ${esc((e.projects ?? []).join(' + '))}${e.minutes ? ` for ${num(e.minutes)} min` : ''}`
    case 'focus_end': return `focus ended ${esc(ENDED[e.reason] ?? e.reason)}: ${plural(e.summary?.approved, 'approval')}, ${num(e.summary?.denied)} denied, ${plural(e.summary?.runs, 'run')}, ${plural(e.summary?.todos, 'to-do')} done`
    case 'armed': return e.armed ? 'gate armed' : 'gate disarmed'
    case 'view': return null
    default: return null
  }
}

const clock = ts => {
  const d = ts ? new Date(ts) : new Date()
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

// ---- the UI -------------------------------------------------------------------------------------

/**
 * Mount the deck into `root`.
 * @param {HTMLElement} root
 * @param {DeckApi} api
 * @param {DeckOptions} opts
 */
export function mountDeck(root, api, opts = {}) {
  const present = !!opts.present
  const state = {
    route: opts.hash ? hashToRoute(location.hash) : { view: 'deck', scope: 'all' },
    revealAll: !opts.hidePaths, revealed: new Set(), cursor: 0, ticker: [], status: null, summary: null, summaryUntil: 0,
    lastHtml: '', stopped: false, blank: false,
  }

  root.classList.add('deck', present ? 'deck--present' : 'deck--page')
  root.innerHTML = present ? `
    <header class="present-head"><p class="brand">Command Deck</p><p class="status" id="deck-status"></p></header>
    <main class="present-main" id="deck-view"></main>
    <aside class="present-ticker" aria-labelledby="ticker-h"><h2 id="ticker-h">Live</h2><ol class="ticker" id="deck-ticker"></ol></aside>
    <div class="present-summary" id="deck-summary" hidden></div>` : `
    <a class="skip-link visually-hidden" href="#deck-view">Skip to content</a>
    <header class="top">
      <p class="brand">${esc(opts.title ?? 'Command Deck')}</p>
      <p class="status" id="deck-status"></p>
      ${opts.hidePaths ? '<button type="button" class="toggle" id="deck-reveal" aria-pressed="false">Show all paths</button>' : ''}
    </header>
    <nav class="sections" aria-label="Sections"><ul id="deck-nav"></ul></nav>
    <main id="deck-view" tabindex="-1"></main>
    <aside class="side" aria-labelledby="ticker-h"><h2 id="ticker-h">Live</h2><ol class="ticker" id="deck-ticker"></ol></aside>
    <div class="visually-hidden" id="deck-announce" aria-live="polite" aria-atomic="true"></div>`
  const $ = id => root.querySelector(`#${id}`)

  // A target is a command or a path. Where paths are hidden, each one waits for a tap.
  const target = (key, text) => {
    if (!text) return ''
    if (state.revealAll || state.revealed.has(key)) return `<code class="target">${esc(text)}</code>`
    return `<button type="button" class="reveal" data-reveal="${esc(key)}" aria-label="Show target">show</button>`
  }

  async function get(path) {
    try {
      return await api.get(path)
    } catch {
      return null
    }
  }

  // ---- views ------------------------------------------------------------------------------------

  async function viewHtml(r) {
    const scope = r.scope ?? 'all'
    const q = `scope=${encodeURIComponent(scope)}`
    switch (r.view) {
      case 'deck': case 'project': {
        const s = r.view === 'project' ? r.id ?? scope : scope
        const [deck, needs, runs, project] = await Promise.all([get(`/deck?scope=${encodeURIComponent(s)}`), get(`/needs?scope=${encodeURIComponent(s)}&limit=5`),
          get(`/runs?scope=${encodeURIComponent(s)}&active=1&limit=5`), r.view === 'project' ? get(`/projects/${encodeURIComponent(s)}`) : null])
        if (!deck) return offline()
        const sc = s
        const tiles = [
          tile({ view: 'needs', scope: sc }, 'Needs you', deck.needs_you, deck.needs_you ? 'hot' : ''),
          tile({ view: 'sessions', scope: sc }, 'Sessions', deck.sessions, '', deck.waiting ? `${deck.waiting} waiting` : 'active'),
          tile({ view: 'running', scope: sc }, 'Running', deck.running, '', deck.running ? `longest ${ago(deck.longest_s)}` : 'nothing running'),
          r.view === 'deck' ? tile({ view: 'projects' }, 'Projects', deck.projects) : tile({ view: 'commits', id: s, scope: s }, 'Recent commits', project?.commits?.length ?? 0),
          tile({ view: 'todos', scope: sc }, 'To-dos', deck.todos_open, '', 'open'),
          r.view === 'deck' ? tile({ view: 'focus' }, 'Focus', deck.focus ? deck.focus.projects.join(' + ') : 'off', deck.focus ? 'on' : '',
            deck.focus?.left_s != null ? `${ago(deck.focus.left_s)} left` : deck.focus ? 'no timer' : '') : '',
        ].join('')
        const err = deck.errors?.[0]
        const heading = r.view === 'project' ? `${esc(project?.name ?? s)}` : 'Deck'
        const sub = r.view === 'project' && project ? `<p class="sub">${esc(project.branch || 'no branch')} · ${project.dirty ? `${num(project.dirty)} dirty` : 'clean'}</p>` : ''
        const outside = deck.outside_focus ? `<p class="outside">Outside focus: ${plural(deck.outside_focus.active, 'session')} active, ${num(deck.outside_focus.waiting)} waiting</p>` : ''
        return `<h1 tabindex="-1">${heading}</h1>${sub}
          ${err ? `<p class="spotlight" role="note"><strong>Failed</strong> ${esc(BADGE[err.agent] ?? '')} ${esc(err.project)} · ${target(`err:${err.id ?? err.error}`, err.target || err.tool)} · ${esc(err.detail)}</p>` : ''}
          <ul class="tiles" role="list">${tiles}</ul>${outside}
          <div class="previews">
            <section aria-labelledby="pv-needs"><h2 id="pv-needs">${link({ view: 'needs', scope: sc }, 'Needs you')}</h2>${needList(needs?.items ?? [], 'Nothing needs you.')}</section>
            <section aria-labelledby="pv-run"><h2 id="pv-run">${link({ view: 'running', scope: sc }, 'Running')}</h2>${runList(runs?.items ?? [], 'Nothing running.')}</section>
          </div>`
      }
      case 'needs': {
        const d = await get(`/needs?${q}&limit=200`)
        return d ? `<h1 tabindex="-1">Needs you</h1>${scopeNote(scope)}${needList(d.items, 'Nothing needs you.')}` : offline()
      }
      case 'sessions': {
        const d = await get(`/sessions?${q}&limit=200`)
        if (!d) return offline()
        const rows = d.items.map(s => `<li>${link({ view: 'session', id: s.id, scope: s.project }, `
          <span class="badge">${BADGE[s.agent] ?? ''}</span><span class="where">${esc(s.tag ? `${s.project_name}:${s.tag}` : s.project === '(other)' ? s.folder : s.project_name)}</span>
          <span class="what ${s.state}">${stateText(s)}</span><span class="when">${s.state === 'waiting' && s.state_for_s < 3600 ? ago(s.state_for_s, true) : ago(s.state_for_s)}</span>
          ${s.muted ? '<span class="tag">muted</span>' : ''}`, `row state-${s.state}`)}</li>`)
        return `<h1 tabindex="-1">Sessions</h1>${scopeNote(scope)}${rows.length ? `<ul class="rows" role="list">${rows.join('')}</ul>` : '<p class="empty">No sessions.</p>'}`
      }
      case 'running': {
        const d = await get(`/runs?${q}&active=1&limit=200`)
        return d ? `<h1 tabindex="-1">Running <span class="count">${num(d.items.length)}</span></h1>${scopeNote(scope)}<p class="sub">Oldest first.</p>${runList(d.items, 'Nothing running.')}` : offline()
      }
      case 'projects': {
        const d = await get('/projects')
        if (!d) return offline()
        const rows = d.items.map(p => `<li>${link({ view: 'project', id: p.id, scope: p.id }, `
          <span class="pin" aria-hidden="true">${p.pinned ? '●' : ''}</span><span class="where">${esc(p.name)}${p.pinned ? '<span class="visually-hidden"> (in focus)</span>' : ''}</span>
          <span class="what">${[num(p.counts.waiting) && `${num(p.counts.waiting)} waiting`, num(p.counts.running) && `${num(p.counts.running)} running`, plural(p.counts.sessions, 'session'), `${num(p.counts.todos_open)} to-dos`].filter(Boolean).join(' · ')}</span>
          <span class="when">${esc(p.branch)}${p.dirty ? ` · ${num(p.dirty)} dirty` : ''}</span>`, 'row')}</li>`)
        return `<h1 tabindex="-1">Projects</h1>${rows.length ? `<ul class="rows" role="list">${rows.join('')}</ul>` : '<p class="empty">No projects configured.</p>'}`
      }
      case 'todos': {
        const d = await get(`/todos?${q}&limit=200`)
        if (!d) return offline()
        const rows = d.items.map(t => `<li>${link({ view: 'todo', id: t.id, scope }, `<span class="where">${esc(t.project_name)}</span><span class="what">${esc(t.text)}</span>
          <span class="when">${t.source === 'claude-task' ? 'Claude Code task' : esc(t.file)}</span>`, 'row')}</li>`)
        return `<h1 tabindex="-1">To-dos <span class="count">${num(d.items.length) + num(d.more)}</span></h1>${scopeNote(scope)}${rows.length ? `<ul class="rows" role="list">${rows.join('')}</ul>` : '<p class="empty">No open to-dos.</p>'}`
      }
      case 'focus': {
        const d = await get('/focus')
        if (!d) return offline()
        const f = d.focus
        const s = d.last_summary
        return `<h1 tabindex="-1">Focus</h1>
          ${f ? `<p class="lead">On <strong>${esc(f.projects.join(' + '))}</strong>${f.left_s != null ? ` · ${ago(f.left_s)} left` : ' · no timer'}.</p>
            <p class="sub">Approvals from other projects go straight to their terminal. Their sessions show only as a count.</p>`
            : '<p class="lead">Focus is off. Start it from the glasses: Deck, then Focus, or long-press a project.</p>'}
          ${s ? `<h2>Last focus block</h2>${summaryList(s)}` : ''}`
      }
      case 'session': {
        const s = await get(`/sessions/${encodeURIComponent(r.id ?? '')}`)
        if (!s) return gone('This session ended a while ago.')
        const events = (s.events ?? []).slice().reverse().map(e => {
          const line = eventLine(e)
          return line ? `<li><time datetime="${esc(e.ts)}">${clock(e.ts)}</time> ${line.replace(esc(e.target ?? '\u0000'), target(`ev:${e.id}`, e.target))}</li>` : ''
        }).join('')
        return `<h1 tabindex="-1">${esc(s.project_name)} · ${esc(AGENT[s.agent] ?? s.agent)}</h1>
          <p class="lead state-${s.state}">${s.state === 'waiting' ? (s.waiting_for === 'input' ? 'Waiting for input' : 'Waiting on you') : esc(s.state[0].toUpperCase() + s.state.slice(1))} · ${s.state === 'waiting' ? ago(s.state_for_s, true) : ago(s.state_for_s)}
          ${s.current ? `<br>${esc(s.current.tool)} ${target(`cur:${s.id}`, s.current.target)}` : ''}</p>
          <dl class="facts">
            ${s.title ? `<dt>Title</dt><dd>${esc(s.title)}</dd>` : ''}
            <dt>Model</dt><dd>${esc(s.model || '—')}</dd>
            <dt>Tools</dt><dd>${num(s.counts.tools)} · ${num(s.counts.approved)} approved · ${num(s.counts.denied)} denied · ${num(s.counts.failed)} failed</dd>
            ${s.tag ? `<dt>Worktree</dt><dd>${esc(s.tag)}</dd>` : ''}
          </dl>
          <h2>Timeline</h2>${events ? `<ol class="timeline">${events}</ol>` : '<p class="empty">Nothing yet.</p>'}`
      }
      case 'run': {
        const x = await get(`/runs/${encodeURIComponent(r.id ?? '')}`)
        if (!x) return gone('This run is no longer kept.')
        const live = x.status === 'running' || x.status === 'waiting'
        return `<h1 tabindex="-1">${esc(x.tool)} ${target(`run:${x.id}`, x.target)}</h1>
          <p class="lead">${live ? `Elapsed <span class="elapsed" data-since="${Date.now() - num(x.elapsed_s) * 1000}">${ago(x.elapsed_s, true)}</span>` : `${esc(x.status)} after ${ago(x.elapsed_s, true)}`}</p>
          <dl class="facts"><dt>Agent</dt><dd>${esc(AGENT[x.agent] ?? x.agent)} · ${esc(x.project_name)}</dd><dt>Kind</dt><dd>${esc(x.kind)}</dd>
          <dt>Started</dt><dd><time datetime="${esc(x.started_at)}">${clock(x.started_at)}</time></dd></dl>
          <p>${link({ view: 'session', id: x.session, scope: x.project }, 'Open its session')}</p>`
      }
      case 'todo': {
        const d = await get(`/todos?scope=all&limit=200`)
        const t = d?.items.find(x => x.id === r.id)
        if (!t) return gone('This to-do is done or no longer in its file.')
        return `<h1 tabindex="-1">${esc(t.text)}</h1><dl class="facts"><dt>Project</dt><dd>${esc(t.project_name)}</dd>
          <dt>From</dt><dd>${t.source === 'claude-task' ? 'Claude Code task list' : target(`todo:${t.id}`, t.file)}</dd></dl>
          <p class="sub">Mark it done from the glasses: open it, then tap again.</p>`
      }
      case 'commits': {
        const p = await get(`/projects/${encodeURIComponent(r.id ?? scope)}`)
        if (!p) return gone('Unknown project.')
        return `<h1 tabindex="-1">${esc(p.name)} · recent commits</h1><p class="sub">${esc(p.branch || 'no branch')}</p>
          ${p.commits?.length ? `<ol class="commits">${p.commits.map(c => `<li>${esc(c)}</li>`).join('')}</ol>` : '<p class="empty">No commits.</p>'}`
      }
      default: {
        const d = await get(`/deck?scope=all`)
        const e = d?.errors?.find(x => (x.id ?? x.error) === r.id)
        return e ? `<h1 tabindex="-1">Failed · ${esc(e.detail)}</h1><p>${esc(AGENT[e.agent] ?? e.agent)} · ${esc(e.project)} · ${target(`err:${r.id}`, e.target || e.tool)}</p>
          <p>${link({ view: 'session', id: e.session, scope: e.project }, 'Open its session')}</p>` : gone('Gone.')
      }
    }
  }

  const offline = () => '<h1 tabindex="-1">Relay offline</h1><p class="lead">Retrying.</p>'
  const gone = text => `<h1 tabindex="-1">Gone</h1><p class="lead">${esc(text)}</p>`
  const scopeNote = scope => (scope === 'all' ? '' : `<p class="sub">${scope === 'focus' ? 'In focus' : `Project ${esc(scope)}`}</p>`)

  function tile(r, label, value, cls = '', note = '') {
    return `<li>${link(r, `<span class="label">${esc(label)}</span><span class="value">${esc(value)}</span>${note ? `<span class="note">${esc(note)}</span>` : ''}`, `tile ${cls}`)}</li>`
  }

  function needList(items, empty) {
    if (!items.length) return `<p class="empty">${esc(empty)}</p>`
    return `<ul class="rows" role="list">${items.map(n => {
      const r = n.kind === 'approval' ? { view: 'session', id: n.session ?? '', scope: n.project } : n.kind === 'session' ? { view: 'session', id: n.id, scope: n.project } : { view: 'card', id: n.id, scope: n.project }
      const what = n.kind === 'approval' ? `${esc(n.tool)} ${target(`need:${n.id}`, n.title)} <span class="tag">on the glasses</span>`
        : n.kind === 'session' ? (n.waiting_for === 'input' ? 'waiting for input' : `${esc(n.tool)} ${target(`need:${n.id}`, n.title)} <span class="tag">in its terminal</span>`)
          : `<span class="tag level-${esc(n.level)}">${esc(n.level)}</span> ${esc(n.title)}`
      const body = `<span class="badge">${BADGE[n.agent] ?? '·'}</span><span class="where">${esc(n.project_name)}</span><span class="what">${what}</span><span class="when">${n.for_s < 3600 ? ago(n.for_s, true) : ago(n.for_s)}</span>`
      return `<li>${n.kind === 'approval' && !n.session ? `<span class="row">${body}</span>` : link(r, body, 'row')}</li>`
    }).join('')}</ul>`
  }

  function runList(items, empty) {
    if (!items.length) return `<p class="empty">${esc(empty)}</p>`
    return `<ul class="rows" role="list">${items.map(x => `<li>${link({ view: 'run', id: x.id, scope: x.project }, `
      <span class="when lead-col">${ago(x.elapsed_s)}</span><span class="badge">${BADGE[x.agent] ?? ''}</span><span class="where">${esc(x.project_name)}</span>
      <span class="what">${x.status === 'waiting' ? '<span class="tag">waiting</span> ' : ''}${target(`run:${x.id}`, x.target || x.tool)}</span>`, 'row')}</li>`).join('')}</ul>`
  }

  function summaryList(s) {
    return `<dl class="facts summary"><dt>Length</dt><dd>${num(s.minutes)} min</dd><dt>Approved</dt><dd>${num(s.approved)}</dd><dt>Denied</dt><dd>${num(s.denied)}</dd>
      <dt>Runs finished</dt><dd>${num(s.runs)}</dd><dt>To-dos done</dt><dd>${num(s.todos)}</dd></dl>`
  }

  // ---- render -----------------------------------------------------------------------------------

  async function statusHtml() {
    const d = await get('/deck?scope=focus')
    state.status = d
    if (!d) return '<span class="pill off">relay offline</span>'
    const f = d.focus
    return `<span class="pill ${d.armed ? 'armed' : 'off'}">${d.armed ? 'Armed' : 'Off'}</span>
      ${f ? `<span class="pill on">Focus ${esc(f.projects.join(' + '))}${f.left_s != null ? ` · ${ago(f.left_s)}` : ''}</span>` : ''}
      <time class="clock">${clock()}</time>`
  }

  function navHtml() {
    const d = state.status
    const counts = d ? { needs: d.needs_you, sessions: d.sessions, running: d.running, todos: d.todos_open, projects: d.projects } : {}
    return SECTIONS.map(v => {
      const current = state.route.view === v ? ' aria-current="page"' : ''
      const n = counts[v]
      return `<li><a href="${routeToHash({ view: v, scope: ['projects', 'focus'].includes(v) ? 'all' : state.route.scope && DEPTH[state.route.view] <= 1 && state.route.view !== 'project' ? state.route.scope : 'all' })}"${current}>${TITLES[v]}${n != null ? ` <span class="count">${num(n)}</span>` : ''}</a></li>`
    }).join('')
  }

  let rendering = null
  let again = null // 'none' or a transition type, when a render was asked for during one
  async function render(transition = null) {
    if (rendering) {
      again = transition ?? again ?? 'none'
      return rendering
    }
    rendering = (async () => {
      const [view, status] = await Promise.all([viewHtml(state.route), statusHtml()])
      const apply = () => {
        $('deck-status').innerHTML = status
        if (!present) $('deck-nav').innerHTML = navHtml()
        if (view !== state.lastHtml) {
          const focusKey = document.activeElement?.closest?.('#deck-view') ? document.activeElement.getAttribute('href') ?? document.activeElement.dataset?.reveal : null
          $('deck-view').innerHTML = view
          state.lastHtml = view
          if (focusKey && !transition) root.querySelector(`#deck-view [href="${CSS.escape(focusKey)}"], #deck-view [data-reveal="${CSS.escape(focusKey)}"]`)?.focus()
        }
        document.title = present ? 'Command Deck · present' : `${TITLES[state.route.view] ?? 'Deck'} · ${opts.title ?? 'Command Deck'}`
      }
      if (transition && document.startViewTransition && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
        let vt
        try {
          vt = document.startViewTransition({ update: apply, types: [transition] })
        } catch {
          vt = document.startViewTransition(apply) // browsers without transition types
        }
        await vt.finished.catch(() => {})
      } else {
        apply()
      }
      if (transition && !present) root.querySelector('#deck-view h1')?.focus({ preventScroll: false })
    })()
    try {
      await rendering
    } finally {
      rendering = null
    }
    if (again) {
      // A route change or refresh arrived mid-render: draw once more, so the newest state wins.
      const next = again === 'none' ? null : again
      again = null
      await render(next)
    }
  }

  function go(r, push = true) {
    const before = state.route
    state.route = { scope: 'all', ...r }
    if (routeToHash(state.route) === routeToHash(before)) return render() // same place: refresh, no transition
    const dir = depthOf(state.route) > depthOf(before) ? 'forward' : depthOf(state.route) < depthOf(before) ? 'backward' : 'sibling'
    if (opts.hash && push && location.hash !== routeToHash(state.route)) history.pushState(null, '', routeToHash(state.route))
    opts.onRoute?.(state.route)
    return render(dir)
  }

  // ---- input --------------------------------------------------------------------------------------

  root.addEventListener('click', e => {
    const reveal = e.target.closest?.('[data-reveal]')
    if (reveal) {
      state.revealed.add(reveal.dataset.reveal)
      state.lastHtml = ''
      return void render()
    }
    const toggle = e.target.closest?.('#deck-reveal')
    if (toggle) {
      state.revealAll = !state.revealAll
      toggle.setAttribute('aria-pressed', String(state.revealAll))
      toggle.textContent = state.revealAll ? 'Hide paths' : 'Show all paths'
      state.lastHtml = ''
      return void render()
    }
    const a = e.target.closest?.('a[href^="#/"]')
    if (a && !opts.hash) {
      e.preventDefault()
      void go(hashToRoute(a.getAttribute('href')))
    }
  })
  // Links, Back and Forward all change the hash; hashchange covers each once.
  if (opts.hash) window.addEventListener('hashchange', () => void go(hashToRoute(location.hash), false))

  // ---- live ---------------------------------------------------------------------------------------

  let refreshTimer = 0
  const refreshSoon = () => {
    clearTimeout(refreshTimer)
    refreshTimer = setTimeout(() => void render(), 250)
  }

  function addTicker(events) {
    for (const e of events) {
      if (e.type === 'view' && present) {
        // The presenter view follows the glasses.
        const r = e.route
        if (r && DEPTH[r.view] !== undefined) void go(r, false)
        continue
      }
      if (e.type === 'focus_end' && e.summary) showSummary({ ...e.summary, reason: e.reason, projects: e.projects })
      const line = eventLine(e)
      // Runs worth a glance: how shell commands and subagents ended, if they took a while or went
      // wrong. Starts are on the Running tile; reads and edits come and go too fast to read.
      const shellish = ['Bash', 'PowerShell', 'exec_command'].includes(e.tool) || e.kind === 'subagent'
      if (!line || (e.type === 'run' && (!shellish || e.status === 'running' || (e.status === 'ok' && num(e.elapsed_s) < 5)))) continue
      state.ticker.unshift(`<li class="ev ev-${esc(e.type)} ${e.status ? `st-${esc(e.status)}` : ''}"><time datetime="${esc(e.ts)}">${clock(e.ts)}</time> <span>${line}</span></li>`)
      if (!present && (e.type === 'error' || (e.type === 'approval' && e.status === 'pending'))) announce(line.replace(/<[^>]+>/g, ''))
    }
    state.ticker = state.ticker.slice(0, present ? 9 : 30)
    $('deck-ticker').innerHTML = state.ticker.join('')
  }

  function announce(text) {
    const box = $('deck-announce')
    if (!box) return
    box.textContent = ''
    setTimeout(() => { box.textContent = text }, 150)
  }

  function showSummary(s) {
    const box = present ? $('deck-summary') : null
    if (box) {
      box.innerHTML = `<h2>Focus block done</h2><p class="lead">${esc((s.projects ?? []).join(' + '))}, ${esc(ENDED[s.reason] ?? s.reason)}</p>${summaryList(s)}`
      box.hidden = false
      setTimeout(() => { box.hidden = true }, 12_000)
    }
  }

  async function live() {
    let cursor = -1
    while (!state.stopped) {
      try {
        if (cursor < 0) {
          const first = await api.events(0, 0)
          const all = first?.events ?? []
          cursor = first?.cursor ?? 0
          addTicker(all.filter(e => e.type !== 'view').slice(-(present ? 9 : 30)))
          const lastView = [...all].reverse().find(e => e.type === 'view')
          if (present && lastView?.route) void go(lastView.route, false)
          continue
        }
        const got = await api.events(cursor, 25)
        if (got) {
          cursor = got.cursor
          if (got.reset) state.ticker = []
          addTicker(got.events)
          refreshSoon()
        }
      } catch {
        await new Promise(res => setTimeout(res, 2000))
      }
    }
  }

  // Elapsed counters and the clock tick here; data refreshes come from the event stream.
  const tick = setInterval(() => {
    for (const el of root.querySelectorAll('.elapsed[data-since]')) el.textContent = ago((Date.now() - Number(el.dataset.since)) / 1000, true)
    const c = root.querySelector('.clock')
    if (c) c.textContent = clock()
  }, 1000)
  const slow = setInterval(refreshSoon, 15_000) // ages and counts drift even without events

  void render()
  void live()

  return {
    go,
    refresh: () => render(),
    /** Blank the page (the phone hides it when its WebView goes to the background). */
    setBlank(on) {
      state.blank = on
      root.classList.toggle('deck--blank', on)
    },
    stop() {
      state.stopped = true
      clearInterval(tick)
      clearInterval(slow)
    },
  }
}
