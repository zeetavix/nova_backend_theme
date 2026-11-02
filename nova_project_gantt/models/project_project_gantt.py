# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Project-specific Gantt behaviour (clean-room, original logic).

The generic engine already moves/resizes bars by writing the start/stop
dates. Project dates have one extra rule worth enforcing explicitly:
an expiration date before the start date is a planning mistake, not a
drag target — clamp it and say so, instead of silently writing it.
"""
from __future__ import annotations

from odoo import api, models
from odoo.exceptions import UserError


class ProjectProject(models.Model):
    _inherit = "project.project"

    @api.model
    def nova_gantt_update_dates(self, domain, date_start_field="date_start", date_stop_field="date", new_start=None, new_stop=None):
        if not new_start:
            raise UserError(self.env._("A bar needs a start date."))
        if new_stop and str(new_stop) < str(new_start):
            raise UserError(self.env._("A project cannot end before it starts."))
        return super().nova_gantt_update_dates(
            domain, date_start_field, date_stop_field, new_start, new_stop or new_start
        )
