# Part of Nova. See LICENSE file for full copyright and licensing details.
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    nova_split_auto_inject = fields.Boolean(
        string="Offer Split view on list+form screens",
        default=True,
        config_parameter="nova_split.auto_inject",
        help="Automatically add Split to the view switcher of every action that shows both a list and a form.",
    )
    nova_split_auto_open = fields.Boolean(
        string="Open the first record automatically",
        default=True,
        config_parameter="nova_split.auto_open",
        help="When Split opens, immediately show the first row in the side panel instead of an empty state.",
    )
    nova_split_default_width = fields.Integer(
        string="Side panel width (pixels)",
        default=560,
        config_parameter="nova_split.default_width",
        help="Initial width of the form panel in pixels (320 to 1600). Users can resize or expand it; their width choice is remembered.",
    )
