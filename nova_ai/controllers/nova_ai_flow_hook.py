# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Flow triggers — webhook entry + record-event hook.

* ``POST /nova/ai/flow/hook/<token>`` — external systems (Stripe,
  Shopify, n8n, Zapier…) start a flow with a JSON body. Token auth
  only, rate-limited, never requires a login.
* Record events — any model create/write fires matching
  ``trigger_type == 'record'`` flows via ``ir.rule``-safe user env.
  Registered dynamically so Community installs without
  ``base.automation`` still work (the hook lives on the flow model
  and is called from ``models`` patches below).
"""

import json
import time

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request

_THROTTLE = {}
_MIN_INTERVAL_MS = 500


def valid_signature(secret, body, got):
    """HMAC-SHA256 check for webhook calls. Pure function so tests
    cover the exact comparison the route relies on (constant-time,
    hex, case-insensitive)."""
    import hashlib as _hl
    import hmac as _hm
    try:
        expect = _hm.new(
            str(secret or "").encode(), body or b"",
            _hl.sha256).hexdigest()
    except Exception:
        return False
    try:
        return _hm.compare_digest(
            expect, (got or "").strip().lower())
    except Exception:
        return False


_MAX_BODY_BYTES = 256 * 1024


def _throttled(token):
    now = int(time.time() * 1000)
    if now - _THROTTLE.get(token, 0) < _MIN_INTERVAL_MS:
        return True
    _THROTTLE[token] = now
    # Evict oldest instead of wiping: a flood of random tokens must
    # never reset every legitimate token's throttle.
    while len(_THROTTLE) > 2048:
        _THROTTLE.pop(next(iter(_THROTTLE)))
    return False


class NovaAiFlowHookController(http.Controller):

    @http.route("/nova/ai/flow/hook/<string:token>", type="http",
                auth="public", methods=["POST"], csrf=False)
    def flow_hook(self, token="", **kw):
        # POST only: a state-changing run must never fire from a GET
        # (crawlers, prefetch). Empty tokens never match anything.
        if not (token or "").strip():
            return request.make_json_response(
                {"ok": False, "error": "unknown webhook"}, status=404)
        if _throttled(token):
            return request.make_json_response(
                {"ok": False, "error": "slow down"}, status=429)
        Flow = request.env["nova.ai.flow"].sudo()
        flow = Flow.search([
            ("webhook_token", "=", token),
            ("active", "=", True)], limit=1)
        if not flow:
            return request.make_json_response(
                {"ok": False, "error": "unknown webhook"}, status=404)
        # Only webhook-trigger flows answer here: a manual/chat token
        # must never be remotely triggerable, even if guessed.
        if (flow.trigger_type or "manual") != "webhook":
            return request.make_json_response(
                {"ok": False, "error": "webhook disabled"}, status=403)
        try:
            length = int(request.httprequest.headers.get(
                "Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length > _MAX_BODY_BYTES:
            return request.make_json_response(
                {"ok": False, "error": "body too large"}, status=413)
        try:
            raw = request.httprequest.get_data() or b"{}"
            if len(raw) > _MAX_BODY_BYTES:
                return request.make_json_response(
                    {"ok": False, "error": "body too large"}, status=413)
            payload = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(payload, dict):
                payload = {"value": payload}
        except Exception:
            payload = dict(kw or {})
        # P071: optional HMAC (X-Nova-Signature: sha256 hex of body).
        # Any failure on a configured secret refuses: fail-closed,
        # never fail-open. With HMAC on, query/form params are NOT
        # merged: the signature covers the body only, so accepting
        # extra unsigned keys would bypass it.
        hmac_on = False
        try:
            cfg = flow.trigger_config or {}
            ref = cfg.get("hmac_secret_ref")
            if ref:
                hmac_on = True
                secret = None
                try:
                    secret = request.env[
                        "nova.ai.flow.credential"].sudo().resolve(ref)
                except AccessError:
                    return request.make_json_response(
                        {"ok": False,
                         "error": "unknown credential"}, status=403)
                got = request.httprequest.headers.get("X-Nova-Signature")
                if not valid_signature(secret, raw, got):
                    return request.make_json_response(
                        {"ok": False,
                         "error": "bad signature"}, status=403)
        except AccessError:
            return request.make_json_response(
                {"ok": False, "error": "unknown credential"}, status=403)
        if not hmac_on:
            payload.update(dict(kw or {}))
        # Idempotency key passthrough (P078).
        try:
            key = request.httprequest.headers.get("X-Idempotency-Key")
            if key:
                payload["$idempotency_key"] = key.strip()[:120]
        except Exception:
            pass
        # Never sudo-run: the flow executes as its creator (the user
        # who owns the webhook), so record rules and field gates apply
        # exactly as if they pressed Run. A shared service account
        # would turn every webhook into a full-DB read.
        runner = flow.create_uid
        if not runner or not runner.exists() or not runner.active:
            return request.make_json_response(
                {"ok": False, "error": "webhook owner unavailable"},
                status=403)
        try:
            outcome = flow.with_user(runner).run_graph(
                payload, trigger_kind="webhook")
        except Exception as err:
            return request.make_json_response(
                {"ok": False, "error": str(err)[:300]}, status=500)
        code = 200 if outcome.get("ok") else 422
        return request.make_json_response(
            {"ok": bool(outcome.get("ok")),
             "run_id": outcome.get("run_id", 0),
             "needs_approval": bool(outcome.get("needs_approval")),
             "error": outcome.get("error", "")}, status=code)
