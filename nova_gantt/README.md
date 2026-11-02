# Nova Gantt

Modular 2D Gantt view engine for Odoo Community (better than Enterprise)

Nova Gantt — clean-room Gantt view engine for Odoo Community (OPL-1).

* Generic <gantt> view type usable by ANY model with a start/end date pair
  (projects, events, leaves, manufacturing orders, ...).
* Lanes group by any many2one (section + rows, like Nova Grid), day columns
  across month/quarter ranges, sticky lane headers, today line, weekend
  dimming, drag-to-move and edge-resize with snap-to-day, per-record dialog.
* Server helpers are model-agnostic: one-RPC record fetch plus a guarded
  date-shift write, so new Gantts need only XML + an optional per-model
  guard override.
* Theme-aware accent, self-contained light/dark palettes that follow the
  backend theme (Nova theme included).

No Enterprise code is copied. All Python + OWL is original.

| | |
|---|---|
| Odoo | 20.0 Community |
| Depends | `web` |
| License | OPL-1 (Odoo Proprietary License v1.0) |
| Price | **Free** |

No Enterprise code is copied or required.

## Installation

1. Copy the addon folder into your addons path.
2. Update the apps list (`-u base` or the Apps menu).
3. Install **Nova Gantt**.

## Support

support@zeetavix.com — https://zeetavix.com
