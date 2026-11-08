# Part of Nova. See LICENSE file for full copyright and licensing details.
{
    "name": "Nova Grid",
    "summary": "Modular 2D grid view engine for Odoo Community",
    "description": """
Nova Grid — clean-room grid view engine for Odoo Community (LGPL-3).

* Generic <grid> view type usable by ANY model (timesheets, attendance,
  sales, stock, ...), not just one app.
* Date / selection / many2one columns, week & month ranges, section+rows,
  inline editing, keyboard navigation, sticky headers, heatmap intensity,
  column/row/grand totals, weekend + today highlights, copy-previous-period,
  CSV export, empty states, dark-mode + Nova theme tokens.
* Server helpers are model-agnostic (read_group based) so new grids need
  only XML + an optional per-model cell-write override.
* Enterprise arch compatible: <grid>, <field type="row|col|measure"/>,
  <range span/step> parse the same way, plus Nova extras
  (show_heatmap, show_progress, heatmap_scale).

No Enterprise code is copied. All Python + OWL is original.
""",
    "version": "20.0.2.3.1",
    "category": "Productivity",
    "author": "Zeetavix",
    "maintainer": "Zeetavix",
    "support": "support@zeetavix.com",
    "website": "https://zeetavix.com",
    "license": "LGPL-3",
    "price": 0.0,
    "currency": "USD",
    "images": [
        "static/description/img/01_engine_week.png",
        "static/description/img/02_engine_sections.png"
    ],
    "depends": ["web"],
    "data": [],
    "assets": {
        # Styles ship with the always-loaded backend bundle: Odoo 20 picks
        # web.assets_backend_lazy(_dark) from a JS cookie the Nova theme does
        # not set, so lazy CSS would silently stay light under a dark theme.
        "web.assets_backend": [
            "nova_grid/static/src/views/nova_grid.scss",
        ],
        # Dark palette: Odoo includes web.assets_web (hence the light grid
        # styles) in web.assets_web_dark, then appends these overrides.
        "web.assets_web_dark": [
            "nova_grid/static/src/views/nova_grid.dark.scss",
        ],
        "web.assets_backend_lazy": [
            "nova_grid/static/src/**/*",
            ("remove", "nova_grid/static/src/**/*.scss"),
        ],
        "web.assets_unit_tests": [
            "nova_grid/static/tests/**/*.test.js",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
