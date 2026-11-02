# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Knowledge tools: search, read, propose — and the skill router.

The agent gets three knowledge tools, shaped after Javis OS' skill
loader and OKF's search:

* ``search_knowledge`` — finds concepts AND source chunks, ranked the
  way OKF ranks (field weights, terms ANDed), so the agent can then
  read exactly what it needs.
* ``read_knowledge`` — loads one concept by code. This is the
  progressive-disclosure step: the system prompt carries only a one
  line per skill, the body comes on demand.
* ``write_knowledge`` — proposes a concept. It never writes straight
  into the corpus: a proposal lands as a draft for a human to accept
  or reject (Javis' learn gate), and the server refuses to store a
  secret or a prompt injection.
"""

import re

from odoo import _, models
from odoo.exceptions import AccessError

# OKF search weights: title beats tags beats body.
_SEARCH_WEIGHTS = [
    ("name", 5),
    ("code", 4),
    ("tags", 3),
    ("kind", 2),
    ("description", 2),
    ("body", 1),
]

# The router's trigger text is capped (OKF/Javis both cap it).
_DESC_MAX = 150

# Javis' write-side safety filters. A concept that carries a key or a
# token is never stored, and neither is one that reads like an
# instruction to the model.
_SECRET_PATTERNS = [
    re.compile(r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token)\b\s*[:=]"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"(?i)\b(password|passwd|pin)\b\s*[:=]"),
    re.compile(r"(?i)\b(telegram[_-]?bot[_-]?token|api[_-]?key)\b\s*[:=]\s*\S{12,}"),
    re.compile(r"(?i)postgres(ql)?://[^\s]+:[^\s]+@"),
]
_INJECTION_PATTERNS = [
    re.compile(r"(?i)\bignore (all |any )?(previous|prior|above)\b"),
    re.compile(r"(?i)\bdisregard (the )?(system|developer) (prompt|instructions)\b"),
    re.compile(r"(?i)\byou are now\b.{0,20}\b(DAN|developer mode|jailbreak)\b"),
    re.compile(r"(?i)\breveal (your |the )?(system )?prompt\b"),
    re.compile(r"(?i)<\s*(system|assistant)\s*>"),
]


def secret_hits(text):
    """Secret-shaped strings. Empty is the normal answer."""
    text = text or ""
    return [p.pattern for p in _SECRET_PATTERNS if p.search(text)]


def injection_hits(text):
    """Instruction-shaped content aimed at the model, not the user."""
    text = text or ""
    return [p.pattern for p in _INJECTION_PATTERNS if p.search(text)]


def _norm(text):
    return " ".join((text or "").lower().split())


def _score_concept(concept, terms):
    """OKF-style field-weighted score. Terms are ANDed."""
    fields = {
        "name": concept.name or "",
        "code": concept.code or "",
        "tags": " ".join(concept.tags or []) if isinstance(
            concept.tags, (list, tuple)) else (concept.tags or ""),
        "kind": concept.kind or "",
        "description": concept.description or "",
        "body": concept.body or "",
    }
    matched = []
    score = 0.0
    for term in terms:
        hit = False
        for field, weight in _SEARCH_WEIGHTS:
            if term in _norm(fields.get(field)):
                score += weight
                hit = True
        if not hit:
            # AND semantics: a term that hits nothing drops the concept.
            return 0.0, []
        matched.append(term)
    return score, matched


def _visible_concepts(env, user=None):
    """Concepts this user may read: shared ones, their own, or admin."""
    user = user or env.user
    domain = [("active", "=", True)]
    if not user.has_group("base.group_system"):
        domain = ["|"] + domain + [
            "|", ("owner_user_id", "=", False),
            ("owner_user_id", "=", user.id)]
    return env["nova.ai.knowledge"].search(domain)


# Semantic search: a chunk is relevant when the cosine clears the
# floor. Below it, the honest answer is "no match" — never a forced
# hit.
_SEMANTIC_FLOOR = 0.25
_SEMANTIC_MAX = 6
_CHUNK_SCAN = 500


def _semantic_chunks(env, query):
    """The indexed chunks that match the query by meaning.

    Returns the hits, or ``None`` when embeddings are not available at
    all — that is a different thing from "no match", and the caller
    falls back to lexical search only in the first case.
    """
    try:
        from odoo.addons.nova_ai.models import nova_ai_source as source_mod
    except Exception:
        return None
    try:
        vectors = source_mod._embed_texts(env, [query])
    except Exception:
        return None
    if not vectors:
        return None
    try:
        vector = vectors[0]
    except (IndexError, TypeError):
        return None
    if not vector:
        return None
    try:
        chunks = env["nova.ai.chunk"].search(
            [("source_id.active", "=", True)], limit=_CHUNK_SCAN)
    except Exception:
        return None
    hits = []
    for chunk in chunks:
        stored = chunk.embedding
        if not stored:
            continue
        try:
            score = source_mod._cosine(vector, stored)
        except Exception:
            continue
        if score < _SEMANTIC_FLOOR:
            continue
        hits.append({
            "source": chunk.source_id.name,
            "code": "",
            "content": (chunk.content or "")[:600],
            "title": chunk.title or chunk.source_id.name,
            "uri": chunk.uri or "",
            "page": chunk.page or 0,
            "score": round(score, 3),
        })
    hits.sort(key=lambda hit: -hit["score"])
    return hits[:_SEMANTIC_MAX]


def tool_search_knowledge(env, args, ctx):
    """Search the agent's knowledge: skills, memory, wiki and sources.

    Three layers, in the order the agent needs them:

    1. **Concepts** — the skills, memories and wiki entries, ranked the
       way OKF ranks (field weights, terms ANDed).
    2. **Chunks** — the indexed sources, by meaning when embeddings are
       available and by words when they are not.

    The mode says which one answered, so the agent knows whether it is
    holding a procedure or a passage.
    """
    query = (args.get("query") or "").strip()[:200]
    if not query:
        return {"hits": [], "note": "empty query"}
    terms = [t for t in _norm(query).split() if t]
    user = (ctx or {}).get("user") or env.user
    # 1) Concepts (skills, memory, wiki) — field-weighted.
    hits = []
    for concept in _visible_concepts(env, user):
        score, matched = _score_concept(concept, terms)
        if not score:
            continue
        hits.append({
            "code": concept.code,
            "name": concept.name,
            "kind": concept.kind,
            "description": (concept.description or "")[:200],
            "trust": concept.trust_tier,
            "score": round(score, 2),
        })
    hits.sort(key=lambda item: (-item["score"], item["code"]))
    concepts = hits[:8]
    # 2) Indexed source chunks — semantic when embeddings are there.
    semantic = _semantic_chunks(env, query)
    chunks = []
    try:
        if "nova.ai.chunk" in env:
            like = "%" + re.sub(r"[%_]", "", query) + "%"
            for chunk in env["nova.ai.chunk"].search(
                    [("content", "ilike", like),
                     ("source_id.active", "=", True)], limit=6):
                chunks.append({
                    "source": chunk.source_id.name,
                    "code": "",
                    "content": (chunk.content or "")[:600],
                    "title": chunk.title or chunk.source_id.name,
                    "uri": chunk.uri or "",
                    "page": chunk.page or 0,
                })
    except Exception:
        pass
    out = {"hits": concepts + chunks, "mode": "lexical"}
    if concepts:
        out["mode"] = "concepts"
    elif semantic:
        out["hits"] = semantic
        out["mode"] = "semantic"
    out["note"] = (
        "Read a concept with read_knowledge(code=...) before acting on it.")
    return out


def tool_read_knowledge(env, args, ctx):
    """Load one concept by code. The body is the procedure to follow."""
    code = (args.get("code") or "").strip()
    if not code:
        raise AccessError(env._("read_knowledge needs a code."))
    user = (ctx or {}).get("user") or env.user
    concepts = _visible_concepts(env, user).filtered(
        lambda c: c.code == code)
    if not concepts:
        # Give the model the codes it may use, not a bare "not found".
        available = [
            "%s (%s)" % (c.code, c.kind)
            for c in _visible_concepts(env, user)[:20]]
        return {
            "error": "No concept with code %r." % code,
            "available": available,
        }
    concept = concepts[0]
    try:
        concept.action_touch()
    except Exception:
        pass
    return {
        "code": concept.code,
        "name": concept.name,
        "kind": concept.kind,
        "trust": concept.trust_tier,
        "stale": concept.is_stale,
        "body": (concept.body or "")[:8000],
        "sources": concept.sources or [],
    }


def tool_write_knowledge(env, args, ctx):
    """Propose a concept. A human reviews it before it counts."""
    name = (args.get("name") or "").strip()[:120]
    body = (args.get("body") or "").strip()[:8000]
    kind = (args.get("kind") or "memory").strip()
    description = (args.get("description") or "").strip()[:_DESC_MAX]
    if not name or not body:
        raise AccessError(env._(
            "write_knowledge needs a name and a body."))
    if kind not in ("skill", "memory", "wiki", "reference", "document"):
        kind = "memory"
    # Safety filters: a secret or an injection never reaches the corpus.
    hits = secret_hits(body) + secret_hits(name)
    if hits:
        return {
            "error": "Refused: the text looks like it carries a secret. "
                     "Knowledge never stores credentials.",
        }
    hits = injection_hits(body)
    if hits:
        return {
            "error": "Refused: the text reads like an instruction to the "
                     "model, not knowledge for a person.",
        }
    # A proposal, not a write: it lands as a draft awaiting review.
    agent = (ctx or {}).get("agent")
    proposed_by = "assistant:%s" % (agent.id if agent else "nova")
    concept = env["nova.ai.knowledge"].sudo().create({
        "name": name,
        "kind": kind,
        "description": description,
        "body": body,
        "status": "draft",
        "proposed_by": proposed_by,
        "generated_by": proposed_by,
        "sources": args.get("sources") or [],
    })
    return {
        "queued": True,
        "code": concept.code,
        "name": concept.name,
        "message": "Proposed as a draft. A human reviews it before the "
                   "agent may use it.",
    }


def tool_list_flows(env, args, ctx):
    """List the flows this agent may run.

    Returns the trigger, the node count and the input variables so the
    model can run a flow with the right arguments in one call — and
    draft a new one with draft_flow when nothing matches.
    """
    agent = (ctx or {}).get("agent")
    domain = [("active", "=", True)]
    if agent:
        domain = ["|", ("agent_id", "=", agent.id), ("agent_id", "=", False)]
    query = (args.get("query") or "").strip()
    rows = []
    for flow in env["nova.ai.flow"].search(domain, limit=20):
        if query:
            haystack = "%s %s" % (flow.name or "", flow.description or "")
            if query.lower() not in haystack.lower():
                continue
        try:
            nodes, _edges = flow._graph()
        except Exception:
            nodes = []
        inputs = []
        try:
            for node in nodes:
                if isinstance(node, dict) and node.get("type") in (
                        "start", "trigger"):
                    for var in ((node.get("config") or {}).get(
                            "variables") or []):
                        if isinstance(var, dict) and var.get("name"):
                            inputs.append(var.get("name"))
                    break
        except Exception:
            pass
        rows.append({
            "name": flow.name,
            "description": (flow.description or "")[:200],
            "steps": len(flow.steps or []),
            "nodes": len(nodes or []),
            "trigger": flow.trigger_type or "manual",
            "inputs": inputs,
        })
    note = ("Run one with run_flow(flow=name, arguments={...}). "
            "When nothing matches, build it with draft_flow(nodes=[...], "
            "edges=[...], trigger={...}) — node types: trigger, "
            "odoo.search/read/create/write/note, if, switch, set, code, "
            "http, ai.agent, loop.foreach, merge, human.approval, "
            "subflow, output. IF branches use source_handle true/false; "
            "variables flow as ${name} or {{$json.name}}.")
    return {"flows": rows, "note": note}


def tool_run_flow(env, args, ctx):
    """Run a saved flow. Writes queue a card; nothing lands unapproved."""
    name = (args.get("flow") or args.get("name") or "").strip()
    if not name:
        raise AccessError(env._("run_flow needs a flow name."))
    # No sudo: the caller may only run flows they can read, and the
    # flow then runs with their rights (see _agent_env), so record
    # rules apply to every step.
    flows = env["nova.ai.flow"].search(
        [("name", "=ilike", name), ("active", "=", True)], limit=1)
    if not flows:
        return {"error": "No flow named %r." % name}
    flow = flows[0]
    trigger = (args.get("trigger") or "chat").strip() or "chat"
    depth = 0
    try:
        depth = int((ctx or {}).get("_flow_depth") or 0)
    except (TypeError, ValueError):
        depth = 0
    result = flow.with_context(_flow_depth=depth).run_graph(
        args.get("arguments") or {}, trigger_kind=trigger)
    return result


def tool_list_flow_runs(env, args, ctx):
    """Recent flow runs: what ran, how it ended, how long it took."""
    name = (args.get("flow") or args.get("name") or "").strip()
    try:
        limit = max(1, min(int(args.get("limit") or 10), 25))
    except (TypeError, ValueError):
        limit = 10
    domain = []
    if name:
        flows = env["nova.ai.flow"].search(
            [("name", "=ilike", name)], limit=1)
        if not flows:
            return {"error": "No flow named %r." % name}
        domain.append(("flow_id", "=", flows.id))
    runs = env["nova.ai.flow.run"].search(
        domain, order="id desc", limit=limit)
    return {"runs": [{
        "flow": run.flow_id.name or "",
        "status": run.status or "",
        "trigger": run.trigger or "",
        "at": str(run.started_at or ""),
        "duration_ms": run.duration_ms or 0,
        "error": (run.error or "")[:200],
    } for run in runs]}


# ------------------------------------------------------------------ router
# Javis' skill router, ported: the system prompt carries ONE LINE per
# skill (the trigger text) and the body is loaded on demand. A skill that
# was never useful does not cost a token; a skill that matches is put in
# front of the model before it acts.

_LIST_MAX = 40
_LIST_CHAR_BUDGET = 4000
_SKILL_MAX = 12000
_MIN_SCORE = 0.24
_AMBIGUITY_MARGIN = 0.08
# The model asking for a skill by name is an explicit request, not a
# guess — Javis routes it even when the score is low.
_EXPLICIT_RE = re.compile(
    r"(?i)\b(skill|procedure|playbook|how do i|how to|walk me through|"
    r"process for|steps to)\b")


def _skill_score(query, concept):
    """Javis' skill score: coverage, discriminativeness, phrase, explicit."""
    query_terms = set(_norm(query).split())
    if not query_terms:
        return 0.0
    name_terms = set(_norm(
        "%s %s" % (concept.name or "", concept.code or "")).split())
    all_terms = name_terms | set(_norm(
        "%s %s" % (concept.description or "", concept.body or "")).split())
    coverage = len(query_terms & all_terms) / float(max(1, len(query_terms)))
    discriminative = len(query_terms & name_terms) / float(
        max(1, len(name_terms)))
    phrase = bool(
        _norm(query) and (_norm(query) in _norm(concept.description or "")
                          or _norm(query) in _norm(concept.name or "")))
    explicit = _norm(query) == _norm(concept.name or "") or \
        _norm(query) == _norm(concept.code or "")
    return min(1.0, coverage * 0.52 + discriminative * 0.25
               + (0.13 if phrase else 0.0) + (0.25 if explicit else 0.0))


def _skills_of(env, user=None):
    """The skills this user may use, ordered by how often they helped."""
    user = user or env.user
    domain = [("active", "=", True), ("kind", "=", "skill")]
    if not user.has_group("base.group_system"):
        domain = ["|"] + domain + [
            "|", ("owner_user_id", "=", False),
            ("owner_user_id", "=", user.id)]
    concepts = env["nova.ai.knowledge"].sudo().search(domain)
    return concepts.sorted(
        key=lambda c: (-(c.use_count or 0), c.code or ""))


def router_block(env, user=None, loaded=None):
    """The compact skill list that goes into the system prompt.

    Capped by count AND by rendered characters: a skill the model cannot
    see is a skill that never fires. ``loaded`` is the skill already
    injected for this request — it is left out of the list and named in
    the closing line, so the model does not fetch it twice.
    """
    try:
        skills = _skills_of(env, user)
    except Exception:
        return ""
    if not skills:
        return ""
    loaded_code = (loaded or "")
    lines = ["", "# === SKILLS YOU MAY USE ==="]
    budget = _LIST_CHAR_BUDGET
    kept = 0
    for skill in skills:
        if kept >= _LIST_MAX:
            break
        if loaded_code and skill.code == loaded_code:
            continue
        description = (skill.description or "").strip()[:_DESC_MAX]
        if not description:
            continue
        line = "- %s (%s): %s" % (skill.code, skill.name, description)
        if len(line) + 1 > budget:
            break
        lines.append(line)
        budget -= len(line) + 1
        kept += 1
    if not kept and not loaded_code:
        return ""
    if loaded_code:
        lines.append(
            "The skill `%s` is already loaded for this request — follow it "
            "from the text above." % loaded_code)
    lines.append(
        "When a request matches one of these, call read_knowledge(code=...) "
        "first and follow it exactly. Load one at a time, only when it "
        "truly matches — never all of them.")
    return "\n".join(lines)


def select_skill(env, query, user=None):
    """The one skill that best matches this request, or None.

    Javis' scoring, verbatim in spirit: coverage of the query, extra
    weight when the match is in the skill NAME, a bonus for a phrase
    hit, and an explicit request always wins. A near-tie is a near-miss:
    the router refuses to guess, and the model falls back to the text
    router (the list in the system prompt).
    """
    try:
        if not query or not str(query).strip():
            return None
        skills = _skills_of(env, user)
        if not skills:
            return None
        scored = []
        for skill in skills:
            score = _skill_score(str(query), skill)
            if score > 0:
                scored.append((score, skill))
        if not scored:
            return None
        scored.sort(key=lambda item: (-item[0], item[1].code or ""))
        best_score, best = scored[0]
        runner = scored[1][0] if len(scored) > 1 else 0.0
        explicit = bool(_EXPLICIT_RE.search(str(query)))
        if best_score - runner < _AMBIGUITY_MARGIN and not explicit:
            # Too close to call: do not pick.
            return None
        if best_score < _MIN_SCORE and not explicit:
            return None
        return best
    except Exception:
        return None
