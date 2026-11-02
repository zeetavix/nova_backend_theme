# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Reusable prompts.

The drawer suggestions and the chatter menu both read from here, so
an admin can edit the house voice without touching code. Entries are
keyed by `code`; the clients fall back to built-in text when a code
has no row, so a fresh database still works before anyone edits.
"""

from odoo import fields, models


class NovaAiPrompt(models.Model):
    _name = "nova.ai.prompt"
    _description = "Nova AI Prompt"
    _order = "sequence, name"

    name = fields.Char(required=True, help="Label shown on the chip/menu.")
    code = fields.Char(required=True, index=True,
                       help="Stable key the clients ask for.")
    body = fields.Text(required=True,
                       help="Prompt text sent to the agent.")
    place = fields.Selection(
        [("drawer", "Drawer suggestions"), ("chatter", "Chatter menu"),
         ("both", "Both")],
        required=True, default="both")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint(
        "UNIQUE(code)", "One prompt definition per code.")
