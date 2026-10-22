// The desktop page on 127.0.0.1:8799: the same UI as the phone page, reading /api/* on this origin.
import { mountDeck } from './deck-ui.js'

/** @type {import('./deck-ui.js').DeckApi} */
export const sameOrigin = {
  async get(path) {
    const res = await fetch(`/api${path}`, { cache: 'no-store' })
    if (!res.ok) throw new Error(`relay answered ${res.status}`)
    return res.json()
  },
  async events(since, wait) {
    const res = await fetch(`/api/events?since=${since}&wait=${wait}`, { cache: 'no-store' })
    if (res.status === 204) return { events: [], cursor: Number(res.headers.get('X-Deck-Cursor') ?? since) }
    if (!res.ok) throw new Error(`relay answered ${res.status}`)
    return res.json()
  },
}

if (document.getElementById('deck') && !document.documentElement.dataset.present) {
  mountDeck(document.getElementById('deck'), sameOrigin, { hash: true, hidePaths: true })
}
