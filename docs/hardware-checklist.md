# Hardware checklist

Everything here was built and tested against `evenhub-simulator` 0.9.5, which is not the glasses.
Each row is a `// HARDWARE:` comment in the code; `scripts/test_hardware.py` fails if one is
added without a row here. Check each on a G2, then fix the code or delete the comment and the row.

| # | File | Assumption | What to check on a G2 |
|---|---|---|---|
| 1 | `plugin/src/text.ts` | pretext's font metrics match the firmware, so pixel-fitted rows never wrap | A long session row and a full status bar stay on one line |
| 2 | `plugin/src/glasses.ts` (layout) | 38 px rows, five visible, 31 px single lines | Rows read at a glance; nothing clips or shows a scrollbar |
| 3 | `plugin/src/glasses.ts` (list capture) | A one-line text container sends SCROLL_TOP/BOTTOM on every swipe, which moves the plugin's cursor | Swipe up and down on any list: the › marker moves one row per swipe |
| 4 | `plugin/src/glasses.ts` (fade-in) | The brightness fade-in (rows at 2, then their own level) feels smooth | Turn on Deck menu > Transitions, then compare with it off |
| 5 | `plugin/src/gestures.ts` (menu events) | The menu opening and closing sends FOREGROUND_ENTER then FOREGROUND_EXIT, which must not stop polling | Open and close the menu, then confirm prompts still reach the glasses |
| 6 | `plugin/src/gestures.ts` (long-press) | Long-press opens the menu the page declared | Long-press on the Deck shows Arm / Disarm, Start focus, End focus, Transitions |
| 7 | `plugin/src/main.ts` (prompt wrap) | The firmware wraps the summary at word boundaries | `rm -r build` stays whole; a 60-character summary reads at a glance |
| 8 | `plugin/src/main.ts` (foreground) | FOREGROUND_ENTER arrives after a real background, so polling resumes | Background the plugin and bring it back: the Deck refreshes within 2 s |

## Not verifiable in the simulator at all

- Whether the phone's WebView survives the screen turning off. The relay's nobody-watching rule
  covers the case where it doesn't: prompts fall back to the terminal at once.
- Whether a 45 s countdown is readable at a glance.
