// Client for the Command Deck reads (contract.md, "Contract v2"). The relay builds every object;
// the plugin only shows them. Same device token as the gate.
import { RelayClient } from './relay.ts'

export type Agent = 'claude-code' | 'codex'
export type State = 'idle' | 'working' | 'waiting' | 'quiet' | 'ended'

export interface Session {
  id: string
  agent: Agent
  title: string
  project: string
  project_name: string
  folder: string
  tag: string
  model: string
  state: State
  waiting_for: 'approval' | 'input' | null
  state_for_s: number
  current: { tool: string; target: string; since: string; run?: string } | null
  counts: { tools: number; approved: number; denied: number; failed: number }
  error: boolean
  muted: boolean
}

export interface Run {
  id: string
  session: string
  agent: Agent
  project: string
  project_name: string
  kind: 'tool' | 'subagent'
  tool: string
  target: string
  parent: string | null
  started_at: string
  ended_at: string | null
  status: 'running' | 'waiting' | 'ok' | 'failed' | 'denied' | 'interrupted' | 'ended'
  elapsed_s: number
}

export interface Project {
  id: string
  name: string
  branch: string
  dirty: number
  pinned: boolean
  counts: { sessions: number; running: number; waiting: number; todos_open: number }
  commits?: string[]
}

export interface Todo {
  id: string
  project: string
  project_name: string
  text: string
  source: 'todo-file' | 'obsidian' | 'claude-task'
  file: string | null
  done: boolean
}

export interface Need {
  kind: 'approval' | 'session' | 'card'
  id: string
  session: string | null
  agent: Agent | null
  project: string
  project_name: string
  tool: string
  title: string
  for_s: number
  waiting_for?: 'approval' | 'input'
  level?: 'info' | 'warn' | 'error'
  risk?: 'low' | 'medium' | 'high'
}

export interface DeckError {
  error?: string
  id?: string
  session: string
  agent: Agent
  project: string
  tool: string
  target: string
  run: string | null
  kind: 'tool' | 'stop'
  detail: string
  at: string
}

export interface Focus {
  projects: string[]
  minutes: 25 | 50 | null
  started_at: string
  ends_at: string | null
  left_s: number | null
}

export interface FocusSummary {
  minutes: number
  approved: number
  denied: number
  runs: number
  todos: number
  reason: string
  projects: string[]
}

export interface DeckCounts {
  scope: string
  armed: boolean
  needs_you: number
  sessions: number
  waiting: number
  running: number
  longest_s: number
  projects: number
  todos_open: number
  focus: Focus | null
  outside_focus: { active: number; waiting: number } | null
  errors: DeckError[]
  cursor: number
}

export interface DeckEvent {
  id: number
  ts: string
  type: string
  status?: string
  project?: string
  tool?: string
  target?: string
  summary?: string
  state?: string
  detail?: string
  [k: string]: unknown
}

export interface Rows<T> {
  items: T[]
  more: number
}

export interface SessionDetail extends Session {
  events: { id: number; type: string; ts: string; [k: string]: unknown }[]
  pending_request: string | null
}

const q = (scope: string) => `scope=${encodeURIComponent(scope)}`

export class DeckClient extends RelayClient {
  /** Any v2 read, for the phone page's shared UI (web/deck-ui.js). */
  get = (path: string) => this.read<unknown>(path)

  private async read<T>(path: string): Promise<T> {
    const res = await this.call('GET', path)
    if (res.status !== 200) throw new Error(`relay answered ${res.status} for ${path}`)
    return res.json() as Promise<T>
  }

  deck = (scope: string) => this.read<DeckCounts>(`/deck?${q(scope)}`)
  needs = (scope: string) => this.read<Rows<Need>>(`/needs?${q(scope)}`)
  sessions = (scope: string) => this.read<Rows<Session>>(`/sessions?${q(scope)}`)
  session = (id: string) => this.read<SessionDetail>(`/sessions/${encodeURIComponent(id)}`)
  runs = (scope: string) => this.read<Rows<Run>>(`/runs?${q(scope)}&active=1`)
  run = (id: string) => this.read<Run>(`/runs/${encodeURIComponent(id)}`)
  projects = () => this.read<Rows<Project>>('/projects')
  project = (id: string) => this.read<Project>(`/projects/${encodeURIComponent(id)}`)
  todos = (scope: string) => this.read<Rows<Todo>>(`/todos?${q(scope)}`)
  focus = () => this.read<{ focus: Focus | null; last_summary: FocusSummary | null }>('/focus')

  /** 'done', or 'changed' when the line moved, changed or isn't in a configured file (409). */
  async todoDone(id: string): Promise<'done' | 'changed'> {
    const res = await this.call('POST', `/todos/${encodeURIComponent(id)}/done`)
    if (res.status === 200) return 'done'
    if (res.status === 409) return 'changed'
    throw new Error(`relay answered ${res.status}`)
  }

  /** Events after `since`, without waiting. `reset` means the relay's log started over. */
  async events(since: number, wait = 0): Promise<{ events: DeckEvent[]; cursor: number; reset: boolean }> {
    const res = await this.call('GET', `/events?since=${since}&wait=${wait}`, undefined, false, (wait + 5) * 1000)
    if (res.status === 204)
      return { events: [], cursor: Number(res.headers.get('X-Deck-Cursor') ?? since), reset: false }
    if (res.status !== 200) throw new Error(`relay answered ${res.status}`)
    return res.json()
  }

  /** Tell the relay which page the glasses show, so the presenter view can follow. Display only. */
  async view(route: { view: string; scope?: string; id?: string }): Promise<void> {
    await this.call('POST', '/view', { route })
  }

  async mute(session: string, muted: boolean): Promise<void> {
    const res = await this.call('POST', `/sessions/${encodeURIComponent(session)}/mute`, { muted })
    if (res.status !== 200) throw new Error(`relay answered ${res.status}`)
  }

  async setFocus(projects: string[], minutes: 25 | 50 | null): Promise<Focus> {
    const res = await this.call('PUT', '/focus', { projects, minutes })
    if (res.status !== 200) throw new Error(`relay answered ${res.status}`)
    return (await res.json()).focus
  }

  async endFocus(): Promise<FocusSummary | null> {
    const res = await this.call('DELETE', '/focus')
    if (res.status !== 200) throw new Error(`relay answered ${res.status}`)
    return (await res.json()).last_summary
  }
}
