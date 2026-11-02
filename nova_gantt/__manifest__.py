# Part of Nova. See LICENSE file for full copyright and licensing details.
{
    "name": "Nova Gantt",
    "summary": "Modular 2D Gantt view engine for Odoo Community (better than Enterprise)",
    "description": """
Nova Gantt — clean-room Gantt view engine for Odoo Community (OPL-1).

* Generic <gantt> view type usable by ANY model with a start/end date pair
  (projects, events, leaves, manufacturing orders, ...).
* Lanes group by any many2one (section + rows, like Nova Grid), day columns
  across month/quarter ranges, sticky lane headers, today line, weekend
  dimming, drag-to-move and edge-resize with snap-to-day, per-record dialog.
* Server helpers are model-agnostic: one-RPC record fetch plus a guarded
  date-shift write, so new Gantts need only XML + an optional per-model
  guard override.
* Theme-aware accent, self-contained light/dark palettes that follow the
  backend theme (Nova theme included).

No Enterprise code is copied. All Python + OWL is original.
""",
    "version": "20.0.1.0.0",
    "category": "Productivity",
    "author": "Zeetavix",
    "maintainer": "Zeetavix",
    "support": "support@zeetavix.com",
    "website": "https://zeetavix.com",
    "license": "OPL-1",
    "price": 0.0,
    "currency": "USD",
    "images": [
        "static/description/img/01_engine_month.png",
        "static/description/img/02_engine_sections.png"
    ],
    "depends": ["web"],
    "data": [],
    "assets": {
        # Styles ship with the always-loaded backend bundle: Odoo picks
        # web.assets_backend_lazy(_dark) from a JS cookie the Nova theme does
        # not set, so lazy CSS would silently stay light under a dark theme.
        "web.assets_backend": [
            "nova_gantt/static/src/views/nova_gantt.scss",
        ],
        # Dark palette: Odoo includes web.assets_web (hence the light Gantt
        # styles) in web.assets_web_dark, then appends these overrides.
        "web.assets_web_dark": [
            "nova_gantt/static/src/views/nova_gantt.dark.scss",
        ],
        "web.assets_backend_lazy": [
            "nova_gantt/static/src/**/*",
            ("remove", "nova_gantt/static/src/**/*.scss"),
        ],
        "web.assets_unit_tests": [
            "nova_gantt/static/tests/**/*.test.js",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
