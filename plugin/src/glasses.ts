// The glasses side: three layouts with fixed container ids, diffed upgrades, rebuilds only when the
// page (and so its menu) changes. All bridge writes are serialized.
import {
  CreateStartUpPageContainer,
  MenuContainerProperty,
  MenuItemProperty,
  RebuildPageContainer,
  TextContainerProperty,
  TextContainerUpgrade,
  type EvenAppBridge,
} from '@evenrealities/even_hub_sdk'
import type { Menu, Template } from './screens.ts'

interface Slot {
  id: number
  x: number
  y: number
  w: number
  h: number
  border?: boolean
  capture?: boolean
}

// 576 x 288. pretext puts every text line at 27 px, so one line needs about 31 px with padding.
// HARDWARE: row height (38 px) and the five visible rows were fitted in evenhub-simulator 0.9.5.
// Check that a row is readable at a glance and that 31 px lines don't clip on a G2.
export const LAYOUTS: Record<Template, Record<string, Slot>> = {
  list: {
    // HARDWARE: lists are text rows with a plugin-owned cursor because a firmware list reports no
    // highlight moves and resets to row 0 on rebuild (evenhub-simulator 0.9.5). They rely on this
    // one-line capture container sending SCROLL_TOP/BOTTOM on every swipe, as the simulator does.
    bar: { id: 1, x: 0, y: 0, w: 576, h: 32, capture: true },
    r0: { id: 2, x: 0, y: 34, w: 576, h: 38 },
    r1: { id: 3, x: 0, y: 72, w: 576, h: 38 },
    r2: { id: 4, x: 0, y: 110, w: 576, h: 38 },
    r3: { id: 5, x: 0, y: 148, w: 576, h: 38 },
    r4: { id: 6, x: 0, y: 186, w: 576, h: 38 },
    note: { id: 7, x: 0, y: 224, w: 576, h: 31 },
    hint: { id: 8, x: 0, y: 256, w: 576, h: 32 },
  },
  detail: {
    bar: { id: 1, x: 0, y: 0, w: 576, h: 32 },
    headline: { id: 2, x: 0, y: 34, w: 576, h: 66, border: true },
    body: { id: 3, x: 0, y: 104, w: 576, h: 150, capture: true }, // overflow scrolls natively
    hint: { id: 4, x: 0, y: 256, w: 576, h: 32 },
  },
  prompt: {
    head: { id: 1, x: 0, y: 0, w: 470, h: 32 },
    clock: { id: 2, x: 470, y: 0, w: 106, h: 32 },
    source: { id: 3, x: 0, y: 32, w: 576, h: 31 },
    body: { id: 4, x: 0, y: 64, w: 576, h: 140, border: true, capture: true },
    risk: { id: 5, x: 0, y: 206, w: 576, h: 36 },
    hint: { id: 6, x: 0, y: 248, w: 576, h: 36 },
  },
}

export interface Paint {
  template: Template
  /** Page identity. A new key means a new menu, so a rebuild. */
  key: string
  slots: Record<string, { text: string; color: number }>
  menu: Menu
}

export class Glasses {
  private current: Paint | null = null
  private painted: Record<string, { text: string; color: number }> = {}
  private queue: Promise<unknown> = Promise.resolve()
  readonly stats = { rebuilds: 0, upgrades: 0 }
  fadeIn = false // HARDWARE: the brightness fade-in's feel is a hardware question; off by default

  constructor(private readonly bridge: EvenAppBridge) {}

  show(next: Paint): void {
    const prev = this.current
    this.current = next
    if (!prev || prev.template !== next.template || prev.key !== next.key) {
      // What the screen will show once the queue drains, so later diffs compare against the truth.
      const fade = this.fadeIn && prev !== null
      this.painted = Object.fromEntries(
        Object.keys(LAYOUTS[next.template]).map(name => {
          const slot = next.slots[name] ?? { text: '', color: 2 }
          return [name, { text: slot.text, color: fade ? Math.min(slot.color, 2) : slot.color }]
        }),
      )
      this.queue = this.queue
        .then(() => this.rebuild(prev, next, fade))
        .catch(err => console.error('[deck] rebuild failed', err))
      return
    }
    const changed: string[] = []
    for (const [name, slot] of Object.entries(next.slots)) {
      const was = this.painted[name]
      if (was && was.text === slot.text && was.color === slot.color) continue
      this.painted[name] = { ...slot }
      changed.push(name)
      const id = LAYOUTS[next.template][name].id
      this.stats.upgrades++
      this.queue = this.queue
        .then(() =>
          this.bridge.textContainerUpgrade(
            new TextContainerUpgrade({
              containerID: id,
              containerName: name,
              content: slot.text || ' ',
              textColor: slot.color,
            }),
          ),
        )
        .catch(err => console.error('[deck] upgrade failed', name, err))
    }
    if (changed.length) console.debug('[deck] upgrade', next.key, changed.join(','))
  }

  private async rebuild(prev: Paint | null, next: Paint, fade: boolean): Promise<void> {
    // The breadcrumb moves first, so the new location shows before the page swaps.
    const crumbSlot = next.slots.bar ?? next.slots.head
    if (prev && crumbSlot) {
      const first = Object.keys(LAYOUTS[prev.template])[0]
      await this.bridge.textContainerUpgrade(
        new TextContainerUpgrade({
          containerID: 1,
          containerName: first,
          content: crumbSlot.text || ' ',
          textColor: 3,
        }),
      )
    }
    const layout = LAYOUTS[next.template]
    const textObject = Object.entries(layout).map(([name, l]) => {
      const slot = next.slots[name] ?? { text: '', color: 2 }
      return new TextContainerProperty({
        xPosition: l.x,
        yPosition: l.y,
        width: l.w,
        height: l.h,
        borderWidth: l.border ? 1 : 0,
        borderColor: l.border ? 6 : 0,
        borderRadius: l.border ? 6 : 0,
        paddingLength: 2,
        containerID: l.id,
        containerName: name,
        content: slot.text || ' ',
        textColor: fade ? Math.min(slot.color, 2) : slot.color,
        isEventCapture: l.capture ? 1 : 0,
      })
    })
    const page = {
      containerTotalNum: textObject.length,
      textObject,
      menuObject: new MenuContainerProperty({
        menuItems: next.menu.map(m => new MenuItemProperty({ itemName: m.name, itemID: m.id })),
      }),
    }
    const ok =
      prev === null
        ? (await this.bridge.createStartUpPageContainer(new CreateStartUpPageContainer(page))) === 0
        : await this.bridge.rebuildPageContainer(new RebuildPageContainer(page))
    if (!ok) console.error('[deck] page rejected', next.template, next.key)
    this.stats.rebuilds++
    console.log('[deck] rebuild', next.key)
    if (fade) setTimeout(() => this.current && this.show(this.current), 150) // body up from 2 to its own level
  }
}
