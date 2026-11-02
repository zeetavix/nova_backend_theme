# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Register ``split`` as a first-class view type (Community clean-room)."""

from odoo import fields, models


class IrUiView(models.Model):
    _inherit = "ir.ui.view"

    type = fields.Selection(selection_add=[("split", "Split")])

    def _get_view_info(self):
        # "view_column" is part of the bundled Material Symbols subset (see
        # web/tooling/icons/icons_wishlist.txt); names outside it (for
        # example "vertical_split") render as a blank button.
        return {"split": {"icon": "view_column"}} | super()._get_view_info()
