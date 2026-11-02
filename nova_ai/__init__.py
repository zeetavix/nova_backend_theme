# Part of Nova. See LICENSE file for full copyright and licensing details.
from . import models
from . import controllers


def post_init_hook(env):
    """Seed the 114 business-action tool rows + grants (missing only)."""
    from .models.nova_ai_business_tools import (
        ensure_tool_rows, grant_to_agents)
    ensure_tool_rows(env)
    grant_to_agents(env)
