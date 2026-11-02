# Part of Nova. See LICENSE file for full copyright and licensing details.

import re

from odoo import api, fields, models

_BOOKMARK_LIMIT = 100
_MODEL_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")
_NAME_MAX = 120


class NovaBookmark(models.Model):
    _name = "nova.bookmark"
    _description = "Nova Bookmark"
    _order = "sequence, id"

    user_id = fields.Many2one(
        "res.users", string="User", required=True, index=True, ondelete="cascade",
        default=lambda self: self.env.user,
    )
    kind = fields.Selection(
        [("menu", "Menu"), ("record", "Record")], string="Kind",
        required=True, default="menu",
    )
    menu_id = fields.Many2one(
        "ir.ui.menu", string="Menu", ondelete="cascade", index=True,
    )
    res_model = fields.Char(string="Model", index=True)
    res_id = fields.Integer(string="Record ID")
    name = fields.Char(string="Label")
    sequence = fields.Integer(string="Sequence", default=10)

    def _state(self, prune=True):
        rows = self.search([("user_id", "=", self.env.uid)])
        menus, records, seen_menus = [], [], set()
        for row in rows:
            if row.kind == "menu" and row.menu_id:
                if row.menu_id.id in seen_menus:
                    continue
                seen_menus.add(row.menu_id.id)
                try:
                    if row.menu_id.exists() and row.menu_id.has_access("read"):
                        menus.append(row.menu_id.id)
                        continue
                except Exception:  # noqa: BLE001 - stale rows pruned below
                    pass
                if prune:
                    try:
                        row.unlink()
                    except Exception:  # noqa: BLE001 - prune best-effort
                        pass
            elif row.kind == "record" and row.res_model and row.res_id:
                try:
                    rec = self.env[row.res_model].browse(row.res_id)
                    if not rec.exists():
                        raise ValueError("gone")
                    rec.check_access("read")
                except Exception:  # noqa: BLE001 - inaccessible rows skipped
                    if prune:
                        try:
                            row.unlink()
                        except Exception:  # noqa: BLE001 - prune best-effort
                            pass
                    continue
                records.append(
                    {
                        "res_model": row.res_model,
                        "res_id": row.res_id,
                        "name": row.name or row.res_model,
                    }
                )
        return {"menus": menus, "records": records}

    @api.model
    def get_mine(self):
        return self._state()

    def _check_limit(self):
        return (
            self.search_count([("user_id", "=", self.env.uid)]) < _BOOKMARK_LIMIT
        )

    @api.model
    def toggle_menu(self, menu_id):
        try:
            menu_id = int(menu_id or 0)
        except (TypeError, ValueError):
            menu_id = 0
        if menu_id:
            existing = self.search(
                [("user_id", "=", self.env.uid), ("kind", "=", "menu"), ("menu_id", "=", menu_id)],
                limit=1,
            )
            if existing:
                try:
                    existing.unlink()
                except Exception:  # noqa: BLE001 - unstar best-effort
                    pass
                return self._state()
            try:
                menu = self.env["ir.ui.menu"].browse(menu_id)
                if not menu.exists() or not menu.has_access("read"):
                    return self._state()
            except Exception:  # noqa: BLE001 - unresolvable menu
                return self._state()
            if self._check_limit():
                try:
                    self.create(
                        {"kind": "menu", "menu_id": menu_id, "name": menu.name}
                    )
                except Exception:  # noqa: BLE001 - star best-effort
                    pass
        return self._state()

    @api.model
    def toggle_record(self, res_model, res_id, name=None):
        try:
            res_id = int(res_id or 0)
        except (TypeError, ValueError):
            res_id = 0
        if (
            not res_model
            or not isinstance(res_model, str)
            or not _MODEL_RE.match(res_model)
            or not res_id
        ):
            return self._state()
        label = (name or res_model)[:_NAME_MAX] if isinstance(name, str) else res_model
        existing = self.search(
            [
                ("user_id", "=", self.env.uid),
                ("kind", "=", "record"),
                ("res_model", "=", res_model),
                ("res_id", "=", res_id),
            ],
            limit=1,
        )
        if existing:
            try:
                existing.unlink()
            except Exception:  # noqa: BLE001 - unstar best-effort
                pass
            return self._state()
        try:
            model = self.env[res_model]
        except KeyError:
            return self._state()
        try:
            if not model.has_access("read"):
                return self._state()
            probe = model.browse(res_id)
            if not probe.exists():
                return self._state()
            probe.check_access("read")
        except Exception:  # noqa: BLE001 - access check best-effort
            return self._state()
        if self._check_limit():
            try:
                self.create(
                    {
                        "kind": "record",
                        "res_model": res_model,
                        "res_id": res_id,
                        "name": label,
                    }
                )
            except Exception:  # noqa: BLE001 - star best-effort
                pass
        return self._state()
