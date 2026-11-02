# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Offer Split on every form action without per-model work.

When a window action shows a form, clients can render a list next to it,
but stock Odoo never offers that combination. This override appends a
``split`` entry to the action description (right after the list, or after
the last multi-record view) plus the module defaults the client needs,
unless the action opts out. Actions without an explicit list view reuse
the model's default list view, so kanban-first actions (Employees, CRM
pipeline, ...) get Split too.

Opt out per action by putting ``nova_split_disable`` anywhere in the
action context, or globally with the ``nova_split.auto_inject``
parameter in Settings.
"""

from odoo import models


class IrActionsActWindow(models.Model):
    _inherit = "ir.actions.act_window"

    def _get_readable_fields(self):
        return super()._get_readable_fields() | {"nova_split_config"}

    def _get_action_dict(self):
        result = super()._get_action_dict()
        self._inject_nova_split_view(result)
        return result

    def _inject_nova_split_view(self, action):
        """Append the split view and client defaults to an action dict."""
        views = action.get("views") or []
        modes = [view[1] for view in views]
        if "split" in modes or "form" not in modes:
            return
        if action.get("target", "current") != "current":
            return
        if not action.get("res_model"):
            return
        if "nova_split_disable" in (action.get("context") or ""):
            return
        if not self._nova_split_bool_param("nova_split.auto_inject", True):
            return
        if "list" in modes:
            insert_at = modes.index("list") + 1
        elif not self._has_nova_split_list_view(action):
            # Nowhere to source the left pane from.
            return
        else:
            # After the last multi-record view (kanban, calendar, ...), so
            # the entry sits with its siblings; form-only actions keep the
            # form first and gain the entry at the end.
            insert_at = len(views)
            for index, mode in enumerate(modes):
                if mode not in ("form", "search"):
                    insert_at = index + 1
        views.insert(insert_at, [False, "split"])
        action["nova_split_config"] = {
            "auto_open": self._nova_split_bool_param("nova_split.auto_open", True),
            "default_width": self._nova_split_width_param(),
        }

    def _has_nova_split_list_view(self, action):
        """Whether a usable list arch exists for an action without one."""
        default_list = (
            self.env["ir.ui.view"].sudo().default_view(action.get("res_model"), "list")
        )
        return bool(default_list)

    def _nova_split_bool_param(self, key, default):
        # The action description is served to every user allowed to open the
        # action, so read the module-owned, non-sensitive UI defaults with a
        # scoped sudo: typed getters of fixed keys only, no record access.
        return self.env["ir.config_parameter"].sudo().get_bool(key, default)

    def _nova_split_width_param(self):
        width = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_int("nova_split.default_width", 560)
        )
        return min(max(width, 320), 1600)
