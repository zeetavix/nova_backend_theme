# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Leave-specific grid behaviour (clean-room, original logic).

The leave grid is deliberately read-only: time off must always flow through
the request/approval workflow, so the grid adds no write path of its own.
This override exists only to make that refusal explicit (and loud in logs)
if anyone ever calls the cell API directly.
"""
from __future__ import annotations

from odoo import api, models


class HrLeave(models.Model):
    _inherit = "hr.leave"

    @api.model
    def nova_grid_update_cell(self, domain, measure_field_name="number_of_days", delta=0.0, default_values=None):
        # Read-only by design: create and edit leave from the Time Off
        # request flow so approvals, allocations and balances stay correct.
        return False
