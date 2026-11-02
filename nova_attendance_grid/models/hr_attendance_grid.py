# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Attendance-specific grid behaviour (clean-room, original logic).

* The grid *corrects* recorded attendance: a cell delta adjusts the latest
  record's check-out for that employee/day. It never fabricates check-ins —
  an empty day stays empty until someone checks in (kiosk or Attendances
  list), because inventing presence data is worse than missing data.
* Guards: check-out can never precede check-in, and a day can never exceed
  24 hours. Overlaps with the next record surface Odoo's own validation.
* Access-safe: regular users are bound to their own employee record;
  touching other people's attendance needs officer/manager rights.
"""
from __future__ import annotations

from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError


class HrAttendance(models.Model):
    _inherit = "hr.attendance"

    @api.model
    def _nova_attendance_manager(self):
        return self.env.user.has_group(
            "hr_attendance.group_hr_attendance_officer"
        ) or self.env.user.has_group("hr_attendance.group_hr_attendance_manager") or self.env.su

    @api.model
    def nova_grid_update_cell(self, domain, measure_field_name="worked_hours", delta=0.0, default_values=None):
        if measure_field_name != "worked_hours":
            return super().nova_grid_update_cell(domain, measure_field_name, delta, default_values)
        try:
            delta = float(delta or 0.0)
        except (TypeError, ValueError):
            return False
        if not delta:
            return False
        domain = self._nova_normalize_domain(domain or [])
        records = self.search(domain)
        if records and not self._nova_attendance_manager():
            others = records.filtered(lambda r: r.employee_id.user_id != self.env.user)
            if others:
                raise AccessError(self.env._("You cannot edit other people's attendance."))
        if not records:
            # Never fabricate presence: a missed check-in must be created
            # from the kiosk or the Attendances list first.
            return False
        target = records.sorted(key=lambda r: (r.check_in or fields.Datetime.now(), r.id))[-1]
        if target.check_out:
            new_checkout = target.check_out + timedelta(hours=delta)
        elif delta <= 0:
            return False
        else:
            # Close an open attendance by setting its check-out.
            new_checkout = target.check_in + timedelta(hours=delta)
        if new_checkout < target.check_in:
            raise UserError(self.env._("Check-out cannot be before check-in."))
        span_hours = (new_checkout - target.check_in).total_seconds() / 3600.0
        others_hours = sum(records.filtered(lambda r: r != target).mapped("worked_hours") or [0.0])
        if others_hours + span_hours > 24:
            raise UserError(self.env._("A day cannot hold more than 24 hours."))
        target.write({"check_out": new_checkout})
        return False

    @api.model
    def nova_grid_copy_previous(self, domain, date_field="date", period_start=None,
                                period_end=None, prev_start=None, prev_end=None,
                                row_fields=None):
        """No seeding: attendance history must never auto-create presence."""
        return {"rows": [], "period_start": period_start, "period_end": period_end}
