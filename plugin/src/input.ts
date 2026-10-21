// Glasses gestures → gate actions. No bridge and no DOM, so test/input.test.ts can
// replay SDK event envelopes against a fake clock.
import { OsEventTypeList } from '@evenrealities/even_hub_sdk'

// A prompt must be on screen this long before a tap can approve it, so a tap
// meant for something else never approves a prompt that just arrived.
export const GRACE_MS = 700
// A tap waits this long before approving, in case it is the first half of a
// double-tap. If the touchpad reports a double as tap + double, the deny wins.
export const DOUBLE_WINDOW_MS = 350

/** What the gestures need to know about the screen right now. */
export type Screen =
  | { kind: 'prompt'; id: string; shownAt: number }
  | { kind: 'busy' } // sending a decision or showing its result
  | { kind: 'armed' }
  | { kind: 'off' }
  | { kind: 'offline' }
  | { kind: 'unpaired' }

interface Envelope {
  eventType?: OsEventTypeList
}

export interface HubEvent {
  sysEvent?: Envelope
  textEvent?: Envelope
}

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

export interface Clock {
  now(): number
  setTimeout(fn: () => void, ms: number): unknown
  clearTimeout(handle: unknown): void
}

// CLICK_EVENT is 0 and protobuf omits zero values, so a tap arrives with no
// eventType. Resolve the default inside the envelope check; flattening it would
// read every scroll and lifecycle event as a tap, and here a tap approves.
function eventTypeOf(envelope?: Envelope): OsEventTypeList | null {
  if (!envelope) return null
  return envelope.eventType ?? OsEventTypeList.CLICK_EVENT
}

export function createInput(screen: () => Screen, act: Actions, clock: Clock) {
  let tapTimer: unknown

  function cancelTap(): void {
    if (tapTimer !== undefined) clock.clearTimeout(tapTimer)
    tapTimer = undefined
  }

  function onTap(): void {
    const s = screen()
    if (s.kind === 'prompt') {
      if (clock.now() - s.shownAt < GRACE_MS) return
      cancelTap()
      tapTimer = clock.setTimeout(() => {
        tapTimer = undefined
        const still = screen()
        if (still.kind === 'prompt' && still.id === s.id) act.approve(s.id)
      }, DOUBLE_WINDOW_MS)
    } else if (s.kind === 'off') {
      act.arm()
    } else if (s.kind === 'offline') {
      act.retry()
    }
  }

  function onDoubleTap(): void {
    cancelTap()
    const s = screen()
    if (s.kind === 'prompt') act.deny()
    else if (s.kind === 'armed') act.disarm()
    else if (s.kind !== 'busy') act.exit()
  }

  function handle(event: HubEvent): void {
    const sys = eventTypeOf(event.sysEvent)
    const text = eventTypeOf(event.textEvent)
    const is = (type: OsEventTypeList) => sys === type || text === type

    if (sys === OsEventTypeList.FOREGROUND_ENTER_EVENT || sys === OsEventTypeList.FOREGROUND_EXIT_EVENT) {
      cancelTap()
      return act.foreground(sys === OsEventTypeList.FOREGROUND_ENTER_EVENT)
    }
    if (sys === OsEventTypeList.SYSTEM_EXIT_EVENT || sys === OsEventTypeList.ABNORMAL_EXIT_EVENT) {
      cancelTap()
      return act.systemExit(sys === OsEventTypeList.ABNORMAL_EXIT_EVENT)
    }
    // Explicit ids first; CLICK_EVENT is the fallback an untyped envelope lands on, so it goes last.
    if (is(OsEventTypeList.DOUBLE_CLICK_EVENT)) return onDoubleTap()
    if (is(OsEventTypeList.SCROLL_BOTTOM_EVENT) || is(OsEventTypeList.SCROLL_TOP_EVENT)) {
      if (screen().kind === 'prompt') act.swipe(is(OsEventTypeList.SCROLL_BOTTOM_EVENT))
      return
    }
    if (is(OsEventTypeList.LONG_PRESS_EVENT) || is(OsEventTypeList.LONG_PRESS_RELEASE_EVENT)) return
    if (is(OsEventTypeList.CLICK_EVENT)) return onTap()
  }

  return { handle, cancelTap }
}
