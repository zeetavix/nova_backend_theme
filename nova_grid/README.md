# Nova Grid

Modular 2D grid view engine for Odoo Community (better than Enterprise)

Nova Grid — clean-room grid view engine for Odoo Community (OPL-1).

* Generic <grid> view type usable by ANY model (timesheets, attendance,
  sales, stock, ...), not just one app.
* Date / selection / many2one columns, week & month ranges, section+rows,
  inline editing, keyboard navigation, sticky headers, heatmap intensity,
  column/row/grand totals, weekend + today highlights, copy-previous-period,
  CSV export, empty states, dark-mode + Nova theme tokens.
* Server helpers are model-agnostic (read_group based) so new grids need
  only XML + an optional per-model cell-write override.
* Enterprise arch compatible: <grid>, <field type="row|col|measure"/>,
  <range span/step> parse the same way, plus Nova extras
  (show_heatmap, show_progress, heatmap_scale).

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
3. Install **Nova Grid**.

## Support

support@zeetavix.com — https://zeetavix.com
