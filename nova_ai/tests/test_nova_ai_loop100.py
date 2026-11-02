# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Loop-100 regression tests: domain building, flow validation, gates."""

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestNovaAiLoop100(TransactionCase):
    def test_build_domain_all_is_and(self):
        from odoo.addons.nova_ai.models.nova_ai_write_tools import (
            tool_build_domain)
        out = tool_build_domain(self.env, {
            "model": "res.partner",
            "conditions": [
                {"field": "name", "operator": "ilike", "value": "Acme"},
                {"field": "customer_rank", "operator": ">", "value": 0}],
            "match": "all"}, {})
        self.assertEqual(out["domain"], [
            ["name", "ilike", "Acme"], ["customer_rank", ">", 0]])

    def test_build_domain_any_is_or(self):
        from odoo.addons.nova_ai.models.nova_ai_write_tools import (
            tool_build_domain)
        out = tool_build_domain(self.env, {
            "model": "res.partner",
            "conditions": [
                {"field": "name", "operator": "ilike", "value": "Acme"},
                {"field": "email", "operator": "ilike", "value": "acme"}],
            "match": "any"}, {})
        self.assertEqual(out["domain"], [
            "|", "name", "ilike", "Acme", "email", "ilike", "acme"])

    def test_build_domain_rejects_bad_match(self):
        from odoo.addons.nova_ai.models.nova_ai_write_tools import (
            tool_build_domain)
        with self.assertRaises(AccessError):
            tool_build_domain(self.env, {
                "model": "res.partner",
                "conditions": [{"field": "name", "operator": "ilike",
                                "value": "x"}],
                "match": "sometimes"}, {})

    def test_placeholder_only_domain_refuses_match_all(self):
        from odoo.addons.nova_ai.models.nova_ai_tools import _safe_domain
        with self.assertRaises(AccessError):
            _safe_domain(self.env, [[None, "=", None]])
        self.assertEqual(_safe_domain(self.env, []), [])

    def test_interp_unknown_path_stays_loud(self):
        from odoo.addons.nova_ai.models.nova_ai_flow import _interp
        self.assertEqual(_interp("Hello ${nobody.here}", {"a": 1}),
                         "Hello ${nobody.here}")

    def test_validate_flags_nested_forward_ref_but_not_builtins(self):
        flow = self.env["nova.ai.flow"].create({
            "name": "Loop100 validation", "active": False, "steps": [
                {"type": "search", "resultVariable": "rows",
                 "config": {"args": {
                     "model": "res.partner",
                     "domain": [["name", "ilike", "${later}"]]}}},
                {"type": "output",
                 "config": {"value": "from ${today_start}"}},
            ]})
        problems = flow.validate()
        texts = " ".join(text for _id, text in problems)
        self.assertIn("later", texts)
        self.assertNotIn("today_start", texts)

    def test_default_variables_seed_today(self):
        flow = self.env["nova.ai.flow"].create({
            "name": "Loop100 defaults", "active": False, "steps": [
                {"type": "start", "config": {"variables": []}}]})
        defaults = flow._default_variables()
        self.assertIn("today", defaults)
        self.assertIn("today_start", defaults)
        self.assertIn("today_end", defaults)

    def test_scan_codebase_refuses_outside_roots(self):
        from odoo.addons.nova_ai.models.nova_ai_tools import (
            tool_scan_codebase)
        with self.assertRaises(AccessError):
            tool_scan_codebase(self.env, {"paths": ["/etc"],
                                          "limit": 5}, {})

    def test_card_fields_covers_both_write_kinds(self):
        from odoo.addons.nova_ai.controllers.nova_ai import _card_fields
        self.assertEqual(
            _card_fields({"kind": "set_field",
                          "payload": {"field": "name"}}), {"name"})
        self.assertEqual(
            _card_fields({"kind": "set_fields",
                          "payload": {"values": {"a": 1, "b": 2}}}),
            {"a", "b"})
        self.assertEqual(_card_fields({}), set())

    def test_partner_dupe_asks(self):
        self.env["res.partner"].create({
            "name": "Loop100 Partner", "email": "loop100@example.com"})
        from odoo.addons.nova_ai.models.nova_ai_tools import execute_tool
        kind, payload, card = execute_tool(
            self.env, "create_draft",
            {"model": "res.partner",
             "values": {"name": "Loop100 Partner",
                        "email": "loop100@example.com"}}, {})
        self.assertEqual(kind, "ask")
        self.assertIn("already exists", payload["question"])
        self.assertIn("Create anyway", payload["options"])
        self.assertIsNone(card)

    def test_product_dupe_asks_on_default_code(self):
        self.env["product.template"].create({
            "name": "Loop100 Widget", "default_code": "L100"})
        from odoo.addons.nova_ai.models.nova_ai_tools import execute_tool
        kind, payload, card = execute_tool(
            self.env, "create_draft",
            {"model": "product.template",
             "values": {"name": "Another Name",
                        "default_code": "L100"}}, {})
        self.assertEqual(kind, "ask")
        self.assertIn("already exists", payload["question"])

    def test_partner_dupe_force_queues(self):
        self.env["res.partner"].create({"name": "Loop100 Force"})
        from odoo.addons.nova_ai.models.nova_ai_tools import execute_tool
        kind, payload, card = execute_tool(
            self.env, "create_draft",
            {"model": "res.partner",
             "values": {"name": "Loop100 Force"},
             "force": True}, {})
        self.assertEqual(kind, "confirm")
        self.assertEqual(card["kind"], "create_draft")

    def test_flow_recursion_stops_at_depth_cap(self):
        flow = self.env["nova.ai.flow"].create({
            "name": "Loop100 ouroboros", "active": False, "steps": [
                {"type": "tool", "config": {"tool": "run_flow",
                 "args": {"flow": "Loop100 ouroboros"}}}]})
        self.assertEqual(flow.validate(), [])
        outcome = flow.with_context(_flow_depth=3).run({})
        self.assertFalse(outcome.get("ok"))
        self.assertIn("deep", outcome.get("error") or "")
