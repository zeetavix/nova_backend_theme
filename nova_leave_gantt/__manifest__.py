# Part of Nova. See LICENSE file for full copyright and licensing details.
{
    "name": "Nova Leave Gantt",
    "summary": "Better-than-Enterprise time-off timeline for Odoo Community",
    "description": """
Nova Leave Gantt — clean-room who's-out timeline for Community (OPL-1).

* All Time Off gains a Gantt next to the grid: sections by employee,
  bars per leave request from start to end date.
* Drag to reschedule, drag edges to resize, snap-to-day, click to open.
  Month/quarter ranges, prev/today/next, weekend toggle, CSV export and
  the hero-number dashboard.
* Sticky lane headers, today line, dark-mode + Nova theme polish.
* Access-safe: record rules keep applying; validated leave keeps its
  approval semantics (rescheduling writes new dates, never new states).

No Enterprise code is copied. All Python + OWL is original.
""",
    "version": "20.0.1.0.0",
    "category": "Human Resources/Time Off",
    "author": "Zeetavix",
    "maintainer": "Zeetavix",
    "support": "support@zeetavix.com",
    "website": "https://zeetavix.com",
    "license": "OPL-1",
    "price": 0.0,
    "currency": "USD",
    "images": [
        "static/description/img/01_who_is_out.png",
        "static/description/img/02_who_is_out_dark.png"
    ],
    "depends": ["nova_gantt", "hr_holidays"],
    "data": [
        "views/leave_gantt_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
