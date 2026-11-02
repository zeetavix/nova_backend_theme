# Part of Nova. See LICENSE file for full copyright and licensing details.
{
    "name": "Nova Split View",
    "summary": "Productivity split view: list on the left, form on the right",
    "description": """
Nova Split View, clean-room productivity view for Odoo Community (LGPL-3).

Any window action that already offers both a list and a form view
automatically gains a third view-switcher entry, Split. The list stays
on the left; clicking a row opens the record in a form panel docked on
the right, without leaving the list. Creating, saving, deleting and
paging all work in place, and the panel is resizable.

* Zero per-model work: eligibility is computed when the action loads,
  so every existing list+form screen (Sales, Contacts, Projects, ...)
  offers Split with no XML or code changes.
* First-class view type: ``split`` is registered on ``ir.ui.view`` and
  ``ir.actions.act_window.view`` exactly like ``grid`` in Nova Grid, so
  Studio and developers may also add it to ``view_mode`` explicitly.
* A per-model ``<split>`` view record stays optional; without one the
  server serves a minimal arch and the client reuses the action's own
  list and form views.
* Opt out per action with ``nova_split_disable`` in the action context,
  or globally in Settings.

No Enterprise code is copied. All Python + OWL is original.
""",
    "version": "20.0.1.7.0",
    "category": "Productivity",
    "author": "Zeetavix",
    "maintainer": "Zeetavix",
    "support": "support@zeetavix.com",
    "website": "https://zeetavix.com",
    "license": "LGPL-3",
    "price": 0.0,
    "currency": "USD",
        "images": [
        "static/description/img/01_split_partner.png",
        "static/description/img/02_split_invoice.png",
    ],
    "depends": ["web"],
    "data": [
        "views/nova_split_settings_views.xml",
    ],
    "demo": [
        "demo/nova_split_demo.xml",
    ],
    "assets": {
        "web.assets_tests": [
            "nova_split/static/tests/tours/**/*",
        ],
        "web.assets_backend_lazy": [
            "nova_split/static/src/**/*",
            ("remove", "nova_split/static/src/**/*.dark.scss"),
        ],
        "web.assets_backend_lazy_dark": [
            "nova_split/static/src/**/*.dark.scss",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
