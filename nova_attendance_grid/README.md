# Nova Attendance Grid

Better-than-Enterprise attendance grid for Odoo Community

Nova Attendance Grid — clean-room attendance matrix for Community (LGPL-3).

* My Attendance grid (rows: employee, columns: days/weeks), All Attendances
  grid for officers and managers.
* Correct recorded time inline by adjusting check-outs, 24h/day guard,
  overtime dots past 8h, heatmap intensity, row/column/grand totals,
  weekend toggle, copy-previous-week skeletons, CSV export.
* Corrections only: the grid never fabricates check-ins. Missed check-ins
  are created from the kiosk or the Attendances list, then corrected here.
* Sticky headers, dark-mode + Nova theme polish.
* Access-safe: regular users can only touch their own attendance; editing
  other people's time requires officer/manager rights.

No Enterprise code is copied. All Python + OWL is original.

| | |
|---|---|
| Odoo | 20.0 Community |
| Depends | `nova_grid`, `hr_attendance` |
| License | LGPL-3 (GNU Lesser General Public License v3.0) |
| Price | **Free** |

No Enterprise code is copied or required.

## Installation

1. Copy the addon folder into your addons path.
2. Update the apps list (`-u base` or the Apps menu).
3. Install **Nova Attendance Grid**.

## Support

support@zeetavix.com — https://zeetavix.com
