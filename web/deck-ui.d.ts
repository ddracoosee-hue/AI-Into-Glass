// Types for deck-ui.js, so the plugin (TypeScript) can mount the same UI as the desktop page.
export interface DeckApi {
  get(path: string): Promise<unknown>
  events(since: number, wait: number): Promise<{ events: unknown[]; cursor: number; reset?: boolean } | null>
}
export interface Route {
  view: string
  scope?: string
  id?: string
}
export interface DeckOptions {
  hidePaths?: boolean
  present?: boolean
  hash?: boolean
  title?: string
  onRoute?: (r: Route) => void
}
export interface DeckHandle {
  go(r: Route, push?: boolean): Promise<void>
  refresh(): Promise<void>
  setBlank(on: boolean): void
  stop(): void
}
export function mountDeck(root: HTMLElement, api: DeckApi, opts?: DeckOptions): DeckHandle
export function ago(seconds: number, precise?: boolean): string
export function routeToHash(r: Route): string
export function hashToRoute(hash: string): Route
