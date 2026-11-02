# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Event-specific Gantt behaviour (clean-room, original logic).

The generic engine already moves and resizes bars by writing the begin/end
datetimes (preserving the time-of-day). This override adds the one rule
events need: an event can never end before it begins — clamp it and say so,
instead of silently writing an inverted range.
"""
from __future__ import annotations

from odoo import api, models
from odoo.exceptions import UserError


class EventEvent(models.Model):
    _inherit = "event.event"

    @api.model
    def nova_gantt_update_dates(self, domain, date_start_field="date_begin", date_stop_field="date_end", new_start=None, new_stop=None):
        if not new_start:
            raise UserError(self.env._("An event needs a start date."))
        if new_stop and str(new_stop) < str(new_start):
            raise UserError(self.env._("An event cannot end before it begins."))
        return super().nova_gantt_update_dates(domain, date_start_field, date_stop_field, new_start, new_stop or new_start)
