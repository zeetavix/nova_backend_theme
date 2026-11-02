# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Allow ``grid`` inside ``ir.actions.act_window.view`` view_mode."""
from odoo import fields, models


class IrActionsActWindowView(models.Model):
    _inherit = "ir.actions.act_window.view"

    view_mode = fields.Selection(selection_add=[("grid", "Grid")], ondelete={"grid": "cascade"})
