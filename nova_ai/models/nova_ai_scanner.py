# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Scan custom Odoo code into knowledge the agent can use.

An agent that cannot see the deployment's own models is blind to half
the business: it knows Odoo's core from its seeds, but not YOUR
sale.order.line extension, YOUR approval model, or YOUR computed
fields. This module reads the code the way a developer would - models,
fields, relations, methods - and turns each model into one knowledge
concept (kind="reference") that the router can match and the agent can
read before acting.

Read-only and import-free: the files are parsed with ``ast``, never
imported and never executed. A scan cannot run anyone's code, and it
skips tests, migrations, static assets and bytecode by construction.

Two ways to use it:

* The Studio's "Scan custom code" button (an administrator's explicit
  act): every model found becomes a stable concept with its file as
  the source. The brain fills up in one click.
* The ``scan_codebase`` agent tool: returns the catalogue, or the full
  body for the models the agent names. The agent proposes what it
  learns via write_knowledge, so the human review gate still applies
  to everything the agent itself writes.
"""

import ast
import os

# Directories that are never code worth indexing, whatever they hold.
_SKIP_DIRS = frozenset({
    "__pycache__", ".git", "static", "tests", "migrations", "demo",
    "data", "i18n", "views", "report", "wizard",
})

# Files that are never models, whatever their name.
_SKIP_FILES = frozenset({
    "__init__.py",
})

# A model is a class statement whose bases name one of these.
_MODEL_BASES = frozenset({
    "Model", "TransientModel", "AbstractModel",
})

# Relational fields carry their target as the first positional arg.
_RELATIONAL = {
    "Many2one": "many2one",
    "One2many": "one2many",
    "Many2many": "many2many",
    "Reference": "reference",
}

# Fields a concept never lists: the ORM's own bookkeeping.
_SKIP_FIELDS = frozenset({
    "id", "create_uid", "create_date", "write_uid", "write_date",
    "__last_update", "display_name",
})


def _string(node):
    """A string literal's value, or None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Str):  # Python < 3.8 AST shape
        return node.s
    return None


def _name(node):
    """A dotted name from an attribute chain: fields.Char -> fields.Char."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    parts.reverse()
    return ".".join(parts)


def _const(node):
    """A small literal (string/number/bool/None/list of those)."""
    if isinstance(node, ast.Constant) and isinstance(
            node.value, (str, int, float, bool, type(None))):
        return node.value
    if isinstance(node, ast.Str):
        return node.s
    if isinstance(node, ast.Num):
        return node.n
    if isinstance(node, ast.List):
        out = []
        for item in node.elts:
            value = _const(item)
            if value is Ellipsis:
                return Ellipsis
            out.append(value)
        return out
    if isinstance(node, ast.Tuple):
        return _const_list(node.elts)
    return Ellipsis


def _const_list(items):
    out = []
    for item in items:
        value = _const(item)
        if value is Ellipsis:
            return Ellipsis
        out.append(value)
    return out


def _field_info(assign):
    """A ``name = fields.X(...)`` assignment, or None."""
    if len(assign.targets) != 1:
        return None
    target = assign.targets[0]
    if not isinstance(target, ast.Name):
        return None
    call = assign.value
    if not isinstance(call, ast.Call):
        return None
    dotted = _name(call.func)
    if "." in dotted:
        head, _, tail = dotted.rpartition(".")
        if head != "fields":
            return None
    else:
        tail = dotted
    info = {"name": target.id, "type": tail}
    for keyword in call.keywords:
        if keyword.arg is None:  # **kwargs - opaque, skip the field
            return None
        value = _const(keyword.value)
        if value is Ellipsis:
            continue
        info[keyword.arg] = value
    # Relational target lives in the first positional argument.
    if tail in _RELATIONAL and call.args:
        target_model = _const(call.args[0])
        if target_model and target_model is not Ellipsis:
            info["comodel"] = target_model
    return info


def _method_info(node):
    """A ``def`` statement: name, docstring head, decorators."""
    doc = (ast.get_docstring(node) or "").strip().splitlines()
    head = doc[0].strip()[:160] if doc else ""
    decorators = []
    for deco in node.decorator_list:
        if isinstance(deco, (ast.Name, ast.Attribute)):
            decorators.append(_name(deco).split(".")[-1])
        elif isinstance(deco, ast.Call):
            decorators.append(_name(deco.func).split(".")[-1])
    return {
        "name": node.name,
        "doc": head,
        "decorators": [d for d in decorators if d],
    }


def _model_info(tree, path):
    """Every Odoo model a module defines, with its fields and methods."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = set()
        for base in node.bases:
            full = _name(base)
            bases.add(full.split(".")[-1])
        if not (bases & _MODEL_BASES):
            continue
        model = {"kind": "model", "class": node.name, "file": path,
                 "fields": [], "methods": []}
        for item in node.body:
            if isinstance(item, ast.Assign):
                field = _field_info(item)
                if field and field["name"] not in _SKIP_FIELDS:
                    model["fields"].append(field)
            elif isinstance(item, ast.FunctionDef):
                if item.name.startswith("_") and not ast.get_docstring(item):
                    continue
                model["methods"].append(_method_info(item))
            elif isinstance(item, ast.Expr):
                value = _string(item.value)
                if value and not model.get("doc"):
                    model["doc"] = value.strip().splitlines()[0][:200]
        names = []
        for item in node.body:
            if isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name) and target.id == "_name":
                        value = _const(item.value)
                        if isinstance(value, str):
                            names.append(value)
        if names:
            model["model"] = names[0]
            found.append(model)
    return found


def _iter_py_files(roots):
    """Every indexable .py file under the roots, deepest first."""
    seen = set()
    for root in roots or []:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(
                d for d in dirnames if d not in _SKIP_DIRS)
            for filename in sorted(filenames):
                if not filename.endswith(".py"):
                    continue
                if filename in _SKIP_FILES:
                    continue
                if "test" in filename.lower() or "test" in dirpath.lower():
                    continue
                full = os.path.normpath(os.path.join(dirpath, filename))
                if full in seen:
                    continue
                seen.add(full)
                yield full


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="strict") as handle:
            return handle.read()
    except (OSError, ValueError):
        return ""


def scan_paths(paths, limit_models=200):
    """Scan code paths. Returns {"models": [...], "files": N}."""
    models = []
    files = 0
    for path in _iter_py_files(paths):
        files += 1
        text = _read(path)
        if not text:
            continue
        try:
            tree = ast.parse(text, filename=path)
        except (SyntaxError, ValueError):
            continue
        for model in _model_info(tree, path):
            models.append(model)
            if len(models) >= limit_models:
                return {"models": models, "files": files, "truncated": True}
    return {"models": models, "files": files, "truncated": False}


def _slug(text):
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:64] or "concept"


def concept_for_model(model, root=""):
    """One knowledge concept for one scanned model."""
    name = model.get("model") or ""
    label = model.get("doc") or name
    rel = model["file"]
    if root and rel.startswith(root):
        rel = rel[len(root):].lstrip(os.sep)
    lines = ["# %s (%s)" % (label, name), ""]
    fields = model.get("fields") or []
    if fields:
        lines.append("## Fields")
        for field in fields[:60]:
            bits = ["- `%s`" % field["name"], field.get("type", "?")]
            if field.get("string"):
                bits.append(str(field["string"])[:60])
            if field.get("comodel"):
                bits.append("-> %s" % field["comodel"])
            for flag in ("required", "readonly", "compute"):
                if field.get(flag):
                    bits.append(flag)
            if isinstance(field.get("selection"), list):
                keys = [str(k) for k, _v in field["selection"][:12]
                        if isinstance(k, str)]
                if keys:
                    bits.append("one of: %s" % ", ".join(keys))
            if field.get("help"):
                bits.append("- %s" % str(field["help"])[:120])
            lines.append(" ".join(bits))
        lines.append("")
    methods = [m for m in (model.get("methods") or [])
               if not m["name"].startswith("_") or m["doc"]]
    if methods:
        lines.append("## Methods")
        for method in methods[:30]:
            bits = ["- `%s()`" % method["name"]]
            if method.get("decorators"):
                bits.append("[%s]" % ", ".join(method["decorators"]))
            if method.get("doc"):
                bits.append("- %s" % method["doc"])
            lines.append(" ".join(bits))
        lines.append("")
    lines.append("Defined in `%s`." % rel)
    return {
        "code": "odoo-model-%s" % _slug(name),
        "name": "Model %s (%s)" % (label, name),
        "kind": "reference",
        "description": ("The %s model (%s): its fields, relations and "
                        "methods." % (label, name))[:150],
        "body": "\n".join(lines)[:8000],
        "generated_by": "process:code-scan",
        "sources": [{"resource": rel, "id": name, "title": label}],
    }
