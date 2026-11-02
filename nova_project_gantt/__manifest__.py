# Part of Nova. See LICENSE file for full copyright and licensing details.
{
    "name": "Nova Project Gantt",
    "summary": "Better-than-Enterprise project portfolio Gantt for Odoo Community",
    "description": """
Nova Project Gantt — clean-room portfolio timeline for Community (LGPL-3).

* Projects app grid... timeline: sections by project manager, one lane per
  project, bars from start to expiration date.
* Drag bars to reschedule, drag edges to resize, snap-to-day, click to open.
  Month/quarter ranges, prev/today/next, weekend toggle, CSV export and the
  hero-number dashboard (total, starting, lanes, average duration).
* Sticky lane headers, today line, dark-mode + Nova theme polish.
* Honest scope: projects without a start date stay out of the timeline —
  set a start date to plan them. Access rules keep applying.

No Enterprise code is copied. All Python + OWL is original.
""",
    "version": "20.0.1.0.0",
    "category": "Services/Project",
    "author": "Zeetavix",
    "maintainer": "Zeetavix",
    "support": "support@zeetavix.com",
    "website": "https://zeetavix.com",
    "license": "LGPL-3",
    "price": 0.0,
    "currency": "USD",
    "images": [
        "static/description/img/01_portfolio_month.png",
        "static/description/img/02_portfolio_month_dark.png"
    ],
    "depends": ["nova_gantt", "project"],
    "data": [
        "views/project_gantt_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
