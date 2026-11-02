# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Timesheet-specific grid behaviour (clean-room, original logic).

* My grid — current user, rows grouped by project/task.
* All grid — managers, sectioned by employee.
* Cell write distributes a day-delta across that day's lines, creates a
  line when empty, removes zeroed placeholders, and guards 24h/day.
* Copy-previous-week is pure (no writes): it returns prior row skeletons
  that the client renders as addable rows.
"""
from __future__ import annotations

from odoo import api, fields, models
from odoo.exceptions import AccessError


def _first(value):
    if isinstance(value, (list, tuple)) and value:
        return value[0]
    return value


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    @api.model
    def nova_grid_update_cell(self, domain, measure_field_name="unit_amount", delta=0.0, default_values=None):
        if measure_field_name != "unit_amount":
            return super().nova_grid_update_cell(domain, measure_field_name, delta, default_values)
        try:
            delta = float(delta or 0.0)
        except (TypeError, ValueError):
            return False
        if not delta:
            return False
        domain = self._nova_normalize_domain(domain or [])
        default_values = self._nova_normalize_vals(dict(default_values or {}))
        lines = self.search(domain)
        # Guard: nobody edits other people's timesheets without rights.
        if lines and not (
            self.env.user.has_group("hr_timesheet.group_hr_timesheet_approver") or self.env.su
        ):
            others = lines.filtered(lambda l: l.user_id != self.env.user)
            if others:
                raise AccessError(self.env._("You cannot edit timesheets that are not yours."))
        if lines:
            # Prefer a described line (keeps placeholders for last), then oldest.
            target = lines.sorted(key=lambda l: (l.name in ("/", False, None), l.id))[0]
            new_value = max(0.0, float(target.unit_amount or 0.0) + delta)
            if new_value > 24:
                new_value = 24.0
            if new_value == 0 and target.name in ("/", False, None):
                target.unlink()
            else:
                target.write({"unit_amount": new_value})
            return False
        if delta <= 0:
            return False
        vals = {}
        for leaf in domain:
            if isinstance(leaf, (list, tuple)) and len(leaf) == 3:
                fname, op, value = leaf
                if op == "=" and fname not in vals:
                    vals[fname] = value
        vals.update(default_values)
        vals = self._nova_normalize_vals(vals)
        vals["unit_amount"] = min(24.0, delta)
        if not vals.get("name"):
            vals["name"] = "/"
        is_manager = self.env.user.has_group("hr_timesheet.group_hr_timesheet_approver") or self.env.su
        if not is_manager:
            # A regular user may only log time as themselves: ignore any
            # client-supplied employee/user (domain or defaults) and bind
            # the line to their own employee record.
            employee = self.env["hr.employee"].search([("user_id", "=", self.env.uid)], limit=1)
            if not employee:
                raise AccessError(self.env._("You can only log time for your own employee record."))
            vals["employee_id"] = employee.id
            vals.pop("user_id", None)
        elif not vals.get("employee_id") and not vals.get("user_id"):
            employee = self.env["hr.employee"].search([("user_id", "=", self.env.uid)], limit=1)
            if employee:
                vals["employee_id"] = employee.id
        self.create(vals)
        return False

    # Enterprise-compatible alias so shared views keep working.
    @api.model
    def grid_update_cell(self, domain, measure_field_name, value):
        return self.nova_grid_update_cell(domain, measure_field_name, value)

    @api.model
    def nova_grid_unavailability(self, start_date, end_date, groupby="", res_ids=None):
        # Community baseline: weekends are dimmed client-side; a future
        # holidays module can override this hook with real leave data.
        return {}

    @api.model
    def nova_grid_copy_previous(self, domain, date_field="date", period_start=None,
                                 period_end=None, prev_start=None, prev_end=None,
                                 row_fields=None):
        """Previous-period project/task skeletons (pure — creates nothing)."""
        row_fields = row_fields or ["project_id", "task_id"]
        domain = self._nova_normalize_domain(domain or [])
        raw = self.read_group(
            domain + [(date_field, ">=", prev_start), (date_field, "<", prev_end)],
            row_fields,
            ["unit_amount:sum"],
        )
        groups = self._nova_normalize_groups(raw, row_fields, ["unit_amount:sum"])
        rows = []
        seen = set()
        for group in groups:
            values = {f: group.get(f) for f in row_fields if f in group}
            sig = tuple(_first(v) for v in values.values())
            if sig in seen:
                continue
            seen.add(sig)
            row_domain = [[f, "=", _first(v)] for f, v in values.items()]
            rows.append({"values": values, "domain": row_domain})
        return {"rows": rows, "period_start": period_start, "period_end": period_end}
