# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Lightweight validation for ``<gantt>`` arch (mirrors RNG intent, original code)."""
from __future__ import annotations

from lxml import etree

_GANTT_ATTRS = {
    "string", "editable", "create_inline", "create",
    "date_start", "date_stop", "kpi_unit", "action", "type",
}
_FIELD_TYPES = {"row"}
_RANGE_SPANS = {"day", "week", "month", "quarter", "year"}


def validate_gantt_arch(arch: str) -> list[str]:
    """Return a list of human-readable arch problems (empty = valid)."""
    errors = []
    try:
        root = etree.fromstring(arch.encode() if isinstance(arch, str) else arch)
    except Exception as exc:  # noqa: BLE001
        return [f"Invalid XML: {exc}"]
    grids = [root] if root.tag == "gantt" else root.findall(".//gantt")
    if not grids:
        errors.append("Gantt arch must contain a <gantt> root node.")
        return errors
    for grid in grids:
        for attr in grid.attrib:
            if attr not in _GANTT_ATTRS and not attr.startswith("js_"):
                errors.append(f"Unknown <gantt> attribute: {attr}")
        if not grid.get("date_start"):
            errors.append("<gantt> needs a date_start attribute.")
        if not grid.get("date_stop"):
            errors.append("<gantt> needs a date_stop attribute.")
        for f in grid.findall("field"):
            ftype = f.get("type", "row")
            if ftype not in _FIELD_TYPES:
                errors.append(f"Unknown field type '{ftype}' on field '{f.get('name')}'.")
            if not f.get("name"):
                errors.append("<field> without a name attribute.")
        for rng in grid.findall("range"):
            for req in ("name", "span"):
                if not rng.get(req):
                    errors.append(f"<range> missing required attribute '{req}'.")
            if rng.get("span") and rng.get("span") not in _RANGE_SPANS:
                errors.append(f"<range> has unknown span '{rng.get('span')}'.")
    return errors
