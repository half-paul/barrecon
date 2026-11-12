# Design: Back of house

The locked system. Every Hallmark run reads this first and all screens share it. Change it on purpose; the file is the rule.

## System
- Genre: modern-minimal
- Shape: app screen, kept top bar
- Theme: brand (Night Shift dark by default, Day Shift light from the header toggle)
- Axes: dark/grotesk-sans/warm, posture Restrained

## Tokens
`frontend/src/tokens.css` is the source of truth. The roles:
- Paper `--color-paper`, wells `--color-paper-2` (alias `surface`), hover and selection `--color-paper-3` (alias `raised`)
- Ink `--color-ink`, secondary text `--color-muted`, least important text `--color-faint`
- Rules `--color-line`, stronger rules `--color-line-strong`, field edges `--color-edge`
- Accent `--color-accent` with `--color-accent-ink` on it; state colours `ok`, `danger`, `info`
- Radii: `rounded-card` 8px, `rounded-control` 6px, `rounded-chip` 4px

## Type
- Hanken Grotesk for everything people read
- JetBrains Mono only for figures and SKUs
- Labels, table heads and group names: sentence case, untracked, never capitals

## Surfaces
- Tables sit on the page with hairlines only. Never put one in a card.
- `.card` is the one container: a tinted well with no border, used for forms and side panels.
- Sibling figures sit in `.stats`, parted by hairlines.
- Never a card inside a card.

## Signature
- The weekly loop on Overview (`.loop`): five steps on one rule, the rule's colour is the step's state.
- The variance ledger (`.lg-cols`, `.lg-row`): one ruled row per item, figures in shared columns.
- The bottle mark, the one drawn object in the app.

## Controls
- 36px tall (`h-9`), 44px on touch through `.ctl`
- One filled primary per screen; secondaries outlined
- Disabled: `opacity-55`, `cursor-not-allowed` and the native attribute together
- Accent marks the primary action, the current selection and state, nothing else

## Copy
- No em or en dashes: a comma, a colon or the word "to"
- Curly apostrophes and quotes
- An empty table cell is `<Nil />`: blank on screen, read as "none"

## Motion
State only: `--dur-micro` on press and colour. Never animate width or height.
