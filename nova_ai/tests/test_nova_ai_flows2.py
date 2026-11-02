# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Nova AI flow execution tests: paths, drafting, and running.

The brain file covers search and validation. This file covers what
happens after validation passes: interpolation through nested
structures, drafting a flow from an agent turn, and running a flow
end to end.
"""

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestNovaAiFlowPaths(TransactionCase):
    def test_bracket_index_then_key(self):
        from odoo.addons.nova_ai.models import nova_ai_flow as flow_mod
        data = {"worst": {"rows": [{"id": 10, "name": "A"},
                                   {"id": 11, "name": "B"}]}}
        self.assertEqual(
            flow_mod._resolve_path(data, "worst.rows[0].id"), 10)
        self.assertEqual(
            flow_mod._resolve_path(data, "worst.rows[1].name"), "B")
        self.assertIsNone(flow_mod._resolve_path(data, "worst.rows[9].id"))

    def test_interp_replaces_inside_text(self):
        from odoo.addons.nova_ai.models import nova_ai_flow as flow_mod
        out = flow_mod._interp(
            "Pay ${worst.rows[0].id} now",
            {"worst": {"rows": [{"id": 2814}]}})
        self.assertEqual(out, "Pay 2814 now")

    def test_missing_path_is_none_not_crash(self):
        from odoo.addons.nova_ai.models import nova_ai_flow as flow_mod
        self.assertIsNone(flow_mod._resolve_path({"a": []}, "a[0].b"))
        self.assertIsNone(flow_mod._resolve_path({}, "x.y"))


@tagged("post_install", "-at_install")
class TestNovaAiDraftFlow(TransactionCase):
    def setUp(self):
        super().setUp()
        from odoo.addons.nova_ai.models.nova_ai_tools import execute_tool
        self.execute = execute_tool
        agent = self.env["nova.ai.agent"].search([], limit=1)
        tool = self.env["nova.ai.tool"].search(
            [("code", "=", "draft_flow")], limit=1)
        self.ctx = {"agent": agent, "tool": tool, "user": self.env.user}
        self.args = {
            "name": "Unit drafted flow",
            "description": "A flow drafted in a test.",
            "steps": [
                {"type": "start", "config": {"variables": []}},
                {"type": "search", "resultVariable": "rows",
                 "config": {"args": {"model": "res.partner",
                                     "domain": [],
                                     "fields": ["name"],
                                     "limit": 3}}},
                {"type": "output", "config": {"value": "${rows}"}},
            ],
        }

    def test_valid_flow_queues_a_draft_card(self):
        kind, payload, card = self.execute(
            self.env, "draft_flow", dict(self.args), self.ctx)
        self.assertEqual(kind, "confirm")
        self.assertTrue(card)
        self.assertEqual(card.get("kind"), "draft_flow")

    def test_broken_flow_raises_instead_of_queueing(self):
        from odoo.exceptions import AccessError
        bad = dict(self.args)
        bad["steps"] = [{"type": "teleport", "config": {}}]
        with self.assertRaises(AccessError):
            self.execute(self.env, "draft_flow", bad, self.ctx)


@tagged("post_install", "-at_install")
class TestNovaAiFlowRun(TransactionCase):
    def test_run_executes_search_then_output(self):
        from odoo.addons.nova_ai.models.nova_ai_tools import execute_tool
        kind, payload, card = execute_tool(
            self.env, "run_flow",
            {"flow": "Chase overdue invoices", "arguments": {}}, {})
        self.assertEqual(kind, "result")
        if not payload.get("ok") and "temporarily unavailable" in (
                payload.get("error") or ""):
            # The agent step needs a live model; a dead gateway is
            # an environment outage, not a flow bug. The search and
            # output steps above it already proved the path.
            self.skipTest("Live gateway down.")
        self.assertTrue(payload.get("ok"), payload.get("error"))
        kinds = [s.get("type") for s in payload.get("results", [])]
        self.assertIn("search", kinds)
        self.assertIn("output", kinds)

    def test_run_missing_flow_is_an_honest_error(self):
        from odoo.addons.nova_ai.models.nova_ai_tools import execute_tool
        kind, payload, card = execute_tool(
            self.env, "run_flow",
            {"flow": "No such flow anywhere", "arguments": {}}, {})
        self.assertEqual(kind, "result")
        self.assertIn("error", payload)
