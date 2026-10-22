// The presenter view at /present: the Deck at 1920 x 1080 with large type and a live ticker. It
// follows the page the glasses show, so it can sit beside the simulator in the demo video.
import { mountDeck } from './deck-ui.js'

document.documentElement.dataset.present = '1'
const { sameOrigin } = await import('./desktop.js')
mountDeck(document.getElementById('deck'), sameOrigin, { present: true })
