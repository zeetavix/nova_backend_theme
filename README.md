# Nova Backend Theme

Zeetavix Odoo addons for **Odoo 20 Community**: a calm, accessible,
white-label backend theme, plus two auto-installed bridges.

| Addon | What it does | Price |
|---|---|---|
| `nova_backend_theme` | The theme: appearance controls, white-label branding, dark mode, every view restyled, branded login page | **$10.00 USD** |
| `nova_backend_theme_mail` | Auto-installed bridge: renames the stock Discuss menus to "Messages" when `mail` is present | free companion |
| `nova_backend_theme_portal` | Auto-installed bridge: exposes the company accent as `--nova-accent` on the customer portal when `portal` is present | free companion |

The two bridges exist so the core theme never depends on `mail` or `portal`;
they install themselves only when both parents are installed.

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
| Core dependency | `web` only |
| License | OPL-1 (Odoo Proprietary License v1.0) |
| Price | $10.00 USD for `nova_backend_theme` |

No Enterprise code is used or required.

## Installation

1. Copy the addon folders you need into your addons path.
2. Update the apps list (`-u base` or the Apps menu).
3. Install **Nova Backend Theme**. The two bridges install themselves.

## Configuration

- **Settings › Appearance** — accent (preset or custom hex), density, corners,
  column spotlight.
- **Settings › Appearance › Branding** — brand name, contact email, login logo,
  favicon, with live previews of the current assets.
- Dark mode is toggled from the user menu and remembered per user.

## Layout

```
nova_backend_theme/           the theme (paid)
nova_backend_theme_mail/      free bridge for mail
nova_backend_theme_portal/    free bridge for portal
scripts/sync-from-addons.ps1  mirror the modules from a working addons path
```

## License

Odoo Proprietary License v1.0 (OPL-1) — see [LICENSE](LICENSE).
A valid purchased license is required; buy via Odoo Apps or contact
support@zeetavix.com.
