# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Nova AI settings: the commercial setup surface.

One page under Settings that answers the buyer's only questions —
"is voice on?", "which voice?", "is it ready?" — with switches, a
voice picker, a live status line, and the Prepare button that
downloads and proves everything in one click. Every field binds to
the same config params the engines read, so the screen and the
behavior can never disagree.
"""

from odoo import _, api, fields, models


class NovaAiSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # Off by default on purpose: the browser already listens and
    # speaks for free, and server engines allocate hundreds of MB.
    # An admin turns these on deliberately from this screen after
    # reading the status line — never as a surprise side effect.
    nova_local_stt = fields.Selection(
        [("0", "Off"),
         ("tiny.en", "Tiny English (fastest)"),
         ("base.en", "Base English (default)"),
         ("small.en", "Small English (most accurate)")],
        string="Hear locally with",
        default="0",
        config_parameter="nova_ai.local_stt")
    nova_local_tts = fields.Boolean(
        string="Speak locally", default=False,
        config_parameter="nova_ai.local_tts")
    nova_tts_voice = fields.Selection(
        [("en_US-lessac-medium", "Lessac — American woman (default)"),
         ("en_US-amy-medium", "Amy — American woman"),
         ("en_US-ryan-medium", "Ryan — American man"),
         ("en_GB-alan-medium", "Alan — British man")],
        string="Voice", default="en_US-lessac-medium",
        config_parameter="nova_ai.local_tts_voice")
    nova_voice_wake = fields.Boolean(
        string="Listen for wake phrase", default=False,
        config_parameter="nova_ai.voice_wake")
    nova_voice_wake_phrase = fields.Char(
        string="Wake phrase", default="hey nova",
        config_parameter="nova_ai.voice_wake_phrase")
    nova_voice_status = fields.Char(
        string="Status", compute="_compute_voice_status")

    @api.depends("nova_local_stt", "nova_local_tts", "nova_tts_voice")
    def _compute_voice_status(self):
        from odoo.addons.nova_ai.models import nova_ai_voice_local as local
        for settings in self:
            try:
                ears = local.stt_enabled(settings.env)
            except Exception:
                ears = False
            try:
                mouth = local.tts_enabled(settings.env)
            except Exception:
                mouth = False
            try:
                voice = local._param(
                    settings.env, "nova_ai.local_tts_voice",
                    "en_US-lessac-medium")
            except Exception:
                voice = "en_US-lessac-medium"
            bits = []
            bits.append("hearing ready" if ears
                        else "hearing off or missing engine")
            if mouth:
                bits.append("voice %s ready" % (voice or "—"))
            else:
                bits.append("speech off or missing engine")
            settings.nova_voice_status = " · ".join(bits)

    def action_prepare_voice(self):
        """Same one-click readiness as the provider button, from the
        setup screen where the buyer expects it."""
        self.ensure_one()
        from odoo.addons.nova_ai.models import nova_ai_voice_local as local
        report = local.prepare_voice(self.env)
        bits = []
        for key in ("stt", "tts"):
            part = report.get(key) or {}
            mark = "✓" if part.get("ok") else "×"
            bits.append("%s %s: %s" % (
                mark, key.upper(), part.get("detail") or "—"))
        ready = all((report.get(key) or {}).get("ok")
                    for key in ("stt", "tts"))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Offline voice ready")
                if ready else _("Offline voice incomplete"),
                "message": " · ".join(bits),
                "type": "success" if ready else "warning",
                "sticky": True,
            },
        }
