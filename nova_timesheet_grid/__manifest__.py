# Part of Nova. See LICENSE file for full copyright and licensing details.
{
    "name": "Nova Timesheet Grid",
    "summary": "Better-than-Enterprise timesheet grid for Odoo Community",
    "description": """
Nova Timesheet Grid — clean-room timesheet grid for Community (LGPL-3).

* My Timesheets grid (rows: project/task, columns: days/weeks), All
  Timesheets grid (section: employee, rows: project/task), Project grid.
* Inline day editing, 1:30 / 1h30 quick entry, 24h guard, heatmap intensity,
  row/column/grand totals, weekend toggle, copy-previous-week, CSV export,
  sticky headers, dark-mode + Nova theme polish.
* Server logic is an override of the generic Nova Grid API — reusable for
  attendance, sales, stock, or any future grid.

No Enterprise code is copied. All Python + OWL is original.
""",
    "version": "20.0.2.1.1",
    "category": "Services/Timesheets",
    "author": "Zeetavix",
    "maintainer": "Zeetavix",
    "support": "support@zeetavix.com",
    "website": "https://zeetavix.com",
    "license": "LGPL-3",
    "price": 0.0,
    "currency": "USD",
    "images": [
        "static/description/img/01_my_week.png",
        "static/description/img/02_inline_edit.png",
        "static/description/img/03_my_month.png",
        "static/description/img/04_all_sections.png",
        "static/description/img/05_my_week_dark.png"
    ],
    "depends": ["nova_grid", "hr_timesheet", "project"],
    "data": [
        "views/timesheet_grid_views.xml",
    ],
    "assets": {
        # Theme-agnostic styles stay in the always-loaded bundle (see nova_grid).
        "web.assets_backend": [
            "nova_timesheet_grid/static/src/**/*.scss",
        ],
        "web.assets_backend_lazy": [
            "nova_timesheet_grid/static/src/**/*",
            ("remove", "nova_timesheet_grid/static/src/**/*.scss"),
        ],
    },
    "installable": True,
    "application": True,
    "auto_install": False,
}
