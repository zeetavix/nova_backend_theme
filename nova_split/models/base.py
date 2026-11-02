# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Serve split views from the action's own list arch.

The split view reuses the action's list view on its left pane, so models
do not need a stored ``<split>`` arch. When ``get_views`` asks for one
and none exists (nor a ``_get_default_split_view`` on the model), answer
the action's list arch instead of raising, mirroring how core falls back
to generated archs. A per-model ``<split>`` view record keeps working: it
is found first, exactly like any other view type.
"""

import copy

from lxml import etree

from odoo import api, models


class Base(models.AbstractModel):
    _inherit = "base"

    @api.model
    def _get_view(self, view_id=None, view_type="form", **options):
        if view_type == "split" and not view_id:
            view_id = self.env["ir.ui.view"].sudo().default_view(self._name, view_type)
        if view_type == "split" and not view_id:
            # No stored split view: reuse the action's list view so the
            # split controller parses a real list arch on its left pane.
            list_view_id = self._get_nova_split_list_view_id(options.get("action_id"))
            if list_view_id:
                arch, view = super()._get_view(list_view_id, view_type="list", **options)
                return self._get_nova_split_arch(arch), view
            return etree.fromstring('<split string="Split"/>'), self.env["ir.ui.view"]
        return super()._get_view(view_id, view_type=view_type, **options)

    @api.model
    def _get_nova_split_arch(self, arch):
        """Adapt a list arch for the split left pane.

        A model's custom list ``js_class`` would make the web client mount
        the model's own list subclass instead of our SplitController, which
        silently drops the side panel (no error is raised). The split pane
        is navigation-only, so it always uses the standard list behavior.
        """
        # ``_get_view`` returns an etree node; tolerate a raw string in case
        # another override serializes the arch. Copy before mutating so a
        # cached/shared node never loses ``js_class`` for the plain list view.
        root = arch if isinstance(arch, etree._Element) else etree.fromstring(arch.encode("utf-8"))
        root = copy.deepcopy(root)
        if root.get("js_class"):
            del root.attrib["js_class"]
        return root

    @api.model
    def _get_nova_split_list_view_id(self, action_id):
        """Return the list view backing Split for an action, if any."""
        views = []
        if type(action_id) is int:
            # View ids of the action are already exposed to this user in the
            # action description, so reading them with a scoped sudo only
            # reuses known data to pick the arch, nothing more.
            action = self.env["ir.actions.act_window"].sudo().browse(action_id)
            if action.exists():
                views = action.views or []
        for view_id, view_mode in views:
            if view_mode == "list" and view_id:
                return view_id
        return self.env["ir.ui.view"].sudo().default_view(self._name, "list")

    @api.model
    def _get_view_cache_key(self, view_id=None, view_type="form", **options):
        key = super()._get_view_cache_key(view_id, view_type=view_type, **options)
        if view_type == "split":
            # The fallback arch depends on the requesting action.
            key += (options.get("action_id"),)
        return key

    @api.model
    def _get_view_fields(self, view_type, models):
        # A split arch carries list fields; ask for the same field set.
        if view_type == "split":
            view_type = "list"
        return super()._get_view_fields(view_type, models)
