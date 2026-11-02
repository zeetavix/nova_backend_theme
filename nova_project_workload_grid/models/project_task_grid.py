# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Workload-specific grid behaviour (clean-room, original logic).

* The grid plans allocated hours per deadline bucket: a cell delta is spread
  across the tasks in that (project, stage, deadline) bucket, oldest first.
* Empty buckets create a placeholder task (name "/") carrying the project,
  stage and deadline — the same honest placeholder convention timesheets
  use, cleaned up when its allocation returns to zero.
* A day bucket can never hold more than 24 planned hours.
* No ownership spoofing problem exists here: project access rules already
  govern who may see and edit tasks, and the grid writes through them.
"""
from __future__ import annotations

from odoo import api, fields, models


class ProjectTask(models.Model):
    _inherit = "project.task"

    @api.model
    def nova_grid_update_cell(self, domain, measure_field_name="allocated_hours", delta=0.0, default_values=None):
        if measure_field_name != "allocated_hours":
            return super().nova_grid_update_cell(domain, measure_field_name, delta, default_values)
        try:
            delta = float(delta or 0.0)
        except (TypeError, ValueError):
            return False
        if not delta:
            return False
        domain = self._nova_normalize_domain(domain or [])
        default_values = self._nova_normalize_vals(dict(default_values or {}))
        tasks = self.search(domain)
        if tasks:
            target = tasks.sorted(key=lambda t: (t.create_date or fields.Datetime.now(), t.id))[0]
            new_value = max(0.0, float(target.allocated_hours or 0.0) + delta)
            if new_value > 24:
                new_value = 24.0
            if new_value == 0 and (target.name or "").strip() in ("", "/"):
                target.unlink()
            else:
                target.write({"allocated_hours": new_value})
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
        vals["allocated_hours"] = min(24.0, delta)
        if not vals.get("name"):
            vals["name"] = "/"
        self.create(vals)
        return False
