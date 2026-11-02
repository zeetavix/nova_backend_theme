# Part of Nova. See LICENSE file for full copyright and licensing details.
{
    "name": "Nova Attendance Grid",
    "summary": "Better-than-Enterprise attendance grid for Odoo Community",
    "description": """
Nova Attendance Grid — clean-room attendance matrix for Community (OPL-1).

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
""",
    "version": "20.0.1.0.0",
    "category": "Human Resources/Attendances",
    "author": "Zeetavix",
    "maintainer": "Zeetavix",
    "support": "support@zeetavix.com",
    "website": "https://zeetavix.com",
    "license": "OPL-1",
    "price": 0.0,
    "currency": "USD",
    "images": [
        "static/description/img/01_my_week.png",
        "static/description/img/02_my_week_dark.png"
    ],
    "depends": ["nova_grid", "hr_attendance"],
    "data": [
        "views/attendance_grid_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
