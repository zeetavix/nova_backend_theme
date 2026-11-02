# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Lightweight validation for ``<grid>`` arch (mirrors RNG intent, original code)."""
from __future__ import annotations

from lxml import etree

_GRID_ATTRS = {
    "string", "editable", "create_inline", "hide_line_total", "hide_column_total",
    "barchart_total", "display_empty", "show_heatmap", "show_progress",
    "heatmap_scale", "action", "type", "form_view_id",
    "kpi_capacity_target", "kpi_capacity_label", "kpi_unit", "overtime_at",
    "force_editable",
}
_FIELD_TYPES = {"row", "col", "measure", "readonly"}


def validate_grid_arch(arch: str) -> list[str]:
    """Return a list of human-readable arch problems (empty = valid)."""
    errors = []
    try:
        root = etree.fromstring(arch.encode() if isinstance(arch, str) else arch)
    except Exception as exc:  # noqa: BLE001
        return [f"Invalid XML: {exc}"]
    grids = [root] if root.tag == "grid" else root.findall(".//grid")
    if not grids:
        errors.append("Grid arch must contain a <grid> root node.")
        return errors
    for grid in grids:
        for attr in grid.attrib:
            if attr not in _GRID_ATTRS and not attr.startswith("js_"):
                errors.append(f"Unknown <grid> attribute: {attr}")
        fields = grid.findall("field")
        col_fields = [f for f in fields if f.get("type", "row") == "col"]
        measure_fields = [f for f in fields if f.get("type") == "measure"]
        if len(col_fields) != 1:
            errors.append(f"Grid needs exactly one <field type='col'>, found {len(col_fields)}.")
        if len(measure_fields) > 1:
            errors.append("Grid supports at most one <field type='measure'>.")
        for f in fields:
            ftype = f.get("type", "row")
            if ftype not in _FIELD_TYPES:
                errors.append(f"Unknown field type '{ftype}' on field '{f.get('name')}'.")
            if not f.get("name"):
                errors.append("<field> without a name attribute.")
        for col in col_fields:
            for rng in col.findall("range"):
                for req in ("name", "span", "step"):
                    if not rng.get(req):
                        errors.append(f"<range> missing required attribute '{req}'.")
    return errors
