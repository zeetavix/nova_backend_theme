# Part of Nova. See LICENSE file for full copyright and licensing details.
from lxml import etree

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestNovaSplit(TransactionCase):
    def _make_action(self, **values):
        defaults = {
            "name": "Split Test",
            "res_model": "res.partner",
            "view_mode": "list,form",
        }
        defaults.update(values)
        return self.env["ir.actions.act_window"].create(defaults)

    def test_split_view_type_registered(self):
        type_field = self.env["ir.ui.view"].fields_get(["type"])["type"]
        selection = dict(type_field["selection"])
        self.assertIn("split", selection)
        info = self.env["ir.ui.view"].get_view_info()
        self.assertIn("split", info)
        self.assertTrue(info["split"]["icon"])
        self.assertTrue(info["split"]["multi_record"])

    def test_split_view_mode_selection(self):
        selection = dict(
            self.env["ir.actions.act_window.view"].fields_get(["view_mode"])[
                "view_mode"
            ]["selection"],
        )
        self.assertIn("split", selection)

    def test_split_get_view_falls_back_to_list_arch(self):
        description = self.env["res.partner"].get_view(view_type="split")
        root = etree.fromstring(description["arch"])
        self.assertEqual(root.tag, "list")

    def test_split_arch_strips_list_js_class(self):
        # A model list arch carrying js_class (e.g. hr.employee) would make
        # the client mount the model's list subclass instead of our split
        # controller, silently dropping the side panel.
        self.env["ir.ui.view"].create(
            {
                "name": "Test js_class list",
                "model": "res.partner",
                "priority": 1,
                "arch": '<list js_class="test_nova_js"><field name="name"/></list>',
            },
        )
        action = self._make_action()
        split = self.env["res.partner"].get_views(
            [[False, "split"]],
            {"action_id": action.id},
        )["views"]["split"]
        self.assertNotIn("js_class", split["arch"])
        plain = self.env["res.partner"].get_views(
            [[False, "list"]],
            {"action_id": action.id},
        )["views"]["list"]
        self.assertIn("js_class", plain["arch"])

    def test_action_auto_injects_split_after_list(self):
        action = self._make_action()
        result = action._get_action_dict()
        modes = [view[1] for view in result["views"]]
        self.assertEqual(modes, ["list", "split", "form"])
        self.assertIn("nova_split_config", result)
        self.assertTrue(result["nova_split_config"]["auto_open"])
        self.assertEqual(result["nova_split_config"]["default_width"], 560)
        self.assertIn("nova_split_config", action._get_readable_fields())

    def test_action_without_form_gets_no_split(self):
        action = self._make_action(view_mode="list,kanban")
        result = action._get_action_dict()
        self.assertNotIn("split", [view[1] for view in result["views"]])
        self.assertNotIn("nova_split_config", result)

    def test_action_form_only_reuses_default_list_view(self):
        action = self._make_action(view_mode="form")
        result = action._get_action_dict()
        modes = [view[1] for view in result["views"]]
        self.assertEqual(modes, ["form", "split"])
        self.assertIn("nova_split_config", result)

    def test_action_kanban_first_gets_split_after_kanban(self):
        action = self._make_action(view_mode="kanban,form")
        result = action._get_action_dict()
        modes = [view[1] for view in result["views"]]
        self.assertEqual(modes, ["kanban", "split", "form"])

    def test_action_without_any_list_view_gets_no_split(self):
        action = self._make_action(res_model="res.config.settings", view_mode="form")
        result = action._get_action_dict()
        self.assertNotIn("split", [view[1] for view in result["views"]])
        self.assertNotIn("nova_split_config", result)

    def test_action_explicit_split_is_not_duplicated(self):
        action = self._make_action(view_mode="list,form,split")
        result = action._get_action_dict()
        modes = [view[1] for view in result["views"]]
        self.assertEqual(modes.count("split"), 1)

    def test_action_dialog_target_gets_no_split(self):
        action = self._make_action(target="new")
        result = action._get_action_dict()
        self.assertNotIn("split", [view[1] for view in result["views"]])

    def test_action_context_opt_out(self):
        action = self._make_action(context="{'nova_split_disable': True}")
        result = action._get_action_dict()
        self.assertNotIn("split", [view[1] for view in result["views"]])

    def test_global_opt_out_disables_injection(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_bool("nova_split.auto_inject", False)
        try:
            result = self._make_action()._get_action_dict()
        finally:
            params.set_bool("nova_split.auto_inject", True)
        self.assertNotIn("split", [view[1] for view in result["views"]])

    def test_width_param_is_clamped(self):
        params = self.env["ir.config_parameter"].sudo()
        action = self._make_action()
        for raw, expected in [(50, 320), (5000, 1600), (640, 640)]:
            params.set_int("nova_split.default_width", raw)
            try:
                width = action._nova_split_width_param()
            finally:
                params.set_int("nova_split.default_width", 560)
            self.assertEqual(width, expected)
