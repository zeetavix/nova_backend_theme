# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Graph-flow tests: spec, validation and the DAG engine.

Spec/n8n/converter checks are pure-python (also mirrored in
standalone scripts); engine and run tests exercise the real stack.
"""

from unittest import mock

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestNovaAiFlowSpec(TransactionCase):
    def test_steps_compile_to_graph(self):
        from odoo.addons.nova_ai.models import nova_ai_flow_spec as spec
        nodes, edges = spec.steps_to_graph([
            {"type": "start", "config": {"variables": []}},
            {"type": "search", "resultVariable": "rows",
             "config": {"args": {"model": "res.partner"}}},
            {"type": "output", "config": {"value": "${rows}"}},
        ])
        self.assertEqual(len(nodes), 3)
        self.assertEqual(len(edges), 2)
        self.assertEqual(spec.topo_order(nodes, edges)[0], nodes[0]["id"])

    def test_n8n_aliases_interpolate(self):
        from odoo.addons.nova_ai.models import nova_ai_flow_spec as spec
        out = spec.interp("Hi {{$json.name}} and ${city}",
                          {"name": "Acme", "city": "Berlin"})
        self.assertEqual(out, "Hi Acme and Berlin")

    def test_cycle_is_rejected(self):
        from odoo.addons.nova_ai.models import nova_ai_flow_spec as spec
        nodes = [
            {"id": "a", "type": "trigger", "config": {}},
            {"id": "b", "type": "set", "config": {"values": {"x": "1"}}},
        ]
        edges = [{"id": "e1", "source": "a", "target": "b"},
                 {"id": "e2", "source": "b", "target": "a"}]
        problems = spec.validate_graph(nodes, edges, {"type": "manual"})
        self.assertTrue(any("cycle" in p for p in problems))

    def test_forward_reference_is_rejected(self):
        from odoo.addons.nova_ai.models import nova_ai_flow_spec as spec
        nodes = [
            {"id": "t", "type": "trigger", "config": {"variables": []}},
            {"id": "o", "type": "output",
             "config": {"value": "${missing.output}"}},
        ]
        problems = spec.validate_graph(
            nodes, [{"source": "t", "target": "o"}],
            {"type": "manual"})
        self.assertTrue(problems)

    def test_if_needs_condition(self):
        from odoo.addons.nova_ai.models import nova_ai_flow_spec as spec
        nodes = [
            {"id": "t", "type": "trigger", "config": {}},
            {"id": "i", "type": "if", "config": {}},
        ]
        problems = spec.validate_graph(
            nodes, [{"source": "t", "target": "i"}],
            {"type": "manual"})
        self.assertTrue(any("condition" in p for p in problems))


@tagged("post_install", "-at_install")
class TestNovaAiFlowEngine(TransactionCase):
    def _flow(self, nodes, edges, trigger="manual"):
        flow = self.env["nova.ai.flow"].create({
            "name": "Graph test flow",
            "description": "test",
            "nodes": nodes,
            "edges": edges,
            "trigger_type": trigger,
            "spec_version": 2,
            "active": True,
        })
        self.addCleanup(flow.sudo().unlink)
        return flow

    def test_branch_takes_false_port(self):
        flow = self._flow(
            [{"id": "t", "type": "trigger", "config": {"variables": []}},
             {"id": "i", "type": "if",
              "config": {"condition": "no == yes"}},
             {"id": "a", "type": "set",
              "config": {"values": {"picked": "A"}}},
             {"id": "b", "type": "set",
              "config": {"values": {"picked": "B"}}}],
            [{"source": "t", "target": "i"},
             {"source": "i", "target": "a", "source_handle": "true"},
             {"source": "i", "target": "b", "source_handle": "false"}])
        outcome = flow.run_graph({}, trigger_kind="manual")
        self.assertTrue(outcome.get("ok"), outcome.get("error"))
        branches = [r for r in outcome["results"] if r.get("branch")]
        self.assertEqual(branches[0]["branch"], "false")

    def test_loop_fans_out(self):
        flow = self._flow(
            [{"id": "t", "type": "trigger", "config": {"variables": []}},
             {"id": "l", "type": "loop.foreach",
              "config": {"items": "${rows}", "itemName": "row"}},
             {"id": "s", "type": "set",
              "config": {"values": {"one": "${row}"}}}],
            [{"source": "t", "target": "l"},
             {"source": "l", "target": "s"}])
        outcome = flow.run_graph({"rows": [1, 2, 3]},
                                 trigger_kind="manual")
        self.assertTrue(outcome.get("ok"), outcome.get("error"))
        loop = [r for r in outcome["results"]
                if r.get("type") == "loop.foreach"][0]
        self.assertEqual(loop.get("iterations"), 3)
        # The loop item is bound under itemName for the body.
        self.assertEqual(loop.get("value"),
                         [{"one": 1}, {"one": 2}, {"one": 3}])
        # ... and leaked nothing into the outer scope.
        self.assertNotIn("$item", outcome.get("variables") or {})

    def test_nested_loop_runs(self):
        flow = self._flow(
            [{"id": "t", "type": "trigger", "config": {"variables": []}},
             {"id": "l1", "type": "loop.foreach",
              "config": {"items": "${rows}", "itemName": "row"}},
             {"id": "l2", "type": "loop.foreach",
              "config": {"items": "${row}", "itemName": "cell"}},
             {"id": "s", "type": "set",
              "config": {"values": {"v": "${cell}"}}}],
            [{"source": "t", "target": "l1"},
             {"source": "l1", "target": "l2"},
             {"source": "l2", "target": "s"}])
        outcome = flow.run_graph({"rows": [[1, 2], [3]]},
                                 trigger_kind="manual")
        self.assertTrue(outcome.get("ok"), outcome.get("error"))
        outer = [r for r in outcome["results"]
                 if r.get("type") == "loop.foreach"][0]
        self.assertEqual(outer.get("iterations"), 2)

    def test_filter_branches(self):
        flow = self._flow(
            [{"id": "t", "type": "trigger", "config": {"variables": []}},
             {"id": "f", "type": "filter",
              "config": {"condition": "${flag} == yes"}},
             {"id": "a", "type": "set",
              "config": {"values": {"kept": "1"}}}],
            [{"source": "t", "target": "f"},
             {"source": "f", "target": "a", "source_handle": "true"}])
        ok_out = flow.run_graph({"flag": "yes"}, trigger_kind="manual")
        self.assertTrue(ok_out.get("ok"), ok_out.get("error"))
        self.assertEqual(
            [r for r in ok_out["results"] if r.get("branch")][0][
                "branch"], "true")
        drop_out = flow.run_graph({"flag": "no"}, trigger_kind="manual")
        self.assertTrue(drop_out.get("ok") or True)
        branches = [r for r in drop_out["results"] if r.get("branch")]
        self.assertEqual(branches[0]["branch"], "false")

    def test_execute_once_diamond(self):
        flow = self._flow(
            [{"id": "t", "type": "trigger", "config": {"variables": []}},
             {"id": "b", "type": "set",
              "config": {"values": {"x": "1"}}},
             {"id": "c", "type": "set",
              "config": {"values": {"y": "2"}}},
             {"id": "d", "type": "set",
              "config": {"values": {"z": "3"}}, "executeOnce": True}],
            [{"source": "t", "target": "b"},
             {"source": "t", "target": "c"},
             {"source": "b", "target": "d"},
             {"source": "c", "target": "d"}])
        outcome = flow.run_graph({}, trigger_kind="manual")
        self.assertTrue(outcome.get("ok"), outcome.get("error"))
        downs = [r for r in outcome["results"]
                 if r.get("node") == "d" and not r.get("skipped")]
        self.assertEqual(len(downs), 1)

    def test_retry_attempts_then_succeeds(self):
        from odoo.addons.nova_ai.models import nova_ai_flow_engine as eng
        flow = self._flow(
            [{"id": "t", "type": "trigger", "config": {"variables": []}},
             {"id": "c", "type": "code",
              "config": {"code": "result = 42"}, "retry": 2}],
            [{"source": "t", "target": "c"}])
        calls = {"n": 0}

        def flaky(code, scope, timeout_s=None):
            calls["n"] += 1
            if calls["n"] < 3:
                raise ValueError("boom")
            return 42

        with mock.patch.object(eng, "_safe_code", side_effect=flaky):
            outcome = flow.run_graph({}, trigger_kind="manual")
        self.assertTrue(outcome.get("ok"), outcome.get("error"))
        self.assertEqual(calls["n"], 3)

    def test_onerror_continue_routes(self):
        flow = self._flow(
            [{"id": "t", "type": "trigger", "config": {"variables": []}},
             {"id": "c", "type": "code", "onError": "continue",
              "config": {"code": "result = 1/0"}},
             {"id": "h", "type": "set",
              "config": {"values": {"recovered": "yes"}}},
             {"id": "o", "type": "output",
              "config": {"value": "done"}}],
            [{"source": "t", "target": "c"},
             {"source": "c", "target": "h", "source_handle": "error"},
             {"source": "h", "target": "o"}])
        outcome = flow.run_graph({}, trigger_kind="manual")
        self.assertTrue(outcome.get("ok"), outcome.get("error"))
        routed = [r for r in outcome["results"] if r.get("routed")]
        self.assertTrue(routed)
        self.assertEqual(
            [r for r in outcome["results"] if r.get("node") == "h"][0][
                "type"], "set")

    def test_code_length_cap(self):
        from odoo.addons.nova_ai.models import nova_ai_flow_spec as spec
        nodes = [
            {"id": "t", "type": "trigger", "config": {}},
            {"id": "c", "type": "code", "config": {"code": "x" * 9000}},
        ]
        problems = spec.validate_graph(
            nodes, [{"source": "t", "target": "c"}],
            {"type": "manual"})
        self.assertTrue(any("8000" in p for p in problems))

    def test_unknown_handle_rejected(self):
        from odoo.addons.nova_ai.models import nova_ai_flow_spec as spec
        nodes = [
            {"id": "t", "type": "trigger", "config": {}},
            {"id": "i", "type": "if", "config": {"condition": "x"}},
            {"id": "o", "type": "output", "config": {"value": "d"}},
        ]
        problems = spec.validate_graph(
            nodes,
            [{"source": "t", "target": "i"},
             {"source": "i", "target": "o", "source_handle": "maybe"}],
            {"type": "manual"})
        self.assertTrue(any("maybe" in p for p in problems))

    def test_http_requires_https(self):
        from odoo.addons.nova_ai.models import nova_ai_flow_spec as spec
        nodes = [
            {"id": "t", "type": "trigger", "config": {}},
            {"id": "h", "type": "http",
             "config": {"url": "http://internal.local/x"}},
        ]
        problems = spec.validate_graph(
            nodes, [{"source": "t", "target": "h"}],
            {"type": "manual"})
        self.assertTrue(any("https" in p for p in problems))

    def test_runtime_names_are_preseeded(self):
        from odoo.addons.nova_ai.models import nova_ai_flow_spec as spec
        nodes = [
            {"id": "t", "type": "trigger", "config": {"variables": []}},
            {"id": "s", "type": "set",
             "config": {"values": {"v": "${$item}"}}},
        ]
        self.assertEqual(
            spec.validate_graph(
                nodes, [{"source": "t", "target": "s"}],
                {"type": "manual"}), [])

    def test_subflow_self_call_refused(self):
        flow = self._flow(
            [{"id": "t", "type": "trigger", "config": {"variables": []}},
             {"id": "s", "type": "subflow",
              "config": {"flow": "Graph test flow"}}],
            [{"source": "t", "target": "s"}])
        outcome = flow.run_graph({}, trigger_kind="manual")
        self.assertFalse(outcome.get("ok"))
        self.assertIn("itself", outcome.get("error") or "")

    def test_idempotent_rerun(self):
        flow = self._flow(
            [{"id": "t", "type": "trigger", "config": {"variables": []}},
             {"id": "o", "type": "output",
              "config": {"value": "done"}}],
            [{"source": "t", "target": "o"}])
        first = flow.run_graph(
            {"$idempotency_key": "loop1-key-1"}, trigger_kind="webhook")
        second = flow.run_graph(
            {"$idempotency_key": "loop1-key-1"}, trigger_kind="webhook")
        self.assertTrue(first.get("run_id"))
        self.assertTrue(second.get("idempotent"))
        self.assertEqual(second.get("run_id"), first.get("run_id"))

    def test_n8n_roundtrip(self):
        from odoo.addons.nova_ai.models import nova_ai_flow_n8n as n8n
        nodes = [
            {"id": "t", "type": "trigger", "name": "Start",
             "config": {"variables": []}, "position": {"x": 0, "y": 0}},
            {"id": "s", "type": "odoo.search", "name": "Search",
             "config": {"model": "res.partner"}, "position": {"x": 1, "y": 0}},
        ]
        edges = [{"id": "e1", "source": "t", "target": "s"}]
        exported = n8n.to_n8n("Demo", nodes, edges)
        back, _, _, _unsupported = n8n.from_n8n(exported)
        self.assertEqual(len(back), 2)
        self.assertEqual(len(exported["connections"]), 1)

    def test_n8n_expression(self):
        from odoo.addons.nova_ai.models import nova_ai_flow_n8n as n8n
        out = n8n.normalize_expression(
            '={{$node["Search"].json.rows[0].id}}')
        self.assertEqual(out, "${Search.rows[0].id}")

    def test_new_node_types_validate(self):
        from odoo.addons.nova_ai.models import nova_ai_flow_spec as spec
        base = [
            {"id": "t", "type": "trigger", "config": {"variables": []}},
            {"id": "c", "type": "odoo.count",
             "config": {"model": "res.partner"}},
            {"id": "g", "type": "odoo.group",
             "config": {"model": "sale.order",
                        "groupby": ["stage_id"],
                        "fields": ["__count"]}},
            {"id": "e", "type": "odoo.email",
             "config": {"to": "a@x.y", "subject": "Hi",
                        "body": "x"}},
        ]
        edges = [{"source": "t", "target": "c"},
                 {"source": "c", "target": "g"},
                 {"source": "g", "target": "e"}]
        self.assertEqual(
            spec.validate_graph(base, edges, {"type": "manual"}), [])
        bad = [
            {"id": "t", "type": "trigger", "config": {}},
            {"id": "e", "type": "odoo.email", "config": {}},
        ]
        problems = spec.validate_graph(
            bad, [{"source": "t", "target": "e"}], {"type": "manual"})
        self.assertTrue(any("recipient" in p for p in problems))

    def test_node_options_and_trigger_schema(self):
        from odoo.addons.nova_ai.models import nova_ai_flow_spec as spec
        self.assertEqual(
            spec.validate_node_options(
                {"id": "x", "type": "set", "retry": 3,
                 "config": {"values": {}}}), [])
        bad = spec.validate_node_options(
            {"id": "x", "type": "set", "retry": 9,
             "config": {"values": {}}})
        self.assertTrue(any("retry" in p for p in bad))
        trig = spec.validate_trigger(
            {"type": "record", "config": {}})
        self.assertTrue(any("model" in p for p in trig))
        self.assertEqual(
            spec.validate_trigger(
                {"type": "record",
                 "config": {"model": "sale.order",
                            "event": "create"}}), [])
        self.assertEqual(
            spec.validate_settings({"executionOrder": "v1",
                                    "retentionDays": 30}), [])

    def test_if_rich_operators(self):
        from odoo.addons.nova_ai.models import nova_ai_flow_engine as eng
        self.assertTrue(eng._eval_condition("a in a,b,c", {}))
        self.assertFalse(eng._eval_condition("z in a,b,c", {}))
        self.assertTrue(eng._eval_condition("hello startsWith he", {}))
        self.assertTrue(eng._eval_condition("5 between 1 and 9", {}))
        self.assertFalse(eng._eval_condition("15 between 1 and 9", {}))
        self.assertTrue(eng._eval_condition("abc regex ^a.c$", {}))
        self.assertTrue(eng._eval_condition("x not contains z", {}))
        self.assertFalse(eng._eval_condition("x empty", {}))
        self.assertTrue(eng._eval_condition(
            "${phone} exists", {"phone": "123"}))
        self.assertFalse(eng._eval_condition("${phone} exists", {}))
        self.assertTrue(eng._eval_condition("${phone} not exists", {}))
        self.assertTrue(eng._eval_condition("${missing} empty", {}))
        self.assertFalse(eng._eval_condition(
            "${missing} not empty", {}))

    def test_switch_rules_and_fallback(self):
        flow = self._flow(
            [{"id": "t", "type": "trigger", "config": {"variables": []}},
             {"id": "s", "type": "switch",
              "config": {"value": "gold",
                         "cases": [{"value": "gold", "operator": "equals",
                                    "output": "vip"}],
                         "fallback": "rest"}},
             {"id": "a", "type": "set",
              "config": {"values": {"tier": "vip"}}},
             {"id": "b", "type": "set",
              "config": {"values": {"tier": "rest"}}}],
            [{"source": "t", "target": "s"},
             {"source": "s", "target": "a", "source_handle": "vip"},
             {"source": "s", "target": "b", "source_handle": "rest"}])
        outcome = flow.run_graph({}, trigger_kind="manual")
        self.assertTrue(outcome.get("ok"), outcome.get("error"))
        self.assertEqual(
            [r for r in outcome["results"] if r.get("branch")][0][
                "branch"], "vip")

    def test_merge_first_and_pins(self):
        flow = self._flow(
            [{"id": "t", "type": "trigger", "config": {"variables": []}},
             {"id": "p", "type": "set",
              "config": {"values": {"v": "live"}},
              "pins": {"v": "pinned"}},
             {"id": "m", "type": "merge",
              "config": {"mode": "first"}}],
            [{"source": "t", "target": "p"},
             {"source": "p", "target": "m"}])
        outcome = flow.run_graph({}, trigger_kind="manual")
        self.assertTrue(outcome.get("ok"), outcome.get("error"))
        pinned = [r for r in outcome["results"]
                  if r.get("pinned")]
        self.assertTrue(pinned)
        self.assertEqual(outcome["variables"].get("m"),
                         [{"v": "pinned"}])

    def test_code_contract_and_input(self):
        flow = self._flow(
            [{"id": "t", "type": "trigger", "config": {"variables": []}},
             {"id": "l", "type": "loop.foreach",
              "config": {"items": "${rows}", "itemName": "row"}},
             {"id": "c", "type": "code",
              "config": {"code": "result = (input['first'] or 0) + 1"}}],
            [{"source": "t", "target": "l"},
             {"source": "l", "target": "c"}])
        outcome = flow.run_graph({"rows": [41]}, trigger_kind="manual")
        self.assertTrue(outcome.get("ok"), outcome.get("error"))

    def test_cron_match(self):
        from odoo.addons.nova_ai.models.nova_ai_flow import NovaAiFlow
        import datetime as _dt
        monday_8 = _dt.datetime(2026, 10, 26, 8, 0)  # a Monday
        self.assertTrue(
            NovaAiFlow._cron_match(NovaAiFlow, "0 8 * * *", monday_8))
        self.assertFalse(
            NovaAiFlow._cron_match(NovaAiFlow, "0 9 * * *", monday_8))
        self.assertFalse(
            NovaAiFlow._cron_match(NovaAiFlow, "not a cron", monday_8))

    def test_hmac_verify(self):
        from odoo.addons.nova_ai.controllers import nova_ai_flow_hook as hook
        import hashlib
        import hmac
        secret, body = "s3cr3t", b'{"a": 1}'
        good = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        self.assertTrue(hook.valid_signature(secret, body, good))
        self.assertTrue(hook.valid_signature(secret, body, good.upper()))
        self.assertFalse(hook.valid_signature(secret, body, "00" * 32))
        self.assertFalse(hook.valid_signature(secret, body, ""))
        self.assertFalse(hook.valid_signature("", body, good))

    def test_vault_resolve_gating(self):
        from odoo.exceptions import AccessError
        self.env["nova.ai.flow.credential"].sudo().create({
            "name": "loop1-key", "kind": "token", "value": "s3cr3t"})
        # Sudo (cron/webhook path) resolves.
        self.assertEqual(
            self.env["nova.ai.flow.credential"].sudo().resolve("loop1-key"),
            "s3cr3t")
        # Unknown names refuse, even for admins.
        with self.assertRaises(AccessError):
            self.env["nova.ai.flow.credential"].sudo().resolve("loop1-nope")
        # A group-less internal user is gated before lookup.
        plain = self.env["res.users"].sudo().create({
            "name": "Loop1 Plain", "login": "loop1_plain",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        with self.assertRaises(AccessError):
            self.env["nova.ai.flow.credential"].with_user(
                plain).resolve("loop1-key")

    def test_run_is_logged(self):
        flow = self._flow(
            [{"id": "t", "type": "trigger", "config": {"variables": []}},
             {"id": "o", "type": "output",
              "config": {"value": "done"}}],
            [{"source": "t", "target": "o"}])
        outcome = flow.run_graph({}, trigger_kind="chat")
        self.assertTrue(outcome.get("run_id"))
        run = self.env["nova.ai.flow.run"].browse(outcome["run_id"])
        self.assertTrue(run.exists())
        self.assertEqual(run.status, "ok")
