# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Seed the 114 business-action tool rows + tier grants (missing only)."""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    try:
        from odoo.addons.nova_ai.models.nova_ai_business_tools import (
            ensure_tool_rows, grant_to_agents)
    except ImportError as err:
        _logger.warning("nova_ai migration: business tools unavailable: %s", err)
        return
    made = ensure_tool_rows(env)
    granted = grant_to_agents(env)
    # Chat-loop tools fresh installs were missing (ask_user got an
    # upgrade-only grant; fetch_url had none): missing-only, tier-gated.
    extra = 0
    order = {"t1": 1, "t2": 2, "t3": 3}
    for code, tier_min in (("ask_user", "t2"), ("fetch_url", "t2")):
        tool = env["nova.ai.tool"].search([("code", "=", code)], limit=1)
        if not tool:
            continue
        for agent in env["nova.ai.agent"].search([]):
            if order.get(agent.tier or "t1", 1) < order[tier_min]:
                continue
            if tool in agent.tool_ids:
                continue
            agent.sudo().write({"tool_ids": [(4, tool.id)]})
            extra += 1
    _logger.info(
        "nova_ai migration: %d tool rows, %d+%d grants",
        made, granted, extra)
