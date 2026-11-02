# Part of Nova. See LICENSE file for full copyright and licensing details.

from odoo import models
from odoo.http import request


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    def color_scheme(self):
        """Nova dark mode.

        Community 20 hardcodes "light", so the theme's dark bundle could
        never load. Nova reads a per-browser cookie set from the user
        menu (''/light/dark), which keeps the choice user-local and
        needs no schema change. Everything downstream — the dark
        asset bundle, every .dark.scss twin — then activates for real.
        """
        try:
            scheme = (request.httprequest.cookies.get("nova_color_scheme")
                      or "").lower()
            if scheme in ("light", "dark"):
                return scheme
        except Exception:
            pass
        return super().color_scheme()

    def session_info(self):
        info = super().session_info()
        try:
            info["nova_brand"] = self.env.company._nova_branding()
        except Exception:
            pass
        try:
            user = self.env.user
            if user and not user._is_public():
                info["nova_theme"] = user._nova_effective_theme()
                info["nova_dnd_sticky_only"] = bool(user.nova_dnd_sticky_only)
                try:
                    bypass = []
                    if user.nova_dnd_bypass_danger:
                        bypass.append("danger")
                    if user.nova_dnd_bypass_warning:
                        bypass.append("warning")
                    info["nova_dnd_bypass"] = bypass
                except Exception:
                    info["nova_dnd_bypass"] = []
                try:
                    info["nova_quiet"] = {
                        "start": float(user.nova_quiet_start or 0),
                        "end": float(user.nova_quiet_end or 0),
                    }
                except Exception:
                    info["nova_quiet"] = {"start": 0, "end": 0}
        except Exception:
            pass
        return info
