# Part of Nova. See LICENSE file for full copyright and licensing details.
"""LLM connection layer.

One provider record = one reachable endpoint. Two dialects are
supported out of the box:

* ``openai_compat`` — POST {base}/chat/completions, GET {base}/models.
  This single dialect already covers OpenAI, OpenRouter, Groq,
  Together, vLLM, LM Studio and llama.cpp's server, which is why most
  providers need no bespoke code.
* ``ollama`` — Ollama's native /api/chat, /api/tags and /api/show,
  because Ollama is the default way to run a local model and its
  capability answers (context length, tools) are richer there.

Everything HTTP lives here so P2's agentic loop never talks to a
socket. Keys are stored encrypted (Fernet, see _fernet) with
admin-only ACLs and are never returned to clients; the service layer
builds keys into headers, not payloads.
"""

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_ENC_PREFIX = "enc$"
_FERNET_PARAM = "nova_ai.fernet_key"


def _fernet(env):
    """Fernet for api_key storage, or None (plaintext fallback).

    Key precedence: odoo.conf [options] nova_ai_key, then the
    NOVA_AI_KEY environment variable, then a generated key kept in an
    ir.config_parameter. The first two keep the key out of the
    database (a bare DB dump then reveals nothing); the last is a
    working default, and its use is logged once so it gets moved.
    """
    import os
    key = ""
    try:
        from odoo.tools import config
        key = (config.get("nova_ai_key") or "").strip()
    except Exception:
        key = ""
    if not key:
        try:
            key = (os.environ.get("NOVA_AI_KEY") or "").strip()
        except Exception:
            key = ""
    if key:
        try:
            from cryptography.fernet import Fernet
            return Fernet(key.encode("utf-8"))
        except Exception:
            return None
    try:
        stored = env["ir.config_parameter"].sudo().get_str(
            _FERNET_PARAM, "")
    except Exception:
        stored = ""
    if not stored:
        try:
            from cryptography.fernet import Fernet
            stored = Fernet.generate_key().decode("utf-8")
            env["ir.config_parameter"].sudo().set_str(
                _FERNET_PARAM, stored)
        except Exception:
            return None
        try:
            import logging
            logging.getLogger(__name__).warning(
                "nova.ai.fernet_key generated into the database; move "
                "it to odoo.conf [options] nova_ai_key for dump safety.")
        except Exception:
            pass
    try:
        from cryptography.fernet import Fernet
        return Fernet(stored.encode("utf-8"))
    except Exception:
        return None


def _seal(env, secret):
    """Plaintext key -> storage form (enc$... or plaintext fallback)."""
    if not secret:
        return ""
    try:
        fernet = _fernet(env)
    except Exception:
        fernet = None
    if fernet is None:
        return secret
    try:
        return _ENC_PREFIX + fernet.encrypt(
            secret.encode("utf-8")).decode("utf-8")
    except Exception:
        return secret


def _open(env, stored):
    """Storage form -> plaintext key. Legacy plaintext passes through
    so existing providers keep working until re-saved."""
    if not stored or not stored.startswith(_ENC_PREFIX):
        return stored or ""
    try:
        fernet = _fernet(env)
    except Exception:
        fernet = None
    if fernet is None:
        return ""
    try:
        return fernet.decrypt(
            stored[len(_ENC_PREFIX):].encode("utf-8")).decode("utf-8")
    except Exception:
        return ""

# Default 30s, hard ceiling 120s — a chat call that cannot finish in
# two minutes is a sick endpoint, and the UI must not hang forever.
_TIMEOUT_PARAM = "nova_ai.http_timeout"
_TIMEOUT_DEFAULT = 60
_TIMEOUT_MAX = 600
# Local runtimes load a cold model from disk first — GBs on a spinning
# disk can take minutes, so local dialects get a patient default.
_TIMEOUT_LOCAL = 300


class NovaAiProviderUnsupported(Exception):
    """The endpoint lacks a hosted capability (STT/TTS/vision/embeddings).

    Not an error: the caller falls back to the local engine. Kept
    separate from transport errors so real failures still surface.
    """


class NovaAiProvider(models.Model):
    _name = "nova.ai.provider"
    _description = "Nova AI Provider"
    _order = "name"

    name = fields.Char(required=True)
    kind = fields.Selection(
        [("openai_compat", "OpenAI-compatible"),
         ("ollama", "Ollama (native)")],
        required=True,
        default="openai_compat",
    )
    base_url = fields.Char(
        required=True,
        help="No trailing slash. e.g. http://localhost:11434 (Ollama), "
             "https://api.openai.com/v1, http://localhost:1234/v1 "
             "(LM Studio), http://localhost:8000/v1 (vLLM).",
    )
    api_key = fields.Char(
        help="Bearer token where the dialect needs one. Admin-only: "
             "never echoed to the client.",
    )
    default_model = fields.Char(help="Model name used when none is chosen.")
    timeout = fields.Integer(
        help="Seconds per request. 0 = dialect default "
             "(300 for local Ollama, 60 for hosted endpoints).")
    active = fields.Boolean(default=True)
    note = fields.Text()
    model_ids = fields.One2many("nova.ai.model", "provider_id")
    model_count = fields.Integer(compute="_compute_model_count")

    _name_uniq = models.Constraint(
        "UNIQUE(name)", "Provider names must be unique.")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            key = vals.get("api_key")
            if key and not str(key).startswith(_ENC_PREFIX):
                vals["api_key"] = _seal(self.env, str(key))
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("api_key") and not str(
                vals["api_key"]).startswith(_ENC_PREFIX):
            vals = dict(vals, api_key=_seal(
                self.env, str(vals["api_key"])))
        return super().write(vals)

    @api.depends("model_ids")
    def _compute_model_count(self):
        for provider in self:
            provider.model_count = len(provider.model_ids)

    # ---- HTTP -------------------------------------------------------

    def _timeout(self):
        # Odoo 20 dropped the generic get_param; typed accessors only.
        try:
            ceiling = self.env["ir.config_parameter"].sudo().get_int(
                _TIMEOUT_PARAM, _TIMEOUT_MAX)
        except (TypeError, ValueError):
            ceiling = _TIMEOUT_MAX
        if self.timeout:
            return max(1, min(self.timeout, ceiling))
        default = _TIMEOUT_LOCAL if self.kind == "ollama" else _TIMEOUT_DEFAULT
        return max(1, min(default, ceiling))

    def _url(self, path):
        self.ensure_one()
        return self.base_url.rstrip("/") + "/" + path.lstrip("/")

    def _headers(self):
        self.ensure_one()
        headers = self._auth_headers()
        headers["Content-Type"] = "application/json"
        return headers

    def _auth_headers(self):
        """Auth only (no content type): multipart uploads set their own."""
        self.ensure_one()
        headers = {}
        key = _open(self.env, self.api_key or "")
        if self.kind == "openai_compat" and key:
            headers["Authorization"] = "Bearer " + key
        if "opencode.ai" in (self.base_url or ""):
            # The Go gateway routes on this header (400 MissingSessionID
            # without it) and its edge challenges default library
            # user-agents (403), so both are set here, not per call.
            try:
                db_uuid = self.env["ir.config_parameter"].sudo().get_str(
                    "database.uuid", "odoo")
            except Exception:
                db_uuid = "odoo"
            headers["x-opencode-session"] = "odoo-%s" % db_uuid
            headers["User-Agent"] = "Zeetavix/1.0 (nova-ai)"
        return headers

    # ---- Hosted capabilities (try first, fall back gracefully) ----
    # A customer pointing at api.openai.com gets STT/TTS/vision/embeddings
    # with zero local engines. A free gateway 404s them, and the caller
    # falls back (browser speech, lexical search, honest refusal) — same
    # code path, no configuration.

    def _hosted_unsupported(self, response):
        """True when the endpoint simply lacks the capability (fall back),
        False when the failure is real (surface it: bad key, quota...)."""
        if response.status_code in (404, 405, 501):
            return True
        if response.status_code == 400:
            try:
                body = response.json()
                text = str(body.get("error") or body)[:300].lower()
            except ValueError:
                text = ""
            if any(word in text for word in (
                    "vision", "image", "audio", "speech", "embedding",
                    "not supported", "unsupported", "unknown model")):
                return True
        return False

    def _get(self, path, params=None):
        return requests.get(
            self._url(path), params=params, headers=self._headers(),
            timeout=self._timeout())

    def _post(self, path, payload):
        return requests.post(
            self._url(path), json=payload, headers=self._headers(),
            timeout=self._timeout())

    def transcribe_via_provider(self, model_name, wav_bytes):
        """Hosted STT (/audio/transcriptions). Returns transcript text.

        Raises NovaAiProviderUnsupported so the caller falls back local;
        any other failure (auth, quota, 5xx) propagates to surface it.
        Local Ollama runtimes have no STT: always unsupported there.
        """
        self.ensure_one()
        if self.kind == "ollama":
            raise NovaAiProviderUnsupported("local runtime")
        response = requests.post(
            self._url("/audio/transcriptions"),
            files={"file": ("note.wav", wav_bytes, "audio/wav")},
            data={"model": model_name},
            headers=self._auth_headers(), timeout=120)
        if self._hosted_unsupported(response):
            raise NovaAiProviderUnsupported(
                response.status_code)
        response.raise_for_status()
        try:
            return (response.json().get("text") or "").strip()
        except ValueError:
            raise NovaAiProviderUnsupported("non-JSON answer")

    def speak_via_provider(self, model_name, text):
        """Hosted TTS (/audio/speech). Returns (bytes, mime).

        Same contract: unsupported falls back, real failures surface.
        """
        self.ensure_one()
        if self.kind == "ollama":
            raise NovaAiProviderUnsupported("local runtime")
        response = requests.post(
            self._url("/audio/speech"),
            json={"model": model_name, "input": text[:600],
                  "voice": "alloy", "response_format": "wav"},
            headers=self._headers(), timeout=120)
        if self._hosted_unsupported(response):
            raise NovaAiProviderUnsupported(
                response.status_code)
        response.raise_for_status()
        mime = (response.headers.get("Content-Type") or "").split(
            ";")[0].strip()
        if not mime.startswith("audio/") or not response.content:
            raise NovaAiProviderUnsupported("non-audio answer")
        return response.content, mime

    def embed_via_provider(self, model_name, texts):
        """Hosted embeddings (/embeddings). Returns aligned vectors."""
        self.ensure_one()
        if self.kind == "ollama":
            raise NovaAiProviderUnsupported("local runtime")
        response = requests.post(
            self._url("/embeddings"),
            json={"model": model_name, "input": texts},
            headers=self._headers(), timeout=120)
        if self._hosted_unsupported(response):
            raise NovaAiProviderUnsupported(
                response.status_code)
        response.raise_for_status()
        try:
            rows = response.json().get("data") or []
            vectors = [row.get("embedding") for row in rows]
        except (ValueError, AttributeError):
            raise NovaAiProviderUnsupported("bad answer")
        if len(vectors) != len(texts) or not all(vectors):
            raise NovaAiProviderUnsupported("short answer")
        return vectors

    # ---- Provider actions -------------------------------------------

    def action_prepare_voice(self):
        """One-click offline voice setup: downloads the TTS voice,
        loads whisper, speaks one word to prove it. Slow the first
        time (models download), instant after — this is the whole
        voice setup story for the buyer."""
        self.ensure_one()
        from odoo.addons.nova_ai.models import nova_ai_voice_local as local
        report = local.prepare_voice(self.env)
        bits = []
        for key in ("stt", "tts"):
            part = report.get(key) or {}
            mark = "✓" if part.get("ok") else "×"
            bits.append("%s %s: %s" % (
                mark, key.upper(), part.get("detail") or "—"))
        self.env["nova.ai.log"].log(
            "probe", model=None, ok=all(
                (report.get(k) or {}).get("ok") for k in ("stt", "tts")),
            detail={"provider": self.name, "prepare_voice": report})
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Offline voice ready")
                if all((report.get(k) or {}).get("ok")
                       for k in ("stt", "tts"))
                else _("Offline voice incomplete"),
                "message": " · ".join(bits),
                "type": "success"
                if all((report.get(k) or {}).get("ok")
                       for k in ("stt", "tts")) else "warning",
                "sticky": True,
            },
        }

    def action_test_connection(self):
        self.ensure_one()
        try:
            if self.kind == "ollama":
                response = self._get("/api/tags")
            else:
                response = self._get("/models")
            response.raise_for_status()
            self.env["nova.ai.log"].log(
                "probe", model=None, ok=True,
                detail={"provider": self.name, "models": "ok"})
        except requests.RequestException as err:
            self.env["nova.ai.log"].log(
                "error", ok=False, error=str(err),
                detail={"provider": self.name})
            raise UserError(self.env._(
                "Could not reach the provider: %s", err))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Provider reachable"),
                "message": _("Model list returned successfully."),
                "type": "success",
                "sticky": False,
            },
        }

    def action_sync_models(self):
        """Create/refresh nova.ai.model rows from the live model list."""
        self.ensure_one()
        try:
            names = self._list_models()
        except requests.RequestException as err:
            self.env["nova.ai.log"].log(
                "error", ok=False, error=str(err),
                detail={"provider": self.name, "action": "sync_models"})
            raise UserError(self.env._(
                "Could not list models: %s", err))
        if not names:
            raise UserError(_(
                "No models returned. Check the base URL and that the "
                "provider has at least one model pulled."))
        existed = {
            m.name: m for m in self.env["nova.ai.model"].with_context(
                # Archived rows stay out of model_ids (active_test)
                # but still own their (provider, name) key at the DB
                # level: re-creating one raises a duplicate-key error.
                # Look them up with inactive included and leave them
                # archived — sync never resurrects a model an admin
                # deliberately retired.
                active_test=False).search(
                    [("provider_id", "=", self.id)])
        }
        added = 0
        for name in names:
            if name in existed:
                continue
            self.env["nova.ai.model"].create(
                {"provider_id": self.id, "name": name})
            added += 1
        self.env["nova.ai.log"].log(
            "probe", ok=True, detail={
                "provider": self.name, "action": "sync_models",
                "added": added, "total": len(self.model_ids)})
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Models synced"),
                "message": self.env._(
                    "%d new, %d known.", added, len(self.model_ids)),
                "type": "success",
                "sticky": False,
            },
        }

    def _list_models(self):
        """Return the provider's raw model names (deduplicated)."""
        self.ensure_one()
        if self.kind == "ollama":
            response = self._get("/api/tags")
            response.raise_for_status()
            names = [m.get("name", "") for m in response.json().get(
                "models", [])]
        else:
            response = self._get("/models")
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("data", payload if isinstance(
                payload, list) else [])
            names = [m.get("id", "") for m in rows if isinstance(m, dict)]
        return sorted({n for n in names if n})
