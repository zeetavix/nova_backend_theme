# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Reality fixes from a 200-prompt live battery: Odoo 20 has no
expiry field on stock.lot (the old lot_watch prompt asked for
"expiring lots" and forced the model to guess invalid fields), and
mrp.production tracks planning on date_start/date_deadline (the
delay prompt's "planned date" caused the same guessing). Prompt
seeds are noupdate, so the new bodies deploy by code here.
"""

from odoo import api, SUPERUSER_ID

BODIES = {
    "lot_watch":
        "Search stock.quant quants first (count_records, then "
        "search_records), then stock.lot records: negative quants, "
        "zero-quantity lots, lots with no quants on hand. Per row: "
        "lot, product, quantity, location, the action. Empty lot "
        "tracking is a finding — report the exact search and count. "
        "Use only figures the tools return.",
    "delay_flag":
        "Search mrp.production manufacturing orders with date_start "
        "in the past and state not done first (count_records, then "
        "search_records), then purchase receipts past their "
        "scheduled date (stock.picking scheduled_date). Per row: "
        "order, planned vs today, days late, the blocker. Empty "
        "shops are a finding — report the exact search and count. "
        "Use only figures the tools return.",
}


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Prompt = env["nova.ai.prompt"]
    for code, body in BODIES.items():
        prompt = Prompt.search([("code", "=", code)], limit=1)
        if prompt and prompt.body != body:
            prompt.write({"body": body})
