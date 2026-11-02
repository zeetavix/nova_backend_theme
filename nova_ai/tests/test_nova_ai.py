# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Nova AI tests: the tier engine, tool safety, and the chat loop.

The loop is exercised with a stubbed model (no network): the stub
answers through the same parsing the real dialects use, so the tool
execution, card queueing, repeat guard and wrap-up are all covered
offline.
"""

import json

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase, tagged
from odoo.addons.nova_ai.controllers.nova_ai import run_chat_turn
from odoo.addons.nova_ai.models.nova_ai_tools import execute_tool, run_confirm


@tagged("post_install", "-at_install")
class TestNovaAiTier(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Provider = self.env["nova.ai.provider"]
        self.Model = self.env["nova.ai.model"]
        self.provider = self.Provider.create({
            "name": "Unit Provider", "kind": "openai_compat",
            "base_url": "http://unit.invalid/v1",
        })

    def _model(self, **vals):
        return self.Model.create(dict(provider_id=self.provider.id,
                                      name="unit-model", **vals))

    def test_tier_no_tools_is_t1(self):
        model = self._model(supports_tools=False, context_length=100000,
                            supports_embeddings=True)
        self.assertEqual(model.tier, "t1")

    def test_tier_tools_usable_context_is_t2(self):
        model = self._model(supports_tools=True, context_length=16000)
        self.assertEqual(model.tier, "t2")

    def test_tier_tools_unadvertised_context_stays_t2(self):
        # The endpoint never said how big the window is: a short leash,
        # not a demotion to Basic.
        model = self._model(supports_tools=True, context_length=0)
        self.assertEqual(model.tier, "t2")
        self.assertEqual(self.Model.history_cap(model), 4)

    def test_tier_tools_big_context_embeddings_is_t3(self):
        model = self._model(supports_tools=True, context_length=64000,
                            supports_embeddings=True)
        self.assertEqual(model.tier, "t3")

    def test_tier_override_wins(self):
        model = self._model(supports_tools=False, tier_override="t3")
        self.assertEqual(model.tier, "t3")


@tagged("post_install", "-at_install")
class TestNovaAiToolSafety(TransactionCase):

    def test_denied_model_is_refused(self):
        with self.assertRaises(AccessError):
            execute_tool(self.env, "search_records",
                         {"model": "res.users"}, {})

    def test_config_model_is_refused(self):
        with self.assertRaises(AccessError):
            execute_tool(self.env, "read_record",
                         {"model": "ir.config_parameter", "res_id": 1}, {})

    def test_ssrf_private_host_is_refused(self):
        with self.assertRaises(AccessError):
            execute_tool(self.env, "fetch_url",
                         {"url": "http://127.0.0.1:8020/"}, {})

    def test_non_http_scheme_is_refused(self):
        with self.assertRaises(AccessError):
            execute_tool(self.env, "fetch_url",
                         {"url": "file:///etc/passwd"}, {})

    def test_confirm_card_then_execute(self):
        partner = self.env["res.partner"].create({"name": "Nova Unit Partner"})
        kind, payload, card = execute_tool(
            self.env, "log_note",
            {"model": "res.partner", "res_id": partner.id,
             "body": "Unit note."}, {})
        self.assertEqual(kind, "confirm")
        self.assertEqual(card["kind"], "log_note")
        run_confirm(self.env, card)
        self.assertTrue(partner.message_ids.filtered(
            lambda m: "Unit note." in (m.body or "")))

    def test_auto_apply_requires_t3(self):
        provider = self.env["nova.ai.provider"].create({
            "name": "Gate Provider", "kind": "ollama",
            "base_url": "http://unit.invalid",
        })
        model = self.env["nova.ai.model"].create({
            "provider_id": provider.id, "name": "gate-model",
            "supports_tools": True, "context_length": 16000,
        })
        with self.assertRaises(ValidationError):
            self.env["nova.ai.agent"].create({
                "name": "Gate Agent", "model_id": model.id,
                "auto_apply": True,
            })


class _StubChat:
    """Sequence of canned responses; records every request it served."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, model, messages, tools=None, extra=None):
        self.calls.append({"messages": list(messages), "tools": tools})
        if self.responses:
            return self.responses.pop(0)
        return {"choices": [{"message": {"content": "done"}}]}


def _text_tool_call(name, arguments):
    return {"choices": [{"message": {
        "content": json.dumps({"name": name, "arguments": arguments})}}]}


def _text_reply(text):
    return {"choices": [{"message": {"content": text}}]}


@tagged("post_install", "-at_install")
class TestNovaAiLoop(TransactionCase):

    def setUp(self):
        super().setUp()
        provider = self.env["nova.ai.provider"].create({
            "name": "Loop Provider", "kind": "openai_compat",
            "base_url": "http://unit.invalid/v1",
        })
        self.model = self.env["nova.ai.model"].create({
            "provider_id": provider.id, "name": "loop-model",
            "supports_tools": True, "context_length": 16000,
        })
        tools = self.env["nova.ai.tool"].search(
            [("code", "in", ("count_records", "log_note"))])
        self.agent = self.env["nova.ai.agent"].create({
            "name": "Loop Agent", "model_id": self.model.id,
            "tool_ids": [(6, 0, tools.ids)],
        })

    def _run(self, responses, text="hello"):
        stub = _StubChat(responses)
        orig = type(self.model)._chat

        def fake(record, messages, tools=None, extra=None):
            return stub(record, messages, tools=tools, extra=extra)

        type(self.model)._chat = fake
        try:
            session = self.env["nova.ai.session"].create(
                {"agent_id": self.agent.id})
            result = run_chat_turn(self.env, self.agent, session, text, {})
        finally:
            type(self.model)._chat = orig
        return result, stub, session

    def test_result_carries_key(self):
        result, stub, session = self._run([
            _text_tool_call("count_records", {"model": "res.partner"}),
            _text_reply("There are some partners."),
        ])
        self.assertIn("count_records",
                      [s.get("tool") for s in result["steps"]])
        self.assertTrue(result["reply"].strip())
        self.assertTrue(stub.calls)

    def test_tool_steps_carry_ms(self):
        result, stub, session = self._run([
            _text_tool_call("count_records", {"model": "res.partner"}),
            _text_reply("Counted."),
        ])
        timed = [s for s in result["steps"]
                 if s.get("tool") == "count_records"
                 and "note" not in s]
        self.assertTrue(timed)
        for step in timed:
            self.assertIsInstance(step.get("ms"), int)
            self.assertGreaterEqual(step["ms"], 0)

    def test_repeat_call_is_guarded(self):
        result, stub, session = self._run([
            _text_tool_call("count_records", {"model": "res.partner"}),
            _text_tool_call("count_records", {"model": "res.partner"}),
            _text_reply("There are some partners."),
        ])
        notes = [s for s in result["steps"] if s.get("note") == "repeat ignored"]
        self.assertTrue(notes)

    def test_write_queues_a_card_and_does_not_execute(self):
        partner = self.env["res.partner"].create({"name": "Card Target"})
        result, stub, session = self._run([
            _text_tool_call("log_note", {
                "model": "res.partner", "res_id": partner.id,
                "body": "Should stay pending."}),
            _text_reply("Queued for your approval."),
        ])
        self.assertEqual(len(result["cards"]), 1)
        self.assertTrue(session.pending)
        self.assertFalse(partner.message_ids.filtered(
            lambda m: "Should stay pending." in (m.body or "")))

    def test_empty_reply_gets_a_prose_wrapup(self):
        result, stub, session = self._run([
            _text_tool_call("count_records", {"model": "res.partner"}),
            _text_reply(""),           # nothing usable
            _text_reply("Final prose answer."),
        ])
        self.assertIn("Final prose answer.", result["reply"])

    def _run_as(self, agent, responses, text="hello"):
        stub = _StubChat(responses)
        orig = type(agent.model_id)._chat

        def fake(record, messages, tools=None, extra=None):
            return stub(record, messages, tools=tools, extra=extra)

        type(agent.model_id)._chat = fake
        try:
            session = self.env["nova.ai.session"].create(
                {"agent_id": agent.id})
            result = run_chat_turn(self.env, agent, session, text, {})
        finally:
            type(agent.model_id)._chat = orig
        return result, stub, session

    def _write_agent(self):
        tools = self.env["nova.ai.tool"].search(
            [("code", "in", ("set_field", "execute_method",
                              "log_note", "count_records",
                              "create_draft"))])
        return self.env["nova.ai.agent"].create({
            "name": "Repeat Agent", "model_id": self.model.id,
            "tool_ids": [(6, 0, tools.ids)],
        })

    def test_repeat_same_field_ignored(self):
        agent = self._write_agent()
        partner = self.env["res.partner"].create({"name": "Repeat Co"})
        result, stub, session = self._run_as(agent, [
            _text_tool_call("set_field", {
                "model": "res.partner", "res_id": partner.id,
                "field": "phone", "value": "111"}),
            # Same field, corrected value: still the same intent.
            _text_tool_call("set_field", {
                "model": "res.partner", "res_id": partner.id,
                "field": "phone", "value": "222"}),
            _text_reply("Queued."),
        ])
        self.assertEqual(len(result["cards"]), 1)
        self.assertTrue([s for s in result["steps"]
                         if s.get("note") == "repeat ignored"])

    def test_repeat_same_method_same_record_ignored(self):
        product = self.env["product.product"].search(
            [("sale_ok", "=", True)], limit=1)
        if not product:
            self.skipTest("No saleable product in this database.")
        agent = self._write_agent()
        partner = self.env["res.partner"].create({"name": "Repeat SO"})
        order = self.env["sale.order"].create({
            "partner_id": partner.id,
            "order_line": [(0, 0, {"product_id": product.id,
                                   "product_uom_qty": 1})],
        })
        result, stub, session = self._run_as(agent, [
            _text_tool_call("execute_method", {
                "model": "sale.order", "res_id": order.id,
                "method": "action_confirm"}),
            _text_tool_call("execute_method", {
                "model": "sale.order", "res_id": order.id,
                "method": "action_confirm"}),
            _text_reply("Queued."),
        ])
        self.assertEqual(len(result["cards"]), 1)
        self.assertTrue([s for s in result["steps"]
                         if s.get("note") == "repeat ignored"])

    def test_repeat_reworded_note_ignored(self):
        agent = self._write_agent()
        partner = self.env["res.partner"].create({"name": "Repeat Note"})
        result, stub, session = self._run_as(agent, [
            _text_tool_call("log_note", {
                "model": "res.partner", "res_id": partner.id,
                "body": "Call them back."}),
            _text_tool_call("log_note", {
                "model": "res.partner", "res_id": partner.id,
                "body": "Call them back!"}),
            _text_reply("Queued."),
        ])
        self.assertEqual(len(result["cards"]), 1)
        self.assertTrue([s for s in result["steps"]
                         if s.get("note") == "repeat ignored"])

    def test_repeat_distinct_reads_both_run(self):
        agent = self._write_agent()
        result, stub, session = self._run_as(agent, [
            _text_tool_call("count_records",
                            {"model": "res.partner"}),
            _text_tool_call("count_records",
                            {"model": "sale.order"}),
            _text_reply("Counted both."),
        ])
        ran = [s for s in result["steps"]
               if s.get("tool") == "count_records"
               and "note" not in s]
        self.assertEqual(len(ran), 2)

    def test_ask_ends_turn_with_question(self):
        agent = self._write_agent()
        self.env["res.partner"].create({"name": "Loop Twin"})
        self.env["res.partner"].create({"name": "Loop Twin"})
        target = self.env["res.partner"].create({"name": "Loop Target"})
        result, stub, session = self._run_as(agent, [
            _text_tool_call("set_field", {
                "model": "res.partner", "res_id": target.id,
                "field": "parent_id", "value": "Loop Twin"}),
        ])
        self.assertIn("Loop Twin", result["reply"])
        self.assertEqual(result["cards"], [])
        self.assertTrue([s for s in result["steps"]
                         if s.get("asked")])
        self.assertFalse(target.parent_id)

    def test_transient_failure_retried_then_runs(self):
        from unittest.mock import patch
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        agent = self._write_agent()
        self.env["nova.ai.tool"].search(
            [("code", "=", "count_records")]).max_retries = 1
        calls = []
        orig = ctrl.execute_tool

        def flaky(env, code, args, ctx):
            calls.append(code)
            if len(calls) == 1:
                raise RuntimeError("blip")
            return orig(env, code, args, ctx)

        with patch.object(ctrl, "execute_tool", flaky):
            result, stub, session = self._run_as(agent, [
                _text_tool_call("count_records",
                                {"model": "res.partner"}),
                _text_reply("Counted."),
            ])
        self.assertEqual(calls, ["count_records", "count_records"])
        self.assertTrue(
            [s for s in result["steps"]
             if s.get("tool") == "count_records" and "note" not in s])

    def test_refusal_never_retried(self):
        from unittest.mock import patch
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        agent = self._write_agent()
        self.env["nova.ai.tool"].search(
            [("code", "=", "count_records")]).max_retries = 3
        calls = []
        orig = ctrl.execute_tool

        def refusing(env, code, args, ctx):
            calls.append(code)
            raise AccessError("No.")

        with patch.object(ctrl, "execute_tool", refusing):
            result, stub, session = self._run_as(agent, [
                _text_tool_call("count_records",
                                {"model": "res.partner"}),
                _text_reply("Failed."),
            ])
        self.assertEqual(calls, ["count_records"])
        self.assertTrue(
            [s for s in result["steps"] if s.get("error")])

    def test_newer_field_card_supersedes(self):
        agent = self._write_agent()
        partner = self.env["res.partner"].create({"name": "Supersede Co"})
        stub1 = _StubChat([
            _text_tool_call("set_field", {
                "model": "res.partner", "res_id": partner.id,
                "field": "phone", "value": "111"}),
            _text_reply("First."),
        ])
        stub2 = _StubChat([
            _text_tool_call("set_field", {
                "model": "res.partner", "res_id": partner.id,
                "field": "phone", "value": "222"}),
            _text_reply("Second."),
        ])
        orig = type(agent.model_id)._chat

        def fake1(record, messages, tools=None, extra=None):
            return stub1(record, messages, tools=tools, extra=extra)

        def fake2(record, messages, tools=None, extra=None):
            return stub2(record, messages, tools=tools, extra=extra)

        type(agent.model_id)._chat = fake1
        try:
            session = self.env["nova.ai.session"].create(
                {"agent_id": agent.id})
            run_chat_turn(self.env, agent, session, "set phone 111", {})
            self.assertEqual(len(session.pending or []), 1)
            type(agent.model_id)._chat = fake2
            result = run_chat_turn(
                self.env, agent, session, "set phone 222", {})
        finally:
            type(agent.model_id)._chat = orig
        pending = session.pending or []
        self.assertEqual(len(pending), 1)
        self.assertTrue(pending[0].get("supersedes"))
        self.assertIn("222", pending[0]["summary"])
        self.assertTrue(
            [s for s in result["steps"] if "Replaces" in s.get("note", "")])

    def test_tool_timeout_retry_defaults(self):
        # code is a closed Selection: probe defaults on a real tool.
        tool = self.env["nova.ai.tool"].search(
            [("code", "=", "search_knowledge")], limit=1)
        self.assertEqual(tool.timeout_s, 60)
        self.assertEqual(tool.max_retries, 0)

    def test_cancel_flag_reads_fresh(self):
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        agent = self.env["nova.ai.agent"].with_context(active_test=False).search([], limit=1)
        session = self.env["nova.ai.session"].create(
            {"agent_id": agent.id})
        self.assertFalse(ctrl._turn_cancelled(session))
        session.write({"cancel_flag": True})
        self.assertTrue(ctrl._turn_cancelled(session))

    def test_cancelled_turn_runs_nothing(self):
        agent = self._write_agent()
        session = self.env["nova.ai.session"].create(
            {"agent_id": agent.id})
        state = {"calls": 0}
        orig = type(agent.model_id)._chat

        def fake(record, messages, tools=None, extra=None):
            session.write({"cancel_flag": True})
            state["calls"] += 1
            return _text_tool_call(
                "count_records", {"model": "res.partner"})

        type(agent.model_id)._chat = fake
        try:
            result = run_chat_turn(
                self.env, agent, session, "count them", {})
        finally:
            type(agent.model_id)._chat = orig
        self.assertEqual(result["reply"], "Stopped.")
        self.assertTrue(
            [s for s in result["steps"]
             if s.get("note") == "stopped by user"])
        self.assertFalse(
            [s for s in result["steps"]
             if s.get("tool") == "count_records" and "note" not in s])

    def test_validate_args_missing_required(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        schema = {"type": "object",
                  "properties": {"model": {"type": "string"}},
                  "required": ["model"]}
        with self.assertRaises(AccessError) as raised:
            tools._validate_args(self.env, "demo", schema, {})
        self.assertIn("model", str(raised.exception))

    def test_validate_args_coerces_and_rejects(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        schema = {"type": "object",
                  "properties": {
                      "res_id": {"type": "integer"},
                      "extra": {"type": "string"}},
                  "required": []}
        out = tools._validate_args(
            self.env, "demo", schema,
            {"res_id": "7", "unknown": 1})
        self.assertEqual(out["res_id"], 7)
        self.assertEqual(out["unknown"], 1)
        with self.assertRaises(AccessError):
            tools._validate_args(
                self.env, "demo", schema, {"res_id": "seven"})

    def _auto_agent(self):
        model = self.env["nova.ai.model"].create({
            "provider_id": self.model.provider_id.id,
            "name": "auto-model", "supports_tools": True,
            "context_length": 64000, "supports_embeddings": True,
        })
        self.assertEqual(model.tier, "t3")
        tools = self.env["nova.ai.tool"].search(
            [("code", "in", ("set_field", "unlink_record"))])
        return self.env["nova.ai.agent"].create({
            "name": "Auto Agent", "model_id": model.id,
            "tool_ids": [(6, 0, tools.ids)], "auto_apply": True,
        })

    def _auto_run(self, agent, responses, text="do it"):
        stub = _StubChat(responses)
        orig = type(agent.model_id)._chat

        def fake(record, messages, tools=None, extra=None):
            return stub(record, messages, tools=tools, extra=extra)

        type(agent.model_id)._chat = fake
        try:
            session = self.env["nova.ai.session"].create(
                {"agent_id": agent.id})
            result = run_chat_turn(
                self.env, agent, session, text, {})
        finally:
            type(agent.model_id)._chat = orig
        return result, stub, session

    def test_auto_deny_without_auto_flag(self):
        # Full auto posture on the agent, but the tool is only
        # write_kind=confirm: the write must queue, not apply.
        agent = self._auto_agent()
        partner = self.env["res.partner"].create({"name": "Auto Deny"})
        result, stub, session = self._auto_run(agent, [
            _text_tool_call("set_field", {
                "model": "res.partner", "res_id": partner.id,
                "field": "phone", "value": "111"}),
            _text_reply("Queued."),
        ])
        self.assertEqual(len(result["cards"]), 1)
        self.assertFalse(
            [s for s in result["steps"] if s.get("applied")])
        self.assertNotEqual(partner.phone, "111")

    def test_auto_apply_runs_when_flagged(self):
        agent = self._auto_agent()
        self.env["nova.ai.tool"].search(
            [("code", "=", "set_field")]).write_kind = "auto"
        partner = self.env["res.partner"].create({"name": "Auto Go"})
        result, stub, session = self._auto_run(agent, [
            _text_tool_call("set_field", {
                "model": "res.partner", "res_id": partner.id,
                "field": "phone", "value": "222"}),
            _text_reply("Applied."),
        ])
        self.assertEqual(result["cards"], [])
        self.assertTrue(
            [s for s in result["steps"] if s.get("applied")])
        self.assertEqual(partner.phone, "222")

    def test_never_auto_origins_always_queue(self):
        # Destructive origins queue even under full auto posture.
        agent = self._auto_agent()
        self.env["nova.ai.tool"].search(
            [("code", "=", "unlink_record")]).write_kind = "auto"
        partner = self.env["res.partner"].create({"name": "Auto Never"})
        result, stub, session = self._auto_run(agent, [
            _text_tool_call("unlink_record", {
                "model": "res.partner", "res_id": partner.id}),
            _text_reply("Queued."),
        ])
        self.assertEqual(len(result["cards"]), 1)
        self.assertTrue(
            self.env["res.partner"].browse(partner.id).exists())


@tagged("post_install", "-at_install")
class TestNovaAiScheduleMail(TransactionCase):

    def test_mail_results_queues_email(self):
        provider = self.env["nova.ai.provider"].create({
            "name": "Mail Provider", "kind": "openai_compat",
            "base_url": "http://unit.invalid/v1",
        })
        model = self.env["nova.ai.model"].create({
            "provider_id": provider.id, "name": "mail-model",
            "supports_tools": False, "context_length": 16000,
        })
        agent = self.env["nova.ai.agent"].create({
            "name": "Mail Agent", "model_id": model.id,
        })
        self.env.user.write({"email": "nova-unit@example.com"})
        schedule = self.env["nova.ai.schedule"].create({
            "name": "Unit Digest", "agent_id": agent.id,
            "prompt": "Say hello.", "mail_results": True,
        })
        stub = _StubChat([_text_reply("Morning numbers are fine.")])
        orig = type(model)._chat

        def fake(record, messages, tools=None, extra=None):
            return stub(record, messages, tools=tools, extra=extra)

        type(model)._chat = fake
        try:
            schedule._run_one()
        finally:
            type(model)._chat = orig
        self.assertTrue(schedule.last_ok)
        mail = self.env["mail.mail"].search([
            ("email_to", "ilike", "nova-unit@example.com"),
            ("subject", "ilike", "Unit Digest"),
        ], limit=1)
        self.assertTrue(mail)


@tagged("post_install", "-at_install")
class TestNovaAiRegenerate(TransactionCase):

    def test_rewind_last_turn_returns_user_text(self):
        provider = self.env["nova.ai.provider"].create({
            "name": "Regen Provider", "kind": "openai_compat",
            "base_url": "http://unit.invalid/v1",
        })
        model = self.env["nova.ai.model"].create({
            "provider_id": provider.id, "name": "regen-model",
            "supports_tools": False, "context_length": 16000,
        })
        agent = self.env["nova.ai.agent"].create({
            "name": "Regen Agent", "model_id": model.id,
        })
        stub = _StubChat([_text_reply("First answer."),
                          _text_reply("Second answer.")])
        orig = type(model)._chat

        def fake(record, messages, tools=None, extra=None):
            return stub(record, messages, tools=tools, extra=extra)

        type(model)._chat = fake
        try:
            session = self.env["nova.ai.session"].create(
                {"agent_id": agent.id})
            run_chat_turn(self.env, agent, session, "Say the word.", {})
            self.assertEqual(len(session.message_ids), 2)
            text = session.rewind_last_turn()
            self.assertEqual(text, "Say the word.")
            # The whole turn is rewound, so the rerun stores the
            # question exactly once.
            self.assertEqual(len(session.message_ids), 0)
            run_chat_turn(self.env, agent, session, text, {})
            roles = session.message_ids.sorted("id").mapped("role")
            self.assertEqual(roles, ["user", "assistant"])
            self.assertIn("Second answer.",
                          session.message_ids.sorted("id")[-1].content)
        finally:
            type(model)._chat = orig

    def test_pop_with_no_assistant_reply_returns_empty(self):
        provider = self.env["nova.ai.provider"].create({
            "name": "Regen Provider 2", "kind": "openai_compat",
            "base_url": "http://unit.invalid/v1",
        })
        model = self.env["nova.ai.model"].create({
            "provider_id": provider.id, "name": "regen-model-2",
            "supports_tools": False, "context_length": 16000,
        })
        agent = self.env["nova.ai.agent"].create({
            "name": "Regen Agent 2", "model_id": model.id,
        })
        session = self.env["nova.ai.session"].create(
            {"agent_id": agent.id})
        self.assertEqual(session.rewind_last_turn(), "")

    def test_rewind_clears_pending_cards(self):
        provider = self.env["nova.ai.provider"].create({
            "name": "Regen Provider 3", "kind": "openai_compat",
            "base_url": "http://unit.invalid/v1",
        })
        model = self.env["nova.ai.model"].create({
            "provider_id": provider.id, "name": "regen-model-3",
            "supports_tools": False, "context_length": 16000,
        })
        agent = self.env["nova.ai.agent"].create({
            "name": "Regen Agent 3", "model_id": model.id,
        })
        session = self.env["nova.ai.session"].create(
            {"agent_id": agent.id})
        Message = self.env["nova.ai.message"]
        Message.add(session, "user", content="Queue something.")
        Message.add(session, "assistant", content="Queued.")
        session.write({"pending": [{
            "token": "stale:1", "kind": "log_note",
            "title": "Stale", "summary": "Stale card",
            "payload": {}}]})
        text = session.rewind_last_turn()
        self.assertEqual(text, "Queue something.")
        self.assertFalse(session.pending)

    def test_manual_title_wins(self):
        agent = self.env["nova.ai.agent"].with_context(active_test=False).search([], limit=1)
        session = self.env["nova.ai.session"].create(
            {"agent_id": agent.id})
        self.env["nova.ai.message"].add(
            session, "user", content="What is up?")
        self.assertIn("What is up?", session.title)
        session.write({"title_manual": "My label"})
        self.assertEqual(session.title, "My label")

    def test_kill_switch(self):
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        self.assertFalse(ctrl._killed(self.env))
        self.env["ir.config_parameter"].sudo().set_str(
            "nova_ai.disabled", "1")
        try:
            self.assertTrue(ctrl._killed(self.env))
        finally:
            self.env["ir.config_parameter"].sudo().set_str(
                "nova_ai.disabled", "")

    def test_killed_schedule_marks_paused(self):
        agent = self.env["nova.ai.agent"].with_context(active_test=False).search([], limit=1)
        schedule = self.env["nova.ai.schedule"].create({
            "name": "Paused Schedule", "agent_id": agent.id,
            "prompt": "Unit prompt.",
        })
        self.env["ir.config_parameter"].sudo().set_str(
            "nova_ai.disabled", "1")
        try:
            schedule._run_one()
        finally:
            self.env["ir.config_parameter"].sudo().set_str(
                "nova_ai.disabled", "")
        self.assertFalse(schedule.last_ok)
        self.assertIn("paused", schedule.last_error.lower())

    def test_schedule_run_completes(self):
        from unittest.mock import patch
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        # Schedules need a ready agent (active + model): the seed
        # agent ships inactive until an admin connects a provider.
        provider = self.env["nova.ai.provider"].create({
            "name": "Schedule Provider", "kind": "openai_compat",
            "base_url": "http://unit.invalid/v1",
        })
        model = self.env["nova.ai.model"].create({
            "provider_id": provider.id, "name": "schedule-model",
            "supports_tools": False, "context_length": 16000,
        })
        agent = self.env["nova.ai.agent"].create({
            "name": "Schedule Agent", "model_id": model.id,
        })
        schedule = self.env["nova.ai.schedule"].create({
            "name": "Unit Schedule", "agent_id": agent.id,
            "prompt": "Unit prompt.",
        })
        with patch.object(
                ctrl, "run_chat_turn",
                return_value={"reply": "Unit done.", "cards": []}):
            schedule._run_one()
        self.assertTrue(schedule.last_ok)
        self.assertTrue(
            self.env["nova.ai.session"].search(
                [("agent_id", "=", agent.id)], limit=1))

    def test_schedule_async_skips_overlap(self):
        from odoo.addons.nova_ai.models import nova_ai_schedule as mod
        agent = self.env["nova.ai.agent"].with_context(active_test=False).search([], limit=1)
        schedule = self.env["nova.ai.schedule"].create({
            "name": "Overlap Schedule", "agent_id": agent.id,
            "prompt": "Unit prompt.",
        })
        mod._RUNNING.add(schedule.id)
        try:
            self.assertFalse(schedule._run_async())
        finally:
            mod._RUNNING.discard(schedule.id)

    def test_render_vars(self):
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        partner = self.env["res.partner"].create({"name": "Vars Co"})
        out = ctrl._render_vars(
            self.env, "Brief {display_name} ({model}#{res_id})",
            "res.partner", partner.id)
        self.assertIn("Vars Co", out)
        self.assertIn("res.partner#%d" % partner.id, out)
        self.assertEqual(
            ctrl._render_vars(self.env, "Hi {unknown}", "", 0),
            "Hi {unknown}")

    def test_relative_dates_coerce(self):
        from datetime import date, timedelta
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        field = self.env["account.move"]._fields["invoice_date"]
        value, _display = tools._coerce_field_value(
            self.env, field, "tomorrow")
        self.assertEqual(value, (date.today() + timedelta(days=1)
                                 ).isoformat())
        value, _display = tools._coerce_field_value(
            self.env, field, "2026-01-31")
        self.assertEqual(value, "2026-01-31")

    def test_create_draft_flags_missing_required(self):
        # A draft with only genuinely-missing fields warns; a draft
        # Odoo can complete itself (sale.order needs only a partner)
        # queues cleanly and approves.
        partner = self.env["res.partner"].create(
            {"name": "Required Check Co"})
        kind, payload, card = execute_tool(
            self.env, "create_draft",
            {"model": "sale.order",
             "values": {"partner_id": partner.id}}, {})
        self.assertEqual(kind, "confirm")
        self.assertFalse(
            [line for line in card.get("preview") or []
             if "Still missing required" in line])
        result = run_confirm(self.env, card)
        order = self.env["sale.order"].browse(result["res_id"])
        self.assertTrue(order.exists())
        self.assertEqual(order.partner_id, partner)

    def test_safe_domain_rejects_bad_operator(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        out = tools._safe_domain(
            self.env, [["name", "ilike", "x"], "|",
                       ["id", "in", [1, 2]]])
        self.assertEqual(len(out), 3)
        with self.assertRaises(AccessError):
            tools._safe_domain(
                self.env, [["name", "regex", "x"]])
        with self.assertRaises(AccessError):
            tools._safe_domain(self.env, [["name"]])

    def test_safe_domain_normalizes_model_shapes(self):
        # Models keep writing domains in slightly wrong shapes. The old
        # validator rejected every one of them with the same dead-end
        # error, so a turn could burn 8 calls and still fail. These are
        # the shapes real models emit; all must come out as clean leaves.
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        # flat triple -> wrapped in a list
        self.assertEqual(
            tools._safe_domain(self.env, ["name", "ilike", "Acme"]),
            [["name", "ilike", "Acme"]])
        # whole domain as a JSON string
        self.assertEqual(
            tools._safe_domain(self.env, '[["name", "ilike", "Acme"]]'),
            [["name", "ilike", "Acme"]])
        # one leaf as an object
        self.assertEqual(
            tools._safe_domain(self.env, {"field": "name",
                                          "operator": "ilike",
                                          "value": "Acme"}),
            [["name", "ilike", "Acme"]])
        # a dict leaf inside a list
        self.assertEqual(
            tools._safe_domain(self.env, [{"field": "name",
                                           "operator": "=",
                                           "value": "Acme"}]),
            [["name", "=", "Acme"]])
        # operator spellings
        self.assertEqual(
            tools._safe_domain(self.env, [["name", "==", "Acme"]])[0][1], "=")
        self.assertEqual(
            tools._safe_domain(self.env, [["name", "<>", "Acme"]])[0][1], "!=")
        self.assertEqual(
            tools._safe_domain(self.env, [["name", "contains", "Acme"]])[0][1],
            "ilike")
        # "in" with a comma string
        self.assertEqual(
            tools._safe_domain(self.env, [["id", "in", "1, 2, 3"]])[0][2],
            ["1", "2", "3"])
        # tuples (python) still work
        self.assertEqual(
            tools._safe_domain(self.env, [("name", "=", "Acme")]),
            [["name", "=", "Acme"]])
        # a leaf wrapped one level too deep
        self.assertEqual(
            tools._safe_domain(self.env, [[["name", "ilike", "Acme"]]]),
            [["name", "ilike", "Acme"]])
        # a stray fourth element is dropped, first three kept
        self.assertEqual(
            tools._safe_domain(self.env, [["name", "ilike", "Acme", "x"]]),
            [["name", "ilike", "Acme"]])
        # an empty operator slot means "=" when field and value are there
        self.assertEqual(
            tools._safe_domain(self.env, [["name", "", "Acme"]]),
            [["name", "=", "Acme"]])

    def test_safe_domain_drops_unfilled_placeholder_leaf(self):
        # The model sometimes emits a half-built leaf: [None, '=', None].
        # Mixed with real leaves it is dropped; alone it would silently
        # become match-all (the whole table), so it refuses instead.
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        with self.assertRaises(AccessError):
            tools._safe_domain(self.env, [[None, "=", None]])
        self.assertEqual(
            tools._safe_domain(
                self.env, [[None, "=", None], ["name", "ilike", "Acme"]]),
            [["name", "ilike", "Acme"]])

    def test_safe_domain_still_refuses_unreadable_input(self):
        # Repair must never widen a read: a domain we genuinely cannot
        # read still fails, and it fails with the shape we wanted.
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        with self.assertRaises(AccessError):
            tools._safe_domain(self.env, "name ilike Acme")
        with self.assertRaises(AccessError):
            tools._safe_domain(self.env, [["name", "regex", "x"]])
        with self.assertRaises(AccessError):
            tools._safe_domain(self.env, [["name"]])
        try:
            tools._safe_domain(self.env, [["name"]])
        except AccessError as err:
            self.assertIn("[field, operator, value]", str(err))

    def test_sensitive_field_list(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        self.assertIn("salary", tools._SENSITIVE_FIELDS)
        self.assertIn("wage", tools._SENSITIVE_FIELDS)

    def test_record_context_fenced(self):
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        partner = self.env["res.partner"].create({"name": "Fence Co"})
        out = ctrl._record_context(
            self.env, None,
            {"model": "res.partner", "res_id": partner.id})
        self.assertTrue(out.startswith("<<<record"))
        self.assertTrue(out.endswith(">>>"))
        self.assertIn("Fence Co", out)

    def test_failed_turn_keeps_queued_cards(self):
        from odoo.addons.nova_ai.controllers.nova_ai import (
            NovaAiProviderError)
        provider = self.env["nova.ai.provider"].create({
            "name": "Fail Provider", "kind": "openai_compat",
            "base_url": "http://unit.invalid/v1",
        })
        model = self.env["nova.ai.model"].create({
            "provider_id": provider.id, "name": "fail-model",
            "supports_tools": True, "context_length": 16000,
        })
        tools = self.env["nova.ai.tool"].search(
            [("code", "=", "log_note")])
        agent = self.env["nova.ai.agent"].create({
            "name": "Fail Agent", "model_id": model.id,
            "tool_ids": [(6, 0, tools.ids)],
        })
        partner = self.env["res.partner"].create({"name": "Fail Keep"})
        calls = []

        def fake(record, messages, tools=None, extra=None):
            calls.append(1)
            if len(calls) == 1:
                return _text_tool_call("log_note", {
                    "model": "res.partner", "res_id": partner.id,
                    "body": "Keep me."})
            raise RuntimeError("provider down")

        orig = type(agent.model_id)._chat
        type(agent.model_id)._chat = fake
        try:
            session = self.env["nova.ai.session"].create(
                {"agent_id": agent.id})
            # Plain try/except, NOT assertRaises: Odoo's assertRaises
            # rolls its savepoint back on the expected exception,
            # which would wipe the very write under test.
            raised = False
            try:
                run_chat_turn(
                    self.env, agent, session, "note it", {})
            except NovaAiProviderError:
                raised = True
            self.assertTrue(raised)
        finally:
            type(agent.model_id)._chat = orig
        pending = session.pending or []
        self.assertEqual(len(pending), 1)
        self.assertIn("Keep me.", pending[0]["summary"])


@tagged("post_install", "-at_install")
class TestNovaAiVoice(TransactionCase):

    def _wav(self, seconds=1):
        import io
        import math
        import struct
        import wave
        out = io.BytesIO()
        frames = b"".join(
            struct.pack("<h", int(10000 * math.sin(
                2 * math.pi * 440 * n / 16000)))
            for n in range(16000 * seconds))
        with wave.open(out, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(frames)
        return out.getvalue()

    def test_empty_recording_refused(self):
        from odoo.addons.nova_ai.models import nova_ai_voice as voice
        with self.assertRaises(AccessError):
            voice.transcribe_audio(self.env, b"", "audio/webm")

    def test_oversize_recording_refused(self):
        from odoo.addons.nova_ai.models import nova_ai_voice as voice
        with self.assertRaises(AccessError):
            voice.transcribe_audio(
                self.env, b"\x00" * (6 * 1024 * 1024), "audio/webm")

    def _agent_on(self, provider, name="Voice Agent"):
        model = self.env["nova.ai.model"].create({
            "provider_id": provider.id, "name": "voice-model",
            "supports_tools": False, "context_length": 16000,
        })
        return self.env["nova.ai.agent"].create({
            "name": name, "model_id": model.id,
        })

    def test_transcribe_provider_first(self):
        from odoo.addons.nova_ai.models import nova_ai_voice as voice
        provider = self.env["nova.ai.provider"].create({
            "name": "Voice Provider", "kind": "openai_compat",
            "base_url": "http://unit.invalid/v1",
        })
        agent = self._agent_on(provider)
        seen = {}
        orig = type(provider).transcribe_via_provider

        def fake(self_, model_name, wav):
            seen["model"] = model_name
            seen["bytes"] = len(wav)
            return "hello hosted"

        type(provider).transcribe_via_provider = fake
        try:
            out = voice.transcribe_audio(
                self.env, self._wav(), "audio/wav", agent=agent)
        finally:
            type(provider).transcribe_via_provider = orig
        self.assertEqual(out["text"], "hello hosted")
        self.assertTrue(seen["bytes"] > 1000)

    def test_voice_busy_refuses_instead_of_piling_up(self):
        # A second concurrent voice job gets an honest busy answer
        # instead of stacking another model load onto the worker.
        import threading
        from odoo.addons.nova_ai.models import nova_ai_voice_local as local
        held = threading.Event()
        release = threading.Event()

        def holder():
            local._lock.acquire()
            held.set()
            release.wait(15)
            try:
                local._lock.release()
            except Exception:
                pass

        worker = threading.Thread(target=holder, daemon=True)
        worker.start()
        self.assertTrue(held.wait(15))
        try:
            with self.assertRaises(RuntimeError):
                local.transcribe_wav(self.env, self._wav())
        finally:
            release.set()
            worker.join(15)

    def _enable_local_voice(self):
        """Force server voice on for one test, restoring after.
        The live DB intentionally runs with it off."""
        params = self.env["ir.config_parameter"].sudo()
        olds = {}
        for key, value in (("nova_ai.local_stt", "base.en"),
                           ("nova_ai.local_tts", "1")):
            rec = params.search([("key", "=", key)], limit=1)
            olds[key] = rec.value if rec else None
            params.set_str(key, value)

        def restore():
            for key, old in olds.items():
                if old is None:
                    params.search(
                        [("key", "=", key)], limit=1).unlink()
                else:
                    params.set_str(key, old)
        return restore

    def test_transcribe_no_agent_uses_local(self):
        # No agent, no endpoint: the in-process engine hears anyway
        # (a sine wave carries no speech, so text is empty — the
        # shape is what matters, not the words).
        from odoo.addons.nova_ai.models import nova_ai_voice as voice
        restore = self._enable_local_voice()
        try:
            out = voice.transcribe_audio(
                self.env, self._wav(), "audio/wav", agent=None)
        finally:
            restore()
        self.assertIn("text", out)
        self.assertGreater(out["duration"], 0)

    def _swap_param(self, key, value):
        """Set a config param, returning a restore closure.

        Restores the exact prior state (value or absence): writing ""
        would leave selection fields holding an invalid value.
        """
        params = self.env["ir.config_parameter"].sudo()
        rec = params.search([("key", "=", key)], limit=1)
        old = rec.value if rec else None
        params.set_str(key, value)

        def restore():
            if old is None:
                params.search(
                    [("key", "=", key)], limit=1).unlink()
            else:
                params.set_str(key, old)
        return restore

    def test_transcribe_refuses_when_local_off(self):
        from odoo.addons.nova_ai.models import nova_ai_voice as voice
        restore = self._swap_param("nova_ai.local_stt", "0")
        try:
            with self.assertRaises(AccessError):
                voice.transcribe_audio(
                    self.env, self._wav(), "audio/wav", agent=None)
        finally:
            restore()

    def test_speak_provider_first_truncates(self):
        from odoo.addons.nova_ai.models import nova_ai_voice as voice
        provider = self.env["nova.ai.provider"].create({
            "name": "Voice Provider 2", "kind": "openai_compat",
            "base_url": "http://unit.invalid/v1",
        })
        agent = self._agent_on(provider)
        seen = {}
        orig = type(provider).speak_via_provider

        def fake(self_, model_name, text):
            seen["text"] = text
            return b"RIFF" + b"\x00" * 100, "audio/wav"

        type(provider).speak_via_provider = fake
        try:
            out, mime = voice.speak_text(
                self.env, "x" * 1000, agent=agent)
        finally:
            type(provider).speak_via_provider = orig
        self.assertEqual(len(seen["text"]), 600)
        self.assertTrue(out[:4] == b"RIFF")
        self.assertTrue(mime.startswith("audio/"))

    def test_speak_no_agent_refuses(self):
        # No agent and local speech switched off: nothing left to
        # talk with, so it refuses instead of staying silent.
        from odoo.addons.nova_ai.models import nova_ai_voice as voice
        restore = self._swap_param("nova_ai.local_tts", "0")
        try:
            with self.assertRaises(AccessError):
                voice.speak_text(self.env, "hello", agent=None)
        finally:
            restore()

    def test_local_engines_report_available(self):
        from odoo.addons.nova_ai.models import nova_ai_voice as voice
        from odoo.addons.nova_ai.models import nova_ai_voice_local as local
        restore = self._enable_local_voice()
        try:
            self.assertTrue(local.stt_enabled(self.env))
            self.assertTrue(local.tts_enabled(self.env))
        finally:
            restore()

    def test_voice_url_builder(self):
        from odoo.addons.nova_ai.models import nova_ai_voice_local as local
        for name in ("en_US-lessac-medium", "en_US-amy-medium",
                     "en_US-ryan-medium", "en_GB-alan-medium"):
            url = local.voice_url(name)
            self.assertIn(name.split("-")[0], url)
            self.assertTrue(url.endswith(name))
        with self.assertRaises(RuntimeError):
            local.voice_url("not-a-voice")
        with self.assertRaises(RuntimeError):
            local.voice_url("")

    def test_prepare_voice_skipped_when_off(self):
        from odoo.addons.nova_ai.models import nova_ai_voice_local as local
        restore_stt = self._swap_param("nova_ai.local_stt", "0")
        restore_tts = self._swap_param("nova_ai.local_tts", "0")
        try:
            report = local.prepare_voice(self.env)
        finally:
            restore_tts()
            restore_stt()
        self.assertFalse(report["stt"]["ok"])
        self.assertFalse(report["tts"]["ok"])

    def test_settings_defaults_are_off(self):
        # Off by default: server voice is an explicit admin choice,
        # never a surprise install side effect. Browser voice (free)
        # needs no setting at all. Hermetic: restore admin values.
        params = self.env["ir.config_parameter"].sudo()
        olds = {}
        for key in ("nova_ai.local_stt", "nova_ai.local_tts"):
            rec = params.search([("key", "=", key)], limit=1)
            olds[key] = rec.value if rec else None
            rec.unlink()
        try:
            settings = self.env["res.config.settings"].create({})
            self.assertEqual(settings.nova_local_stt, "0")
            self.assertFalse(settings.nova_local_tts)
            self.assertIn("lessac", settings.nova_tts_voice)
            self.assertTrue(settings.nova_voice_status)
        finally:
            for key, old in olds.items():
                if old is None:
                    params.search(
                        [("key", "=", key)], limit=1).unlink()
                else:
                    params.set_str(key, old)

    def test_requirements_cover_voice(self):
        import os
        path = os.path.join(
            os.path.dirname(__file__), "..", "requirements.txt")
        with open(os.path.normpath(path), encoding="utf-8") as handle:
            text = handle.read()
        for dep in ("faster-whisper", "piper-tts", "onnxruntime",
                    "reportlab", "Pillow", "requests"):
            self.assertIn(dep, text)

    def test_voice_status_reports_provider_audio(self):
        from odoo.addons.nova_ai.models import nova_ai_voice as voice
        hosted = self.env["nova.ai.provider"].create({
            "name": "Voice Provider 3", "kind": "openai_compat",
            "base_url": "http://unit.invalid/v1",
        })
        agent = self._agent_on(hosted)
        status = voice.voice_status(self.env, agent=agent)
        self.assertTrue(status["providerAudio"])
        local = self.env["nova.ai.provider"].create({
            "name": "Voice Local", "kind": "ollama",
            "base_url": "http://unit.invalid",
        })
        agent2 = self._agent_on(local, "Voice Agent Local")
        status2 = voice.voice_status(self.env, agent=agent2)
        self.assertFalse(status2["providerAudio"])
        # Even with the shelf default off, enabling locally keeps the
        # mic alive with zero configuration beyond the switch.
        restore = self._enable_local_voice()
        try:
            status3 = voice.voice_status(self.env, agent=agent2)
        finally:
            restore()
        self.assertTrue(status3["localStt"])
        self.assertTrue(status3["localTts"])
        self.assertTrue(status3["available"])


@tagged("post_install", "-at_install")
class TestNovaAiProviderCapabilities(TransactionCase):

    def setUp(self):
        super().setUp()
        self.provider = self.env["nova.ai.provider"].create({
            "name": "Cap Provider", "kind": "openai_compat",
            "base_url": "http://unit.invalid/v1",
            "api_key": "unit-key",
        })
        self.model = self.env["nova.ai.model"].create({
            "provider_id": self.provider.id, "name": "cap-model",
            "supports_tools": True, "context_length": 16000,
        })

    def test_api_key_sealed_at_rest(self):
        from odoo.addons.nova_ai.models import nova_ai_provider as mod
        stored = self.provider.read(["api_key"])[0]["api_key"]
        self.assertTrue(stored.startswith("enc$"))
        self.assertNotIn("unit-key", stored)
        self.assertEqual(mod._open(self.env, stored), "unit-key")

    def test_api_key_legacy_passthrough(self):
        from odoo.addons.nova_ai.models import nova_ai_provider as mod
        self.assertEqual(
            mod._open(self.env, "plain-legacy"), "plain-legacy")
        self.assertEqual(mod._open(self.env, ""), "")

    def test_auth_headers_use_opened_key(self):
        headers = self.provider._auth_headers()
        self.assertEqual(
            headers.get("Authorization"), "Bearer unit-key")

    def test_digitize_refuses_oversize(self):
        import base64
        from unittest.mock import patch
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        partner = self.env["res.partner"].create({"name": "Big Co"})
        att = self.env["ir.attachment"].create({
            "name": "big.pdf", "mimetype": "application/pdf",
            "res_model": "res.partner", "res_id": partner.id,
            "raw": base64.b64encode(b"%PDF-1.4 " + b"0" * 100)})
        with patch.object(tools, "_DIGITIZE_MAX_BYTES", 10):
            with self.assertRaises(AccessError):
                tools.tool_digitize_bill(
                    self.env, {"attachment_id": att.id}, {})

    def test_digitize_refuses_many_pages(self):
        import base64
        import io
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        try:
            from pypdf import PdfWriter
        except ImportError:
            self.skipTest("pypdf missing")
        buf = io.BytesIO()
        writer = PdfWriter()
        for _ in range(tools._DIGITIZE_MAX_PAGES + 1):
            writer.add_blank_page(612, 792)
        writer.write(buf)
        partner = self.env["res.partner"].create({"name": "Long Co"})
        att = self.env["ir.attachment"].create({
            "name": "long.pdf", "mimetype": "application/pdf",
            "res_model": "res.partner", "res_id": partner.id,
            "raw": base64.b64encode(buf.getvalue())})
        with self.assertRaises(AccessError):
            tools.tool_digitize_bill(
                self.env, {"attachment_id": att.id}, {})

    def _fake_post(self, status=200, payload=None, content=b"",
                   ctype="application/json"):
        import json as _json

        class FakeResp:
            status_code = status
            headers = {"Content-Type": ctype}

            def json(self):
                if isinstance(payload, dict):
                    return payload
                return _json.loads((payload or b"{}").decode())

            @property
            def content(self):
                return content

            def raise_for_status(self):
                if self.status_code >= 400:
                    import requests as _rq
                    raise _rq.HTTPError("HTTP %s" % self.status_code)

        def fake(url, **kwargs):
            fake.last = {"url": url, "kwargs": kwargs}
            return FakeResp()

        return fake

    def _patch_post(self, fake):
        from odoo.addons.nova_ai.models import nova_ai_provider as mod
        orig = mod.requests.post
        mod.requests.post = fake
        return mod, orig

    def test_stt_hosted_success(self):
        from odoo.addons.nova_ai.models import nova_ai_provider as mod
        fake = self._fake_post(payload={"text": "hello hosted"})
        mod, orig = self._patch_post(fake)
        try:
            text = self.provider.transcribe_via_provider(
                "cap-model", b"RIFF....")
        finally:
            mod.requests.post = orig
        self.assertEqual(text, "hello hosted")
        self.assertIn("/audio/transcriptions", fake.last["url"])
        self.assertIn("Bearer unit-key",
                      fake.last["kwargs"]["headers"]["Authorization"])

    def test_stt_hosted_unsupported_falls_back(self):
        from odoo.addons.nova_ai.models import nova_ai_provider as mod
        from odoo.addons.nova_ai.models.nova_ai_provider import (
            NovaAiProviderUnsupported)
        fake = self._fake_post(status=404)
        mod, orig = self._patch_post(fake)
        try:
            with self.assertRaises(NovaAiProviderUnsupported):
                self.provider.transcribe_via_provider("cap-model", b"x")
        finally:
            mod.requests.post = orig

    def test_stt_hosted_auth_failure_surfaces(self):
        from odoo.addons.nova_ai.models import nova_ai_provider as mod
        import requests as _rq
        fake = self._fake_post(status=401)
        mod, orig = self._patch_post(fake)
        try:
            with self.assertRaises(_rq.HTTPError):
                self.provider.transcribe_via_provider("cap-model", b"x")
        finally:
            mod.requests.post = orig

    def test_ollama_always_unsupported(self):
        from odoo.addons.nova_ai.models.nova_ai_provider import (
            NovaAiProviderUnsupported)
        local = self.env["nova.ai.provider"].create({
            "name": "Cap Local", "kind": "ollama",
            "base_url": "http://unit.invalid",
        })
        with self.assertRaises(NovaAiProviderUnsupported):
            local.transcribe_via_provider("m", b"x")
        with self.assertRaises(NovaAiProviderUnsupported):
            local.speak_via_provider("m", "hi")
        with self.assertRaises(NovaAiProviderUnsupported):
            local.embed_via_provider("m", ["hi"])

    def test_tts_hosted_success(self):
        from odoo.addons.nova_ai.models import nova_ai_provider as mod
        fake = self._fake_post(content=b"RIFF....", ctype="audio/wav")
        mod, orig = self._patch_post(fake)
        try:
            data, mime = self.provider.speak_via_provider(
                "cap-model", "hi")
        finally:
            mod.requests.post = orig
        self.assertTrue(data.startswith(b"RIFF"))
        self.assertEqual(mime, "audio/wav")

    def test_embed_hosted_success(self):
        from odoo.addons.nova_ai.models import nova_ai_provider as mod
        fake = self._fake_post(
            payload={"data": [{"embedding": [0.1, 0.2]},
                              {"embedding": [0.3, 0.4]}]})
        mod, orig = self._patch_post(fake)
        try:
            vectors = self.provider.embed_via_provider(
                "emb-model", ["a", "b"])
        finally:
            mod.requests.post = orig
        self.assertEqual(vectors, [[0.1, 0.2], [0.3, 0.4]])

    def test_probe_vision_detects_and_refuses(self):
        orig = type(self.model)._chat

        def fake_ok(record, messages, tools=None, extra=None):
            content = messages[0].get("content")
            self.assertTrue(isinstance(content, list))
            self.assertTrue(any(
                isinstance(part, dict) and "image_url" in part
                for part in content))
            return _text_reply("OK")

        type(self.model)._chat = fake_ok
        try:
            self.assertTrue(self.model._probe_vision())
        finally:
            type(self.model)._chat = orig

        import requests as _rq

        def fake_no(record, messages, tools=None, extra=None):
            err = _rq.HTTPError("400 vision denied")

            class Resp:
                status_code = 400
            err.response = Resp()
            raise err

        type(self.model)._chat = fake_no
        try:
            self.assertFalse(self.model._probe_vision())
        finally:
            type(self.model)._chat = orig

    def test_speak_empty_refuses(self):
        from odoo.addons.nova_ai.models import nova_ai_voice as voice
        with self.assertRaises(AccessError):
            voice.speak_text(self.env, "   ")


@tagged("post_install", "-at_install")
class TestNovaAiSemantic(TransactionCase):

    def setUp(self):
        super().setUp()
        # These tests exercise the chunk layer (embeddings, cosine,
        # thresholds). The seeded knowledge concepts would also match
        # some of the queries and correctly change the mode to
        # "concepts" - so they are cleared here to keep each layer's
        # contract isolated.
        self.env["nova.ai.knowledge"].search([]).unlink()

    def test_cosine(self):
        from odoo.addons.nova_ai.models.nova_ai_source import _cosine
        self.assertAlmostEqual(_cosine([1, 0], [1, 0]), 1.0)
        self.assertAlmostEqual(_cosine([1, 0], [0, 1]), 0.0)
        self.assertEqual(_cosine([], []), 0.0)
        self.assertEqual(_cosine(None, [1]), 0.0)

    def _source_with(self, rows):
        source = self.env["nova.ai.source"].create({
            "name": "Unit Source", "kind": "url",
            "url": "https://unit.invalid/"})
        for seq, (content, vector) in enumerate(rows, 1):
            self.env["nova.ai.chunk"].create({
                "source_id": source.id, "seq": seq, "content": content,
                "embedding": vector})
        return source

    def test_semantic_beats_lexical_miss(self):
        from odoo.addons.nova_ai.models import nova_ai_source as mod
        self._source_with([
            ("overdue invoices must be chased weekly", [1.0, 0.0]),
            ("warehouse pallets stacking rules", [0.0, 1.0])])
        orig = mod._embed_texts
        mod._embed_texts = lambda env, texts, agent=None: [[1.0, 0.0]]
        try:
            kind, payload, card = execute_tool(
                self.env, "search_knowledge", {"query": "unpaid bills"},
                {})
        finally:
            mod._embed_texts = orig
        self.assertEqual(kind, "result")
        self.assertEqual(payload.get("mode"), "semantic")
        self.assertTrue(payload["hits"])
        self.assertIn("overdue invoices", payload["hits"][0]["content"])

    def test_lexical_fallback_without_vectors(self):
        from odoo.addons.nova_ai.models import nova_ai_source as mod
        self._source_with([("zebra crossing safety notes", None)])
        orig = mod._embed_texts
        mod._embed_texts = lambda env, texts, agent=None: None
        try:
            kind, payload, card = execute_tool(
                self.env, "search_knowledge", {"query": "zebra"}, {})
        finally:
            mod._embed_texts = orig
        self.assertEqual(kind, "result")
        self.assertEqual(payload.get("mode"), "lexical")
        self.assertTrue(payload["hits"])

    def test_semantic_threshold_falls_back(self):
        from odoo.addons.nova_ai.models import nova_ai_source as mod
        self._source_with([
            ("overdue invoices must be chased weekly", [1.0, 0.0])])
        orig = mod._embed_texts
        # Orthogonal query vector: cosine 0, under the 0.25 floor.
        mod._embed_texts = lambda env, texts, agent=None: [[0.0, 1.0]]
        try:
            kind, payload, card = execute_tool(
                self.env, "search_knowledge", {"query": "pallets"}, {})
        finally:
            mod._embed_texts = orig
        self.assertEqual(kind, "result")
        # "pallets" shares no words either: honestly empty, not forced.
        self.assertEqual(payload.get("mode"), "lexical")
        self.assertEqual(payload["hits"], [])

    def test_semantic_top_six_cut(self):
        from odoo.addons.nova_ai.models import nova_ai_source as mod
        self._source_with([
            ("matchable alpha %d" % n, [1.0, 0.0]) for n in range(8)])
        orig = mod._embed_texts
        mod._embed_texts = lambda env, texts, agent=None: [[1.0, 0.0]]
        try:
            kind, payload, card = execute_tool(
                self.env, "search_knowledge", {"query": "alpha"}, {})
        finally:
            mod._embed_texts = orig
        self.assertEqual(payload.get("mode"), "semantic")
        self.assertEqual(len(payload["hits"]), 6)


@tagged("post_install", "-at_install")
class TestNovaAiApprovals(TransactionCase):

    def setUp(self):
        super().setUp()
        self.partner = self.env["res.partner"].create(
            {"name": "Nova Approval Partner"})

    def test_confirm_sale_order_queues_then_confirms(self):
        product = self.env["product.product"].search(
            [("sale_ok", "=", True)], limit=1)
        if not product:
            self.skipTest("No saleable product in this database.")
        order = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "order_line": [(0, 0, {"product_id": product.id,
                                   "product_uom_qty": 1})],
        })
        self.assertEqual(order.state, "draft")
        kind, payload, card = execute_tool(
            self.env, "confirm_sale_order", {"res_id": order.id}, {})
        self.assertEqual(kind, "confirm")
        self.assertEqual(card["kind"], "confirm_sale_order")
        order.invalidate_recordset()
        self.assertEqual(order.state, "draft")
        result = run_confirm(self.env, card)
        self.assertEqual(result["res_id"], order.id)
        order.invalidate_recordset()
        self.assertNotEqual(order.state, "draft")

    def test_confirm_sale_order_refuses_confirmed(self):
        product = self.env["product.product"].search(
            [("sale_ok", "=", True)], limit=1)
        if not product:
            self.skipTest("No saleable product in this database.")
        order = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "order_line": [(0, 0, {"product_id": product.id,
                                   "product_uom_qty": 1})],
        })
        order.action_confirm()
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "confirm_sale_order", {"res_id": order.id},
                {})

    def test_validate_invoice_queues_then_posts(self):
        journal = self.env["account.journal"].search(
            [("type", "=", "purchase")], limit=1)
        account = self.env["account.account"].search(
            [("account_type", "=", "expense")], limit=1)
        if not journal or not account:
            self.skipTest("No purchase journal or expense account.")
        move = self.env["account.move"].create({
            "move_type": "in_invoice",
            "journal_id": journal.id,
            "partner_id": self.partner.id,
            "invoice_date": fields.Date.today(),
            "invoice_line_ids": [(0, 0, {
                "name": "Unit service", "quantity": 1,
                "price_unit": 100, "account_id": account.id})],
        })
        self.assertEqual(move.state, "draft")
        kind, payload, card = execute_tool(
            self.env, "validate_invoice", {"res_id": move.id}, {})
        self.assertEqual(kind, "confirm")
        self.assertEqual(card["kind"], "validate_invoice")
        move.invalidate_recordset()
        self.assertEqual(move.state, "draft")
        result = run_confirm(self.env, card)
        self.assertEqual(result["res_id"], move.id)
        move.invalidate_recordset()
        self.assertEqual(move.state, "posted")

    def _card_session(self, card):
        agent = self.env["nova.ai.agent"].with_context(active_test=False).search([], limit=1)
        return self.env["nova.ai.session"].create({
            "agent_id": agent.id, "pending": [card]})

    def test_pop_takes_card_once(self):
        kind, payload, card = execute_tool(
            self.env, "log_note",
            {"model": "res.partner", "res_id": self.partner.id,
             "body": "Pop me."}, {})
        session = self._card_session(card)
        first = session.pop_card(card["token"])
        self.assertEqual(first["token"], card["token"])
        self.assertFalse(session.pending)
        # The double-click: nothing left to run twice.
        self.assertIsNone(session.pop_card(card["token"]))

    def test_restore_never_duplicates(self):
        kind, payload, card = execute_tool(
            self.env, "log_note",
            {"model": "res.partner", "res_id": self.partner.id,
             "body": "Restore me."}, {})
        session = self._card_session(card)
        taken = session.pop_card(card["token"])
        session.restore_card(taken)
        session.restore_card(taken)
        self.assertEqual(len(session.pending), 1)

    def test_applied_keys_noop_and_cap(self):
        agent = self.env["nova.ai.agent"].with_context(active_test=False).search([], limit=1)
        session = self.env["nova.ai.session"].create(
            {"agent_id": agent.id})
        self.assertFalse(session.already_applied(""))
        self.assertFalse(session.already_applied("k1"))
        session.mark_applied("k1")
        self.assertTrue(session.already_applied("k1"))
        session.mark_applied("k1")
        self.assertEqual(len(session.applied_keys), 1)
        for num in range(60):
            session.mark_applied("k%d" % num)
        self.assertEqual(len(session.applied_keys), 50)
        self.assertTrue(session.already_applied("k59"))
        self.assertFalse(session.already_applied("k0"))

    def test_card_freshness(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        partner = self.env["res.partner"].create(
            {"name": "Fresh Co", "phone": "555"})
        fresh = {"kind": "set_field", "token": "a",
                 "payload": {"model": "res.partner",
                             "res_id": partner.id,
                             "field": "phone", "value": "999"}}
        self.assertEqual(tools.card_freshness(self.env, fresh), "")
        done = dict(fresh, payload=dict(
            fresh["payload"], value="555"))
        self.assertIn("already set",
                      tools.card_freshness(self.env, done))
        gone = dict(fresh, payload=dict(
            fresh["payload"], res_id=999999999))
        self.assertIn("gone", tools.card_freshness(self.env, gone))

    def test_pop_then_execute_applies_once(self):
        kind, payload, card = execute_tool(
            self.env, "log_note",
            {"model": "res.partner", "res_id": self.partner.id,
             "body": "Apply once."}, {})
        session = self._card_session(card)
        taken = session.pop_card(card["token"])
        run_confirm(self.env, taken)
        # A second approve finds no card: no double note.
        self.assertIsNone(session.pop_card(card["token"]))
        notes = self.partner.message_ids.filtered(
            lambda m: "Apply once." in (m.body or ""))
        self.assertEqual(len(notes), 1)

    def test_card_token_budget_and_expiry(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        kind, payload, card = execute_tool(
            self.env, "log_note",
            {"model": "res.partner", "res_id": self.partner.id,
             "body": "x" * 500}, {})
        self.assertLessEqual(
            len(card["token"]), len("log_note:") + 160)
        self.assertIn("expires", card)
        from datetime import datetime, timedelta, timezone
        expires = datetime.fromisoformat(card["expires"])
        soon = datetime.now(timezone.utc) + timedelta(hours=23)
        self.assertGreater(expires, soon)

    def test_card_liveness(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        self.assertTrue(tools._card_live({}))
        self.assertTrue(tools._card_live({"expires": None}))
        self.assertTrue(tools._card_live({"expires": "not-a-date"}))
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        future = (now + timedelta(hours=1)).isoformat()
        past = (now - timedelta(hours=1)).isoformat()
        self.assertTrue(tools._card_live({"expires": future}))
        self.assertFalse(tools._card_live({"expires": past}))


@tagged("post_install", "-at_install")
class TestNovaAiSetField(TransactionCase):

    def test_set_field_char_queues_then_writes(self):
        partner = self.env["res.partner"].create(
            {"name": "Nova Field Partner"})
        kind, payload, card = execute_tool(
            self.env, "set_field",
            {"model": "res.partner", "res_id": partner.id,
             "field": "comment", "value": "Ring the bell twice"}, {})
        self.assertEqual(kind, "confirm")
        self.assertEqual(card["kind"], "set_field")
        self.assertIn("Ring the bell twice", card["summary"])
        self.assertFalse(partner.comment)
        result = run_confirm(self.env, card)
        self.assertEqual(result["res_id"], partner.id)
        partner.invalidate_recordset()
        # Odoo 20 stores comment as sanitized Html: the words land
        # wrapped in <p>, so assert on text, not exact markup.
        self.assertIn("Ring the bell twice", str(partner.comment))

    def test_set_field_many2one_resolves(self):
        parent = self.env["res.partner"].create(
            {"name": "Nova Field Parent"})
        child = self.env["res.partner"].create(
            {"name": "Nova Field Child"})
        kind, payload, card = execute_tool(
            self.env, "set_field",
            {"model": "res.partner", "res_id": child.id,
             "field": "parent_id", "value": parent.id}, {})
        self.assertEqual(kind, "confirm")
        run_confirm(self.env, card)
        child.invalidate_recordset()
        self.assertEqual(child.parent_id, parent)

    def test_set_field_unknown_field_refuses(self):
        partner = self.env["res.partner"].create(
            {"name": "Nova Field Partner"})
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "set_field",
                {"model": "res.partner", "res_id": partner.id,
                 "field": "nope_not_a_field", "value": "x"}, {})

    def test_set_field_readonly_refuses(self):
        partner = self.env["res.partner"].create(
            {"name": "Nova Field Partner"})
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "set_field",
                {"model": "res.partner", "res_id": partner.id,
                 "field": "create_date", "value": "2026-01-01"}, {})

    def test_set_field_bad_selection_refuses(self):
        partner = self.env["res.partner"].create(
            {"name": "Nova Field Partner"})
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "set_field",
                {"model": "res.partner", "res_id": partner.id,
                 "field": "lang", "value": "xx-bad"}, {})

    def test_set_field_boolean_date_selection(self):
        partner = self.env["res.partner"].create(
            {"name": "Nova Field Partner"})
        kind, _payload, card = execute_tool(
            self.env, "set_field",
            {"model": "res.partner", "res_id": partner.id,
             "field": "active", "value": "yes"}, {})
        self.assertEqual(kind, "confirm")
        run_confirm(self.env, card)
        partner.invalidate_recordset()
        self.assertTrue(partner.active)
        # Odoo 20 selections may be callables: read them through the
        # same helper the tool uses. Cards show the human label,
        # not the key.
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        langs = tools._selection_options(
            self.env["res.partner"]._fields["lang"], partner)
        if langs:
            kind, _payload, card = execute_tool(
                self.env, "set_field",
                {"model": "res.partner", "res_id": partner.id,
                 "field": "lang", "value": langs[0][0]}, {})
            self.assertEqual(kind, "confirm")
            self.assertIn(langs[0][1], card["summary"])
            self.assertNotIn(langs[0][0], card["summary"].replace(
                langs[0][1], ""))
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "set_field",
                {"model": "res.partner", "res_id": partner.id,
                 "field": "active", "value": "maybe"}, {})
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "set_field",
                {"model": "res.partner", "res_id": partner.id,
                 "field": "parent_id", "value": "not-an-id"}, {})

    def test_set_field_missing_record_refuses(self):
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "set_field",
                {"model": "res.partner", "res_id": 42424242,
                 "field": "comment", "value": "x"}, {})
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "set_field",
                {"model": "no.such.model", "res_id": 1,
                 "field": "comment", "value": "x"}, {})


@tagged("post_install", "-at_install")
class TestNovaAiDigestDeltas(TransactionCase):

    def test_deltas_count_this_week(self):
        import re
        from odoo.addons.nova_ai.controllers.nova_ai import (
            _digest_deltas)
        before = _digest_deltas(self.env)
        was = int(re.search(
            r"Quotations created: (\d+) this week", before).group(1))
        partner = self.env["res.partner"].create(
            {"name": "Nova Delta Partner"})
        for _ in range(2):
            self.env["sale.order"].create({"partner_id": partner.id})
        facts = _digest_deltas(self.env)
        now = int(re.search(
            r"Quotations created: (\d+) this week", facts).group(1))
        self.assertEqual(now - was, 2)
        self.assertIn("Overdue customer invoices now:", facts)

    def test_deltas_surge_marker(self):
        from odoo.addons.nova_ai.controllers.nova_ai import (
            _delta_mark)
        self.assertEqual(_delta_mark(4, 2), " (surge)")
        self.assertEqual(_delta_mark(2, 5), " (drop)")
        self.assertEqual(_delta_mark(4, 3), "")
        self.assertEqual(_delta_mark(1, 0), "")
        self.assertEqual(_delta_mark(0, 1), "")
        self.assertEqual(_delta_mark(1, 4), "")


@tagged("post_install", "-at_install")
class TestNovaAiToolDiscovery(TransactionCase):

    def test_agent_tools_carry_schemas(self):
        provider = self.env["nova.ai.provider"].create({
            "name": "Discovery Provider", "kind": "openai_compat",
            "base_url": "http://unit.invalid/v1",
        })
        model = self.env["nova.ai.model"].create({
            "provider_id": provider.id, "name": "discovery-model",
            "supports_tools": True, "context_length": 16000,
        })
        tools = self.env["nova.ai.tool"].search([])
        agent = self.env["nova.ai.agent"].create({
            "name": "Discovery Agent", "model_id": model.id,
            "tool_ids": [(6, 0, tools.ids)],
        })
        offered = agent.tools_for_tier()
        codes = set(offered.mapped("code"))
        for expected in ("search_records", "set_field", "digitize_bill",
                         "confirm_sale_order", "validate_invoice"):
            self.assertIn(expected, codes)
        for tool in offered:
            self.assertTrue(tool.write_kind)
            self.assertIsInstance(
                tool.schema if isinstance(tool.schema, dict) else {},
                dict)

    def test_tool_hints(self):
        by_code = {t.code: t for t in
                   self.env["nova.ai.tool"].search([])}
        search = by_code["search_records"]
        self.assertTrue(search.read_only)
        self.assertFalse(search.destructive)
        unlink = by_code["unlink_record"]
        self.assertFalse(unlink.read_only)
        self.assertTrue(unlink.destructive)
        setter = by_code["set_field"]
        self.assertFalse(setter.read_only)
        self.assertFalse(setter.destructive)

    def test_tool_examples_present(self):
        tools = self.env["nova.ai.tool"].search([])
        self.assertGreaterEqual(len(tools), 24)
        for tool in tools:
            self.assertIsInstance(
                tool.example, dict,
                "tool %s carries no example" % tool.code)
            self.assertTrue(
                tool.example, "tool %s example is empty" % tool.code)


@tagged("post_install", "-at_install")
class TestNovaAiDigitize(TransactionCase):

    OCR_TEXT = ("ACME SUPPLIES LLC VAT TRN 100234567890 "
                "TAX INVOICE INV/2026/042 Date: 2026-10-01 "
                "Widget A 2 x 500.00 = 1000.00 "
                "Widget B 5 x 664.00 = 3320.00 "
                "TOTAL: 4320.00 USD")

    def setUp(self):
        super().setUp()
        provider = self.env["nova.ai.provider"].create({
            "name": "Digitize Provider", "kind": "openai_compat",
            "base_url": "http://unit.invalid/v1",
        })
        self.model = self.env["nova.ai.model"].create({
            "provider_id": provider.id, "name": "digitize-model",
            "supports_tools": True, "context_length": 16000,
        })
        tools = self.env["nova.ai.tool"].search(
            [("code", "=", "digitize_bill")])
        self.agent = self.env["nova.ai.agent"].create({
            "name": "Digitize Agent", "model_id": self.model.id,
            "tool_ids": [(6, 0, tools.ids)],
        })
        import base64
        self.attachment = self.env["ir.attachment"].create({
            "name": "unit-bill.png", "mimetype": "image/png",
            "raw": base64.b64encode(b"fakepng"),
        })

    def _patch(self, extraction):
        from odoo.addons.nova_ai.models import nova_ai_tools as mod
        orig_ocr = mod._ocr_attachment
        mod._ocr_attachment = lambda env, att: {
            "text": self.OCR_TEXT, "pages": 1,
            "mean_confidence": 0.9, "engine": "ocr"}
        stub = _StubChat([_text_reply(json.dumps(extraction))])
        orig_chat = type(self.model)._chat

        def fake(record, messages, tools=None, extra=None):
            return stub(record, messages, tools=tools, extra=extra)

        type(self.model)._chat = fake
        return mod, orig_ocr, orig_chat

    def _unpatch(self, patched):
        mod, orig_ocr, orig_chat = patched
        mod._ocr_attachment = orig_ocr
        type(self.model)._chat = orig_chat

    def _extraction(self, **over):
        base = {"partner_name": "Zzz Digit Vendor", "vat": "ZZZ123",
                "date": "2026-10-01", "number": "INV/2026/042",
                "currency": "USD",
                "lines": [{"description": "Widget A", "qty": 2,
                           "price": 500},
                          {"description": "Widget B", "qty": 5,
                           "price": 664}],
                "total": 4320.0, "low_confidence": []}
        base.update(over)
        return base

    def test_digitize_queues_draft_bill_card(self):
        partner = self.env["res.partner"].create({
            "name": "Zzz Digit Vendor", "vat": "ZZZ123"})
        patched = self._patch(self._extraction())
        try:
            kind, payload, card = execute_tool(
                self.env, "digitize_bill",
                {"attachment_id": self.attachment.id},
                {"agent": self.agent})
        finally:
            self._unpatch(patched)
        self.assertEqual(kind, "confirm")
        self.assertEqual(card["kind"], "create_draft")
        values = card["payload"]["values"]
        self.assertEqual(values["partner_id"], partner.id)
        self.assertEqual(values["ref"], "INV/2026/042")
        self.assertEqual(len(values["invoice_line_ids"]), 2)
        # Nothing created until the user approves.
        self.assertFalse(self.env["account.move"].search(
            [("ref", "=", "INV/2026/042")]))
        result = run_confirm(self.env, card)
        bill = self.env["account.move"].browse(result["res_id"])
        self.assertEqual(bill.state, "draft")
        self.assertEqual(bill.partner_id, partner)

    def test_digitize_ambiguous_partner_asks(self):
        self.env["res.partner"].create({"name": "Zzz Amb Vendor A"})
        self.env["res.partner"].create({"name": "Zzz Amb Vendor B"})
        patched = self._patch(self._extraction(
            partner_name="Zzz Amb Vendor", vat=None))
        try:
            kind, payload, card = execute_tool(
                self.env, "digitize_bill",
                {"attachment_id": self.attachment.id},
                {"agent": self.agent})
        finally:
            self._unpatch(patched)
        self.assertEqual(kind, "result")
        self.assertTrue(payload.get("needs_partner"))
        self.assertEqual(len(payload.get("candidates") or []), 2)
        self.assertIsNone(card)

    def test_digitize_totals_mismatch_asks(self):
        self.env["res.partner"].create({
            "name": "Zzz Digit Vendor", "vat": "ZZZ123"})
        patched = self._patch(self._extraction(total=9999.0))
        try:
            kind, payload, card = execute_tool(
                self.env, "digitize_bill",
                {"attachment_id": self.attachment.id},
                {"agent": self.agent})
        finally:
            self._unpatch(patched)
        self.assertEqual(kind, "result")
        self.assertTrue(payload.get("needs_total"))
        self.assertEqual(payload["mismatch"]["ocr_total"], 9999.0)
        self.assertIsNone(card)

    def test_digitize_unreadable_refuses(self):
        import requests as _rq
        from odoo.addons.nova_ai.models import nova_ai_tools as mod
        err = _rq.HTTPError("400 no vision here")

        class Resp:
            status_code = 400
        err.response = Resp()
        orig_chat = type(self.model)._chat

        def fake_chat(record, messages, tools=None, extra=None):
            raise err

        type(self.model)._chat = fake_chat
        orig_ocr = mod._ocr_attachment
        mod._ocr_attachment = lambda env, att: {
            "text": "xx", "pages": 1,
            "mean_confidence": 0.2, "engine": "none"}
        try:
            with self.assertRaises(AccessError):
                execute_tool(
                    self.env, "digitize_bill",
                    {"attachment_id": self.attachment.id},
                    {"agent": self.agent})
        finally:
            type(self.model)._chat = orig_chat
            mod._ocr_attachment = orig_ocr

    def test_digitize_pdf_text_path(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as mod
        partner = self.env["res.partner"].create({
            "name": "Zzz Digit Vendor", "vat": "ZZZ123"})
        orig_layer = mod._ocr_attachment_text_layer
        mod._ocr_attachment_text_layer = lambda env, att, raw: {
            "text": self.OCR_TEXT, "pages": 1}
        orig_vision = mod._vision_extract_fields
        mod._vision_extract_fields = lambda env, agent, pngs: None
        patched = self._patch(self._extraction())
        try:
            kind, payload, card = execute_tool(
                self.env, "digitize_bill",
                {"attachment_id": self.attachment.id},
                {"agent": self.agent})
        finally:
            mod._ocr_attachment_text_layer = orig_layer
            mod._vision_extract_fields = orig_vision
            self._unpatch(patched)
        self.assertEqual(kind, "confirm")
        self.assertEqual(card["payload"]["values"]["partner_id"],
                         partner.id)

    def test_vision_success_learns_flag(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as mod
        self.assertFalse(self.model.supports_vision)
        admin_group = self.env.ref("nova_ai.group_nova_ai_admin")
        self.env.user.write({"group_ids": [(4, admin_group.id)]})
        # Drop the in-memory group cache: the access check below must
        # see the grant made one line up, not the pre-write snapshot.
        self.env.user.invalidate_recordset()
        patched = self._patch(self._extraction())
        try:
            found = mod._vision_extract_fields(
                self.env, self.agent, [b"fakepng"])
        finally:
            self._unpatch(patched)
        self.assertEqual(found["number"], "INV/2026/042")
        self.model.invalidate_recordset()
        self.assertTrue(self.model.supports_vision)

    def test_vision_success_without_rights_keeps_working(self):
        # The flag is config: a turn without model-write rights still
        # extracts, it just does not persist the capability.
        from odoo.addons.nova_ai.models import nova_ai_tools as mod
        low = self.env["res.users"].create({
            "name": "Loop100 Clerk", "login": "loop100_clerk",
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref(
                                      "nova_ai.group_nova_ai_user").id])],
        })
        patched = self._patch(self._extraction())
        try:
            found = mod._vision_extract_fields(
                self.env(user=low), self.agent, [b"fakepng"])
        finally:
            self._unpatch(patched)
        self.assertEqual(found["number"], "INV/2026/042")
        self.model.invalidate_recordset()
        self.assertFalse(self.model.supports_vision)

    def test_vision_refused_skips_without_recall(self):
        import requests as _rq
        from odoo.addons.nova_ai.models import nova_ai_tools as mod
        err = _rq.HTTPError("400 bill vision denied")

        class Resp:
            status_code = 400
        err.response = Resp()
        orig = type(self.model)._chat

        def fake(record, messages, tools=None, extra=None):
            raise err

        type(self.model)._chat = fake
        try:
            found = mod._vision_extract_fields(
                self.env, self.agent, [b"fakepng"])
        finally:
            type(self.model)._chat = orig
        self.assertIsNone(found)
        self.assertIn(self.model.id, mod._NO_VISION)
        self.assertFalse(self.model.supports_vision)
        # Second attempt skips the doomed call entirely.
        calls = []
        type(self.model)._chat = lambda r, m, tools=None, extra=None: \
            calls.append(1) or _text_reply("{}")
        try:
            mod._vision_extract_fields(
                self.env, self.agent, [b"fakepng"])
        finally:
            type(self.model)._chat = orig
        self.assertEqual(calls, [])

    def test_vision_fallback_to_ocr_text(self):
        import requests as _rq
        from odoo.addons.nova_ai.models import nova_ai_tools as mod
        partner = self.env["res.partner"].create({
            "name": "Zzz Digit Vendor", "vat": "ZZZ123"})
        err = _rq.HTTPError("400 no vision here")

        class Resp:
            status_code = 400
        err.response = Resp()
        calls = []
        orig_chat = type(self.model)._chat

        def fake(record, messages, tools=None, extra=None):
            calls.append(1)
            if len(calls) == 1:
                raise err
            return _text_reply(json.dumps(self._extraction()))

        type(self.model)._chat = fake
        orig_ocr = mod._ocr_attachment
        mod._ocr_attachment = lambda env, att: {
            "text": self.OCR_TEXT, "pages": 1,
            "mean_confidence": 0.9, "engine": "ocr"}
        try:
            kind, payload, card = execute_tool(
                self.env, "digitize_bill",
                {"attachment_id": self.attachment.id},
                {"agent": self.agent})
        finally:
            type(self.model)._chat = orig_chat
            mod._ocr_attachment = orig_ocr
        self.assertEqual(kind, "confirm")
        self.assertEqual(card["payload"]["values"]["partner_id"],
                         partner.id)


@tagged("post_install", "-at_install")
class TestNovaAiGenericTools(TransactionCase):

    def setUp(self):
        super().setUp()
        self.partner = self.env["res.partner"].create(
            {"name": "Nova Generic Partner"})

    def _order(self):
        product = self.env["product.product"].search(
            [("sale_ok", "=", True)], limit=1)
        if not product:
            self.skipTest("No saleable product in this database.")
        return self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "order_line": [(0, 0, {"product_id": product.id,
                                   "product_uom_qty": 2})],
        })

    def _invoice(self, post=False):
        journal = self.env["account.journal"].search(
            [("type", "=", "sale")], limit=1)
        account = self.env["account.account"].search(
            [("account_type", "=", "income")], limit=1)
        product = self.env["product.product"].search(
            [("sale_ok", "=", True)], limit=1)
        if not journal or not account or not product:
            self.skipTest("No sale journal/income account in this db.")
        move = self.env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": self.partner.id,
            "journal_id": journal.id,
            "invoice_date": "2026-10-01",
            "invoice_line_ids": [(0, 0, {
                "name": "Nova test service",
                "quantity": 1.0,
                "price_unit": 100.0,
                "account_id": account.id,
            })],
        })
        if post:
            try:
                move.action_post()
            except Exception:
                self.skipTest("Cannot post invoices in this db.")
            move.invalidate_recordset()
        return move

    def test_copy_queues_then_duplicates_draft(self):
        order = self._order()
        kind, _payload, card = execute_tool(
            self.env, "copy_record",
            {"model": "sale.order", "res_id": order.id}, {})
        self.assertEqual(kind, "confirm")
        self.assertEqual(card["kind"], "copy_record")
        result = run_confirm(self.env, card)
        self.assertNotEqual(result["res_id"], order.id)
        twin = self.env["sale.order"].browse(result["res_id"])
        self.assertEqual(twin.state, "draft")
        self.assertEqual(twin.partner_id, self.partner)
        self.assertEqual(len(twin.order_line), 1)

    def test_copy_missing_refuses(self):
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "copy_record",
                {"model": "sale.order", "res_id": 42424242}, {})

    def test_read_related_lines(self):
        order = self._order()
        kind, payload, card = execute_tool(
            self.env, "read_related",
            {"model": "sale.order", "res_id": order.id,
             "field": "order_line",
             "fields": ["product_id", "product_uom_qty"]}, {})
        self.assertEqual(kind, "result")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["rows"][0]["product_uom_qty"], 2)
        self.assertIsNone(card)

    def test_read_related_bad_field_refuses(self):
        order = self._order()
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "read_related",
                {"model": "sale.order", "res_id": order.id,
                 "field": "nope"}, {})

    def test_unlink_queues_then_deletes(self):
        target = self.env["res.partner"].create(
            {"name": "Nova Doomed Partner"})
        kind, _payload, card = execute_tool(
            self.env, "unlink_record",
            {"model": "res.partner", "res_id": target.id}, {})
        self.assertEqual(kind, "confirm")
        self.assertEqual(card["kind"], "unlink_record")
        self.assertTrue(target.exists())
        run_confirm(self.env, card)
        self.assertFalse(target.exists())

    def test_execute_method_gates(self):
        order = self._order()
        for bad in ("_private", "sudo", "create", "write", "unlink",
                    "with_user"):
            with self.assertRaises(AccessError):
                execute_tool(
                    self.env, "execute_method",
                    {"model": "sale.order", "res_id": order.id,
                     "method": bad}, {})
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "execute_method",
                {"model": "sale.order", "res_id": order.id,
                 "method": "no_such_method_xyz"}, {})

    def test_execute_method_unknown_names_available(self):
        order = self._order()
        with self.assertRaises(AccessError) as ctx:
            execute_tool(
                self.env, "execute_method",
                {"model": "sale.order", "res_id": order.id,
                 "method": "action_reset_draft"}, {})
        self.assertIn("fields_get", str(ctx.exception))

    def test_move_draft_refusal_paid_and_draft(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools

        class _Stub:
            display_name = "INV/TEST"

            def __init__(self, state, payment_state, locked=False,
                         name="account.move"):
                self.state = state
                self.payment_state = payment_state
                self.inalterable_hash = locked
                self._name = name

        self.assertTrue(tools._move_draft_refusal(
            _Stub("posted", "paid")))
        self.assertTrue(tools._move_draft_refusal(
            _Stub("posted", "paid"), "button_cancel"))
        self.assertTrue(tools._move_draft_refusal(
            _Stub("draft", "not_paid")))
        self.assertEqual(tools._move_draft_refusal(
            _Stub("posted", "not_paid")), "")
        self.assertEqual(tools._move_draft_refusal(
            _Stub("posted", "not_paid"), "button_cancel"), "")
        self.assertEqual(tools._move_draft_refusal(
            _Stub("draft", "not_paid", name="res.partner")), "")

    def test_move_frozen_refusal(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools

        class _Stub:
            def __init__(self, state, payment_state, locked=False,
                         name="account.move"):
                self.state = state
                self.payment_state = payment_state
                self.inalterable_hash = locked
                self._name = name

        self.assertEqual(tools._move_frozen_refusal(
            _Stub("draft", "not_paid"), "edit"), "")
        self.assertEqual(tools._move_frozen_refusal(
            _Stub("cancel", "not_paid"), "delete"), "")
        self.assertTrue(tools._move_frozen_refusal(
            _Stub("posted", "paid"), "delete"))
        self.assertTrue(tools._move_frozen_refusal(
            _Stub("posted", "not_paid"), "edit"))
        self.assertEqual(tools._move_frozen_refusal(
            _Stub("draft", "not_paid", name="sale.order"), "edit"),
            "")

    def test_editable_card_keys_locked(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        for locked in ("model", "res_id", "method", "field", "value",
                       "values", "args"):
            self.assertNotIn(locked, tools._EDITABLE_CARD_KEYS)
        self.assertIn("body", tools._EDITABLE_CARD_KEYS)

    def test_editable_card_keys_locked(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        for locked in ("model", "res_id", "method", "field", "value",
                       "values", "args"):
            self.assertNotIn(locked, tools._EDITABLE_CARD_KEYS)
        self.assertIn("body", tools._EDITABLE_CARD_KEYS)

    def test_chain_args_validates(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        after, bind = tools._chain_args({})
        self.assertIsNone(after)
        self.assertIsNone(bind)
        after, bind = tools._chain_args(
            {"after": {"kind": "create_draft",
                       "model": "sale.order"}})
        self.assertEqual(
            after, {"kind": "create_draft", "model": "sale.order"})
        self.assertEqual(bind, {"res_id": "created"})
        after, bind = tools._chain_args(
            {"after": {"kind": "create_draft",
                       "model": "sale.order"},
             "bind": {"values.order_id": "created"}})
        self.assertEqual(bind, {"values.order_id": "created"})
        with self.assertRaises(AccessError):
            tools._chain_args(
                {"after": {"kind": "no_such_tool",
                           "model": "sale.order"}})
        with self.assertRaises(AccessError):
            tools._chain_args({"after": {"kind": "create_draft"}})
        with self.assertRaises(AccessError):
            tools._chain_args({"bind": {"res_id": "whatever"}})
        with self.assertRaises(AccessError):
            tools._chain_args({"after": "just-a-string"})

    def test_chain_queue_attaches(self):
        order = self._order()
        # Without a chain, invoicing a draft order refuses.
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "invoice_order", {"res_id": order.id}, {})
        kind, _payload, card = execute_tool(
            self.env, "invoice_order",
            {"res_id": 0,
             "after": {"kind": "create_draft", "model": "sale.order"}},
            {})
        self.assertEqual(kind, "confirm")
        self.assertEqual(
            card["after"], {"kind": "create_draft",
                            "model": "sale.order"})
        self.assertEqual(card["bind"], {"res_id": "created"})
        self.assertEqual(card["payload"].get("res_id"), 0)

    def test_chain_deferred_validation(self):
        # A chained step skips queue-time state checks; approve time
        # still enforces them against the then-real record.
        kind, _payload, card = execute_tool(
            self.env, "confirm_sale_order",
            {"res_id": 0,
             "after": {"kind": "create_draft", "model": "sale.order"}},
            {})
        self.assertEqual(kind, "confirm")
        self.assertTrue(card["after"])

    def _chain_agent(self):
        provider = self.env["nova.ai.provider"].create({
            "name": "Chain Provider", "kind": "openai_compat",
            "base_url": "http://unit.invalid/v1",
        })
        model = self.env["nova.ai.model"].create({
            "provider_id": provider.id, "name": "chain-model",
            "supports_tools": True, "context_length": 16000,
        })
        return self.env["nova.ai.agent"].create({
            "name": "Chain Agent", "model_id": model.id,
        })

    def test_chain_end_to_end_quote_confirm_invoice(self):
        # The flagship: draft -> confirm -> invoice, three chained
        # cards approved in order, ids threading through.
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        product = self.env["product.product"].search(
            [("sale_ok", "=", True)], limit=1)
        if not product:
            self.skipTest("No saleable product in this database.")
        product.product_tmpl_id.write({"invoice_policy": "order"})
        agent = self._chain_agent()
        _kind, _p, card_a = execute_tool(
            self.env, "create_draft",
            {"model": "sale.order",
             "values": {"partner_id": self.partner.id,
                        "order_line": [(0, 0, {
                            "product_id": product.id,
                            "product_uom_qty": 2})]}}, {})
        self.assertEqual(card_a["kind"], "create_draft")
        _kind, _p, card_b = execute_tool(
            self.env, "confirm_sale_order",
            {"res_id": 0,
             "after": {"kind": "create_draft",
                       "model": "sale.order"}}, {})
        _kind, _p, card_c = execute_tool(
            self.env, "invoice_order",
            {"res_id": 0,
             "after": {"kind": "confirm_sale_order",
                       "model": "sale.order"}}, {})
        session = self.env["nova.ai.session"].create(
            {"agent_id": agent.id,
             "pending": [card_a, card_b, card_c]})
        # Out of order is refused: B waits for A.
        pending = list(session.pending or [])
        blocker = tools.find_dependency(pending, card_b)
        self.assertIsNotNone(blocker)
        self.assertEqual(blocker.get("token"), card_a["token"])
        self.assertIsNone(tools.find_dependency(pending, card_a))
        # Approve A: the draft is created and B's id fills in.
        popped = session.pop_card(card_a["token"])
        self.assertIsNotNone(popped)
        result_a = run_confirm(self.env, popped, agent)
        self.assertTrue(result_a["res_id"])
        self.assertEqual(tools.resolve_dependents(
            self.env, session, popped, result_a), 1)
        (bound_b,) = [
            c for c in session.pending
            if c.get("token") == card_b["token"]]
        self.assertEqual(bound_b["payload"]["res_id"],
                         result_a["res_id"])
        self.assertFalse(bound_b.get("after"))
        still = [
            c for c in session.pending
            if c.get("token") == card_c["token"]][0]
        self.assertTrue(still.get("after"))
        # Approve B: the order confirms and C's id fills in.
        popped_b = session.pop_card(card_b["token"])
        result_b = run_confirm(self.env, popped_b, agent)
        self.assertTrue(result_b["res_id"])
        self.assertEqual(tools.resolve_dependents(
            self.env, session, popped_b, result_b), 1)
        (bound_c,) = [
            c for c in session.pending
            if c.get("token") == card_c["token"]]
        self.assertEqual(bound_c["payload"]["res_id"],
                         result_b["res_id"])
        # Approve C: a draft invoice exists for the partner.
        popped_c = session.pop_card(card_c["token"])
        result_c = run_confirm(self.env, popped_c, agent)
        bill = self.env["account.move"].browse(result_c["res_id"])
        self.assertEqual(bill.state, "draft")
        self.assertEqual(bill.partner_id, self.partner)
        self.assertFalse(session.pending)

    def test_chain_fifo_pairs(self):
        # Two parallel chains stay paired: first approval feeds the
        # first waiter, never both.
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        agent = self._chain_agent()
        _kind, _p, card_a1 = execute_tool(
            self.env, "create_draft",
            {"model": "res.partner", "values": {"name": "Pair One"}},
            {})
        _kind, _p, card_a2 = execute_tool(
            self.env, "create_draft",
            {"model": "res.partner", "values": {"name": "Pair Two"}},
            {})
        _kind, _p, card_b1 = execute_tool(
            self.env, "log_note",
            {"model": "res.partner", "res_id": 0,
             "body": "note one",
             "after": {"kind": "create_draft",
                       "model": "res.partner"}}, {})
        _kind, _p, card_b2 = execute_tool(
            self.env, "log_note",
            {"model": "res.partner", "res_id": 0,
             "body": "note two",
             "after": {"kind": "create_draft",
                       "model": "res.partner"}}, {})
        session = self.env["nova.ai.session"].create(
            {"agent_id": agent.id,
             "pending": [card_a1, card_a2, card_b1, card_b2]})
        res1 = run_confirm(
            self.env, session.pop_card(card_a1["token"]), agent)
        self.assertEqual(tools.resolve_dependents(
            self.env, session, dict(card_a1), res1), 1)
        by_token = {c["token"]: c for c in session.pending}
        self.assertEqual(
            by_token[card_b1["token"]]["payload"]["res_id"],
            res1["res_id"])
        self.assertTrue(by_token[card_b2["token"]].get("after"))
        res2 = run_confirm(
            self.env, session.pop_card(card_a2["token"]), agent)
        self.assertEqual(tools.resolve_dependents(
            self.env, session, dict(card_a2), res2), 1)
        by_token = {c["token"]: c for c in session.pending}
        self.assertEqual(
            by_token[card_b2["token"]]["payload"]["res_id"],
            res2["res_id"])
        # Each note lands on its own partner.
        run_confirm(
            self.env, session.pop_card(card_b1["token"]), agent)
        run_confirm(
            self.env, session.pop_card(card_b2["token"]), agent)
        one = self.env["res.partner"].browse(res1["res_id"])
        two = self.env["res.partner"].browse(res2["res_id"])
        self.assertTrue(any("note one" in (m.body or "")
                            for m in one.message_ids))
        self.assertTrue(any("note two" in (m.body or "")
                            for m in two.message_ids))

    def test_session_isolation_between_users(self):
        # Per-user scoping: sessions default to the creator, record
        # rules hide other users' sessions AND their messages.
        group = self.env.ref("nova_ai.group_nova_ai_user")
        other = self.env["res.users"].create({
            "name": "Nova Other", "login": "nova_other",
            "group_ids": [(6, 0, [group.id])],
        })
        agent = self.env["nova.ai.agent"].with_context(
            active_test=False).search([], limit=1)
        mine = self.env["nova.ai.session"].create(
            {"agent_id": agent.id})
        self.assertEqual(mine.user_id, self.env.user)
        theirs = self.env["nova.ai.session"].with_user(other).create(
            {"agent_id": agent.id})
        self.assertEqual(theirs.user_id, other)
        seen = self.env["nova.ai.session"].with_user(other).search([])
        self.assertIn(theirs.id, seen.ids)
        self.assertNotIn(mine.id, seen.ids)
        msg = self.env["nova.ai.message"].add(
            mine, "user", content="secret plans")
        stranger = self.env["nova.ai.message"].with_user(other)
        # Search hides it and content reads refuse: exists() alone
        # is only a row-presence check, never a content gate.
        self.assertNotIn(msg.id, stranger.search([]).ids)
        with self.assertRaises(AccessError):
            stranger.browse(msg.id).read(["content"])

    def test_create_draft_missing_required_refused(self):
        card = {"kind": "create_draft",
                "payload": {"model": "res.partner", "values": {}}}
        with self.assertRaises(AccessError) as ctx:
            run_confirm(self.env, card)
        self.assertIn("name", str(ctx.exception))

    def test_create_draft_skips_inherits_parent_link(self):
        # alias_id is required=True but Odoo auto-creates the alias
        # on create (delegate inheritance mints the parent row), so
        # demanding it blocks valid cards. name stays demanded.
        try:
            self.env["project.project"]
        except KeyError:
            self.skipTest("project not installed")
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        missing = tools._missing_required(
            self.env, "project.project", {"name": "Probe Shimla"})
        self.assertNotIn("alias_id", missing)
        self.assertIn("name", tools._missing_required(
            self.env, "project.project", {}))
        kind, _payload, card = execute_tool(
            self.env, "create_draft",
            {"model": "project.project",
             "values": {"name": "Probe Shimla Party"}}, {})
        self.assertEqual(kind, "confirm")
        self.assertFalse(
            [line for line in card.get("preview") or []
             if "Still missing required" in line])
        result = run_confirm(self.env, card)
        project = self.env["project.project"].browse(result["res_id"])
        self.assertTrue(project.exists())
        self.assertTrue(project.alias_id)
        project.unlink()

    def test_product_create_skips_auto_fields(self):
        # product_variant_ids is required=True but Odoo mints
        # variants during create; invoice_policy is computed.
        # Neither may block the card or the approval.
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        missing = tools._missing_required(
            self.env, "product.template", {"name": "Nova Test"})
        self.assertNotIn("product_variant_ids", missing)
        self.assertNotIn("invoice_policy", missing)
        kind, _payload, card = execute_tool(
            self.env, "create_draft",
            {"model": "product.template",
             "values": {"name": "Nova Test Product"}}, {})
        self.assertEqual(kind, "confirm")
        self.assertFalse(
            [line for line in card.get("preview") or []
             if "Still missing required" in line])
        result = run_confirm(self.env, card)
        product = self.env["product.template"].browse(
            result["res_id"])
        self.assertTrue(product.exists())
        self.assertTrue(product.product_variant_ids)

    def test_product_variant_without_template_refuses(self):        # A variant cannot exist without its template: refuse at
        # queue time with the fix spelled out, so the model
        # re-queues a product.template in the same turn instead of
        # showing the user an un-approvable card.
        with self.assertRaises(AccessError) as ctx:
            execute_tool(
                self.env, "create_draft",
                {"model": "product.product",
                 "values": {"name": "iPhone 17"}}, {})
        self.assertIn("product.template", str(ctx.exception))
        # ...while a variant WITH its template queues normally.
        template = self.env["product.template"].create(
            {"name": "Nova Base"})
        kind, _payload, card = execute_tool(
            self.env, "create_draft",
            {"model": "product.product",
             "values": {"name": "Nova Variant",
                        "product_tmpl_id": template.id}}, {})
        self.assertEqual(kind, "confirm")

    def test_system_prompt_names_template_for_products(self):
        from types import SimpleNamespace
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        agent = SimpleNamespace(system_prompt="", instructions="")
        for tier in ("t2", "t3"):
            prompt = ctrl._system_prompt(agent, tier, "")
            self.assertIn("product.template", prompt)
            self.assertIn("uom.uom", prompt)

    def test_reference_models_are_searchable(self):
        # Category and unit lookups must not hit the allow-list wall:
        # resolving them is routine product work.
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        self.assertTrue(tools._model_allowed(
            self.env, "product.category"))
        self.assertTrue(tools._model_allowed(self.env, "uom.uom"))
        kind, payload, card = execute_tool(
            self.env, "search_records",
            {"model": "product.category", "domain": [],
             "fields": ["display_name"], "limit": 5}, {})
        self.assertEqual(kind, "result")
        self.assertTrue(len(payload["rows"]) >= 1)
        self.assertIsNone(card)
        kind, payload, card = execute_tool(
            self.env, "search_records",
            {"model": "uom.uom", "domain": [],
             "fields": ["name"], "limit": 5}, {})
        self.assertEqual(kind, "result")
        self.assertTrue(len(payload["rows"]) >= 1)
        self.assertIsNone(card)

    def test_create_draft_flags_unknown_fields(self):        # Guessed field names must surface, not vanish: the user sees
        # them on the card and the model gets them echoed back.
        kind, _payload, card = execute_tool(
            self.env, "create_draft",
            {"model": "res.partner",
             "values": {"name": "Nova Fields",
                        "internal_reference": "X-1",
                        "sales_price": 9}}, {})
        self.assertEqual(kind, "confirm")
        self.assertTrue(
            [line for line in card.get("preview") or []
             if "internal_reference" in line])
        self.assertIn("internal_reference", card.get("notice") or "")
        result = run_confirm(self.env, card)
        partner = self.env["res.partner"].browse(result["res_id"])
        self.assertEqual(partner.name, "Nova Fields")

    def test_coerce_tool_args(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        args = tools._coerce_tool_args({
            "model": "res.partner",
            "domain": "[('name', 'ilike', 'Acme')]",
            "fields": '["name", "email"]',
            "date": "yesterday",
        })
        self.assertIsInstance(args["domain"], list)
        self.assertEqual(args["fields"], ["name", "email"])
        self.assertRegex(args["date"], r"^\d{4}-\d{2}-\d{2}$")
        kept = tools._coerce_tool_args({"domain": "not a list"})
        self.assertEqual(kept["domain"], "not a list")

    def test_memory_guard_passes_when_lean(self):
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        self.assertGreaterEqual(ctrl._check_memory(self.env), 0)

    def test_memory_guard_refuses_when_bloated(self):
        from unittest.mock import patch
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        with patch.object(ctrl, "_rss_mb", return_value=2048):
            with self.assertRaises(ctrl.NovaAiProviderError):
                ctrl._check_memory(self.env)

    def test_turn_helpers(self):
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        self.assertEqual(
            ctrl._strip_thought("<thought>plan</thought>Done"), "Done")
        self.assertTrue(ctrl._data_intent("list unpaid invoices"))
        self.assertFalse(ctrl._data_intent("hello there"))
        self.assertTrue(ctrl._retryable(Exception("503 upstream")))
        self.assertFalse(ctrl._retryable(Exception("401 no auth")))
        self.assertGreaterEqual(ctrl._retry_delay(
            Exception("retry in 5"), 0), 5)

    def test_cancel_invoice_queues_then_cancels(self):
        move = self._invoice()
        kind, _payload, card = execute_tool(
            self.env, "cancel_invoice", {"res_id": move.id}, {})
        self.assertEqual(kind, "confirm")
        result = run_confirm(self.env, card)
        self.assertEqual(result["res_id"], move.id)
        move.invalidate_recordset()
        self.assertEqual(move.state, "cancel")

    def test_cancel_invoice_missing_refuses(self):
        with self.assertRaises(AccessError):
            execute_tool(self.env, "cancel_invoice", {"res_id": 42424242},
                         {})

    def test_cancel_sale_order_queues_then_cancels(self):
        order = self._order()
        kind, _payload, card = execute_tool(
            self.env, "cancel_sale_order", {"res_id": order.id}, {})
        self.assertEqual(kind, "confirm")
        run_confirm(self.env, card)
        order.invalidate_recordset()
        self.assertEqual(order.state, "cancel")

    def test_cancel_sale_order_refuses_confirmed(self):
        order = self._order()
        order.action_confirm()
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "cancel_sale_order", {"res_id": order.id}, {})

    def test_add_order_line_queues_and_creates(self):
        order = self._order()
        product = self.env["product.product"].search(
            [("sale_ok", "=", True)], limit=1)
        before = len(order.order_line)
        kind, _payload, card = execute_tool(
            self.env, "add_order_line",
            {"res_id": order.id, "product_id": product.id,
             "quantity": 3}, {})
        self.assertEqual(kind, "confirm")
        run_confirm(self.env, card)
        order.invalidate_recordset()
        self.assertEqual(len(order.order_line), before + 1)

    def test_add_order_line_bad_qty_refuses(self):
        order = self._order()
        product = self.env["product.product"].search(
            [("sale_ok", "=", True)], limit=1)
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "add_order_line",
                {"res_id": order.id, "product_id": product.id,
                 "quantity": 0}, {})

    def test_invoice_order_refuses_draft(self):
        order = self._order()
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "invoice_order", {"res_id": order.id}, {})

    def test_invoice_order_creates_draft_bill(self):
        order = self._order()
        order.order_line.product_id.product_tmpl_id.write(
            {"invoice_policy": "order"})
        order.action_confirm()
        kind, _payload, card = execute_tool(
            self.env, "invoice_order", {"res_id": order.id}, {})
        self.assertEqual(kind, "confirm")
        result = run_confirm(self.env, card)
        bill = self.env["account.move"].browse(result["res_id"])
        self.assertEqual(bill.state, "draft")
        self.assertEqual(bill.partner_id, self.partner)

    def test_register_payment_refuses_draft(self):
        move = self._invoice()
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "register_payment", {"res_id": move.id}, {})

    def test_register_payment_queues_on_posted(self):
        move = self._invoice(post=True)
        kind, _payload, card = execute_tool(
            self.env, "register_payment", {"res_id": move.id}, {})
        self.assertEqual(kind, "confirm")
        self.assertEqual(card["kind"], "register_payment")

    def test_reverse_move_refuses_draft(self):
        move = self._invoice()
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "reverse_move", {"res_id": move.id}, {})

    def test_reverse_move_queues_on_posted(self):
        move = self._invoice(post=True)
        kind, _payload, card = execute_tool(
            self.env, "reverse_move",
            {"res_id": move.id, "reason": "Test return"}, {})
        self.assertEqual(kind, "confirm")
        self.assertEqual(card["kind"], "reverse_move")

    def test_reconcile_needs_payments(self):
        move = self._invoice()
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "reconcile_moves", {"res_id": move.id}, {})

    def test_validate_delivery_refuses_unknown(self):
        try:
            self.env["stock.picking"]
        except KeyError:
            self.skipTest("No stock module in this database.")
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "validate_delivery", {"res_id": 42424242}, {})

    def test_confirm_purchase_missing_refuses(self):
        try:
            self.env["purchase.order"]
        except KeyError:
            self.skipTest("No purchase module in this database.")
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "confirm_purchase_order",
                {"res_id": 42424242}, {})

    def test_resolve_ticket_missing_refuses(self):
        try:
            self.env["helpdesk.ticket"]
        except KeyError:
            self.skipTest("No helpdesk module in this database.")
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "resolve_ticket", {"res_id": 42424242}, {})

    def test_log_timesheet_guards(self):
        try:
            task_model = self.env["project.task"]
        except KeyError:
            self.skipTest("No project module in this database.")
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "log_timesheet",
                {"task_id": 42424242, "hours": 2}, {})
        task = task_model.create({"name": "Nova test task"})
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "log_timesheet",
                {"task_id": task.id, "hours": 0}, {})
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "log_timesheet",
                {"task_id": task.id, "hours": 25}, {})

    def test_side_effect_allowlist(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        params = self.env["ir.config_parameter"].sudo()
        self.assertTrue(tools._side_effect_allowed(
            self.env, "sale.order", "action_confirm"))
        params.set_str("nova_ai.side_effect_methods",
                       "sale.order.action_confirm")
        try:
            self.assertTrue(tools._side_effect_allowed(
                self.env, "sale.order", "action_confirm"))
            self.assertFalse(tools._side_effect_allowed(
                self.env, "sale.order", "action_cancel"))
            order = self._order()
            with self.assertRaises(AccessError):
                execute_tool(
                    self.env, "execute_method",
                    {"model": "sale.order", "res_id": order.id,
                     "method": "action_cancel"}, {})
        finally:
            params.set_str("nova_ai.side_effect_methods", "")

    def test_field_acl_deny(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        params = self.env["ir.config_parameter"].sudo()
        params.set_str("nova_ai.field_acl",
                       '{"res.partner": {"deny": ["credit_limit"]}}')
        try:
            got = tools._safe_fields(
                self.env, "res.partner", ["name", "credit_limit"])
            self.assertIn("name", got)
            self.assertNotIn("credit_limit", got)
        finally:
            params.set_str("nova_ai.field_acl", "")

    def test_sensitive_read_fence(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        got = tools._safe_fields(
            self.env, "res.users", ["login", "password"])
        self.assertIn("login", got)
        self.assertNotIn("password", got)

    def test_redacted_args(self):
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        self.assertEqual(
            ctrl._redacted_args({"body": "secret", "ids": [1, 2]}),
            {"body": "str", "ids": "list[2]"})

    def test_execute_method_confirms_action(self):
        order = self._order()
        kind, _payload, card = execute_tool(
            self.env, "execute_method",
            {"model": "sale.order", "res_id": order.id,
             "method": "action_confirm"}, {})
        self.assertEqual(kind, "confirm")
        self.assertIn("action_confirm", card["title"])
        self.assertEqual(order.state, "draft")
        run_confirm(self.env, card)
        order.invalidate_recordset()
        self.assertNotEqual(order.state, "draft")

    def test_read_group_counts(self):
        self.env["res.partner"].create({"name": "Nova Group A"})
        self.env["res.partner"].create({"name": "Nova Group B"})
        kind, payload, card = execute_tool(
            self.env, "read_group",
            {"model": "res.partner", "domain": [],
             "fields": ["__count"], "groupby": []}, {})
        self.assertEqual(kind, "result")
        total = sum((row.get("__count") or 0)
                    for row in payload["rows"])
        self.assertTrue(total >= 2)
        self.assertIsNone(card)

    def test_fields_get_lists(self):
        kind, payload, card = execute_tool(
            self.env, "fields_get", {"model": "res.partner"}, {})
        self.assertEqual(kind, "result")
        names = [f["name"] for f in payload["fields"]]
        self.assertIn("name", names)
        self.assertIn("email", names)
        for entry in payload["fields"]:
            self.assertNotIn("groups", entry)
        self.assertIsNone(card)

    def test_fields_get_query_filters(self):
        kind, payload, _card = execute_tool(
            self.env, "fields_get",
            {"model": "res.partner", "query": "DATE"}, {})
        self.assertEqual(kind, "result")
        self.assertTrue(payload["fields"])
        for entry in payload["fields"]:
            hay = (entry["name"] + " " + (entry.get("label") or "")
                   ).lower()
            self.assertIn("date", hay)

    def test_fields_get_exact_list(self):
        kind, payload, _card = execute_tool(
            self.env, "fields_get",
            {"model": "res.partner",
             "fields": ["name", "email", "no_such_field"]}, {})
        self.assertEqual(kind, "result")
        self.assertEqual(
            sorted(f["name"] for f in payload["fields"]),
            ["email", "name"])

    def test_fields_get_query_and_fields_conflict(self):
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "fields_get",
                {"model": "res.partner", "query": "date",
                 "fields": ["name"]}, {})

    def test_capped_tool_text_marks_truncation(self):
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        short = ctrl._capped_tool_text(
            "search_records", {"rows": [{"a": 1}]})
        self.assertNotIn("truncated", short)
        big = ctrl._capped_tool_text(
            "fields_get",
            {"fields": [{"name": "f%d" % i, "pad": "x" * 200}
                        for i in range(60)]})
        self.assertIn("[truncated", big)
        self.assertIn("query", big)

    def test_inspect_lists_business_methods(self):
        kind, payload, card = execute_tool(
            self.env, "inspect_method", {"model": "sale.order"}, {})
        self.assertEqual(kind, "result")
        self.assertIn("action_confirm", payload["methods"])
        for bad in ("create", "write", "unlink", "copy"):
            self.assertNotIn(bad, payload["methods"])
        self.assertFalse(
            [m for m in payload["methods"] if m.startswith("_")])
        self.assertIsNone(card)

    def test_inspect_signature_without_source(self):
        import json
        kind, payload, card = execute_tool(
            self.env, "inspect_method",
            {"model": "sale.order", "method": "action_confirm"}, {})
        self.assertEqual(kind, "result")
        self.assertEqual(payload["method"], "action_confirm")
        self.assertIsInstance(payload["params"], list)
        blob = json.dumps(payload, default=str)
        self.assertNotIn("odoo/addons", blob)
        self.assertNotIn(".py", blob)
        self.assertIsNone(card)

    def test_inspect_unknown_and_private_refuse(self):
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "inspect_method",
                {"model": "sale.order", "method": "no_such_xyz"}, {})
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "inspect_method",
                {"model": "sale.order", "method": "_private"}, {})
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "inspect_method",
                {"model": "res.users"}, {})

    def test_migration_grants_new_tools(self):
        import importlib.util
        import os
        path = os.path.normpath(os.path.join(
            os.path.dirname(__file__), "..", "migrations", "20.0.1.1.0",
            "post-migration.py"))
        self.assertTrue(os.path.isfile(path))
        spec = importlib.util.spec_from_file_location(
            "nova_ai_test_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        provider = self.env["nova.ai.provider"].create({
            "name": "Migration Provider", "kind": "openai_compat",
            "base_url": "http://unit.invalid/v1",
        })
        model = self.env["nova.ai.model"].create({
            "provider_id": provider.id, "name": "migration-model",
            "supports_tools": True, "context_length": 16000,
        })
        execute_tool = self.env["nova.ai.tool"].search(
            [("code", "=", "execute_method")], limit=1)
        self.assertTrue(execute_tool)
        agent = self.env["nova.ai.agent"].create({
            "name": "Migration Agent", "model_id": model.id,
            "tool_ids": [(6, 0, [execute_tool.id])],
        })
        self.assertNotIn(
            "inspect_method", agent.tool_ids.mapped("code"))
        migration.migrate(self.env.cr, "20.0.1.1.0")
        agent.invalidate_recordset()
        granted = set(agent.tool_ids.mapped("code"))
        self.assertIn("inspect_method", granted)
        self.assertIn("cancel_invoice", granted)
        self.assertIn("execute_method", granted)
        other = self.env["nova.ai.agent"].create({
            "name": "Bare Agent", "model_id": model.id,
            "tool_ids": [(6, 0, [])],
        })
        migration.migrate(self.env.cr, "20.0.1.1.0")
        other.invalidate_recordset()
        self.assertNotIn("inspect_method",
                         other.tool_ids.mapped("code"))

    def test_inspect_lists_business_methods(self):
        kind, payload, card = execute_tool(
            self.env, "inspect_method", {"model": "sale.order"}, {})
        self.assertEqual(kind, "result")
        self.assertIn("action_confirm", payload["methods"])
        for bad in ("create", "write", "unlink", "copy"):
            self.assertNotIn(bad, payload["methods"])
        self.assertFalse(
            [m for m in payload["methods"] if m.startswith("_")])
        self.assertIsNone(card)

    def test_inspect_signature_without_source(self):
        import json
        kind, payload, card = execute_tool(
            self.env, "inspect_method",
            {"model": "sale.order", "method": "action_confirm"}, {})
        self.assertEqual(kind, "result")
        self.assertEqual(payload["method"], "action_confirm")
        self.assertIsInstance(payload["params"], list)
        blob = json.dumps(payload, default=str)
        self.assertNotIn("odoo/addons", blob)
        self.assertNotIn(".py", blob)
        self.assertIsNone(card)

    def test_inspect_unknown_and_private_refuse(self):
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "inspect_method",
                {"model": "sale.order", "method": "no_such_xyz"}, {})
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "inspect_method",
                {"model": "sale.order", "method": "_private"}, {})
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "inspect_method",
                {"model": "res.users"}, {})

    def test_fetch_extracts_outlinks(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        html = (
            '<a href="/docs/start">Start</a>'
            '<a href="https://example.com/a">A</a>'
            '<a href="https://example.com/a">A again</a>'
            '<a href="javascript:void(0)">x</a>'
            '<a href="mailto:a@b.c">y</a>'
            '<a href="#frag">z</a>')
        links = tools._extract_outlinks(
            html, "https://example.com/docs/")
        self.assertIn("https://example.com/docs/start", links)
        self.assertIn("https://example.com/a", links)
        self.assertEqual(len(links), len(set(links)))
        self.assertTrue(all(
            l.startswith("http") for l in links))

    def test_list_attachments_finds(self):
        import base64
        att = self.env["ir.attachment"].create({
            "name": "unit-note.txt", "mimetype": "text/plain",
            "res_model": "res.partner", "res_id": self.partner.id,
            "raw": base64.b64encode(b"hello")})
        kind, payload, card = execute_tool(
            self.env, "list_attachments",
            {"model": "res.partner", "res_id": self.partner.id},
            {})
        self.assertTrue(any(a["id"] == att.id
                            for a in payload["attachments"]))
        self.assertIsNone(card)

    def test_search_paginates(self):
        for name in ("Page Alpha", "Page Beta", "Page Gamma"):
            self.env["res.partner"].create({"name": name})
        domain = [("name", "like", "Page %")]
        kind, first, card = execute_tool(
            self.env, "search_records",
            {"model": "res.partner", "domain": domain,
             "fields": ["name"], "limit": 2,
             "order": "name asc"}, {})
        self.assertEqual(kind, "result")
        self.assertEqual(first["total"], 3)
        self.assertEqual(len(first["rows"]), 2)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_offset"], 2)
        kind, second, card = execute_tool(
            self.env, "search_records",
            {"model": "res.partner", "domain": domain,
             "fields": ["name"], "limit": 2,
             "offset": first["next_offset"],
             "order": "name asc"}, {})
        self.assertEqual(len(second["rows"]), 1)
        self.assertFalse(second["has_more"])
        self.assertIsNone(second["next_offset"])
        names = [r["name"] for r in first["rows"]]
        self.assertEqual(names, ["Page Alpha", "Page Beta"])

    def test_search_order_desc_and_bad_order(self):
        for name in ("Ord A", "Ord B"):
            self.env["res.partner"].create({"name": name})
        domain = [("name", "like", "Ord %")]
        kind, out, card = execute_tool(
            self.env, "search_records",
            {"model": "res.partner", "domain": domain,
             "fields": ["name"], "order": "name desc"}, {})
        names = [r["name"] for r in out["rows"]]
        self.assertEqual(names, ["Ord B", "Ord A"])
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "search_records",
                {"model": "res.partner", "order": "nope asc"}, {})
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "search_records",
                {"model": "res.partner", "order": "name sideways"},
                {})

    def test_read_related_paginates_and_sorts(self):
        parent = self.env["res.partner"].create({"name": "Rel Parent"})
        for name in ("Rel Kid A", "Rel Kid B", "Rel Kid C"):
            self.env["res.partner"].create(
                {"name": name, "parent_id": parent.id})
        kind, out, card = execute_tool(
            self.env, "read_related",
            {"model": "res.partner", "res_id": parent.id,
             "field": "child_ids", "fields": ["name"],
             "limit": 2, "order": "name desc"}, {})
        self.assertEqual(out["count"], 3)
        self.assertEqual(
            [r["name"] for r in out["rows"]],
            ["Rel Kid C", "Rel Kid B"])
        self.assertTrue(out["has_more"])
        kind, rest, card = execute_tool(
            self.env, "read_related",
            {"model": "res.partner", "res_id": parent.id,
             "field": "child_ids", "fields": ["name"],
             "limit": 2, "offset": out["next_offset"],
             "order": "name desc"}, {})
        self.assertEqual([r["name"] for r in rest["rows"]],
                         ["Rel Kid A"])
        self.assertFalse(rest["has_more"])

    def test_many2one_name_unique_resolves(self):
        vendor = self.env["res.partner"].create(
            {"name": "Only Vendor XYZ"})
        kind, payload, card = execute_tool(
            self.env, "set_field",
            {"model": "res.partner", "res_id": self.partner.id,
             "field": "parent_id", "value": "Only Vendor XYZ"}, {})
        self.assertEqual(kind, "confirm")
        self.assertEqual(card["payload"]["value"], vendor.id)

    def test_many2one_name_ambiguous_asks(self):
        self.env["res.partner"].create({"name": "Twin Vendor"})
        self.env["res.partner"].create({"name": "Twin Vendor"})
        kind, payload, card = execute_tool(
            self.env, "set_field",
            {"model": "res.partner", "res_id": self.partner.id,
             "field": "parent_id", "value": "Twin Vendor"}, {})
        self.assertEqual(kind, "ask")
        self.assertIn("Twin Vendor", payload["question"])
        self.assertEqual(len(payload["options"]), 2)
        self.assertIsNone(card)

    def test_many2one_name_missing_still_fails(self):
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "set_field",
                {"model": "res.partner", "res_id": self.partner.id,
                 "field": "parent_id", "value": "Nobody Here ZZ"}, {})

    def test_set_field_card_carries_preview(self):
        kind, payload, card = execute_tool(
            self.env, "set_field",
            {"model": "res.partner", "res_id": self.partner.id,
             "field": "phone", "value": "999"}, {})
        self.assertEqual(kind, "confirm")
        self.assertEqual(len(card["preview"]), 1)
        self.assertIn("999", card["preview"][0])

    def test_create_draft_card_carries_preview(self):
        kind, payload, card = execute_tool(
            self.env, "create_draft",
            {"model": "res.partner",
             "values": {"name": "Preview Co", "email": "a@b.c"}}, {})
        self.assertEqual(kind, "confirm")
        self.assertTrue(any("Preview Co" in line
                            for line in card["preview"]))
        self.assertTrue(any("Email" in line or "email" in line
                            for line in card["preview"]))

    def test_create_draft_bill_title_uses_move_type(self):
        # F4: account.move._description is the generic "Journal
        # Entry" — approvers must see Bill vs Invoice (T-806 note).
        _kind, _payload, bill = execute_tool(
            self.env, "create_draft",
            {"model": "account.move",
             "values": {"move_type": "in_invoice"}}, {})
        self.assertIn("Vendor Bill", bill["title"])
        self.assertNotIn("Journal Entry", bill["title"])
        _kind, _payload, inv = execute_tool(
            self.env, "create_draft",
            {"model": "account.move",
             "values": {"move_type": "out_invoice"}}, {})
        self.assertIn("Customer Invoice", inv["title"])

    def test_lead_dupe_asks(self):
        self.env["crm.lead"].create({
            "name": "Dupe Lead", "email_from": "dupe@example.com"})
        kind, payload, card = execute_tool(
            self.env, "create_draft",
            {"model": "crm.lead",
             "values": {"name": "Dupe Lead",
                        "email_from": "dupe@example.com"}}, {})
        self.assertEqual(kind, "ask")
        self.assertIn("already exists", payload["question"])
        self.assertIn("Create anyway", payload["options"])
        self.assertIsNone(card)

    def test_lead_dupe_force_queues(self):
        self.env["crm.lead"].create({
            "name": "Dupe Lead", "email_from": "dupe@example.com"})
        kind, payload, card = execute_tool(
            self.env, "create_draft",
            {"model": "crm.lead",
             "values": {"name": "Dupe Lead",
                        "email_from": "dupe@example.com"},
             "force": True}, {})
        self.assertEqual(kind, "confirm")
        self.assertEqual(card["kind"], "create_draft")

    def test_dupe_affirmed_detects(self):
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl

        class Msg:
            def __init__(self, content):
                self.content = content

        asked = [Msg("<p>Create anyway</p>"),
                 Msg("<p>A matching lead already exists: X. "
                     "Create anyway or open?</p>")]
        self.assertTrue(
            ctrl._dupe_affirmed(asked, "Create anyway."))
        self.assertFalse(ctrl._dupe_affirmed(asked, "no, stop"))
        self.assertFalse(ctrl._dupe_affirmed([], "create anyway"))

    def test_dupe_affirm_bridges_force(self):
        provider = self.env["nova.ai.provider"].create({
            "name": "Bridge Provider", "kind": "openai_compat",
            "base_url": "http://unit.invalid/v1",
        })
        model = self.env["nova.ai.model"].create({
            "provider_id": provider.id, "name": "bridge-model",
            "supports_tools": True, "context_length": 16000,
        })
        tools = self.env["nova.ai.tool"].search(
            [("code", "in", ("create_draft",))])
        agent = self.env["nova.ai.agent"].create({
            "name": "Bridge Agent", "model_id": model.id,
            "tool_ids": [(6, 0, tools.ids)],
        })
        self.env["crm.lead"].create({
            "name": "Bridge Lead", "email_from": "bridge@example.com"})
        stub1 = _StubChat([
            _text_tool_call("create_draft", {
                "model": "crm.lead",
                "values": {"name": "Bridge Lead",
                           "email_from": "bridge@example.com"}}),
        ])
        stub2 = _StubChat([
            _text_tool_call("create_draft", {
                "model": "crm.lead",
                "values": {"name": "Bridge Lead",
                           "email_from": "bridge@example.com"}}),
            _text_reply("Queued after confirm."),
        ])
        orig = type(agent.model_id)._chat

        def fake1(record, messages, tools=None, extra=None):
            return stub1(record, messages, tools=tools, extra=extra)

        def fake2(record, messages, tools=None, extra=None):
            return stub2(record, messages, tools=tools, extra=extra)

        type(agent.model_id)._chat = fake1
        try:
            session = self.env["nova.ai.session"].create(
                {"agent_id": agent.id})
            first = run_chat_turn(
                self.env, agent, session, "new lead Bridge", {})
            self.assertEqual(first["cards"], [])
            self.assertIn("already exists", first["reply"])
            type(agent.model_id)._chat = fake2
            second = run_chat_turn(
                self.env, agent, session, "Create anyway.", {})
        finally:
            type(agent.model_id)._chat = orig
        self.assertEqual(len(second["cards"]), 1)

    def test_read_record_batch(self):
        first = self.env["res.partner"].create({"name": "Batch One"})
        second = self.env["res.partner"].create({"name": "Batch Two"})
        kind, out, card = execute_tool(
            self.env, "read_record",
            {"model": "res.partner",
             "res_ids": [first.id, second.id, 999999999],
             "fields": ["name"]}, {})
        self.assertEqual(kind, "result")
        self.assertEqual(
            [r["name"] for r in out["records"]],
            ["Batch One", "Batch Two"])
        self.assertEqual(out["missing"], [999999999])
        self.assertIsNone(card)

    def test_read_record_batch_cap(self):
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "read_record",
                {"model": "res.partner",
                 "res_ids": list(range(1, 12))}, {})

    def test_read_record_single_shape_unchanged(self):
        kind, out, card = execute_tool(
            self.env, "read_record",
            {"model": "res.partner", "res_id": self.partner.id,
             "fields": ["name"]}, {})
        self.assertEqual(kind, "result")
        self.assertIsInstance(out, dict)
        self.assertNotIn("records", out)

    def test_read_related_batch(self):
        first = self.env["res.partner"].create({"name": "BB Parent 1"})
        second = self.env["res.partner"].create({"name": "BB Parent 2"})
        self.env["res.partner"].create(
            {"name": "BB Kid", "parent_id": first.id})
        kind, out, card = execute_tool(
            self.env, "read_related",
            {"model": "res.partner",
             "res_ids": [first.id, second.id],
             "field": "child_ids", "fields": ["name"]}, {})
        self.assertEqual(kind, "result")
        by_id = {r["res_id"]: r for r in out["records"]}
        self.assertEqual(by_id[first.id]["count"], 1)
        self.assertEqual(by_id[second.id]["count"], 0)
        self.assertEqual(out["missing"], [])

    def test_read_attachment_text(self):
        import base64
        att = self.env["ir.attachment"].create({
            "name": "unit-note.txt", "mimetype": "text/plain",
            "res_model": "res.partner", "res_id": self.partner.id,
            "raw": base64.b64encode(b"hello attachment")})
        kind, out, card = execute_tool(
            self.env, "read_attachment",
            {"attachment_id": att.id}, {})
        self.assertEqual(kind, "result")
        self.assertEqual(out["kind"], "text")
        self.assertIn("hello attachment", out["content"])
        self.assertIsNone(card)

    def test_read_attachment_refuses_foreign_model(self):
        import base64
        att = self.env["ir.attachment"].create({
            "name": "unit-secret.txt", "mimetype": "text/plain",
            "res_model": "res.users", "res_id": self.env.user.id,
            "raw": base64.b64encode(b"secret")})
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "read_attachment",
                {"attachment_id": att.id}, {})

    def test_read_attachment_image_envelope(self):
        import base64
        att = self.env["ir.attachment"].create({
            "name": "unit-pic.png", "mimetype": "image/png",
            "res_model": "res.partner", "res_id": self.partner.id,
            "raw": base64.b64encode(b"\x89PNG" + b"0" * 100)})
        kind, out, card = execute_tool(
            self.env, "read_attachment",
            {"attachment_id": att.id}, {})
        self.assertEqual(kind, "result")
        self.assertEqual(out["kind"], "image")
        self.assertNotIn("content", out)

    def _scoped_agent(self, scope):
        return self.env["nova.ai.agent"].create({
            "name": "Scope Agent %s" % (scope or "open"),
            "model_scope": scope, "active": False})

    def test_agent_scope_blocks_other_models(self):
        agent = self._scoped_agent("sale.order,sale.order.line")
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "count_records", {"model": "res.partner"},
                {"agent": agent})
        kind, out, card = execute_tool(
            self.env, "count_records", {"model": "sale.order"},
            {"agent": agent})
        self.assertEqual(kind, "result")
        self.assertIn("count", out)

    def test_agent_scope_open_by_default(self):
        agent = self._scoped_agent("")
        kind, out, card = execute_tool(
            self.env, "count_records", {"model": "res.partner"},
            {"agent": agent})
        self.assertEqual(kind, "result")

    def test_agent_scope_checked_at_confirm(self):
        agent = self._scoped_agent("sale.order")
        kind, payload, card = execute_tool(
            self.env, "log_note",
            {"model": "res.partner", "res_id": self.partner.id,
             "body": "Out of scope."}, {})
        self.assertEqual(kind, "confirm")
        with self.assertRaises(AccessError):
            run_confirm(self.env, card, agent)
        self.assertFalse(self.partner.message_ids.filtered(
            lambda m: "Out of scope." in (m.body or "")))

    def _grouped_tool_agent(self):
        group = self.env["res.groups"].create({"name": "Nova Gated"})
        tool = self.env["nova.ai.tool"].search(
            [("code", "=", "count_records")], limit=1)
        tool.write({"group_ids": [(6, 0, [group.id])]})
        provider = self.env["nova.ai.provider"].create({
            "name": "Group Provider", "kind": "openai_compat",
            "base_url": "http://unit.invalid/v1",
        })
        model = self.env["nova.ai.model"].create({
            "provider_id": provider.id, "name": "group-model",
            "supports_tools": True, "context_length": 16000,
        })
        agent = self.env["nova.ai.agent"].create({
            "name": "Group Agent", "model_id": model.id,
            "tool_ids": [(6, 0, [tool.id])], "active": False})
        return agent, group, tool

    def test_grouped_tool_hidden_until_granted(self):
        agent, group, tool = self._grouped_tool_agent()
        codes = set(agent.tools_for_tier().mapped("code"))
        member = group.id in self.env.user.all_group_ids.ids
        if member:
            self.skipTest("Test user already holds the gate group.")
        self.assertNotIn("count_records", codes)
        self.env.user.write({"group_ids": [(4, group.id)]})
        codes = set(agent.tools_for_tier().mapped("code"))
        self.assertIn("count_records", codes)

    def test_grouped_card_refuses_at_confirm(self):
        agent, group, tool = self._grouped_tool_agent()
        if group.id in self.env.user.all_group_ids.ids:
            self.skipTest("Test user already holds the gate group.")
        card = {"token": "gated:1", "kind": "count_records",
                "title": "Gated", "summary": "Gated",
                "payload": {"model": "res.partner"}}
        with self.assertRaises(AccessError):
            run_confirm(self.env, card, agent)


@tagged("post_install", "-at_install")
class TestNovaAiInlineContext(TransactionCase):

    def test_pinned_record_grounds(self):
        from odoo.addons.nova_ai.controllers.nova_ai import (
            _record_context)
        partner = self.env["res.partner"].create({"name": "Ctx Co"})
        out = _record_context(
            self.env, None,
            {"model": "res.partner", "res_id": partner.id})
        self.assertIn("res.partner#%s" % partner.id, out)
        self.assertIn("Ctx Co", out)

    def test_thread_excerpt_grounds(self):
        from odoo.addons.nova_ai.controllers.nova_ai import (
            _record_context)
        partner = self.env["res.partner"].create({"name": "Thread Co"})
        partner.message_post(body="<p>Please send the invoice</p>",
                             message_type="comment")
        out = _record_context(
            self.env, None,
            {"model": "res.partner", "res_id": partner.id})
        self.assertIn("Recent thread", out)
        self.assertIn("send the invoice", out)

    def test_missing_record_grounds_nothing(self):
        from odoo.addons.nova_ai.controllers.nova_ai import (
            _record_context)
        out = _record_context(
            self.env, None,
            {"model": "res.partner", "res_id": 999999999})
        self.assertEqual(out, "")

    def test_unknown_op_has_no_prompt(self):
        from odoo.addons.nova_ai.controllers.nova_ai import (
            _inline_prompt_text)
        self.assertEqual(_inline_prompt_text(self.env, "nope"), "")


@tagged("post_install", "-at_install")
class TestNovaAiFetchHardening(TransactionCase):

    def test_userinfo_stripped(self):
        from unittest.mock import patch
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        with patch.object(tools, "_is_public_host",
                          return_value=True):
            clean = tools._normalize_fetch_url(
                self.env, "https://user:pass@example.com/x")
        self.assertEqual(clean, "https://example.com/x")
        self.assertNotIn("user", clean)
        self.assertNotIn("pass", clean)

    def test_odd_port_refused(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        with self.assertRaises(AccessError):
            tools._normalize_fetch_url(
                self.env, "http://example.com:22/admin")

    def test_private_host_refused(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        with self.assertRaises(AccessError):
            tools._normalize_fetch_url(self.env, "http://127.0.0.1/")

    def test_redirect_to_private_refused(self):
        from unittest.mock import patch
        from odoo.addons.nova_ai.models import nova_ai_tools as tools

        class Resp:
            status_code = 302
            headers = {"location": "http://127.0.0.1/secret"}

            def close(self):
                pass

        with patch.object(tools.requests, "get",
                          return_value=Resp()) as get:
            # Public first hop, private redirect target: the real
            # _is_public_host answers per host.
            with patch.object(tools, "_is_public_host",
                              side_effect=lambda h: h != "127.0.0.1"):
                with self.assertRaises(AccessError):
                    tools._fetch_public_url(
                        self.env, "https://example.com/go")
            # The private target was never requested.
            self.assertEqual(get.call_count, 1)

    def test_binary_mime_refused(self):
        from unittest.mock import patch
        from odoo.addons.nova_ai.models import nova_ai_tools as tools

        class Resp:
            status_code = 200
            headers = {"content-type": "application/pdf"}

            def close(self):
                pass

        with patch.object(tools.requests, "get",
                          return_value=Resp()):
            with self.assertRaises(AccessError):
                tools._fetch_public_url(
                    self.env, "https://example.com/f.pdf")

    def test_html_page_read(self):
        from unittest.mock import patch
        from odoo.addons.nova_ai.models import nova_ai_tools as tools

        class Raw:
            decode_content = False

        class Resp:
            status_code = 200
            headers = {"content-type": "text/html; charset=utf-8"}
            raw = Raw()

            def close(self):
                pass

            def iter_content(self, size):
                yield (b"<html><head><title>Hi</title></head>"
                       b"<body>hello</body></html>")

        with patch.object(tools.requests, "get",
                          return_value=Resp()):
            with patch.object(tools, "_is_public_host",
                              return_value=True):
                out = tools.tool_fetch_url(
                    self.env, {"url": "https://example.com/"}, {})
        self.assertEqual(out["title"], "Hi")
        self.assertIn("hello", out["text"])

    def test_fetch_emits_stages(self):
        from unittest.mock import patch
        from odoo.addons.nova_ai.models import nova_ai_tools as tools

        class Raw:
            decode_content = False

        class Resp:
            status_code = 200
            headers = {"content-type": "text/plain"}
            raw = Raw()

            def close(self):
                pass

            def iter_content(self, size):
                yield b"plain words"

        stages = []
        with patch.object(tools.requests, "get",
                          return_value=Resp()):
            with patch.object(tools, "_is_public_host",
                              return_value=True):
                tools.tool_fetch_url(
                    self.env, {"url": "https://example.com/t.txt"},
                    {"progress": stages.append})
        self.assertIn("connecting", stages)
        self.assertIn("reading", stages)


@tagged("post_install", "-at_install")
class TestNovaAiReport(TransactionCase):

    def test_tables_parsed_and_body_kept(self):
        from odoo.addons.nova_ai.models import nova_ai_report as rep
        md = ("Overdue invoices:\n\n| Name | Amount |\n"
              "| --- | --- |\n| Acme | 8000 |\n\nPay soon.")
        tables = rep.parse_markdown_tables(md)
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0][0], ["Name", "Amount"])
        self.assertNotIn("| Name", rep.strip_tables(md))
        self.assertIn("Pay soon.", rep.strip_tables(md))

    def test_pdf_bytes_and_filename(self):
        from odoo.addons.nova_ai.models import nova_ai_report as rep
        md = ("Overdue invoices:\n\n| Name | Amount |\n"
              "| --- | --- |\n| Acme | 8000 |\n")
        pdf = rep.build_report_pdf(
            md, brand={"name": "Zeetavix", "color": "#0E7C86"})
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertRegex(rep.smart_filename("nova-answer", 12, 3),
                         r"nova-answer-s12-n3-\d{4}-\d{2}-\d{2}\.pdf")

    def test_bad_brand_color_falls_back(self):
        from odoo.addons.nova_ai.models import nova_ai_report as rep
        pdf = rep.build_report_pdf(
            "hello", brand={"name": "X", "color": "not-a-color"})
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_report_png_bytes(self):
        from odoo.addons.nova_ai.models import nova_ai_report as rep
        md = ("| Name | Amount | State |\n| --- | --- | --- |\n"
              "| Acme | 8000 | Overdue |\n| Beta | 200 | Paid |\n")
        png = rep.build_table_png(md, brand={"name": "Zeetavix"})
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertGreater(len(png), 1000)
        with self.assertRaises(RuntimeError):
            rep.build_table_png("no tables here")
        self.assertTrue(rep._is_number_cell("$8,000.00"))
        self.assertFalse(rep._is_number_cell("Overdue"))

    def test_report_pills_and_kpis(self):
        from odoo.addons.nova_ai.models import nova_ai_report as rep
        self.assertEqual(rep._pill_bucket("Overdue"), "red")
        self.assertEqual(rep._pill_bucket("Paid"), "green")
        self.assertEqual(rep._pill_bucket("Draft"), "amber")
        self.assertEqual(rep._pill_bucket("Acme Corp"), "")
        md = ("| Name | Amount | State |\n| --- | --- | --- |\n"
              "| Acme | 8000 | Overdue |\n| Beta | 200 | Paid |\n")
        tables = rep.parse_markdown_tables(md)
        boxes = rep._kpis(tables)
        self.assertEqual(boxes[0], ("Records", "2"))
        self.assertEqual(boxes[1], ("Total", "8200.00"))
        widths = rep._col_widths(tables[0][0], tables[0][1], 500)
        self.assertEqual(len(widths), 3)
        self.assertTrue(all(w >= 35 for w in widths))
        pdf = rep.build_report_pdf(md)
        self.assertTrue(pdf.startswith(b"%PDF"))


@tagged("post_install", "-at_install")
class TestNovaAiBatchSlices(TransactionCase):

    def _loop_agent(self, model):
        tools = self.env["nova.ai.tool"].search(
            [("code", "in", ("count_records", "log_note"))])
        return self.env["nova.ai.agent"].create({
            "name": "Batch Agent", "model_id": model.id,
            "tool_ids": [(6, 0, tools.ids)],
        })

    def _models(self, **vals):
        provider = self.env["nova.ai.provider"].create({
            "name": "Batch Provider", "kind": "openai_compat",
            "base_url": "http://unit.invalid/v1",
        })
        data = {"provider_id": provider.id, "name": "primary-model",
                "supports_tools": True, "context_length": 16000}
        data.update(vals)
        primary = self.env["nova.ai.model"].create(data)
        fallback = self.env["nova.ai.model"].create({
            "provider_id": provider.id, "name": "fallback-model",
            "supports_tools": True, "context_length": 16000,
        })
        primary.write({"fallback_model_id": fallback.id})
        return primary, fallback

    def test_ask_user_header_and_object_options(self):
        kind, payload, card = execute_tool(
            self.env, "ask_user",
            {"question": "Which one?",
             "header": "Pick a customer, please, right now",
             "options": [
                 "Acme Corp",
                 {"label": "Acme Ltd", "description": "Lyon"},
                 "Acme Corp", "  ",
                 {"label": "Acme Ltd", "description": "Lyon"},
                 {"label": "", "description": "blank"},
                 "Beta", "Gamma", "Delta", "Epsilon"]}, {})
        self.assertEqual(kind, "ask")
        self.assertIsNone(card)
        self.assertEqual(payload["header"], "Pick a customer, please, right"[:30])
        labels = [o if isinstance(o, str) else o["label"]
                  for o in payload["options"]]
        self.assertEqual(
            labels, ["Acme Corp", "Acme Ltd", "Beta", "Gamma",
                     "Delta", "Epsilon"][:6])
        self.assertEqual(len(payload["options"]), 6)
        shaped = [o for o in payload["options"]
                  if isinstance(o, dict)]
        self.assertEqual(
            shaped, [{"label": "Acme Ltd", "description": "Lyon"}])

    def test_ask_user_option_cap_six(self):
        from odoo.addons.nova_ai.models.nova_ai_tools import AskUser
        ask = AskUser("q", ["a%d" % i for i in range(8)])
        self.assertEqual(len(ask.options), 6)

    def test_system_prompt_tone(self):
        from types import SimpleNamespace
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        agent = SimpleNamespace(system_prompt="", instructions="")
        self.assertIn("Tone:", ctrl._system_prompt(
            agent, "t2", "", "concise"))
        self.assertNotIn("Tone:", ctrl._system_prompt(
            agent, "t2", "", "loud"))
        self.assertNotIn("Tone:", ctrl._system_prompt(agent, "t2", ""))

    def test_system_prompt_forbids_invented_values(self):
        from types import SimpleNamespace
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        agent = SimpleNamespace(system_prompt="", instructions="")
        for tier in ("t2", "t3"):
            prompt = ctrl._system_prompt(agent, tier, "")
            self.assertIn("never invent", prompt)
            self.assertIn("omit the field instead", prompt)

    def test_system_prompt_partner_ranks_not_flags(self):
        from types import SimpleNamespace
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        agent = SimpleNamespace(system_prompt="", instructions="")
        for tier in ("t2", "t3"):
            prompt = ctrl._system_prompt(agent, tier, "")
            self.assertIn("customer_rank", prompt)
            self.assertIn("never filter on credit", prompt)

    def test_system_prompt_no_promise_endings(self):
        from types import SimpleNamespace
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        agent = SimpleNamespace(system_prompt="", instructions="")
        prompt = ctrl._system_prompt(agent, "t2", "")
        self.assertIn("Never end the turn on a promise", prompt)

    def test_user_now_line_prefers_browser_tz(self):
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        line = ctrl._user_now_line(
            self.env, {"browser_tz": "Asia/Kolkata"})
        self.assertIn("Asia/Kolkata", line)
        self.assertIn("UTC+05:30", line)
        self.assertIn("never say you have no clock", line)

    def test_user_now_line_bad_zone_falls_back(self):
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        line = ctrl._user_now_line(
            self.env, {"browser_tz": "Not/A Zone!!"})
        self.assertTrue(line.startswith("Today is "))
        self.assertNotIn("Not/A Zone", line)

    def test_system_prompt_leads_with_clock(self):
        from types import SimpleNamespace
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        agent = SimpleNamespace(system_prompt="", instructions="")
        prompt = ctrl._system_prompt(
            agent, "t2", "", now_line="Today is X.")
        self.assertTrue(prompt.startswith("Today is X."))
        self.assertIn("product.template", prompt)

    def test_strip_thought_repairs_replacement_char(self):
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        self.assertEqual(ctrl._strip_thought("a � b"), "a — b")

    def test_tool_call_tag_shapes_parse(self):
        from odoo.addons.nova_ai.models import nova_ai_model as mod
        Model = self.env["nova.ai.model"]
        bare = Model._tool_call_from_text("<tool_call>ask_user</tool_call>")
        self.assertEqual(bare, [{"name": "ask_user", "arguments": {}}])
        action = Model._tool_call_from_text(
            "<tool_call>\n{\"action\": \"create_draft\", "
            "\"model\": \"purchase.order\", \"params\": {\"x\": 1}}\n"
            "</tool_call>")
        self.assertEqual(action[0]["name"], "create_draft")
        self.assertEqual(action[0]["arguments"]["model"],
                         "purchase.order")
        self.assertEqual(Model._tool_call_from_text(
            "Just prose, no tags."), [])

    def test_tagged_tool_call_executes_in_loop(self):
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        primary, _fallback = self._models()
        agent = self._loop_agent(primary)
        orig = type(primary)._chat
        tagged = {"choices": [{"message": {"content":
            "<tool_call>\n{\"name\": \"count_records\", "
            "\"arguments\": {\"model\": \"res.partner\"}}\n"
            "</tool_call>"}}]}
        calls = [tagged,
                 {"choices": [{"message": {"content": "done"}}]}]

        def fake(record, messages, tools=None, extra=None):
            return calls.pop(0) if calls else \
                {"choices": [{"message": {"content": "done"}}]}

        type(primary)._chat = fake
        try:
            session = self.env["nova.ai.session"].create({
                "agent_id": agent.id})
            result = ctrl.run_chat_turn(
                self.env, agent, session, "hi", {})
        finally:
            type(primary)._chat = orig
        self.assertTrue([s for s in result["steps"]
                         if s.get("tool") == "count_records"
                         and "error" not in s])

    def test_honesty_postcheck_corrects_card_claim(self):
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        fixed = ctrl._honesty_postcheck(
            "Approve the card to apply the change.", [], False,
            None, [])
        self.assertIn("no approval card", fixed)
        benign = ctrl._honesty_postcheck(
            "Nothing was queued — no cards were created.", [], False,
            None, [])
        self.assertNotIn("Correction", benign)
        asked = ctrl._honesty_postcheck(
            "Which one? - A - B",
            [{"tool": "ask_user", "asked": True}], True, None, [])
        self.assertNotIn("Correction", asked)

    def test_honesty_postcheck_flags_bare_promise(self):
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        fixed = ctrl._honesty_postcheck(
            "Retrying the lookup at the Odoo documentation index.",
            [], False, None,
            [{"tool": "fetch_url", "error": "That page could not "
                                            "be reached."}])
        self.assertIn("couldn't be completed", fixed)
        self.assertIn("fetch_url", fixed)
        no_error = ctrl._honesty_postcheck(
            "Let me know if you need anything.", [], False, None, [])
        self.assertNotIn("Correction", no_error)
        self.assertNotIn("queued", no_error)

    def test_strip_tool_call_blocks(self):
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        self.assertEqual(
            ctrl._strip_tool_call_blocks(
                "Hi <tool_call>ask_user</tool_call> there"), "Hi  there")
        self.assertEqual(ctrl._strip_tool_call_blocks("plain"), "plain")

    def test_set_field_preview_shows_selection_label(self):
        partner = self.env["res.partner"].create({"name": "Label Two"})
        _kind, _payload, card = execute_tool(
            self.env, "set_field",
            {"model": "res.partner", "res_id": partner.id,
             "field": "type", "value": "invoice"}, {})
        self.assertEqual(card["kind"], "set_field")
        self.assertEqual(
            card["preview"], ["Address Type: Contact -> Invoice"])

    def test_create_draft_normalizes_float_ids(self):
        kind, payload, card = execute_tool(
            self.env, "create_draft",
            {"model": "product.template",
             "values": {"name": "Float Probe", "uom_id": 1.0,
                        "categ_id": 1.0}}, {})
        self.assertEqual(kind, "confirm")
        self.assertEqual(card["payload"]["values"]["uom_id"], 1)
        self.assertEqual(card["payload"]["values"]["categ_id"], 1)
        self.assertNotIn("1.0", " ".join(card["preview"]))

    def test_preview_resolves_float_many2one(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        line = tools._preview_value(
            self.env, "res.partner", "parent_id", 1118.0)
        self.assertIn("Acme Corp", line)
        self.assertNotIn("1118", line)

    def test_system_prompt_forbids_guessed_ids(self):
        from types import SimpleNamespace
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        agent = SimpleNamespace(system_prompt="", instructions="")
        for tier in ("t2", "t3"):
            prompt = ctrl._system_prompt(agent, tier, "")
            self.assertIn("can never give you an Odoo id", prompt)
            self.assertIn("no id 1", prompt)

    def test_preview_shows_selection_label(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        line = tools._preview_value(
            self.env, "product.template", "type", "consu")
        self.assertTrue(line.startswith("Product Type: "))
        self.assertNotIn("consu", line)

    def test_create_draft_rejects_bad_selection(self):
        with self.assertRaises(AccessError) as ctx:
            execute_tool(
                self.env, "create_draft",
                {"model": "product.template",
                 "values": {"name": "Bad Type",
                            "type": "nonsense"}}, {})
        self.assertIn("nonsense", str(ctx.exception))

    def test_create_draft_rejects_bad_date(self):
        with self.assertRaises(AccessError) as ctx:
            execute_tool(
                self.env, "create_draft",
                {"model": "sale.order",
                 "values": {"date_order": "not-a-date"}}, {})
        self.assertIn("YYYY-MM-DD", str(ctx.exception))

    def test_create_draft_nested_line_validated(self):
        template = self.env["product.template"].create(
            {"name": "Line Probe"})
        vid = template.product_variant_ids[:1].id
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "create_draft",
                {"model": "sale.order",
                 "values": {"order_line": [
                     [0, 0, {"product_id": vid,
                             "product_uom_qty": "lots"}]]}}, {})

    def test_default_state_value(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        self.assertEqual(
            tools._default_state_value(self.env, "sale.order"),
            "draft")
        self.assertIsNone(
            tools._default_state_value(self.env, "res.partner"))
        self.assertIsNone(
            tools._default_state_value(self.env, "mail.activity"))

    def test_set_field_undo_roundtrip(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        partner = self.env["res.partner"].create({
            "name": "Undo Co", "phone": "111"})
        state = tools.set_field_old_state(
            self.env, "res.partner", partner.id, "phone")
        self.assertEqual(state["value"], "111")
        _kind, _payload, card = execute_tool(
            self.env, "set_field",
            {"model": "res.partner", "res_id": partner.id,
             "field": "phone", "value": "222"}, {})
        run_confirm(self.env, card)
        self.assertEqual(partner.phone, "222")
        tools.apply_set_field_inverse(self.env, state)
        self.assertEqual(partner.phone, "111")

    def test_set_field_old_state_guards(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        self.assertIsNone(tools.set_field_old_state(
            self.env, "res.users", 1, "login"))
        self.assertIsNone(tools.set_field_old_state(
            self.env, "res.partner", 0, "phone"))
        self.assertIsNone(tools.set_field_old_state(
            self.env, "res.partner", 1, "no_such_field"))

    def test_set_field_old_state_many2one(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        parent = self.env["res.partner"].create({"name": "Undo Parent"})
        child = self.env["res.partner"].create({
            "name": "Undo Child", "parent_id": parent.id})
        state = tools.set_field_old_state(
            self.env, "res.partner", child.id, "parent_id")
        self.assertEqual(state["value"], parent.id)

    def test_fallback_reruns_on_dead_primary(self):
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        primary, fallback = self._models()
        agent = self._loop_agent(primary)
        orig = type(primary)._chat

        def fake(record, messages, tools=None, extra=None):
            if record.id == primary.id:
                raise ctrl.NovaAiProviderError("endpoint down")
            return {"choices": [{"message": {"content": "fallback ok"}}]}

        type(primary)._chat = fake
        try:
            session = self.env["nova.ai.session"].create({
                "agent_id": agent.id})
            ctx = {}
            result = run_chat_turn(self.env, agent, session, "hi", ctx)
        finally:
            type(primary)._chat = orig
        self.assertEqual(result["reply"], "fallback ok")
        self.assertTrue(ctx.get("_fell_back"))
        self.assertTrue([s for s in result["steps"]
                         if s.get("tool") == "fallback"])

    def test_no_fallback_for_auto_t3(self):
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        primary, _fallback = self._models(
            supports_embeddings=True, context_length=64000)
        self.assertEqual(primary.tier, "t3")
        agent = self.env["nova.ai.agent"].create({
            "name": "Auto Agent", "model_id": primary.id,
            "auto_apply": True,
        })
        orig = type(primary)._chat

        def fake(record, messages, tools=None, extra=None):
            raise ctrl.NovaAiProviderError("endpoint down")

        type(primary)._chat = fake
        try:
            session = self.env["nova.ai.session"].create({
                "agent_id": agent.id})
            ctx = {}
            with self.assertRaises(ctrl.NovaAiProviderError):
                run_chat_turn(self.env, agent, session, "hi", ctx)
        finally:
            type(primary)._chat = orig
        self.assertFalse(ctx.get("_fell_back"))

    def test_feedback_log_kind(self):
        row = self.env["nova.ai.log"].log("feedback", ok=True,
                                          detail={"vote": 1})
        self.assertEqual(row.kind, "feedback")

    def test_snapshot_save_restore(self):
        agent = self._loop_agent(self._models()[0])
        session = self.env["nova.ai.session"].create({
            "agent_id": agent.id, "context_model": "res.partner",
            "context_res_id": 7,
            "pending": [{"token": "t1", "kind": "set_field"}]})
        snap = self.env["nova.ai.snapshot"].save_for(session, "v1")
        self.assertEqual(snap.name, "v1")
        session.write({"pending": [], "context_res_id": 0})
        snap.restore_to(session)
        self.assertEqual(session.context_res_id, 7)
        self.assertEqual(len(session.pending), 1)
        other = self.env["nova.ai.session"].create({
            "agent_id": agent.id})
        with self.assertRaises(AccessError):
            snap.restore_to(other)


@tagged("post_install", "-at_install")
class TestNovaAiProviderSync(TransactionCase):

    def test_sync_skips_archived_model(self):
        # Archived rows leave model_ids (active_test) but still own
        # their (provider, name) key: sync must not re-create them.
        provider = self.env["nova.ai.provider"].create({
            "name": "Sync Provider", "kind": "ollama",
            "base_url": "http://unit.invalid",
        })
        old = self.env["nova.ai.model"].create({
            "provider_id": provider.id, "name": "retired-model"})
        old.write({"active": False})
        orig = type(provider)._list_models

        def fake(self):
            return ["retired-model", "fresh-model"]

        type(provider)._list_models = fake
        try:
            provider.action_sync_models()
        finally:
            type(provider)._list_models = orig
        self.assertEqual(
            self.env["nova.ai.model"].with_context(
                active_test=False).search_count(
                [("provider_id", "=", provider.id),
                 ("name", "=", "retired-model")]), 1)
        retired = self.env["nova.ai.model"].with_context(
            active_test=False).search(
                [("provider_id", "=", provider.id),
                 ("name", "=", "retired-model")], limit=1)
        self.assertFalse(retired.active)
        self.assertTrue(self.env["nova.ai.model"].search(
            [("provider_id", "=", provider.id),
             ("name", "=", "fresh-model")], limit=1).active)


@tagged("post_install", "-at_install")
class TestNovaAiUserMirror(TransactionCase):

    def _loop_agent(self, model):
        tools = self.env["nova.ai.tool"].search(
            [("code", "in", ("count_records", "log_note"))])
        return self.env["nova.ai.agent"].create({
            "name": "Mirror Agent", "model_id": model.id,
            "tool_ids": [(6, 0, tools.ids)],
        })

    def _models(self, **vals):
        provider = self.env["nova.ai.provider"].create({
            "name": "Mirror Provider", "kind": "openai_compat",
            "base_url": "http://unit.invalid/v1",
        })
        data = {"provider_id": provider.id, "name": "primary-model",
                "supports_tools": True, "context_length": 16000}
        data.update(vals)
        primary = self.env["nova.ai.model"].create(data)
        fallback = self.env["nova.ai.model"].create({
            "provider_id": provider.id, "name": "fallback-model",
            "supports_tools": True, "context_length": 16000,
        })
        primary.write({"fallback_model_id": fallback.id})
        return primary, fallback

    def test_user_mirror_allows_project(self):
        # The user's case: project.project was never allow-listed,
        # yet the user reads projects daily. The mirror rule opens
        # whatever the requesting user can read.
        kind, out, _card = execute_tool(
            self.env, "count_records", {"model": "project.project"}, {})
        self.assertEqual(kind, "result")
        self.assertIn("count", out)

    def test_system_models_stay_denied(self):
        for model in ("res.users", "ir.config_parameter",
                      "ir.attachment", "ir.model"):
            with self.assertRaises(AccessError, msg=model):
                execute_tool(
                    self.env, "count_records", {"model": model}, {})

    def test_absent_model_refused(self):
        with self.assertRaises(AccessError):
            execute_tool(
                self.env, "count_records", {"model": "helpdesk.ticket"},
                {})

    def test_param_subset_still_restricts(self):
        self.env["ir.config_parameter"].set_str(
            "nova_ai.tool_models", "res.partner")
        try:
            kind, out, _card = execute_tool(
                self.env, "count_records", {"model": "res.partner"}, {})
            self.assertEqual(kind, "result")
            with self.assertRaises(AccessError):
                execute_tool(
                    self.env, "count_records", {"model": "sale.order"},
                    {})
        finally:
            self.env["ir.config_parameter"].set_str(
                "nova_ai.tool_models", "")

    def test_denial_points_to_discovery(self):
        from odoo.addons.nova_ai.models import nova_ai_tools as tools
        with self.assertRaises(AccessError) as ctx:
            raise tools._denied("res.users", self.env)
        self.assertIn("fields_get", str(ctx.exception))
        self.assertIn("inspect_method", str(ctx.exception))

    def test_empty_reply_gets_fallback(self):
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        primary, _fallback = self._models()
        agent = self._loop_agent(primary)
        orig = type(primary)._chat

        def fake(record, messages, tools=None, extra=None):
            return {"choices": [{"message": {"content": ""}}]}

        type(primary)._chat = fake
        try:
            session = self.env["nova.ai.session"].create({
                "agent_id": agent.id})
            result = ctrl.run_chat_turn(
                self.env, agent, session, "hi", {})
        finally:
            type(primary)._chat = orig
        self.assertTrue((result["reply"] or "").strip())
        self.assertIn("couldn't complete", result["reply"])

    def test_empty_reply_names_first_failure(self):
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        primary, _fallback = self._models()
        agent = self._loop_agent(primary)
        orig = type(primary)._chat
        calls = [
            _text_tool_call("count_records", {"model": "res.users"}),
            {"choices": [{"message": {"content": ""}}]},
            {"choices": [{"message": {"content": ""}}]},
        ]

        def fake(record, messages, tools=None, extra=None):
            return calls.pop(0) if calls else \
                {"choices": [{"message": {"content": ""}}]}

        type(primary)._chat = fake
        try:
            session = self.env["nova.ai.session"].create({
                "agent_id": agent.id})
            result = ctrl.run_chat_turn(
                self.env, agent, session, "hi", {})
        finally:
            type(primary)._chat = orig
        self.assertIn("couldn't complete", result["reply"])
        self.assertIn("count_records", result["reply"])

    def test_empty_reply_lists_queued_cards(self):
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl
        primary, _fallback = self._models()
        agent = self._loop_agent(primary)
        partner = self.env["res.partner"].create({"name": "Quiet Co"})
        orig = type(primary)._chat
        calls = [
            _text_tool_call("log_note", {
                "model": "res.partner", "res_id": partner.id,
                "body": "Quiet note."}),
            {"choices": [{"message": {"content": ""}}]},
            {"choices": [{"message": {"content": ""}}]},
        ]

        def fake(record, messages, tools=None, extra=None):
            return calls.pop(0) if calls else \
                {"choices": [{"message": {"content": ""}}]}

        type(primary)._chat = fake
        try:
            session = self.env["nova.ai.session"].create({
                "agent_id": agent.id})
            result = ctrl.run_chat_turn(
                self.env, agent, session, "hi", {})
        finally:
            type(primary)._chat = orig
        self.assertEqual(len(result["cards"]), 1)
        self.assertIn("Queued for your approval", result["reply"])
        self.assertNotIn("couldn't complete", result["reply"])

    def test_dupe_affirmed_detects_rephrased_question(self):
        from odoo.addons.nova_ai.controllers import nova_ai as ctrl

        class Msg:
            def __init__(self, content):
                self.content = content

        rephrased = [Msg("<p>A partner with the same name is already "
                         "in the system.</p>"),
                     Msg("<p>Acme (2 records). Create anyway, or open "
                         "the existing one?</p>")]
        self.assertTrue(
            ctrl._dupe_affirmed(rephrased, "Yes — create anyway."))
        self.assertFalse(
            ctrl._dupe_affirmed(rephrased, "no, open the first one"))
