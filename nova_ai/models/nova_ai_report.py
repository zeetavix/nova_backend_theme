# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Branded answer reports: assistant markdown -> styled PDF bytes.

Pure functions (no Odoo env) so they stay unit-testable: the
controller resolves brand + session ownership, this module only
parses and renders. reportlab is already in the venv; the import
stays lazy so a missing wheel degrades to an honest error, never
a boot failure.
"""

import io
import re

_MAX_TABLE_ROWS = 30
_MAX_TABLE_COLS = 6
_MAX_CHARS = 50000

_GREEN = ("PAID", "POSTED", "DONE", "COMPLETED", "SUCCESS", "VERIFIED",
          "CONFIRMED", "ACTIVE", "IN STOCK", "AVAILABLE", "OK")
_AMBER = ("PENDING", "DRAFT", "PARTIAL", "WARNING", "SYNCING",
         "IN PROGRESS", "LOW", "REORDER")
_RED = ("OVERDUE", "UNPAID", "NOT PAID", "FAILED", "ERROR", "OFFLINE",
       "CANCELLED", "ALERT", "OUT OF STOCK", "ZERO")


def _pill_bucket(cell):
    """Green/amber/red/none for a status-looking cell."""
    text = str(cell or "").strip().upper()
    if not text:
        return ""
    if any(key in text for key in _RED):
        return "red"
    if any(key in text for key in _GREEN):
        return "green"
    if any(key in text for key in _AMBER):
        return "amber"
    return ""


def parse_markdown_tables(markdown):
    """Pull (| a | b |) tables out of markdown.

    Returns [(header, rows)] with the --- separator dropped. Text
    tables the model emits in chat are the only input; HTML tables
    never reach here (the drawer sends markdown, else plain text).
    """
    tables = []
    lines = (markdown or "").splitlines()
    block = []
    for line in lines + [""]:
        if line.strip().startswith("|") and "|" in line.strip()[1:]:
            block.append(line.strip())
            continue
        if len(block) >= 2:
            cells = [[c.strip() for c in row.strip("|").split("|")]
                     for row in block]
            widths = {len(r) for r in cells}
            if len(widths) == 1 and all(
                    set(c) <= set(":- ") for c in cells[1]):
                header = cells[0][: _MAX_TABLE_COLS]
                rows = [r[: _MAX_TABLE_COLS]
                        for r in cells[2: _MAX_TABLE_ROWS + 2]]
                if header and rows:
                    tables.append((header, rows))
        block = []
    return tables


def _col_widths(header, rows, usable):
    """Port of the telegram bot's dynamic PDF widths: widest content
    per column (chars*6+16), surplus shared, deficit scaled, 35 floor."""
    widths = []
    for col in range(len(header)):
        widest = max([len(str(header[col]))] + [
            len(str(row[col])) if col < len(row) else 0
            for row in rows] + [8])
        widths.append(max(35, widest * 6 + 16))
    total = sum(widths)
    if total < usable:
        extra = usable - total
        weights = [w / total for w in widths]
        widths = [w + extra * weight
                  for w, weight in zip(widths, weights)]
    elif total > usable:
        widths = [max(35, w * usable / total) for w in widths]
    return widths


def _kpis(tables):
    """Header KPI boxes from the first money/status table found:
    record count always, summed amount when an amount column parses,
    overdue/outstanding split when a status column exists."""
    import re
    for header, rows in tables:
        lowered = [str(h).lower() for h in header]
        amount_col = next(
            (i for i, name in enumerate(lowered)
             if any(key in name for key in (
                 "amount", "total", "balance", "price", "subtotal"))),
            None)
        status_col = next(
            (i for i, name in enumerate(lowered)
             if "status" in name or "state" in name), None)
        if amount_col is None and status_col is None:
            continue
        boxes = [("Records", str(len(rows)))]
        if amount_col is not None:
            total = 0.0
            for row in rows:
                if amount_col >= len(row):
                    continue
                found = re.findall(r"[\d,]+\.?\d*", str(row[amount_col]))
                if found:
                    try:
                        total += float(found[-1].replace(",", ""))
                    except ValueError:
                        pass
            boxes.append(("Total", "%.2f" % total))
        if status_col is not None:
            bad = sum(1 for row in rows
                      if status_col < len(row) and _pill_bucket(
                          row[status_col]) == "red")
            boxes.append(("Need attention", str(bad)))
            boxes.append(("OK", str(len(rows) - bad)))
        return boxes[:4]
    return []


def strip_tables(markdown):
    """Body text with table blocks removed (they render as tables)."""
    out = []
    in_table = False
    for line in (markdown or "").splitlines():
        is_row = line.strip().startswith("|") and "|" in line.strip()[1:]
        if is_row:
            in_table = True
            continue
        if in_table and not line.strip():
            in_table = False
            continue
        if in_table:
            in_table = False
        out.append(line)
    return "\n".join(out).strip()


def smart_filename(prefix, session_id, index, ext="pdf"):
    """Human filenames: nova-answer-s12-n3-2026-10-15.pdf."""
    from datetime import date
    day = date.today().isoformat()
    try:
        sid = int(session_id or 0)
    except (TypeError, ValueError):
        sid = 0
    try:
        num = int(index if index is not None else 0)
    except (TypeError, ValueError):
        num = 0
    return "%s-s%d-n%d-%s.%s" % (prefix, sid, num, day, ext)


def build_report_pdf(markdown, brand=None, title="Ask Nova — answer"):
    """Styled PDF bytes for one assistant answer.

    brand: {name, color} — color as #rrggbb. Raises RuntimeError
    when reportlab is missing so the route answers honestly.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (Paragraph, SimpleDocTemplate,
                                        Spacer, Table, TableStyle)
    except ImportError:
        raise RuntimeError(
            "PDF export needs the reportlab package.")
    brand = brand or {}
    name = str(brand.get("name") or "Nova")[:60]
    color = str(brand.get("color") or "#0E7C86")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        color = "#0E7C86"
    text = (markdown or "")[: _MAX_CHARS]
    tables = parse_markdown_tables(text)
    body = strip_tables(text)[: _MAX_CHARS // 2]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=22 * mm,
                            bottomMargin=18 * mm, leftMargin=16 * mm,
                            rightMargin=16 * mm)
    accent = colors.HexColor(color)
    ink = colors.HexColor("#1a1a1a")
    muted = colors.HexColor("#5a5a5a")
    sheet = getSampleStyleSheet()
    title_style = ParagraphStyle("NovaTitle", parent=sheet["Title"],
                                 textColor=accent, fontSize=18,
                                 spaceAfter=2 * mm)
    brand_style = ParagraphStyle("NovaBrand", parent=sheet["Normal"],
                                 textColor=muted, fontSize=9,
                                 spaceAfter=6 * mm)
    body_style = ParagraphStyle("NovaBody", parent=sheet["Normal"],
                                textColor=ink, fontSize=10, leading=14)
    head_style = ParagraphStyle("NovaHead", parent=sheet["Normal"],
                                textColor=colors.white, fontSize=10,
                                leading=13, fontName="Helvetica-Bold")
    cell_style = ParagraphStyle("NovaCell", parent=sheet["Normal"],
                                textColor=ink, fontSize=9, leading=12)
    pill = {
        "green": ParagraphStyle("NovaGreen", parent=cell_style,
                                textColor=colors.HexColor("#166534")),
        "amber": ParagraphStyle("NovaAmber", parent=cell_style,
                                textColor=colors.HexColor("#92400e")),
        "red": ParagraphStyle("NovaRed", parent=cell_style,
                              textColor=colors.HexColor("#991b1b")),
    }

    def _esc(value):
        return (str(value or "").replace("&", "&amp;")
                .replace("<", "&lt;").replace(">", "&gt;"))

    def _cell(value):
        bucket = _pill_bucket(value)
        marked = ("\u25cf " if bucket else "") + str(value or "")
        return Paragraph(_esc(marked),
                         pill.get(bucket, cell_style))

    story = [Paragraph(_esc(name), brand_style),
             Paragraph(_esc(title), title_style),
             Spacer(1, 2 * mm)]
    for para in [p.strip() for p in body.split("\n\n") if p.strip()][:40]:
        story.append(Paragraph(_esc(para).replace("\n", "<br/>"),
                               body_style))
        story.append(Spacer(1, 2 * mm))
    boxes = _kpis(tables)
    usable = A4[0] - 32 * mm
    if boxes:
        kpi_row = [Paragraph(
            '<font color="#5a5a5a">%s</font><br/>'
            '<font color="#1a1a1a"><b>%s</b></font>' % (
                _esc(label), _esc(value)), body_style)
            for label, value in boxes]
        kpi_table = Table([kpi_row],
                          colWidths=[usable / max(len(boxes), 1)
                                     ] * len(boxes))
        kpi_table.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6)]))
        story.append(kpi_table)
        story.append(Spacer(1, 3 * mm))
    for header, rows in tables:
        data = [[Paragraph(_esc(h), head_style) for h in header]]
        zebra = [colors.whitesmoke, colors.white]
        for row in rows:
            padded = list(row) + [""] * (len(header) - len(row))
            data.append([_cell(c) for c in padded[:len(header)]])
        table = Table(data, repeatRows=1,
                      colWidths=_col_widths(header, rows, usable))
        style = [("BACKGROUND", (0, 0), (-1, 0), accent),
                 ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                 ("VALIGN", (0, 0), (-1, -1), "TOP"),
                 ("TOPPADDING", (0, 0), (-1, -1), 3),
                 ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
        for i in range(len(rows)):
            style.append(("BACKGROUND", (0, i + 1), (-1, i + 1),
                          zebra[i % 2]))
        table.setStyle(TableStyle(style))
        story.append(Spacer(1, 3 * mm))
        story.append(table)
    if len(tables) == 0 and not body:
        story.append(Paragraph("Empty answer — nothing to export.",
                               body_style))

    def _chrome(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(muted)
        canvas.drawString(16 * mm, 12 * mm, _esc(name))
        canvas.drawRightString(A4[0] - 16 * mm, 12 * mm,
                               "Page %d" % _doc.page)
        canvas.restoreState()

    doc.build(story, onFirstPage=_chrome, onLaterPages=_chrome)
    return buf.getvalue()


def _is_number_cell(cell):
    """Plain money/count cells right-align; status pills never do."""
    import re
    return bool(re.fullmatch(r"[$€£₹]?\s*[\d,]+(\.\d+)?\s*%?",
                             str(cell or "").strip()))


def _png_font(size=13, bold=False):
    """Best available font: DejaVu/Arial where present, else PIL's
    built-in bitmap. Never raises for missing files."""
    from PIL import ImageFont
    candidates = []
    if bold:
        candidates += ["DejaVuSans-Bold.ttf", "arialbd.ttf",
                       "C:/Windows/Fonts/arialbd.ttf"]
    candidates += ["DejaVuSans.ttf", "arial.ttf",
                   "C:/Windows/Fonts/arial.ttf"]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def build_table_png(markdown, brand=None, title="Ask Nova — answer"):
    """First markdown table as a branded PNG (1080px canvas).

    Status cells tint like the PDF pills; numbers right-align.
    Raises RuntimeError without tables (or without PIL) so the
    route answers honestly instead of shipping a blank image.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        raise RuntimeError(
            "PNG export needs the Pillow package.")
    brand = brand or {}
    name = str(brand.get("name") or "Nova")[:60]
    tables = parse_markdown_tables((markdown or "")[: _MAX_CHARS])
    if not tables:
        raise RuntimeError("No table in that answer to render.")
    header, rows = tables[0]
    accent = (14, 124, 134)
    ink = (26, 26, 26)
    muted = (90, 90, 90)
    pill_ink = {"green": (22, 101, 52), "amber": (146, 64, 14),
                "red": (153, 27, 27)}
    title_font = _png_font(20, bold=True)
    head_font = _png_font(13, bold=True)
    cell_font = _png_font(13)
    small_font = _png_font(11)
    pad, row_h, head_h = 16, 26, 32
    draw = Image.new("RGB", (8, 8), "white")
    probe = ImageDraw.Draw(draw)

    def _width(text, font):
        try:
            return int(probe.textbbox((0, 0), str(text or ""),
                                      font=font)[2]) + 24
        except Exception:
            return len(str(text or "")) * 8 + 24

    grid = [[str(h)[:28] for h in header]]
    for row in rows:
        padded = list(row) + [""] * (len(header) - len(row))
        grid.append([str(c)[:28] for c in padded[:len(header)]])
    col_count = len(header)
    widths = [35] * col_count
    for number, row in enumerate(grid):
        font = head_font if number == 0 else cell_font
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], min(_width(cell, font), 320))
    total = sum(widths) + pad * 2
    canvas_w = max(640, min(total, 1600))
    scale = (canvas_w - pad * 2) / total if total > 0 else 1
    widths = [max(35, int(w * scale)) for w in widths]
    canvas_w = sum(widths) + pad * 2
    canvas_h = (pad * 2 + 34 + 10 + head_h + row_h * (len(grid) - 1)
                + 12 + 30)
    image = Image.new("RGB", (canvas_w, canvas_h), "white")
    draw = ImageDraw.Draw(image)
    y = pad
    draw.text((pad, y), name, font=small_font, fill=muted)
    y += 22
    draw.text((pad, y), str(title or "")[:80], font=title_font,
              fill=accent)
    y += 34
    x = pad
    for i, head in enumerate(grid[0]):
        draw.rectangle([x, y, x + widths[i], y + head_h], fill=accent)
        draw.text((x + 12, y + 8), head, font=head_font,
                  fill="white")
        x += widths[i]
    y += head_h
    for number, row in enumerate(grid[1:]):
        fill = (245, 245, 245) if number % 2 else (255, 255, 255)
        x = pad
        for i, cell in enumerate(row):
            draw.rectangle([x, y, x + widths[i], y + row_h], fill=fill,
                           outline=(220, 220, 220))
            bucket = _pill_bucket(cell)
            color = pill_ink.get(bucket, ink)
            text = ("● " if bucket else "") + cell
            right = not bucket and _is_number_cell(cell)
            anchor_x = x + widths[i] - 12 if right else x + 12
            draw.text((anchor_x, y + 5), text, font=cell_font,
                      fill=color, anchor="ra" if right else "la")
            x += widths[i]
        y += row_h
    from datetime import date
    draw.text((pad, y + 8), "%s · %s" % (name, date.today().isoformat()),
              font=small_font, fill=muted)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
