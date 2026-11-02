# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Deploy the honesty-slice prompt rewrites and the ask_user upgrade.

Prompt and tool seeds are noupdate, so plain -u never refreshes them:
this migration writes the new bodies by code. Purely additive in
effect — prompts only gain search triplets, titles stay, and the
ask_user schema only widens (header + {label, description} options).
"""

from odoo import api, SUPERUSER_ID

BODIES = {
    "approval_digest":
        "Search account.move vendor bills with move_type=in_invoice "
        "and state=draft first (count_records, then search_records "
        "oldest first). List number, vendor, amount, age; one line "
        "each on what blocks posting. Empty is a finding — report "
        "the exact search and count. Use only figures the tools return.",
    "collection_radar":
        "Search account.move customer invoices with "
        "move_type=out_invoice first (count_records, then "
        "search_records): number, customer, residual with currency, "
        "days overdue, last chatter or activity date. Worst first, "
        "one collection step per row. Paid-up is a finding — report "
        "the exact search and count. Use only figures the tools return.",
    "close_checklist":
        "Search account.move vendor bills with move_type=in_invoice "
        "and state=draft and list them. Search account.move customer "
        "invoices with move_type=out_invoice and state=draft and "
        "list them. Search bank statement lines not reconciled and "
        "list them. Then report the accounting lock date. Present "
        "all four as a month-end close checklist with the action per "
        "item. Empty is a finding — report the exact search and "
        "count. Use only figures the tools return.",
    "dead_revival":
        "Search crm.lead lost or long-silent leads first "
        "(count_records, then search_records): only leads with real "
        "contact details and past engagement. Per row: lead, "
        "silence, why it may revive, the one reopening step. Empty "
        "is a finding — report the exact search and count. Use only "
        "figures the tools return.",
    "lot_watch":
        "Search stock.quant and stock.lot records first "
        "(count_records, then search_records): expiring soon, "
        "expired with stock, negative quants. Per row: lot, product, "
        "expiry, quantity, the action. Empty lot tracking is a "
        "finding — report the exact search and count. Use only "
        "figures the tools return.",
    "transfer_suggest":
        "Search stock.quant quants and open demand first "
        "(count_records, then search_records per location): where "
        "stock sits vs where demand is, per product and location "
        "pair with quantities. Queue nothing — propose source, "
        "destination, product and qty per row. Do NOT say 'queued'. "
        "Empty is a finding — report the exact search and count. "
        "Use only figures the tools return.",
    "cycle_count":
        "Search stock.quant quants for this location first "
        "(count_records, then search_records, recently moved "
        "first): products with quants, zero-count suspects. Present "
        "the count order with expected quantities; queue nothing "
        "without approval. Do NOT say 'queued'. Empty is a finding "
        "— report the exact search and count. Use only figures the "
        "tools return.",
    "delay_flag":
        "Search mrp.production manufacturing orders past their "
        "planned date first (count_records, then search_records), "
        "then purchase receipts past expected date. Per row: order, "
        "planned vs today, days late, the blocker. Empty shops are "
        "a finding — report the exact search and count. Use only "
        "figures the tools return.",
    "shop_digest":
        "Search mrp.production open manufacturing orders first "
        "(count_records, then search_records) and report the shop "
        "floor: counts by state, late orders, blocked moves, "
        "today's due. End with the single most urgent action. Empty "
        "is a finding — report the exact search and count. Use only "
        "figures the tools return.",
}


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Prompt = env["nova.ai.prompt"]
    for code, body in BODIES.items():
        prompt = Prompt.search([("code", "=", code)], limit=1)
        if prompt and prompt.body != body:
            prompt.write({"body": body})
    tool = env["nova.ai.tool"].search(
        [("code", "=", "ask_user")], limit=1)
    if tool:
        tool.write({
            "description":
                "Ask the user a clarifying question with clickable "
                "options instead of guessing when the request is "
                "ambiguous. One focused question, 1 to 6 concrete "
                "options; the answer arrives as the next message and "
                "the turn resumes. Options accept plain strings or "
                "{label, description} objects; header is an optional "
                "short label.",
            "schema": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The clarifying question"},
                    "header": {
                        "type": "string",
                        "description":
                            "Optional short label (max 30 chars)"},
                    "options": {
                        "type": "array",
                        "description":
                            "1 to 6 clickable options, strings or "
                            "{label, description}"},
                },
                "required": ["question", "options"],
            },
            "example": {
                "question": "Which customer did you mean?",
                "options": [
                    "Acme Corp",
                    {"label": "Acme Ltd",
                     "description": "The Lyon subsidiary"}],
            },
        })
