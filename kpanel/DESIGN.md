# kPanel design system

**Status:** adopted, 2026-10-02. Implemented in [`ui.py`](ui.py).

Goal: before the public release, the panel should look like a well-made tool,
not a generated one. Basic, so it stays small, server-rendered and dependency
free; polished, so every value on screen follows a rule.

## What made it look generated

Measured against the previous stylesheet:

- **Tailwind's default palette, verbatim**: `#2563eb` (blue-600) as the accent,
  `#6b7280` / `#e5e7eb` / `#18181b` (gray-500, gray-200, zinc-900) as neutrals.
  These are the statistical centre of generated UIs; "colour discipline is the
  single biggest tell" ([superdesign](https://superdesign.dev/blog/ai-dashboard-ui-design)).
- **Accent everywhere**: nav, buttons, badges, sparkline points and links were
  all the same blue, so blue meant nothing in particular.
- **Emoji as UI symbols**: ✅ 🔒 ⛏ ↗ ● ✕. They render differently per
  platform and read as chat output.
- **Equal weight**: ten identical cards. Carbon's first dashboard rule is the
  opposite: "the most important data should have the highest contrast and
  occupy the largest area"
  ([Carbon, dashboards](https://carbondesignsystem.com/data-visualization/dashboards/)).
- **Loud warnings**: a full amber fill for "changed on the server", louder
  than an actual error.

## Principles

1. **Monochrome first, colour means state.** Ink on warm neutrals. Green,
   amber and red appear only for online / needs attention / failed, and always
   with an icon or a word (WCAG 1.4.1: colour is never the only signal). Because
   nothing else is coloured, a warning is visible from across the room.
2. **One hero figure per view.** On the dashboard: players online. Everything
   else is a stat in a group.
3. **Group by question, not by data type.** *Is it healthy?* (TPS, latency,
   CPU, memory) and *what is the world?* (difficulty, whitelist, size, backup).
   Identity (address, version, MOTD) belongs to the server card itself.
4. **Thresholds come from the system, not taste.** TPS: Paper's target is 20;
   below 18 is noticeable (warn), below 15 is lag (bad). Backups run every
   12 h; older than 26 h means two were missed (warn).
5. **Hairlines, not shadows.** One container style: 1px border, 8px radius,
   no elevation. Stat groups are one panel split by hairlines, not a card per
   number.
6. **Nothing from another origin.** System fonts, inlined icons and CSS. The
   panel already refuses external requests (the Paper logo is embedded), and a
   self-hosted admin page should work offline on a LAN.

## Tokens

All colours are [Radix Colors](https://www.radix-ui.com/colors) (MIT) steps,
used by their documented role: 1–2 app backgrounds, 3–5 component states,
6–8 borders, 9–10 solids, 11–12 text; steps 11/12 guarantee APCA Lc 60/90 on
step 2. Light and dark are separate picks from the same scales, not an
inversion.

| Token | Role | Light | Dark |
|---|---|---|---|
| `--page` | app background | sand 2 | sand 1 |
| `--surface` | panels, inputs | sand 1 | sand 2 |
| `--sunken` | chips, disabled, hover on panels | sand 3 | sand 3 |
| `--line` / `--line-strong` / `--line-hover` | borders: static / interactive / hovered | sand 6 / 7 / 8 | sand 6 / 7 / 8 |
| `--fg` / `--muted` / `--faint` | text: primary / secondary / decorative only | sand 12 / 11 / 9 | sand 12 / 11 / 9 |
| `--ink` | primary button, active tab, the live chart point | sand 12 | sand 12 |
| `--focus`, `--edited` | focus ring, edited-but-unsaved row | blue 8, blue 3 | blue 8, blue 3 |
| `--ok` / `--warn` / `--bad` (+ `-bg`, `-line`, `-dot`) | status text / tint / border / dot | grass, amber, red 11 / 3 / 6–7 / 9 | same steps, dark scales |

Neutral: **sand**, a warm grey. It reads as paper rather than the cold
blue-grey of generic UI kits, and suits a game about blocks and dirt without
dressing up as Minecraft.

Typography: system UI font, `tabular-nums` everywhere so numbers do not jitter
on refresh, and a monospace stack for anything a person might type or copy
(player names, keys, addresses, versions).

| Token | px | Use |
|---|---|---|
| `--t-xs` | 12 | labels, captions, badges |
| `--t-sm` | 13 | secondary text, code, section titles |
| `--t-md` | 14 | body |
| `--t-lg` | 16 | brand, hero unit |
| `--t-xl` | 20 | stat values |
| `--t-hero` | 48 | the one hero figure (40 on phones) |

Spacing on a 4px grid (`--s1`..`--s6` = 4, 8, 12, 16, 24, 32). Radius 4
(badges), 6 (controls), 8 (panels). Controls are 32px high, row actions 28px.
Motion: 120ms colour transitions only, off under `prefers-reduced-motion`.

## Components

| Component | Markup | Notes |
|---|---|---|
| Panel | `.card` | the only container |
| Section heading | `h2` | 13px semibold; counts as `.pill` |
| Stat group | `section.group > h2 + .tiles > .tile` | `tile(..., tone="warn"\|"bad")` adds the icon and colours the sub line, which must say what is wrong |
| Primary button | `button` | ink; one per form |
| Secondary button | `button.sec` | row actions |
| Destructive | `button.sec.danger` | red text, red tint on hover; still behind a confirm |
| Badge | `.badge`, `.badge.drift` | neutral or warning |
| Status | `.status.up` / `.status.down` | dot + word |
| Callout | `.card.note`, `.card.warn`, `.card.err` | tinted surface, coloured icon and border, ink text |
| Edited row | `tr.changed` | blue tint + 2px left rule |
| Icon | `ui.icon(name, label="")` | Tabler, 16px, `currentColor`; unlabelled icons are `aria-hidden` |
| Sparkline | `history.sparkline` | 2px de-emphasised line, only the live value marked; every point has a hover target |
| Terminal | `.term` | always dark: it is a log, not a page |
| Footer | `about()` | version, licence, author; "vX available" in ink + icon, not a status colour: an update is news, not a fault |

## Libraries

| What | Licence | How it is used |
|---|---|---|
| [Radix Colors](https://github.com/radix-ui/colors) | MIT | values copied into `ui.py` tokens |
| [Tabler Icons](https://github.com/tabler/tabler-icons) | MIT | six outline paths copied into `ui.py` |

Both notices ship in `kpanel/ui.LICENSE.txt`. No runtime dependency was added;
the panel is still the Python standard library plus PyYAML.

Considered and rejected: **Inter / Geist** (OFL, not MIT, and Inter is now
itself a marker of generated UIs); **Lucide** (ISC, not MIT); **a component
framework** (shadcn, Tailwind): needs a JS build the panel deliberately does
not have, and its defaults are exactly the look to avoid.

## Adding to it

Use a token, never a hex value. If a new colour seems necessary, it is
probably a state: reuse `--ok` / `--warn` / `--bad` and add a word. If a new
icon is needed, copy its path from Tabler's `icons/outline/` into `ICONS`.
