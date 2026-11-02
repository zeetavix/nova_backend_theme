# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Grant the ask_user tool to full-toolbox agents.

Same noupdate-seed situation as the previous migration: tools added
after an agent was created never reach it unless granted here.
ask_user is read-only and non-destructive, so every agent holding
execute_method gets it — purely additive, never removing anything
an admin unchecked.
"""

from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    tool = env["nova.ai.tool"].search(
        [("code", "=", "ask_user")], limit=1)
    if not tool:
        return
    have_execute = env["nova.ai.tool"].search(
        [("code", "=", "execute_method")], limit=1)
    if not have_execute:
        return
    agents = env["nova.ai.agent"].search(
        [("tool_ids", "in", [have_execute.id]),
         ("tool_ids", "not in", [tool.id])])
    for agent in agents:
        agent.write({"tool_ids": [(4, tool.id)]})
