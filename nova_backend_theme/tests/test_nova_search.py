# Part of Nova. See LICENSE file for full copyright and licensing details.

import json
import time

from odoo.tests import HttpCase, tagged

from .test_nova_isolation import _rpc_payload, make_plain_user


@tagged("nova", "-at_install", "post_install")
class TestNovaSearch(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        make_plain_user(cls.env, "nova_search_test", "NovaSearch-123")

    def call_search(self, params):
        return self.url_open(
            "/nova/search",
            data=_rpc_payload(params),
            headers={"Content-Type": "application/json"},
        )

    def test_search_requires_min_chars(self):
        self.authenticate("nova_search_test", "NovaSearch-123")
        res = self.call_search({"q": "a"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["result"], {"results": []})

    def test_search_unknown_model_dropped(self):
        self.authenticate("nova_search_test", "NovaSearch-123")
        self.env["ir.config_parameter"].sudo().set_str(
            "nova.search.models", "res.partner,no.such_model"
        )
        time.sleep(0.25)
        res = self.call_search({"q": "Azure"})
        self.assertEqual(res.status_code, 200)
        results = res.json()["result"]["results"]
        self.assertTrue(all(r["model"] == "res.partner" for r in results))
