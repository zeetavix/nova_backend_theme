# Part of Nova. See LICENSE file for full copyright and licensing details.

from odoo.tests import HttpCase, tagged

from .test_nova_isolation import _rpc_payload, make_plain_user


@tagged("nova", "-at_install", "post_install")
class TestNovaInsights(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        make_plain_user(cls.env, "nova_insights_test", "NovaInsights-123")

    def call_insights(self):
        return self.url_open(
            "/nova/insights",
            data=_rpc_payload({}),
            headers={"Content-Type": "application/json"},
        )

    def test_insights_shape(self):
        self.authenticate("nova_insights_test", "NovaInsights-123")
        res = self.call_insights()
        self.assertEqual(res.status_code, 200)
        tiles = res.json()["result"]["tiles"]
        self.assertIsInstance(tiles, list)
        self.assertLessEqual(len(tiles), 5)
        for tile in tiles:
            self.assertIn("key", tile)
            self.assertIn("label", tile)
            self.assertIn("value", tile)
            self.assertIn("app_hints", tile)
            self.assertIn("overdue", tile)
            self.assertIn("res_model", tile)
            self.assertIn("domain", tile)
            self.assertIsInstance(tile["overdue"], int)
            self.assertIsInstance(tile["domain"], list)
