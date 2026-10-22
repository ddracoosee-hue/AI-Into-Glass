// SDK event envelopes -> the four gestures, with the v1 safeguards on every screen. No bridge and
// no DOM, so tests can replay envelopes against a fake clock.
import { OsEventTypeList } from '@evenrealities/even_hub_sdk'

// A screen must be up this long before a tap counts, so a tap meant for the last screen never
// acts on the one that just replaced it.
export const GRACE_MS = 700
// A tap waits this long before it acts, in case it is the first half of a double-tap. If the
// touchpad reports a double as tap + double, the double wins.
export const DOUBLE_WINDOW_MS = 350

interface Envelope {
  eventType?: OsEventTypeList
}

export interface HubEvent {
  sysEvent?: Envelope
  textEvent?: Envelope
  listEvent?: Envelope
  menuItemClickEvent?: { itemID?: number }
}

/** The screen a gesture lands on: its identity and when it appeared. `immediate` skips the tap
 * delay and lockout (v1's idle screens, where a tap only arms or retries). */
export interface View {
  id: string
  shownAt: number
  immediate?: boolean
}

export interface Sink {
  tap(viewId: string): void
  double(): void
  swipe(down: boolean): void
  menu(itemID: number, viewId: string): void
  foreground(entered: boolean): void
  systemExit(abnormal: boolean): void
}

export interface Clock {
  now(): number
  setTimeout(fn: () => void, ms: number): unknown
  clearTimeout(handle: unknown): void
}

// CLICK_EVENT is 0 and protobuf omits zero values, so a tap arrives with no eventType. Resolve the
// default inside the envelope check; flattening it would read every scroll and lifecycle event as
// a tap, and a tap can approve.
function eventTypeOf(envelope?: Envelope): OsEventTypeList | null {
  if (!envelope) return null
  return envelope.eventType ?? OsEventTypeList.CLICK_EVENT
}

export function createGestures(view: () => View, sink: Sink, clock: Clock) {
  let tapTimer: unknown
  let foreground = true
  let menuOpen = false

  function cancelTap(): void {
    if (tapTimer !== undefined) clock.clearTimeout(tapTimer)
    tapTimer = undefined
  }

  function onTap(): void {
    const v = view()
    if (v.immediate) return sink.tap(v.id)
    if (clock.now() - v.shownAt < GRACE_MS) return
    cancelTap()
    tapTimer = clock.setTimeout(() => {
      tapTimer = undefined
      if (view().id === v.id) sink.tap(v.id)
    }, DOUBLE_WINDOW_MS)
  }

  function handle(event: HubEvent): void {
    if (event.menuItemClickEvent) {
      cancelTap()
      const id = event.menuItemClickEvent.itemID
      if (typeof id === 'number' && id > 0) sink.menu(id, view().id)
      return
    }
    const sys = eventTypeOf(event.sysEvent)
    const other = eventTypeOf(event.textEvent) ?? eventTypeOf(event.listEvent)
    const is = (type: OsEventTypeList) => sys === type || other === type

    // HARDWARE: evenhub-simulator 0.9.5 sends FOREGROUND_ENTER when the contextual menu opens and
    // FOREGROUND_EXIT when it closes. An enter while already in front is therefore read as "menu
    // open", and the exit that follows as "menu closed", not as going to the background (which
    // stops polling and hands prompts to the terminal). Whether the G2 sends these for its menu,
    // and FOREGROUND_ENTER at all after a real background, is unverified: needs a G2.
    if (sys === OsEventTypeList.FOREGROUND_ENTER_EVENT) {
      cancelTap()
      if (foreground) menuOpen = true
      foreground = true
      return sink.foreground(true)
    }
    if (sys === OsEventTypeList.FOREGROUND_EXIT_EVENT) {
      cancelTap()
      if (menuOpen) {
        menuOpen = false
        return
      }
      foreground = false
      return sink.foreground(false)
    }
    if (sys === OsEventTypeList.SYSTEM_EXIT_EVENT || sys === OsEventTypeList.ABNORMAL_EXIT_EVENT) {
      cancelTap()
      return sink.systemExit(sys === OsEventTypeList.ABNORMAL_EXIT_EVENT)
    }
    // HARDWARE: the simulator opens the contextual menu with its own "context menu" input and reports
    // long-press as plain events 9/10. On the G2, long-press should open the menu the page declared.
    if (is(OsEventTypeList.LONG_PRESS_EVENT) || is(OsEventTypeList.LONG_PRESS_RELEASE_EVENT)) return
    if (sys === OsEventTypeList.IMU_DATA_REPORT) return
    if (!event.sysEvent && !event.textEvent && !event.listEvent) return // audio and anything else
    if (!foreground) {
      foreground = true // input means we're in front, whatever the lifecycle events said
      sink.foreground(true)
    }
    // Explicit ids first; CLICK_EVENT is the fallback an untyped envelope lands on, so it goes last.
    if (is(OsEventTypeList.DOUBLE_CLICK_EVENT)) {
      cancelTap()
      return sink.double()
    }
    if (is(OsEventTypeList.SCROLL_BOTTOM_EVENT) || is(OsEventTypeList.SCROLL_TOP_EVENT)) {
      return sink.swipe(is(OsEventTypeList.SCROLL_BOTTOM_EVENT))
    }
    if (is(OsEventTypeList.CLICK_EVENT)) onTap()
  }

  return { handle, cancelTap }
}
