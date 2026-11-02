# Nova Backend Theme

A calm, accessible, white-label backend theme for **Odoo 20 Community**.

One teal accent, soft geometry and Inter typography across every view. A real
dark theme. Branding per company. Appearance controls per user. Nothing is
forked: every change is an additive template inherit, so removing the module
brings stock Odoo back.

## Highlights

- **One accent, yours** — six presets or any brand hex. Hover, active, soft and
  readable-foreground tones are derived automatically, so a light accent flips
  button text to dark instead of failing contrast.
- **White-label branding** — brand name, contact email, login logo and favicon
  per company. Oversized uploads are downscaled, wide wordmarks are laid out
  without squashing, dark logos get a light plate, favicons are squared
  automatically.
- **A real dark theme** — charcoal surfaces with elevation by lightness,
  desaturated state colours and re-tinted charts. Per user, or follow the OS.
- **Comfort controls** — density, corner radius and an opt-in column spotlight,
  each with a company default and a per-user override.
- **Every view, one language** — list, form, kanban, calendar, activity, pivot,
  graph, board, spreadsheet, chatter, Discuss, settings, toasts, the command
  palette and the login page.
- **Accessible by default** — visible focus rings, 44px touch targets,
  reduced-motion and forced-colors support, contrast-checked accents.

## Requirements

| | |
|---|---|
| Odoo | 20.0 Community |
| Dependencies | `web` only |
| License | OPL-1 (Odoo Proprietary License v1.0) |
| Price | **Free** |

No Enterprise code is used or required.

## Installation

1. Copy the `nova_backend_theme` folder into your addons path.
2. Update the apps list (`-u base` or the Apps menu).
3. Install **Nova Backend Theme**.

## Configuration

- **Settings › Appearance** — accent (preset or custom hex), density, corners,
  column spotlight.
- **Settings › Appearance › Branding** — brand name, contact email, login logo,
  favicon. Live previews of the current assets are shown.
- Dark mode is toggled from the user menu and remembered per user.

## Screenshots

See `static/description/index.html` (rendered on the Odoo Apps listing) and the
`screens/` folder.

## License

Odoo Proprietary License v1.0 (OPL-1) — see [LICENSE](LICENSE).

Copyright (c) Zeetavix.
