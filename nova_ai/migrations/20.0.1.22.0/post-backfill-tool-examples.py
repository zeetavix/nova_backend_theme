# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Backfill generated examples for spec tools created before examples
existed (the 20.0.1.21.0 seed wrote rows without them, and the tools
test requires every tool to carry an example dict). Hand-written
examples are never touched."""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    try:
        from odoo.addons.nova_ai.models.nova_ai_business_tools import (
            ensure_tool_rows)
    except ImportError as err:
        _logger.warning("nova_ai migration: business tools unavailable: %s",
                        err)
        return
    made = ensure_tool_rows(env)
    _logger.info("nova_ai migration: tool examples ensured (%d new rows)",
                 made)
