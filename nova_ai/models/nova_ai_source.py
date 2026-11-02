# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Knowledge sources and their chunks.

A source is either a public URL (fetched on demand and chunked) or a
record selector (indexed on demand). Retrieval is semantic whenever
chunk vectors exist (local embeddings, free) and lexical otherwise —
the same chunks serve both without any client change. Nothing here
phones home: fetches go through the same SSRF-guarded fetcher as the
agent tool, and embeddings run on the local Ollama endpoint.
"""

from odoo import _, api, fields, models

_CHUNK_SIZE = 1200
_EMBED_MODEL_PARAM = "nova_ai.embed_model"
_EMBED_MODEL_DEFAULT = "nomic-embed-text"


def _chunks_of(text):
    words = (text or "").split()
    out = []
    current = []
    count = 0
    for word in words:
        current.append(word)
        count += len(word) + 1
        if count >= _CHUNK_SIZE:
            out.append(" ".join(current))
            current = []
            count = 0
    if current:
        out.append(" ".join(current))
    return out


def _embed_base_url(env):
    """Local Ollama endpoint for embeddings (never a hosted key)."""
    try:
        provider = env["nova.ai.provider"].search(
            [("kind", "=", "ollama")], limit=1)
        if provider and provider.base_url:
            return provider.base_url.rstrip("/")
    except Exception:
        pass
    return "http://127.0.0.1:11434"


def _embed_texts(env, texts):
    """Free local embeddings (nomic-embed-text). Returns a list of
    vectors aligned with `texts`, or None when unavailable — callers
    fall back to lexical search rather than failing."""
    clean = [(t or "")[:2000] for t in texts]
    if not clean:
        return []
    try:
        import requests
    except ImportError:
        return None
    try:
        model = env["ir.config_parameter"].get_str(
            _EMBED_MODEL_PARAM, _EMBED_MODEL_DEFAULT) \
            or _EMBED_MODEL_DEFAULT
    except Exception:
        model = _EMBED_MODEL_DEFAULT
    vectors = []
    try:
        for start in range(0, len(clean), 32):
            response = requests.post(
                _embed_base_url(env) + "/api/embed",
                json={"model": model,
                      "input": clean[start:start + 32]},
                timeout=180)
            response.raise_for_status()
            vectors.extend(response.json().get("embeddings") or [])
    except Exception:
        return None
    if len(vectors) != len(clean):
        return None
    return vectors


def _cosine(left, right):
    """Pure-Python cosine (worker has no numpy guarantee)."""
    try:
        dot = sum(a * b for a, b in zip(left, right))
        norm = (sum(a * a for a in left) ** 0.5) * (
            sum(b * b for b in right) ** 0.5)
    except (TypeError, ValueError):
        return 0.0
    if not norm:
        return 0.0
    return dot / norm


class NovaAiSource(models.Model):
    _name = "nova.ai.source"
    _description = "Nova AI Knowledge Source"
    _order = "name"

    name = fields.Char(required=True)
    kind = fields.Selection(
        [("url", "Web page"), ("records", "Records")], required=True,
        default="url")
    url = fields.Char(help="Public URL, fetched on demand.")
    model_name = fields.Char(help="Model to index, e.g. product.product.")
    domain = fields.Char(default="[]",
                         help="Domain limiting the indexed records.")
    active = fields.Boolean(default=True)
    chunk_ids = fields.One2many("nova.ai.chunk", "source_id")
    chunk_count = fields.Integer(compute="_compute_chunk_count")
    last_fetch = fields.Datetime(readonly=True)

    @api.depends("chunk_ids")
    def _compute_chunk_count(self):
        for source in self:
            source.chunk_count = len(source.chunk_ids)

    def action_fetch(self):
        self.ensure_one()
        from odoo.addons.nova_ai.models.nova_ai_tools import tool_fetch_url
        if self.kind == "url":
            if not self.url:
                raise ValueError(_("Set a URL first."))
            fetched = tool_fetch_url(self.env, {"url": self.url}, {})
            page_title = fetched.get("title") or ""
            texts = [(fetched.get("title") or "") + "\n" + (fetched.get(
                "text") or "")]
            # Every passage of a page points back at the page, and the
            # page points at itself: a citation that can be opened.
            origin = {"uri": self.url or "", "title": page_title}
            origins = []
        else:
            texts, origins = self._index_records()
            origin = {"uri": "", "title": self.name or ""}
        self.chunk_ids.unlink()
        seq = 0
        fresh = []
        for index, text in enumerate(texts):
            # The origin of THIS passage: the record it was built from,
            # or the page for a web source.
            mine = origins[index] if index < len(origins) else origin
            for piece in _chunks_of(text):
                seq += 1
                fresh.append(self.env["nova.ai.chunk"].create({
                    "source_id": self.id, "seq": seq, "content": piece,
                    "title": mine.get("title") or origin.get("title") or "",
                    "uri": mine.get("uri") or origin.get("uri") or "",
                }))
        # Vectors are best-effort: a fetch without embeddings still
        # leaves fully working lexical chunks behind.
        try:
            vectors = _embed_texts(
                self.env, [c.content for c in fresh])
            if vectors:
                for chunk, vector in zip(fresh, vectors):
                    chunk.write({"embedding": list(vector)})
        except Exception:
            pass
        self.write({"last_fetch": fields.Datetime.now()})
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Source indexed"),
                "message": self.env._("%d chunks.", seq),
                "type": "success",
                "sticky": False,
            },
        }

    def _index_records(self):
        from odoo.addons.nova_ai.models.nova_ai_tools import (
            _allowed_models, _hr)
        if self.model_name not in _allowed_models(self.env):
            raise ValueError(self.env._(
                "Model %s is not available to agents.", self.model_name))
        try:
            from odoo.tools.safe_eval import safe_eval
            domain = safe_eval(self.domain or "[]")
        except Exception:
            domain = []
        if not isinstance(domain, list):
            domain = []
        texts = []
        origins = []
        for record in self.env[self.model_name].search(domain, limit=200):
            bits = [record.display_name]
            for fname in ("description", "name", "notes", "comment"):
                if fname in record._fields:
                    try:
                        bits.append(_hr(record[fname] or "", 800))
                    except Exception:
                        continue
            texts.append("\n".join(b for b in bits if b))
            # The passage points at the record it came from, so an
            # answer built on it can be opened at the source.
            origins.append({
                "uri": "odoo://%s/%s" % (self.model_name, record.id),
                "title": record.display_name or "",
            })
        return [t for t in texts if t], origins


class NovaAiChunk(models.Model):
    _name = "nova.ai.chunk"
    _description = "Nova AI Knowledge Chunk"
    _order = "source_id, seq"
    _log_access = False

    source_id = fields.Many2one(
        "nova.ai.source", required=True, index=True, ondelete="cascade")
    seq = fields.Integer()
    content = fields.Text()
    embedding = fields.Json(
        help="Local embedding vector (free). Empty = lexical only.")
    title = fields.Char(
        help="Heading this passage sits under, when the source has one.")
    page = fields.Integer(
        help="Page number, for a PDF. Lets a citation point at a page.")
    uri = fields.Char(
        help="Where this passage came from: a URL, or an odoo:// deep "
             "link (model/res_id) when the source is a record. A "
             "citation that can be opened is worth ten that cannot.")
    metadata = fields.Json(
        help="Anything else worth keeping with the passage.")
