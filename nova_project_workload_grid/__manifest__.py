# Part of Nova. See LICENSE file for full copyright and licensing details.
{
    "name": "Nova Project Workload Grid",
    "summary": "Better-than-Enterprise workload grid for Odoo Community",
    "description": """
Nova Project Workload Grid — clean-room deadline load matrix for Community (OPL-1).

* Project Tasks grid (section: project, rows: stage, columns: deadline
  weeks) with allocated hours as the measure.
* Plan inline: distribute hours across the tasks in a deadline bucket,
  create tasks from empty cells, 24h/day guard per bucket, heatmap
  intensity, row/column/grand totals, weekend toggle, copy-previous-week
  skeletons, CSV export.
* Sticky headers, dark-mode + Nova theme polish.
* Honest scope: tasks without a deadline do not appear in the grid —
  set a deadline to plan them.

No Enterprise code is copied. All Python + OWL is original.
""",
    "version": "20.0.1.0.0",
    "category": "Services/Project",
    "author": "Zeetavix",
    "maintainer": "Zeetavix",
    "support": "support@zeetavix.com",
    "website": "https://zeetavix.com",
    "license": "OPL-1",
    "price": 0.0,
    "currency": "USD",
    "images": [
        "static/description/img/01_workload_week.png",
        "static/description/img/02_workload_week_dark.png"
    ],
    "depends": ["nova_grid", "project"],
    "data": [
        "views/project_workload_grid_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
