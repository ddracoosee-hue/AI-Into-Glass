// Client for the Approval Gate relay. See contract.md at the repo root: the plugin
// shares no code with the hook or the relay, only that JSON shape.

export interface PendingRequest {
  id: string
  session: { agent: string; name: string; cwd: string }
  tool: string
  summary: string
  detail: string
  risk: 'low' | 'medium' | 'high'
  nonce: string
  expires_in: number
}

export interface Poll {
  armed: boolean
  waiting: number
  session: string
  request: PendingRequest | null
}

export class AuthError extends Error {}

const TIMEOUT_MS = 5000
const ID_RE = /^req_[0-9A-Z]{26}$/

function isPending(value: unknown): value is PendingRequest {
  const r = value as PendingRequest
  return (
    typeof r === 'object' && r !== null &&
    typeof r.id === 'string' && ID_RE.test(r.id) &&
    typeof r.summary === 'string' && typeof r.detail === 'string' && typeof r.tool === 'string' &&
    typeof r.nonce === 'string' && typeof r.expires_in === 'number' &&
    ['low', 'medium', 'high'].includes(r.risk) &&
    typeof r.session?.name === 'string'
  )
}

export class RelayClient {
  constructor(readonly base: string, private readonly token: string) {}

  private async call(method: 'GET' | 'POST', path: string, body?: object): Promise<Response> {
    const abort = new AbortController()
    const timer = setTimeout(() => abort.abort(), TIMEOUT_MS)
    try {
      const res = await fetch(this.base.replace(/\/+$/, '') + path, {
        method,
        headers: { Authorization: `Bearer ${this.token}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
        body: body ? JSON.stringify(body) : undefined,
        cache: 'no-store',
        signal: abort.signal,
      })
      if (res.status === 401) throw new AuthError('device token rejected')
      return res
    } finally {
      clearTimeout(timer)
    }
  }

  async poll(): Promise<Poll> {
    const res = await this.call('GET', '/pending')
    if (res.status !== 200 && res.status !== 204) throw new Error(`relay answered ${res.status}`)
    const request = res.status === 200 ? await res.json() : null
    if (request !== null && !isPending(request)) throw new Error('malformed request from relay')
    return {
      armed: res.headers.get('X-Gate-Armed') === '1',
      waiting: Number(res.headers.get('X-Gate-Waiting') ?? 0),
      session: decodeURIComponent(res.headers.get('X-Gate-Session') ?? ''),
      request,
    }
  }

  /** 'ok' when this decision landed; 'gone' when the request expired, was cancelled or was already decided. */
  async decide(id: string, decision: 'approve' | 'deny', nonce: string): Promise<'ok' | 'gone'> {
    const res = await this.call('POST', `/requests/${id}/decide`, { decision, nonce })
    if (res.status === 200) return 'ok'
    if (res.status === 404 || res.status === 409) return 'gone'
    throw new Error(`relay answered ${res.status}`)
  }

  async setArmed(on: boolean): Promise<boolean> {
    const res = await this.call('POST', on ? '/arm' : '/disarm', {})
    if (res.status !== 200) throw new Error(`relay answered ${res.status}`)
    return (await res.json()).armed === true
  }

  async health(): Promise<{ version: string; armed: boolean; auth: string | null }> {
    const res = await this.call('GET', '/health')
    if (res.status !== 200) throw new Error(`relay answered ${res.status}`)
    return res.json()
  }
}
