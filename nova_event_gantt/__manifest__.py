# Part of Nova. See LICENSE file for full copyright and licensing details.
{
    "name": "Nova Event Gantt",
    "summary": "Event timeline for Odoo Community",
    "description": """
Nova Event Gantt — clean-room event timeline for Community (LGPL-3).

* Events app gets a Gantt as the default view: sections by event type,
  one lane per event, bars from begin to end.
* Drag to reschedule, drag edges to resize, snap-to-day with the original
  time-of-day preserved, click to open. Month/quarter ranges, prev/today/
  next, weekend toggle, CSV export and the hero-number dashboard.
* Sticky lane headers, today line, dark-mode + Nova theme polish.
* Honest scope: events always have begin/end dates, so every event shows.
* Access-safe: event access rules keep applying.

No Enterprise code is copied. All Python + OWL is original.
""",
    "version": "20.0.1.0.1",
    "category": "Marketing/Events",
    "author": "Zeetavix",
    "maintainer": "Zeetavix",
    "support": "support@zeetavix.com",
    "website": "https://zeetavix.com",
    "license": "LGPL-3",
    "price": 0.0,
    "currency": "USD",
    "images": [
        "static/description/img/01_events_month.png",
        "static/description/img/02_events_month_dark.png"
    ],
    "depends": ["nova_gantt", "event"],
    "data": [
        "views/event_gantt_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
