# Design: Back of house

The locked system. Every Hallmark run reads this first and all screens share it. Change it on purpose; the file is the rule.

## System
- Genre: modern-minimal
- Shape: app screen, navy side menu on the left (phones: top bar with a menu button)
- Theme: brand (Night Shift dark by default, Day Shift light from the header toggle)
- Axes: dark/grotesk-sans/warm, posture Restrained

## Tokens
`frontend/src/tokens.css` is the source of truth. The roles:
- Paper `--color-paper`, wells `--color-paper-2` (alias `surface`), hover and selection `--color-paper-3` (alias `raised`)
- Ink `--color-ink`, secondary text `--color-muted`, least important text `--color-faint`
- Rules `--color-line`, stronger rules `--color-line-strong`, field edges `--color-edge`
- Accent `--color-accent` with `--color-accent-ink` on it; state colours `ok`, `danger`, `info`
- Radii: `rounded-panel` 14px, `rounded-card` 8px, `rounded-control` 6px, `rounded-chip` 4px
- Side menu `--color-nav*`: a deep navy rail in both themes; `.sidenav` re-scopes ink, muted, line and raised so shared pieces fit on it
- `info` / `info-soft` for over and for expected figures

## Type
- Hanken Grotesk for everything people read
- JetBrains Mono only for figures and SKUs
- Labels, table heads and group names: sentence case, untracked, never capitals

## Surfaces
- `.panel` is the report section: a raised sheet (hairline, soft shadow, 14px radius) opened by `.panel-head` (icon badge, title, one line of help). Tables and the ledger may sit in one; their head row is a shaded `.band`.
- `.card` stays the plain container for forms and side panels: a tinted well with no border.
- Report headline figures sit in `.kpis` tiles (`.kpi`: icon in a tinted circle, label, big figure, note). Other pages may keep `.stats`, parted by hairlines.
- A state is a `.pill` (`pill-short` amber, `pill-off` red, `pill-ok` green, `pill-over` blue, `pill-none`) with an icon, never colour alone.
- One level of nesting at most: a bordered well inside a panel, nothing inside that.

## Signature
- The weekly loop on Overview (`.loop`): five steps on one rule, the rule's colour is the step's state.
- The variance ledger (`.lg-cols`, `.lg-row`): one ruled row per item, figures in shared columns.
- The bottle mark, the one drawn object in the app.
- Line icons from `lib/icons.tsx` (24px grid, 1.75 stroke) mark menu entries, panel heads and tiles; never decoration alone.

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
