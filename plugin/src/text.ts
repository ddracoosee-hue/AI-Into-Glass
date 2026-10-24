// Pixel-accurate text for the G2's proportional font, via @evenrealities/pretext.
// Never truncate by character count: "iiii" and "WWWW" differ by 3x on the glasses.
import { getAdvW, getTextWidth } from '@evenrealities/pretext'

// HARDWARE: pretext mirrors the firmware's LVGL font metrics and matched evenhub-simulator 0.9.5 in
// every probe, but its README warns that edge cases may differ on the glasses. Check a long session
// row and the status bar on a G2.
export const SCREEN_W = 576
export const LINE_H = 27 // pretext: every line is 27 px tall
const ELLIPSIS = '…'
const SPACE = getTextWidth(' ')

export const width = (text: string): number => getTextWidth(text)

/** True when the firmware font has the glyph; missing ones render blank (▸ and ✓ do). */
export const hasGlyph = (ch: string): boolean => getAdvW(ch.codePointAt(0) ?? 0) > 0

/** `text` cut to fit `px`, ending in … when cut. Codepoint-safe. */
export function fit(text: string, px: number): string {
  if (width(text) <= px) return text
  const chars = [...text]
  let lo = 0
  let hi = chars.length
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2)
    if (width(chars.slice(0, mid).join('').trimEnd() + ELLIPSIS) <= px) lo = mid
    else hi = mid - 1
  }
  return lo ? chars.slice(0, lo).join('').trimEnd() + ELLIPSIS : ''
}

/** Spaces that move the pen from `from` px to `to` px: at least that far for a column start, at
 * most that far (`before`) for right-aligned text, which must not cross the edge and wrap. */
function pad(from: number, to: number, before = false): string {
  return ' '.repeat(Math.max(1, (before ? Math.floor : Math.ceil)((to - from) / SPACE)))
}

/**
 * One line of columns, no wider than `limit`. Each column starts at its `x`, and text that would
 * run into the next column (or past the limit) is cut with …. `right` is right-aligned to its edge.
 */
export function columns(
  cols: { text: string; x: number }[],
  limit: number,
  right?: { text: string; edge: number },
): string {
  let line = ''
  let pen = 0
  const tail = right ? fit(right.text, right.edge / 2) : ''
  const tailX = right ? right.edge - width(tail) : limit + SPACE
  cols.forEach((col, i) => {
    const next = Math.min(cols[i + 1]?.x ?? Infinity, tailX) - SPACE
    const lead = line || col.x > 0 ? line + pad(pen, col.x) : ''
    // Measured from where the padding really lands, and re-checked joined: kerning across the join
    // can add a pixel or two.
    let budget = next - width(lead)
    let text = fit(col.text, budget)
    while (text && width(lead + text) > next) text = fit(col.text, (budget -= 2))
    if (!text) return
    line = lead + text
    pen = width(line)
  })
  if (tail) line += gap(line, tail, right!.edge) + tail
  return line
}

/** Left text and right text on one line of `px`. The right side (the status) wins, but the left
 * (the breadcrumb) keeps at least 160 px. */
export function bar(left: string, right: string, px: number): string {
  const r = fit(right, px - Math.min(width(left), 160) - SPACE * 3)
  const l = fit(left, px - width(r) - SPACE * 3)
  return l + gap(l, r, px) + r
}

/** The spaces between `l` and a right-aligned `r` so the line ends at, never past, `px`. Measured
 * joined, since kerning across the join can add a pixel; a space comes out, never a letter. */
function gap(l: string, r: string, px: number): string {
  let spaces = Math.max(1, Math.floor((px - width(l) - width(r)) / SPACE))
  while (spaces > 1 && width(l + ' '.repeat(spaces) + r) > px) spaces--
  return ' '.repeat(spaces)
}

/** A duration for rows (whole minutes: 0m, 12m, 3h, 2d) or, `precise`, for live counters (0:42, 1:02:05). */
export function ago(seconds: number, precise = false): string {
  const s = Math.max(0, Math.floor(seconds))
  const two = (n: number) => String(n).padStart(2, '0')
  if (precise)
    return s < 3600
      ? `${Math.floor(s / 60)}:${two(s % 60)}`
      : `${Math.floor(s / 3600)}:${two(Math.floor(s / 60) % 60)}:${two(s % 60)}`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  return s < 86400 ? `${Math.floor(s / 3600)}h` : `${Math.floor(s / 86400)}d`
}
