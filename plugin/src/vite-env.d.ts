/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "1" turns on the Command Deck phone page. Off by default, so public builds don't show it. */
  readonly VITE_DECK_PHONE?: string
}
