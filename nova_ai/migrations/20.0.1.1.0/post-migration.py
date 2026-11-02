# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Grant the typed write tools + inspect_method to full-toolbox agents.

The agent seeds are noupdate (an upgrade must never overwrite an
admin's tool choices), so tools added after an agent was created
never reach it: the model literally cannot see them. This migration
grants the newer codes to agents that already hold execute_method
(the full-toolbox marker) and lack them — purely additive, never
removing anything an admin unchecked.
"""

from odoo import api, SUPERUSER_ID

_NEW_TOOL_CODES = (
    "cancel_invoice",
    "cancel_sale_order",
    "confirm_purchase_order",
    "validate_delivery",
    "invoice_order",
    "add_order_line",
    "register_payment",
    "reconcile_moves",
    "reverse_move",
    "resolve_ticket",
    "log_timesheet",
    "inspect_method",
)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    tools = env["nova.ai.tool"].search(
        [("code", "in", list(_NEW_TOOL_CODES))])
    if not tools:
        return
    have_execute = env["nova.ai.tool"].search(
        [("code", "=", "execute_method")], limit=1)
    if not have_execute:
        return
    agents = env["nova.ai.agent"].search(
        [("tool_ids", "in", [have_execute.id])])
    for agent in agents:
        missing = tools.filtered(
            lambda tool, owned=agent.tool_ids.ids:
            tool.id not in owned)
        if missing:
            agent.write({"tool_ids": [(4, tool.id)
                                      for tool in missing]})
