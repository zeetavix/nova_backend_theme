# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Grant the graph-flow tools to qualifying agents (noupdate-safe)."""

import logging

_logger = logging.getLogger(__name__)

NEW_TOOLS = {
    "update_flow": "t2",
    "export_flow": "t1",
    "import_flow": "t2",
    "list_flow_runs": "t1",
}

_TIER_ORDER = {"t1": 1, "t2": 2, "t3": 3}


def migrate(cr, version):
    if not version:
        return
    cr.execute("SELECT a.id, a.name, a.tier FROM nova_ai_agent a WHERE a.active")
    agents = cr.fetchall()
    if not agents:
        return
    for code, tier_min in NEW_TOOLS.items():
        cr.execute("SELECT id FROM nova_ai_tool WHERE code = %s", (code,))
        row = cr.fetchone()
        if not row:
            _logger.warning("nova_ai migration: tool %s missing; skipping.", code)
            continue
        tool_id = row[0]
        for agent_id, agent_name, agent_tier in agents:
            if _TIER_ORDER.get(agent_tier or "t1", 1) < _TIER_ORDER.get(tier_min, 2):
                continue
            cr.execute(
                "SELECT 1 FROM nova_ai_agent_nova_ai_tool_rel "
                "WHERE nova_ai_agent_id = %s AND nova_ai_tool_id = %s",
                (agent_id, tool_id))
            if cr.fetchone():
                continue
            cr.execute(
                "INSERT INTO nova_ai_agent_nova_ai_tool_rel "
                "(nova_ai_agent_id, nova_ai_tool_id) VALUES (%s, %s)",
                (agent_id, tool_id))
            _logger.info("nova_ai migration: granted %s to agent %s", code, agent_name)
