# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Model capabilities and the tier engine.

The tier decides what an agent may attempt against this model:

* T1 Basic — the model cannot (or was not asked to) call tools, or its
  context is small. Only deterministic server-side flows.
* T2 Assistant — tool calling works and context >= 8k. Full tool loop.
* T3 Agent — tool calling, context >= 32k and embeddings available.
  The only tier allowed to auto-apply writes behind the audit log.

Probing asks the endpoint three cheap questions (context length,
tools, embeddings) and records the answers. Nothing is guessed: an
unknown capability is a *negative* answer, so the tier degrades
instead of over-promising. An admin can always override upward with
`tier_override` after verifying a model by hand.

Two realities of local models shape this file:

* **Text tool calls.** Many local quantizations emit the tool call as
  JSON *text* instead of the native `tool_calls` array. Both shapes are
  accepted and normalized, so a 7B local model is genuinely useful
  instead of scoring "no tools" and dropping to Basic.
* **Capability errors are not connection errors.** A 4xx/5xx on one
  probe question is a "no" for that ability (embeddings disabled, no
  JSON mode). Only timeouts and connection failures mean "endpoint
  dead" — those abort the probe with an error log instead of storing
  a false negative.
"""

import json
import re

import requests

from odoo import _, api, fields, models

_CONTEXT_MIN_T2 = 8000
_CONTEXT_MIN_T3 = 32000


class NovaAiModel(models.Model):
    _name = "nova.ai.model"
    _description = "Nova AI Model"
    _order = "provider_id, name"

    name = fields.Char(required=True, index=True)
    provider_id = fields.Many2one(
        "nova.ai.provider", required=True, ondelete="cascade", index=True)
    context_length = fields.Integer(
        help="Max context in tokens (0 = unknown).")
    supports_tools = fields.Boolean()
    supports_embeddings = fields.Boolean()
    supports_json = fields.Boolean()
    supports_vision = fields.Boolean(
        help="Reads images (bill scans). Learned automatically on first "
             "successful vision use — the probe does not test this, so a "
             "plug-and-play endpoint needs no configuration.")
    tier = fields.Selection(
        [("t1", "T1 Basic"), ("t2", "T2 Assistant"), ("t3", "T3 Agent")],
        compute="_compute_tier",
        store=True,
        help="Effective tier after the admin override.",
    )
    tier_override = fields.Selection(
        [("none", "No override"), ("t1", "T1 Basic"),
         ("t2", "T2 Assistant"), ("t3", "T3 Agent")],
        default="none",
    )
    tier_reason = fields.Char(compute="_compute_tier", store=True)
    last_probe = fields.Datetime(readonly=True)
    active = fields.Boolean(default=True)
    # --- routing inputs (Javis' capability router) ------------------
    cost_in_per_mtok = fields.Float(
        digits=(10, 4), default=0.0,
        help="Cost per million input tokens. 0 = free. Used to rank "
             "models that can all do the job.")
    cost_out_per_mtok = fields.Float(
        digits=(10, 4), default=0.0,
        help="Cost per million output tokens.")
    reliability = fields.Float(
        default=0.5, digits=(3, 2),
        help="0..1 — how often this endpoint answers cleanly. Learned "
             "from real turns; a hand-set value wins until the first "
             "failure updates it.")
    latency_hint_ms = fields.Integer(
        default=0,
        help="Typical response time in ms. 0 = unknown. Only a tie-break.")
    supports_reasoning = fields.Boolean(
        help="Long reasoning. Declared by the admin; the probe cannot "
             "tell, so it is never inferred.")
    routing_note = fields.Char(
        compute="_compute_routing_note",
        help="Which jobs the router would send here, and why.")

    @api.depends("supports_tools", "supports_vision", "supports_json",
                 "supports_reasoning", "supports_embeddings",
                 "cost_in_per_mtok", "cost_out_per_mtok", "reliability")
    def _compute_routing_note(self):
        for record in self:
            caps = []
            if record.supports_tools:
                caps.append("tools")
            if record.supports_vision:
                caps.append("vision")
            if record.supports_json:
                caps.append("json")
            if record.supports_reasoning:
                caps.append("reasoning")
            if record.supports_embeddings:
                caps.append("embeddings")
            if not caps:
                record.routing_note = (
                    "No capabilities declared — the router will not pick "
                    "this model for a step that needs one.")
            else:
                record.routing_note = (
                    "Picked for steps needing: %s. Cost %.2f/%.2f per Mtok, "
                    "reliability %.0f%%." % (
                        ", ".join(caps),
                        record.cost_in_per_mtok or 0.0,
                        record.cost_out_per_mtok or 0.0,
                        (record.reliability or 0.0) * 100))
    fallback_model_id = fields.Many2one(
        "nova.ai.model", ondelete="set null",
        help="Failover for transient provider errors: when a turn dies "
             "on this model before anything applied, it re-runs once on "
             "the fallback with the same agent policy. Pick a model of "
             "the same tier; auto-apply (T3) agents never fail over.")

    _model_uniq = models.Constraint(
        "UNIQUE(provider_id, name)", "Each model is known once per provider.")

    @api.depends("supports_tools", "supports_embeddings", "context_length",
                 "tier_override")
    def _compute_tier(self):
        for model in self:
            if model.tier_override != "none":
                model.tier = model.tier_override
                model.tier_reason = _("Manual override")
                continue
            if not model.supports_tools:
                model.tier = "t1"
                model.tier_reason = _("No tool calling")
            elif model.context_length >= _CONTEXT_MIN_T3 and \
                    model.supports_embeddings:
                model.tier = "t3"
                model.tier_reason = _("Tools + large context + embeddings")
            elif not model.context_length:
                # The endpoint never said how big the window is. The model
                # still drives the tool loop — with a short leash (history
                # capped server-side) instead of a demotion to Basic.
                model.tier = "t2"
                model.tier_reason = _("Tools; context unadvertised")
            elif model.context_length >= _CONTEXT_MIN_T2:
                model.tier = "t2"
                model.tier_reason = _("Tools + usable context")
            else:
                model.tier = "t1"
                model.tier_reason = _("Context too small for tools")

    @api.model
    def history_cap(self, model):
        """How many past messages the chat loop may send. Unknown
        windows get a short leash instead of a tier demotion."""
        if not model or not model.context_length:
            return 4
        return 12

    # ---- Probe ------------------------------------------------------

    def action_probe(self):
        """Detect what this model can do, one capability at a time.

        Each question stands on its own: what the endpoint answers is
        stored, what it does not answer keeps its previous value and is
        named in the result. A flaky gateway must never wipe verified
        flags - a dead endpoint reads as dead, not as disproven.
        """
        self.ensure_one()
        started = fields.Datetime.now()
        problems = []
        found = {}

        def _ask(label, func):
            try:
                found[label] = func()
            except requests.RequestException as err:
                problems.append("%s: %s" % (label, err))

        _ask("context_length", self._probe_context_length)
        _ask("supports_tools", self._probe_tools)
        _ask("supports_embeddings", self._probe_embeddings)
        _ask("supports_json", self._probe_json)
        _ask("supports_vision", self._probe_vision)

        values = dict(found)
        values["last_probe"] = fields.Datetime.now()
        if "supports_tools" in found and not found["supports_tools"]:
            # Only a tools answer may clear a hand-verified override:
            # a healthy "no" disproves it, a failed question says
            # nothing and leaves it alone.
            values["tier_override"] = "none"
        self.write(values)
        self.invalidate_recordset()
        verdict = []
        for key, label in (
                ("supports_tools", "tools"),
                ("supports_json", "JSON"),
                ("supports_embeddings", "embeddings"),
                ("supports_vision", "vision"),
                ("context_length", "context")):
            if key in found:
                verdict.append("%s=%s" % (label, found[key]))
            else:
                verdict.append("%s=? (kept)" % label)
        message = self.env._(
            "Tier: %s (%s). Probed: %s.",
            dict(self._fields["tier"].selection).get(self.tier),
            self.tier_reason, ", ".join(verdict))
        if problems:
            message = self.env._(
                "%s Probe could not check: %s",
                message, "; ".join(problems)[:200])
        self.env["nova.ai.log"].log(
            "probe" if not problems else "error",
            model=self, ok=not problems, error="; ".join(problems)[:2000],
            latency_ms=int((values["last_probe"] - started).total_seconds()
                           * 1000),
            detail={
                "model": self.name, "provider": self.provider_id.name,
                # Log what the record HOLDS after the write, not just
                # what this probe answered: on a partial failure the
                # two differ, and the audit trail must show the truth.
                "context_length": self.context_length,
                "supports_tools": self.supports_tools,
                "supports_embeddings": self.supports_embeddings,
                "supports_json": self.supports_json,
                "supports_vision": self.supports_vision,
                "answered": sorted(found),
                "tier": self.tier,
            })
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Probe complete") if not problems else _("Probe failed"),
                "message": message,
                "type": "success" if not problems else "danger",
                "sticky": not problems,
            },
        }

    def _chat(self, messages, tools=None, extra=None):
        """One chat completion through the provider dialect."""
        provider = self.provider_id
        payload = {"model": self.name, "messages": messages}
        if provider.kind == "ollama":
            body = {
                "model": self.name,
                "messages": messages,
                "stream": False,
                "tools": tools or [],
                "options": {
                    "temperature": 0,
                    "num_ctx": min(self.context_length or 4096, 4096)},
            }
            if extra:
                body.update(extra)
            response = provider._post("/api/chat", body)
        else:
            if tools:
                payload["tools"] = tools
            if extra:
                payload.update(extra)
            response = provider._post("/chat/completions", payload)
        response.raise_for_status()
        return response.json()

    def _probe_context_length(self):
        provider = self.provider_id
        if provider.kind == "ollama":
            try:
                response = provider._post(
                    "/api/show", {"model": self.name})
                response.raise_for_status()
                info = response.json().get("model_info", {})
                for key, value in info.items():
                    if key.endswith("context_length"):
                        return int(value)
            except (requests.RequestException, ValueError, TypeError):
                pass
        # OpenAI-compatible servers sometimes advertise the ceiling in
        # the model list entry; vLLM does, OpenAI does not.
        try:
            response = provider._get("/models")
            response.raise_for_status()
            rows = response.json().get("data", [])
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if row.get("id") == self.name or row.get("name") == self.name:
                    for key in ("context_length", "max_model_len",
                                "context_window"):
                        if row.get(key):
                            return int(row[key])
        except (requests.RequestException, ValueError, TypeError):
            pass
        return 0

    def _probe_tools(self):
        """Ask the model to call a trivial function and check it did.

        Connection errors propagate: the caller distinguishes "endpoint
        answered no" from "endpoint never answered".
        """
        probe_tool = [{
            "type": "function",
            "function": {
                "name": "ping",
                "description": "Reply with the word pong.",
                "parameters": {"type": "object", "properties": {}},
            },
        }]
        data = self._chat(
            [{"role": "user", "content": "Call ping."}], tools=probe_tool)
        return self._has_tool_call(data)

    @api.model
    def _has_tool_call(self, data):
        """True when the answer carries a tool call in either shape."""
        return bool(self._tool_calls_of(data))

    @api.model
    def _tool_calls_of(self, data):
        """Normalize tool calls to [{name, arguments}] from both shapes:
        the native array (OpenAI/Ollama) and JSON-text calls, which
        local models emit surprisingly often."""
        message = data.get("message") or {}
        choices = data.get("choices") or []
        if choices:
            message = (choices[0].get("message") or {}) or message
        raw = message.get("tool_calls") or data.get("tool_calls") or []
        calls = []
        for call in raw:
            function = (call or {}).get("function") or {}
            name = function.get("name") or call.get("name")
            if not name:
                continue
            arguments = function.get("arguments", call.get("arguments"))
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = {}
            calls.append({"name": name,
                          "arguments": arguments if isinstance(
                              arguments, dict) else {}})
        if not calls:
            return self._tool_call_from_text(message.get("content"))
        return calls

    @api.model
    def _tool_call_from_text(self, content):
        """Parse `{"name": "x", "arguments": {...}}` emitted as plain
        text (or a JSON array of those). Also unwraps the
        `<tool_call>...</tool_call>` dialect some hosted models emit:
        a bare name, a name/arguments object, or an action/model
        object. Returns [] when the text is ordinary prose."""
        text = (content or "").strip()
        if not text:
            return []
        blocks = re.findall(r"<tool_call>(.*?)</tool_call>", text,
                            re.S | re.I)
        if blocks:
            calls = []
            for block in blocks:
                block = (block or "").strip()
                if not block:
                    continue
                if block[0] not in "{[":
                    if re.fullmatch(r"[A-Za-z0-9_.:=-]+", block):
                        calls.append({"name": block, "arguments": {}})
                    continue
                try:
                    data = json.loads(block)
                except ValueError:
                    continue
                rows = data if isinstance(data, list) else [data]
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    name = row.get("name") or row.get("action")
                    if not name or not isinstance(name, str):
                        continue
                    arguments = row.get("arguments",
                                        row.get("parameters"))
                    if arguments is None:
                        # {"action": "create_draft", "model": ...,
                        # ...rest}: everything but the verb is input.
                        arguments = {
                            key: value for key, value in row.items()
                            if key not in ("name", "action")}
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except ValueError:
                            arguments = {}
                    calls.append({
                        "name": name, "arguments": arguments
                        if isinstance(arguments, dict) else {}})
            return calls
        if text[0] not in "{[":
            return []
        try:
            data = json.loads(text)
        except ValueError:
            match = re.search(r"\{.*\}", text, re.S)
            if not match:
                return []
            try:
                data = json.loads(match.group(0))
            except ValueError:
                return []
        rows = data if isinstance(data, list) else [data]
        calls = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("name"):
                continue
            arguments = row.get("arguments", row.get("parameters")) or {}
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = {}
            if isinstance(arguments, dict):
                calls.append({"name": row["name"], "arguments": arguments})
        return calls

    def _probe_embeddings(self):
        try:
            provider = self.provider_id
            if provider.kind == "ollama":
                response = provider._post(
                    "/api/embeddings", {"model": self.name, "prompt": "ping"})
            else:
                response = provider._post(
                    "/embeddings", {"model": self.name, "input": "ping"})
            response.raise_for_status()
            payload = response.json()
            vector = payload.get("embedding") or (
                payload.get("data") or [{}])[0].get("embedding")
            return bool(vector)
        except requests.HTTPError:
            # A 4xx/5xx here is a real "this server cannot embed"
            # (Ollama answers 500 without --embeddings), not a dead
            # endpoint — it must degrade the tier, not fail the probe.
            return False
        except (ValueError, IndexError, TypeError):
            return False

    # A rendered "OK": the cheapest image a model must actually READ.
    # A blank pixel proves nothing — models answer empty to nothing.
    @api.model
    def _vision_ping_png(self):
        try:
            from PIL import Image, ImageDraw
            import io
            image = Image.new("RGB", (200, 60), "white")
            ImageDraw.Draw(image).text((70, 15), "OK", fill="black")
            out = io.BytesIO()
            image.save(out, format="PNG")
            return out.getvalue()
        except Exception:
            return None

    def _probe_vision(self):
        """One tiny rendered-text ping. Vision is optional: any failure
        means False, never a failed probe (a text-only model is
        healthy). Generous token budget: reasoning models spend the
        first tokens thinking, and the answer may land in
        reasoning_content instead of content."""
        import base64
        raw = self._vision_ping_png()
        if not raw:
            return False
        pixel = base64.b64encode(raw).decode("ascii")
        try:
            if self.provider_id.kind == "ollama":
                messages = [{"role": "user",
                             "content": "Reply with exactly what the "
                                        "image says.",
                             "images": [pixel]}]
            else:
                messages = [{"role": "user", "content": [
                    {"type": "text",
                     "text": "Reply with exactly what the image says."},
                    {"type": "image_url", "image_url": {
                        "url": "data:image/png;base64," + pixel}}]}]
            data = self._chat(messages, extra={"max_tokens": 80})
            choice = (data.get("choices") or [{}])[0]
            if not isinstance(choice, dict):
                choice = {}
            message = choice.get("message") or {}
            if isinstance(data.get("message"), dict):
                message = data.get("message")
            seen = ((message.get("content") or "") + " " +
                    (message.get("reasoning_content") or "")).upper()
            return "OK" in seen
        except Exception:
            return False

    def _probe_json(self):
        # Ollama takes the native `format` key; OpenAI-compat servers
        # take response_format. Asking a weak model for JSON without
        # the key would score a false positive, so dialects diverge
        # exactly here.
        try:
            data = self._chat(
                [{"role": "user", "content": "Reply with the JSON {}."}],
                extra={
                    "format": "json"
                    if self.provider_id.kind == "ollama"
                    else {"type": "json_object"},
                },
            )
            return bool(self._content_of(data))
        except requests.HTTPError:
            # No response_format/json-object support on this server.
            return False

    @api.model
    def _content_of(self, data):
        """Extract text from either dialect's response shape."""
        choices = data.get("choices") or []
        if choices:
            return ((choices[0].get("message") or {}).get("content") or "")
        return (data.get("message") or {}).get("content") or ""

    @api.model
    def _usage_of(self, data):
        usage = data.get("usage") or {}
        return (int(usage.get("prompt_tokens") or 0),
                int(usage.get("completion_tokens") or 0))
