# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Leave-specific Gantt behaviour (clean-room, original logic).

Unlike the leave *grid* (read-only by design), the timeline *does* allow
rescheduling: dragging a bar rewrites the request start/end dates, which is
exactly what the Time Off "reschedule" flow does. State transitions stay
untouched — approvals, refusals and cancellations still happen in the
Time Off app, never from a drag.
"""
from __future__ import annotations

from odoo import api, models
from odoo.exceptions import UserError


class HrLeave(models.Model):
    _inherit = "hr.leave"

    @api.model
    def nova_gantt_update_dates(self, domain, date_start_field="request_date_from", date_stop_field="request_date_to", new_start=None, new_stop=None):
        if not new_start:
            raise UserError(self.env._("Time off needs a start date."))
        if new_stop and str(new_stop) < str(new_start):
            raise UserError(self.env._("Time off cannot end before it starts."))
        return super().nova_gantt_update_dates(domain, date_start_field, date_stop_field, new_start, new_stop or new_start)
