// Phone-side page: a read-only mirror of the glasses, a disarm button and pairing.
// Approving is deliberately glasses-only, so an unlocked phone alone cannot approve.

export interface Frame {
  head: string
  clock: string
  body: string
  risk: string
  hint: string
}

export interface PhoneHandlers {
  /** Store the pairing and test it; resolves to a status line for the page. */
  pair(relay: string, token: string): Promise<string>
  unpair(): Promise<void>
  disarm(): Promise<void>
}

export function mountPhone(root: HTMLElement, handlers: PhoneHandlers, relay: string): (frame: Frame) => void {
  root.innerHTML = `
    <main>
      <h1>Approval Gate</h1>
      <section class="mirror" aria-live="polite">
        <div class="row"><span id="mHead"></span><span id="mClock"></span></div>
        <pre id="mBody"></pre>
        <div class="risk" id="mRisk"></div>
        <div class="hint" id="mHint"></div>
      </section>
      <p class="note">Mirror of the glasses. Approve and deny on the glasses: tap approves, double-tap denies.</p>
      <div class="actions"><button class="danger" id="disarm">Disarm gate</button></div>
      <section id="deckPhone" aria-label="Command Deck" hidden></section>
      <details id="pairing">
        <summary>Pairing</summary>
        <label for="relay">Relay URL</label>
        <input id="relay" inputmode="url" autocomplete="off" spellcheck="false" placeholder="https://your-pc.your-tailnet.ts.net" />
        <label for="token">Device token (from <code>gatectl.py pair</code>)</label>
        <input id="token" type="password" autocomplete="off" spellcheck="false" placeholder="agt_…" />
        <div class="actions">
          <button id="save">Save and test</button>
          <button id="unpair">Unpair</button>
        </div>
        <div id="pairStatus"></div>
      </details>
    </main>`

  const $ = <T extends HTMLElement>(id: string) => root.querySelector<T>(`#${id}`)!
  const relayInput = $<HTMLInputElement>('relay')
  const tokenInput = $<HTMLInputElement>('token')
  const status = $('pairStatus')
  relayInput.value = relay
  if (!relay) $<HTMLDetailsElement>('pairing').open = true

  $('save').addEventListener('click', async () => {
    status.textContent = 'Testing…'
    status.textContent = await handlers.pair(relayInput.value.trim(), tokenInput.value.trim())
    tokenInput.value = ''
  })
  $('unpair').addEventListener('click', async () => {
    await handlers.unpair()
    status.textContent = 'Unpaired. The token is forgotten on this phone; revoke it on the PC too.'
  })
  $('disarm').addEventListener('click', () => void handlers.disarm())

  return frame => {
    $('mHead').textContent = frame.head
    $('mClock').textContent = frame.clock
    $('mBody').textContent = frame.body
    $('mRisk').textContent = frame.risk
    $('mHint').textContent = frame.hint
  }
}
