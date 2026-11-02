# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Grant the knowledge and flow tools to the agents that should have them.

Why this script exists
----------------------
The agent's tool list is seeded from XML, and that data file is
``noupdate`` on purpose: an upgrade must not wipe the tool and model
choices an administrator made. The side effect is that a NEW tool
added to the catalogue never reaches an existing agent — the seed is
skipped, so the agent's ``tool_ids`` still points at the old set.

Seen live: the agent answered "read_knowledge is not permitted" while
trying to load the skill it had just been told to follow. The tool
existed, was in the catalogue, and was in the registry — it simply was
not on the agent.

The fix is deliberately narrow: it adds the four tools this wave
introduced, to agents whose tier already qualifies for them, and only
when they are missing. Every other tool an administrator may have
added or removed is left exactly as it is.
"""

import logging

_logger = logging.getLogger(__name__)

# The tools this wave added, and the tier each one needs.
NEW_TOOLS = {
    "read_knowledge": "t2",
    "write_knowledge": "t2",
    "list_flows": "t2",
    "run_flow": "t2",
    "set_fields": "t2",
    "build_domain": "t1",
    "list_models": "t1",
    "validate_write": "t2",
    "diagnose_access": "t2",
    "scan_codebase": "t2",
}

_TIER_ORDER = {"t1": 1, "t2": 2, "t3": 3}


def migrate(cr, version):
    """Add the new tools to every agent that qualifies for them."""
    if not version:
        return
    cr.execute("""
        SELECT a.id, a.name, a.tier
        FROM nova_ai_agent a
        WHERE a.active
    """)
    agents = cr.fetchall()
    if not agents:
        return
    for code, tier_min in NEW_TOOLS.items():
        cr.execute("""
            SELECT id FROM nova_ai_tool WHERE code = %s
        """, (code,))
        row = cr.fetchone()
        if not row:
            _logger.warning(
                "nova_ai migration: tool %s is not in the catalogue; "
                "skipping it.", code)
            continue
        tool_id = row[0]
        for agent_id, agent_name, agent_tier in agents:
            have = _TIER_ORDER.get(agent_tier or "t1", 1)
            need = _TIER_ORDER.get(tier_min, 2)
            if have < need:
                continue
            cr.execute("""
                SELECT 1 FROM nova_ai_agent_nova_ai_tool_rel
                WHERE nova_ai_agent_id = %s AND nova_ai_tool_id = %s
            """, (agent_id, tool_id))
            if cr.fetchone():
                continue
            cr.execute("""
                INSERT INTO nova_ai_agent_nova_ai_tool_rel (nova_ai_agent_id, nova_ai_tool_id)
                VALUES (%s, %s)
            """, (agent_id, tool_id))
            _logger.info(
                "nova_ai migration: granted %s to agent %s", code, agent_name)
