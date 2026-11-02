# Part of Nova. See LICENSE file for full copyright and licensing details.
from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestNovaSplitTour(HttpCase):
    """Browser flow: open Split, click a row, navigate with arrow keys,
    close and reopen the panel. Runs with the real backend, no mocks."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.partner"].create([
            {"name": "Nova Tour Alpha", "email": "alpha@example.com"},
            {"name": "Nova Tour Bravo", "email": "bravo@example.com"},
            {"name": "Nova Tour Charlie", "email": "charlie@example.com"},
        ])

    def test_split_tour(self):
        self.start_tour("/odoo/contacts", "nova_split_tour", login="admin")
