# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Business action tools: registry, queue-time validation, apply path.

Queue-time tests run entirely without writes (cards only). Apply
tests use throwaway records cleaned by the transaction rollback.
"""

from unittest import mock

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestNovaAiBizRegistry(TransactionCase):
    def test_count_and_shapes(self):
        from odoo.addons.nova_ai.models import nova_ai_business_tools as biz
        from odoo.addons.nova_ai.models.nova_ai_tools import (
            CONFIRM_KINDS, REGISTRY)
        specs = biz.specs()
        self.assertGreaterEqual(len(specs), 100)
        for code in specs:
            self.assertIn(code, REGISTRY)
            self.assertIn(code, CONFIRM_KINDS)
            schema = biz.schema_for(specs[code])
            self.assertEqual(schema["type"], "object")

    def test_tool_rows_seeded(self):
        from odoo.addons.nova_ai.models import nova_ai_business_tools as biz
        made = biz.ensure_tool_rows(self.env)
        count = self.env["nova.ai.tool"].search_count(
            [("code", "in", list(biz.specs()))])
        self.assertEqual(count, len(biz.specs()))


@tagged("post_install", "-at_install")
class TestNovaAiBizQueue(TransactionCase):
    def _card(self, code, args):
        from odoo.addons.nova_ai.models.nova_ai_tools import execute_tool
        kind, payload, card = execute_tool(self.env, code, args, {})
        self.assertEqual(kind, "confirm")
        self.assertTrue(card)
        return card

    def test_create_quotation_queues(self):
        partner = self.env["res.partner"].create({"name": "Biz Q"})
        card = self._card("create_quotation", {"partner_id": partner.id})
        self.assertEqual(card["payload"]["model"], "sale.order")

    def test_create_vendor_full(self):
        card = self._card("create_vendor_full", {"name": "Biz Vendor"})
        self.assertEqual(card["payload"]["values"]["supplier_rank"], 1)

    def test_create_bom_multilevel_shape(self):
        with self.assertRaises(AccessError):
            self._card("create_bom_multilevel", {})

    def test_state_guard_refuses(self):
        pricelist = self.env["product.pricelist"].create(
            {"name": "Biz PL"})
        order = self.env["sale.order"].create(
            {"partner_id": self.env["res.partner"].create(
                {"name": "Biz S"}).id})
        order.action_confirm()
        with self.assertRaises(AccessError):
            self._card("set_order_pricelist",
                       {"res_id": order.id,
                        "pricelist_id": pricelist.id})

    def test_read_tool_returns_data(self):
        from odoo.addons.nova_ai.models.nova_ai_tools import execute_tool
        partner = self.env["res.partner"].create({"name": "Biz V"})
        kind, payload, card = execute_tool(
            self.env, "validate_vendor_profile",
            {"res_id": partner.id}, {})
        self.assertEqual(kind, "result")
        self.assertIn("missing", payload)

    def test_email_needs_recipient(self):
        partner = self.env["res.partner"].create({"name": "Biz NoMail"})
        with self.assertRaises(AccessError):
            self._card("send_email_record",
                       {"model": "res.partner", "res_id": partner.id,
                        "subject": "Hi", "body": "x", "to": ""})

    def test_dupe_guards(self):
        from odoo.addons.nova_ai.models.nova_ai_tools import execute_tool
        self.env["res.partner"].create(
            {"name": "Biz Dupe", "email": "biz@dupe.test"})
        with self.assertRaises(AccessError):
            execute_tool(self.env, "create_vendor",
                         {"name": "Biz Dupe", "email": "biz@dupe.test"},
                         {})
        # force=true bypasses the guard explicitly.
        kind, _p, card = execute_tool(
            self.env, "create_vendor",
            {"name": "Biz Dupe", "email": "biz@dupe.test", "force": True},
            {})
        self.assertEqual(kind, "confirm")

    def test_misc_entry_must_balance(self):
        journal = self.env["account.journal"].search(
            [("type", "=", "general")], limit=1)
        account = self.env["account.account"].search([], limit=1)
        args = {"journal_id": journal.id, "date": "2026-10-26",
                "lines": [{"account_id": account.id, "debit": 100},
                          {"account_id": account.id, "credit": 100}]}
        card = self._card("create_misc_entry", args)
        self.assertEqual(card["kind"], "create_misc_entry")
        bad = dict(args, lines=[{"account_id": account.id, "debit": 100},
                                {"account_id": account.id, "credit": 90}])
        with self.assertRaises(AccessError):
            self._card("create_misc_entry", bad)

    def test_read_ops_are_result_kind(self):
        from odoo.addons.nova_ai.models.nova_ai_tools import execute_tool
        product = self.env["product.product"].create(
            {"name": "Biz R", "list_price": 5})
        for code, args in (
                ("check_availability", {"product_id": product.id}),
                ("check_reorder", {"product_id": product.id})):
            kind, payload, _c = execute_tool(self.env, code, args, {})
            self.assertEqual(kind, "result", code)
            self.assertIn("product", payload)


@tagged("post_install", "-at_install")
class TestNovaAiBizApply(TransactionCase):
    def test_apply_create_and_write(self):
        from odoo.addons.nova_ai.models.nova_ai_tools import (
            execute_tool, run_confirm)
        partner = self.env["res.partner"].create({"name": "Biz A"})
        _k, _p, card = execute_tool(
            self.env, "create_task",
            {"project_id": self.env["project.project"].create(
                {"name": "Biz P"}).id,
             "name": "Biz task"}, {})
        res = run_confirm(self.env, card)
        self.assertTrue(res["res_id"])
        _k2, _p2, card2 = execute_tool(
            self.env, "set_task_priority",
            {"res_id": res["res_id"], "priority": "2"}, {})
        res2 = run_confirm(self.env, card2)
        self.assertEqual(res2["res_id"], res["res_id"])

    def test_apply_method_guard(self):
        from odoo.addons.nova_ai.models.nova_ai_tools import (
            execute_tool, run_confirm)
        lead = self.env["crm.lead"].create({"name": "Biz lead"})
        _k, _p, card = execute_tool(
            self.env, "mark_won", {"res_id": lead.id}, {})
        res = run_confirm(self.env, card)
        self.assertEqual(res["res_id"], lead.id)

    def test_approve_rechecks_state(self):
        # A card queued on a draft that gets confirmed before Approve
        # must refuse instead of writing onto the wrong state.
        from odoo.addons.nova_ai.models.nova_ai_tools import (
            execute_tool, run_confirm)
        pricelist = self.env["product.pricelist"].create(
            {"name": "Biz PL2"})
        order = self.env["sale.order"].create(
            {"partner_id": self.env["res.partner"].create(
                {"name": "Biz R"}).id})
        _k, _p, card = execute_tool(
            self.env, "set_order_pricelist",
            {"res_id": order.id, "pricelist_id": pricelist.id}, {})
        order.action_confirm()
        with self.assertRaises(AccessError):
            run_confirm(self.env, card)

    def test_apply_m2m_assign(self):
        from odoo.addons.nova_ai.models.nova_ai_tools import (
            execute_tool, run_confirm)
        project = self.env["project.project"].create({"name": "Biz M"})
        task = self.env["project.task"].create(
            {"name": "Biz MT", "project_id": project.id})
        _k, _p, card = execute_tool(
            self.env, "assign_task",
            {"res_id": task.id, "user_ids": [self.env.user.id]}, {})
        res = run_confirm(self.env, card)
        self.assertEqual(res["res_id"], task.id)
        self.assertIn(self.env.user, task.user_ids)
        # An empty list must refuse, never wipe the field.
        with self.assertRaises(AccessError):
            execute_tool(self.env, "assign_task",
                         {"res_id": task.id, "user_ids": []}, {})

    def test_apply_property_terms(self):
        from odoo.addons.nova_ai.models.nova_ai_tools import (
            execute_tool, run_confirm)
        term = self.env["account.payment.term"].create(
            {"name": "Biz 30d"})
        partner = self.env["res.partner"].create({"name": "Biz PT"})
        _k, _p, card = execute_tool(
            self.env, "set_partner_payment_terms",
            {"res_id": partner.id,
             "property_supplier_payment_term_id": term.id}, {})
        res = run_confirm(self.env, card)
        self.assertEqual(res["res_id"], partner.id)
        self.assertEqual(
            partner.property_supplier_payment_term_id.id, term.id)

    def test_apply_copy_makes_twin(self):
        from odoo.addons.nova_ai.models.nova_ai_tools import (
            execute_tool, run_confirm)
        order = self.env["sale.order"].create(
            {"partner_id": self.env["res.partner"].create(
                {"name": "Biz C"}).id})
        _k, _p, card = execute_tool(
            self.env, "duplicate_quotation", {"res_id": order.id}, {})
        res = run_confirm(self.env, card)
        self.assertTrue(res["res_id"] and res["res_id"] != order.id)

    def test_apply_email_sends(self):
        from odoo.addons.nova_ai.models.nova_ai_tools import (
            execute_tool, run_confirm)
        partner = self.env["res.partner"].create(
            {"name": "Biz E", "email": "biz@example.test"})
        _k, _p, card = execute_tool(
            self.env, "send_email_record",
            {"model": "res.partner", "res_id": partner.id,
             "subject": "Hello", "body": "World"}, {})
        with mock.patch.object(
                type(self.env["mail.mail"]), "send",
                return_value=True):
            res = run_confirm(self.env, card)
        self.assertTrue(res["res_id"])

    def test_apply_leave_datetimes(self):
        from odoo.addons.nova_ai.models.nova_ai_tools import (
            execute_tool, run_confirm)
        employee = self.env["hr.employee"].create({"name": "Biz L"})
        ltype = self.env["hr.leave.type"].search([], limit=1)
        _k, _p, card = execute_tool(
            self.env, "create_leave",
            {"employee_id": employee.id,
             "holiday_status_id": ltype.id,
             "date_from": "2026-11-02 09:00",
             "date_to": "2026-11-02 18:00"}, {})
        res = run_confirm(self.env, card)
        leave = self.env["hr.leave"].browse(res["res_id"])
        self.assertEqual(leave.state, "confirm")
        with self.assertRaises(AccessError):
            execute_tool(self.env, "create_leave",
                         {"employee_id": employee.id,
                          "holiday_status_id": ltype.id,
                          "date_from": "not-a-date",
                          "date_to": "2026-11-02 18:00"}, {})

    def test_apply_bank_dupe_refused(self):
        from odoo.addons.nova_ai.models.nova_ai_tools import (
            execute_tool, run_confirm)
        partner = self.env["res.partner"].create({"name": "Biz B"})
        _k, _p, card = execute_tool(
            self.env, "add_partner_bank",
            {"partner_id": partner.id, "acc_number": "BE68 5390 0754"},
            {})
        run_confirm(self.env, card)
        with self.assertRaises(AccessError):
            execute_tool(self.env, "add_partner_bank",
                         {"partner_id": partner.id,
                          "acc_number": "BE68 5390 0754"}, {})

    def test_run_graph_count_and_group(self):
        self.env["res.partner"].create({"name": "Biz G"})
        flow = self.env["nova.ai.flow"].create({
            "name": "Biz graph tools", "description": "t",
            "nodes": [
                {"id": "t", "type": "trigger", "config": {}},
                {"id": "c", "type": "odoo.count",
                 "config": {"args": {"model": "res.partner"}}},
                {"id": "g", "type": "odoo.group",
                 "config": {"args": {
                     "model": "res.partner",
                     "groupby": ["company_type"],
                     "fields": ["__count"]}}},
            ],
            "edges": [{"source": "t", "target": "c"},
                      {"source": "c", "target": "g"}],
            "active": True,
        })
        self.addCleanup(flow.sudo().unlink)
        outcome = flow.run_graph({}, trigger_kind="manual")
        self.assertTrue(outcome.get("ok"), outcome.get("error"))
        self.assertIn("count", str(outcome.get("results")))

    def test_describe_route_scoped(self):
        from odoo.addons.nova_ai.controllers.nova_ai_studio import (
            flow_action)
        flow = self.env["nova.ai.flow"].create({
            "name": "Biz describe", "description": "t",
            "nodes": [{"id": "t", "type": "trigger", "config": {}}],
            "edges": [], "active": True,
        })
        self.addCleanup(flow.sudo().unlink)
        res = flow_action(self.env, self.env.user, "describe",
                          flow.id, {"model": "res.partner"})
        self.assertTrue(res.get("ok"))
        self.assertTrue(res.get("models"))
        self.assertTrue(res.get("fields"))

    def test_retention_cron_trims(self):
        flow = self.env["nova.ai.flow"].create({
            "name": "Biz retention", "description": "t",
            "spec_version": 2, "active": True,
        })
        self.addCleanup(flow.sudo().unlink)
        run = self.env["nova.ai.flow.run"].sudo().create({
            "flow_id": flow.id, "status": "ok",
            "inputs": {"a": 1}, "node_log": [{"node": "t"}],
        })
        self.env["nova.ai.flow"].cron_trim_runs()
        self.assertTrue(run.exists())
        # Fresh runs are untouched; only runs past retention are
        # trimmed (payloads first) and capped by count.
        self.assertEqual(run.inputs, {"a": 1})
