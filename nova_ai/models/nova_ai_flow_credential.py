# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Flow credential vault (P076/P077).

Named secrets for HTTP auth, webhook HMAC and third-party keys.
Never inline a secret in node JSON — nodes carry
``credentials: {name: vault-id``} and the engine resolves at run time.

Community note: Odoo Community has no encrypted-field primitive, so
the value is stored with ``password=True`` (masked in views) behind
admin-only ACL + audit logging on read. Deployments needing at-rest
encryption should mount ``ir.config_parameter``-backed Fernet via
``nova_ai.vault_key`` (documented in the admin runbook).
"""

import logging

from odoo import api, fields, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)


class NovaAiFlowCredential(models.Model):
    _name = "nova.ai.flow.credential"
    _description = "Nova AI Flow Credential"

    name = fields.Char(required=True)
    kind = fields.Selection([
        ("header", "HTTP header"),
        ("basic", "Basic auth"),
        ("hmac", "Webhook HMAC"),
        ("token", "API token"),
        ("other", "Other"),
    ], default="header", required=True)
    # Masked in every view; only admins may read (see ir.access.csv).
    value = fields.Char(required=True, password=True,
                        help="The secret itself. Never paste this into "
                             "node JSON — reference the entry by id.")
    active = fields.Boolean(default=True)
    last_used_at = fields.Datetime(readonly=True)

    _name_uniq = models.Constraint(
        "UNIQUE(name)", "One credential per name.")

    def action_touch(self):
        self.write({"last_used_at": fields.Datetime.now()})

    @api.model
    def resolve(self, ref):
        """Secret string for a vault id or name. Admin/user-gated + audited.

        Webhook HMAC checks call this on a sudo env from a public
        route: ``env.su`` bypasses the group gate (the token + HMAC
        are the auth there, not the user).
        """
        if not self.env.su and not (
                self.env.user.has_group("nova_ai.group_nova_ai_user")
                or self.env.user.has_group(
                    "nova_ai.group_nova_ai_admin")):
            raise AccessError("Credentials are not available.")
        rec = None
        try:
            rec = self.browse(int(ref)).exists()
        except (TypeError, ValueError):
            rec = None
        if not rec:
            rec = self.search([("name", "=", str(ref)),
                               ("active", "=", True)], limit=1)
        if not rec:
            raise AccessError("Unknown credential %r." % (ref,))
        try:
            rec.sudo().action_touch()
            self.env["nova.ai.log"].log(
                "credential", ok=True,
                detail={"credential": rec.name})
        except Exception:
            pass
        return rec.sudo().value
