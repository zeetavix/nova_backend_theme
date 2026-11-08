# Part of Nova. See LICENSE file for full copyright and licensing details.
{
    "name": "Nova Leave Grid",
    "summary": "Leave grid for Odoo Community",
    "description": """
Nova Leave Grid — clean-room time-off matrix for Community (LGPL-3).

* My Time Off grid (rows: employee, section: leave type, columns: months),
  All Time Off grid for approvers and managers.
* Read-only by design: leave always flows through the approval workflow.
  The grid is the consumption overview — who is out, on what, and for how
  long — with heatmap intensity, sticky headers, month/week ranges and
  row/column/grand totals.
* Sticky headers, dark-mode + Nova theme polish.
* Access-safe: record rules keep applying; the grid adds no write path.

No Enterprise code is copied. All Python + OWL is original.
""",
    "version": "20.0.1.0.1",
    "category": "Human Resources/Time Off",
    "author": "Zeetavix",
    "maintainer": "Zeetavix",
    "support": "support@zeetavix.com",
    "website": "https://zeetavix.com",
    "license": "LGPL-3",
    "price": 0.0,
    "currency": "USD",
    "images": [
        "static/description/img/01_my_month.png",
        "static/description/img/02_my_month_dark.png"
    ],
    "depends": ["nova_grid", "hr_holidays"],
    "data": [
        "views/leave_grid_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
