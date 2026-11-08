# Nova Grid

Modular 2D grid view engine for Odoo Community

Nova Grid — clean-room grid view engine for Odoo Community (LGPL-3).

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
| License | LGPL-3 (GNU Lesser General Public License v3.0) |
| Price | **Free** |

No Enterprise code is copied or required.

## Installation

1. Copy the addon folder into your addons path.
2. Update the apps list (`-u base` or the Apps menu).
3. Install **Nova Grid**.

## Support

support@zeetavix.com — found a bug? Please open an issue: https://github.com/zeetavix/nova_backend_theme/issues — https://zeetavix.com
