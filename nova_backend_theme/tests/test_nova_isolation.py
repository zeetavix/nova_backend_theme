# Part of Nova. See LICENSE file for full copyright and licensing details.
#
# Backend isolation tests: unread-record masking, model denylist, route auth.
# HttpCase (real stack, real record rules), tagged post_install like the rest.

import json
import time

from odoo import fields
from odoo.tests import HttpCase, tagged


def _rpc_payload(params):
    return json.dumps({"jsonrpc": "2.0", "method": "call", "params": params})


def _call(self, path, params):
    return self.url_open(
        path,
        data=_rpc_payload(params),
        headers={"Content-Type": "application/json"},
    )


def make_plain_user(env, login, password):
    """Internal user with a known password (admin/admin never exists here)."""
    group_user = env.ref("base.group_user")
    user = (
        env["res.users"]
        .sudo()
        .create(
            {
                "name": login,
                "login": login,
                "email": "%s@example.com" % login,
                "group_ids": [(6, 0, [group_user.id])],
            }
        )
    )
    user.sudo().write({"password": password})
    return user


@tagged("nova", "-at_install", "post_install")
class TestNovaAttentionIsolation(HttpCase):
    def test_unread_doc_name_masked(self):
        # Low-priv user with an activity on a document they cannot read:
        # ids stay (needed to open the activity), the name must not leak.
        _low = make_plain_user(self.env, "nova_low", "NovaLow-123")
        user = _low
        move = (
            self.env["account.move"]
            .sudo()
            .create(
                {
                    "move_type": "out_invoice",
                    "partner_id": self.env.ref("base.main_partner").id,
                    "invoice_line_ids": [
                        (0, 0, {"name": "Leak Probe Line", "quantity": 1, "price_unit": 10})
                    ],
                }
            )
        )
        atype = self.env["mail.activity.type"].sudo().search([], limit=1)
        self.assertTrue(atype, "needs a mail.activity.type fixture")
        model_account_move = self.env["ir.model"].sudo()._get("account.move")
        self.env["mail.activity"].sudo().create(
            {
                "res_model_id": model_account_move.id,
                "res_id": move.id,
                "user_id": user.id,
                "activity_type_id": atype.id,
                "summary": "Probe activity",
                "date_deadline": fields.Date.today(),
            }
        )
        self.authenticate("nova_low", "NovaLow-123")
        res = _call(self, "/nova/attention", {})
        self.assertEqual(res.status_code, 200)
        items = res.json()["result"]["items"]
        row = next((i for i in items if i["res_id"] == move.id), None)
        self.assertIsNotNone(row, "own activity must be listed")
        self.assertEqual(row["res_name"], "Private record")
        leak = move.display_name or ""
        if leak:
            self.assertNotIn(leak, json.dumps(items))


@tagged("nova", "-at_install", "post_install")
class TestNovaSearchDenylist(HttpCase):
    def test_denied_models_never_returned(self):
        self.authenticate("admin", "admin")
        self.env["ir.config_parameter"].sudo().set_str(
            "nova.search.models", "res.partner,res.users,ir.model,res.config.settings"
        )
        for query in ("Ad", "Admin", "ir"):
            res = _call(self, "/nova/search", {"q": query})
            self.assertEqual(res.status_code, 200)
            for row in res.json()["result"]["results"]:
                self.assertNotEqual(row["model"], "res.users")
                self.assertFalse(row["model"].startswith(("ir.", "res.config")))
            time.sleep(0.25)

    def test_denylist_only_config_returns_empty(self):
        self.authenticate("admin", "admin")
        self.env["ir.config_parameter"].sudo().set_str(
            "nova.search.models", "res.users,ir.model"
        )
        time.sleep(0.25)
        res = _call(self, "/nova/search", {"q": "Admin"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["result"], {"results": []})


@tagged("nova", "-at_install", "post_install")
class TestNovaAuthTable(HttpCase):
    ROUTES = ("/nova/search", "/nova/attention", "/nova/insights", "/nova/home", "/nova/dock")

    def _params(self, route):
        return {"q": "ab"} if route == "/nova/search" else {}

    def test_routes_require_login(self):
        # Anonymous jsonrpc calls answer HTTP 200 with an error envelope
        # ("Odoo Session Expired") — never a result payload.
        for route in self.ROUTES:
            res = self.url_open(
                route,
                data=_rpc_payload(self._params(route)),
                headers={"Content-Type": "application/json"},
                allow_redirects=False,
            )
            self.assertEqual(res.status_code, 200, route)
            body = res.json()
            self.assertNotIn("result", body, route)
            self.assertIn("error", body, route)

    def test_routes_ok_for_user(self):
        make_plain_user(self.env, "nova_probe_test", "NovaProbe-123")
        self.authenticate("nova_probe_test", "NovaProbe-123")
        for route in self.ROUTES:
            res = _call(self, route, self._params(route))
            self.assertEqual(res.status_code, 200, route)
            self.assertIn("result", res.json(), route)
