# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Register ``grid`` as a first-class view type (Community clean-room)."""
from odoo import fields, models


class IrUiView(models.Model):
    _inherit = "ir.ui.view"

    type = fields.Selection(selection_add=[("grid", "Grid")])

    def _get_view_info(self):
        # 'grid_view' is a Material Icons glyph, same family as the stock
        # 'reorder' / 'bar_chart' / 'calendar_today' entries. (An 'oi-...'-style
        # name here renders as a broken glyph in the view switcher.)
        return {"grid": {"icon": "grid_view"}} | super()._get_view_info()
