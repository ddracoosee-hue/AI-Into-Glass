// Gesture tests. Run: npm test (node --test, Node's built-in TypeScript stripping).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createInput, DOUBLE_WINDOW_MS, GRACE_MS, type HubEvent, type Screen } from '../src/input.ts'

// Envelopes as the SDK delivers them. CLICK_EVENT is 0 and protobuf omits zero
// values, so a tap carries no eventType at all.
const TAP = { sysEvent: { eventSource: 1 } }
const TAP_ON_TEXT = { textEvent: { containerID: 3, containerName: 'body' } }
const DOUBLE_TAP = { sysEvent: { eventType: 3, eventSource: 1 } }
const SWIPE_DOWN = { textEvent: { containerID: 3, containerName: 'body', eventType: 2 } }
const SWIPE_UP = { textEvent: { containerID: 3, containerName: 'body', eventType: 1 } }
const FOREGROUND_ENTER = { sysEvent: { eventType: 4 } }
const FOREGROUND_EXIT = { sysEvent: { eventType: 5 } }
const IMU_REPORT = { sysEvent: { eventType: 8, imuData: { x: 0, y: 0, z: 1 } } }
const AUDIO_FRAME = { audioEvent: { audioPcm: new Uint8Array(4) } }
const LONG_PRESS = { sysEvent: { eventType: 9, eventSource: 1 } }

const START = 100_000

function rig(initial: Screen) {
  let t = START
  let screen = initial
  let nextTimer = 1
  const timers = new Map<number, { at: number; fn: () => void }>()
  const calls: string[] = []
  const record =
    (name: string) =>
    (...args: unknown[]) =>
      void calls.push([name, ...args].join(':'))
  const input = createInput(
    () => screen,
    {
      approve: record('approve'),
      deny: record('deny'),
      arm: record('arm'),
      disarm: record('disarm'),
      exit: record('exit'),
      retry: record('retry'),
      swipe: record('swipe'),
      foreground: record('foreground'),
      systemExit: record('systemExit'),
    },
    {
      now: () => t,
      setTimeout: (fn, ms) => {
        timers.set(nextTimer, { at: t + ms, fn })
        return nextTimer++
      },
      clearTimeout: handle => void timers.delete(handle as number),
    },
  )
  return {
    calls,
    send: (event: object) => input.handle(event as HubEvent),
    wait(ms: number) {
      t += ms
      for (const [id, timer] of [...timers].sort((a, b) => a[1].at - b[1].at)) {
        if (timer.at <= t && timers.delete(id)) timer.fn()
      }
    },
    show(next: Screen) {
      screen = next
    },
  }
}

const prompt = (shownAgo = 5_000, id = 'req_A'): Screen => ({ kind: 'prompt', id, shownAt: START - shownAgo })

test('tap then double-tap (the SDK sequence for a double on some touchpads) denies and never approves', () => {
  const r = rig(prompt())
  r.send(TAP)
  r.wait(120)
  r.send(DOUBLE_TAP)
  r.wait(2_000)
  assert.deepEqual(r.calls, ['deny'])
})

test('the same holds when the tap arrives on the text container', () => {
  const r = rig(prompt())
  r.send(TAP_ON_TEXT)
  r.wait(DOUBLE_WINDOW_MS - 1)
  r.send(DOUBLE_TAP)
  r.wait(2_000)
  assert.deepEqual(r.calls, ['deny'])
})

test('a single tap approves once the double-tap window has passed', () => {
  const r = rig(prompt())
  r.send(TAP)
  r.wait(DOUBLE_WINDOW_MS - 1)
  assert.deepEqual(r.calls, [])
  r.wait(1)
  assert.deepEqual(r.calls, ['approve:req_A'])
})

test('a tap inside the grace period is ignored; a double-tap there still denies', () => {
  const r = rig(prompt(GRACE_MS - 1))
  r.send(TAP)
  r.wait(2_000)
  assert.deepEqual(r.calls, [])
  r.send(DOUBLE_TAP)
  assert.deepEqual(r.calls, ['deny'])
})

test('scroll, IMU, audio, long-press and lifecycle envelopes are never taps', () => {
  const r = rig(prompt())
  for (const event of [SWIPE_DOWN, SWIPE_UP, IMU_REPORT, AUDIO_FRAME, LONG_PRESS, FOREGROUND_ENTER]) r.send(event)
  r.wait(2_000)
  assert.deepEqual(r.calls, ['swipe:true', 'swipe:false', 'foreground:true'])
})

test('a prompt replaced before the window closes is not approved', () => {
  const r = rig(prompt())
  r.send(TAP)
  r.show(prompt(0, 'req_B'))
  r.wait(2_000)
  assert.deepEqual(r.calls, [])
})

test('going to the background cancels a pending tap', () => {
  const r = rig(prompt())
  r.send(TAP)
  r.send(FOREGROUND_EXIT)
  r.wait(2_000)
  assert.deepEqual(r.calls, ['foreground:false'])
})

test('idle gestures: tap arms when off, double-tap disarms when armed, exits when off', () => {
  const off = rig({ kind: 'off' })
  off.send(TAP)
  off.send(DOUBLE_TAP)
  assert.deepEqual(off.calls, ['arm', 'exit'])
  const armed = rig({ kind: 'armed' })
  armed.send(TAP)
  armed.send(DOUBLE_TAP)
  armed.wait(2_000)
  assert.deepEqual(armed.calls, ['disarm'])
  const busy = rig({ kind: 'busy' })
  busy.send(TAP)
  busy.send(DOUBLE_TAP)
  busy.send(SWIPE_DOWN)
  assert.deepEqual(busy.calls, [])
})
