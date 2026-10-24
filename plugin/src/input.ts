// v1's gate screens on top of the shared gesture core (gestures.ts). test/input.test.ts replays SDK
// envelopes against it; the Command Deck screens use the same core directly.
import { createGestures, type Clock, type View } from './gestures.ts'

export { DOUBLE_WINDOW_MS, GRACE_MS } from './gestures.ts'
export type { Clock, HubEvent } from './gestures.ts'

/** What the gestures need to know about the screen right now. */
export type Screen =
  | { kind: 'prompt'; id: string; shownAt: number }
  | { kind: 'busy' } // sending a decision or showing its result
  | { kind: 'armed' }
  | { kind: 'off' }
  | { kind: 'offline' }
  | { kind: 'unpaired' }

export interface Actions {
  approve(id: string): void
  deny(): void
  arm(): void
  disarm(): void
  exit(): void
  retry(): void
  swipe(down: boolean): void
  foreground(entered: boolean): void
  systemExit(abnormal: boolean): void
}

export function createInput(screen: () => Screen, act: Actions, clock: Clock) {
  // Only a prompt's tap approves, so only it waits out the lockout and the double-tap window.
  const view = (): View => {
    const s = screen()
    return s.kind === 'prompt' ? { id: s.id, shownAt: s.shownAt } : { id: s.kind, shownAt: -Infinity, immediate: true }
  }
  return createGestures(
    view,
    {
      tap(id) {
        const s = screen()
        if (s.kind === 'prompt' && s.id === id) act.approve(id)
        else if (s.kind === 'off') act.arm()
        else if (s.kind === 'offline') act.retry()
      },
      double() {
        const s = screen()
        if (s.kind === 'prompt') act.deny()
        else if (s.kind === 'armed') act.disarm()
        else if (s.kind !== 'busy') act.exit()
      },
      swipe(down) {
        if (screen().kind === 'prompt') act.swipe(down)
      },
      menu() {},
      foreground: entered => act.foreground(entered),
      systemExit: abnormal => act.systemExit(abnormal),
    },
    clock,
  )
}
