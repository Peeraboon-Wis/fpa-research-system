"""
Google Sheets + Google Docs integration for the FP&A Multi-Agent System.
- Sheets: Analyst pipe-separated tables → new tab per run
- Docs:   Scout + Architect outputs → prepended to a persistent Google Doc
"""

import os
import re
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]

SHEET_ID = "1Hc4QbjCH7bt4OEjGmRjmHNVVhBkOh-hmgYVnudtXzdk"
DOC_ID   = os.getenv("GOOGLE_DOC_ID", "1QgH5TUDAyUPb1yyRv7MJMYDkSchXX0OR3Z4Gc7iF9q0")


def _get_credentials() -> Credentials:
    try:
        import streamlit as st
        sa_info = dict(st.secrets["gcp_service_account"])
        return Credentials.from_service_account_info(sa_info, scopes=SCOPES)
    except Exception:
        pass
    creds_file = os.getenv("GOOGLE_CREDENTIALS", "multi-agent-fpa-58c3308e1e62.json")
    return Credentials.from_service_account_file(creds_file, scopes=SCOPES)


def _get_client() -> gspread.Client:
    return gspread.authorize(_get_credentials())


def _get_docs_service():
    return build("docs", "v1", credentials=_get_credentials())


def _get_drive_service():
    return build("drive", "v3", credentials=_get_credentials())


def _get_sheets_service():
    return build("sheets", "v4", credentials=_get_credentials())


def _parse_pipe_rows(block: str) -> list:
    """Extract non-separator rows from a markdown pipe table block."""
    rows = []
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        if re.match(r"^\|[-| :]+\|$", line):
            continue
        cells = [re.sub(r"\*+", "", c).strip() for c in line.strip("|").split("|")]
        rows.append(cells)
    return rows


def _parse_analyst_tables(analyst_text: str) -> list:
    """
    Split analyst output into a list of {title, rows} dicts,
    one per # TABLE N or ## TABLE N block.
    """
    parts = re.split(r"(#{1,2}\s+TABLE\s+\d+[^\n]*)", analyst_text, flags=re.IGNORECASE)
    tables = []
    i = 1
    while i < len(parts) - 1:
        title = parts[i].strip().lstrip("#").strip()
        body  = parts[i + 1]
        rows  = _parse_pipe_rows(body)
        if rows:
            tables.append({"title": title, "rows": rows})
        i += 2
    return tables


def save_analyst_to_sheets(analyst_text: str, user_task: str,
                            sheet_id: str = SHEET_ID,
                            tab_name_override: str = None) -> str:
    """
    Create a new tab, write all analyst tables (including TABLE 4 when present),
    and apply rich formatting via the Sheets v4 API.
    Returns the tab name on success.
    """
    gc = _get_client()
    spreadsheet = gc.open_by_key(sheet_id)

    if tab_name_override:
        tab_name = tab_name_override
    else:
        tab_name = datetime.now().strftime("%Y-%m-%d %H:%M")

    try:
        ws = spreadsheet.add_worksheet(title=tab_name, rows=600, cols=26)
    except gspread.exceptions.APIError:
        tab_name = tab_name + " (2)" if tab_name_override else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ws = spreadsheet.add_worksheet(title=tab_name, rows=600, cols=26)

    tables = _parse_analyst_tables(analyst_text)
    ws_id  = ws.id

    # ── Build grid + row-level metadata ───────────────────────────────────
    grid = []   # list of cell-value lists
    meta = []   # parallel list of dicts describing each row's formatting role

    grid.append([user_task])
    meta.append({"type": "task_header"})

    grid.append([f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"])
    meta.append({"type": "date_row"})

    grid.append([])
    meta.append({"type": "spacer"})

    if tables:
        for t_idx, table in enumerate(tables):
            title  = table["title"]
            rows   = table["rows"]
            n_cols = max(len(r) for r in rows) if rows else 1

            is_financial = bool(
                t_idx == 3
                or re.search(
                    r"TABLE\s*4|FINANCIAL|SCENARIO|ASSUMPTION|PROJECTION|UNIT.ECON",
                    title, re.IGNORECASE,
                )
            )

            col_headers = rows[0] if rows else []
            winner_col  = next(
                (ci for ci, ch in enumerate(col_headers)
                 if ch.strip().upper() == "WINNER"),
                None,
            )

            if t_idx > 0:
                grid.append([])
                meta.append({"type": "spacer"})

            grid.append([f">>> {title}"])
            meta.append({"type": "table_header", "n_cols": n_cols})

            grid.append([])
            meta.append({"type": "spacer"})

            for r_idx, row in enumerate(rows):
                grid.append(row)
                if r_idx == 0:
                    meta.append({
                        "type": "col_header",
                        "n_cols": n_cols,
                        "winner_col": winner_col,
                    })
                else:
                    first  = row[0].strip().lower() if row else ""
                    fin_bg = None
                    if is_financial:
                        if "assumption" in first:
                            fin_bg = "#FFFDE7"   # yellow — assumption inputs
                        elif first.startswith("best"):
                            fin_bg = "#E8F5E9"   # green  — best case
                        elif first.startswith("worst"):
                            fin_bg = "#FFEBEE"   # red    — worst case
                        elif not first.startswith("base"):
                            fin_bg = "#E3F2FD"   # blue   — calculated rows
                    meta.append({
                        "type":       "data_row",
                        "r_idx":      r_idx - 1,
                        "n_cols":     n_cols,
                        "winner_col": winner_col,
                        "fin_bg":     fin_bg,
                        "row_data":   row,
                    })
    else:
        grid.append(["[No pipe-separated tables found — raw output below]"])
        meta.append({"type": "info"})
        grid.append([])
        meta.append({"type": "spacer"})
        for line in analyst_text.splitlines()[:300]:
            grid.append([line])
            meta.append({"type": "raw_text"})

    ws.update(values=grid, range_name="A1")

    # ── Formatting via Sheets v4 batchUpdate ──────────────────────────────
    max_cols = max((len(r) for r in grid), default=1)
    max_cols = max(max_cols, 4)

    # ── Nested helpers (capture ws_id / max_cols from closure) ────────────
    def _gr(r0, c0, r1, c1):
        return {
            "sheetId": ws_id,
            "startRowIndex": r0, "endRowIndex": r1,
            "startColumnIndex": c0, "endColumnIndex": c1,
        }

    def _rgb(h):
        h = h.lstrip("#")
        return {
            "red":   int(h[0:2], 16) / 255,
            "green": int(h[2:4], 16) / 255,
            "blue":  int(h[4:6], 16) / 255,
        }

    def _bg(r0, c0, r1, c1, color):
        return {"repeatCell": {
            "range": _gr(r0, c0, r1, c1),
            "cell": {"userEnteredFormat": {"backgroundColor": _rgb(color)}},
            "fields": "userEnteredFormat.backgroundColor",
        }}

    def _tf(r0, c0, r1, c1, bold=False, italic=False, size=10, color="#000000"):
        return {"repeatCell": {
            "range": _gr(r0, c0, r1, c1),
            "cell": {"userEnteredFormat": {"textFormat": {
                "bold": bold, "italic": italic,
                "fontSize": size,
                "foregroundColor": _rgb(color),
            }}},
            "fields": "userEnteredFormat.textFormat",
        }}

    def _ha(r0, c0, r1, c1, align="LEFT"):
        return {"repeatCell": {
            "range": _gr(r0, c0, r1, c1),
            "cell": {"userEnteredFormat": {"horizontalAlignment": align}},
            "fields": "userEnteredFormat.horizontalAlignment",
        }}

    def _merge(r0, c0, r1, c1):
        return {"mergeCells": {
            "range": _gr(r0, c0, r1, c1),
            "mergeType": "MERGE_ALL",
        }}

    def _border(r0, c0, r1, c1, color="#CCCCCC", style="SOLID"):
        b = {"style": style, "color": _rgb(color)}
        return {"updateBorders": {
            "range": _gr(r0, c0, r1, c1),
            "top": b, "bottom": b, "left": b, "right": b,
            "innerHorizontal": b, "innerVertical": b,
        }}

    def _col_w(c0, c1, px):
        return {"updateDimensionProperties": {
            "range": {"sheetId": ws_id, "dimension": "COLUMNS",
                      "startIndex": c0, "endIndex": c1},
            "properties": {"pixelSize": px},
            "fields": "pixelSize",
        }}

    reqs = []

    # Column widths
    reqs.append(_col_w(0, 1, 220))
    if max_cols > 1:
        reqs.append(_col_w(1, max_cols, 160))

    # Freeze row 1 (task header)
    reqs.append({"updateSheetProperties": {
        "properties": {
            "sheetId": ws_id,
            "gridProperties": {"frozenRowCount": 1},
        },
        "fields": "gridProperties.frozenRowCount",
    }})

    for i, m in enumerate(meta):
        mtype = m["type"]

        if mtype == "task_header":
            reqs += [
                _merge(i, 0, i + 1, max_cols),
                _bg(i, 0, i + 1, max_cols, "#1B2A4A"),
                _tf(i, 0, i + 1, max_cols, bold=True, size=14, color="#FFFFFF"),
                _ha(i, 0, i + 1, max_cols, "LEFT"),
            ]

        elif mtype == "date_row":
            reqs += [
                _merge(i, 0, i + 1, max_cols),
                _bg(i, 0, i + 1, max_cols, "#F5F5F5"),
                _tf(i, 0, i + 1, max_cols, italic=True, size=10, color="#555555"),
            ]

        elif mtype == "table_header":
            nc = m.get("n_cols", max_cols)
            reqs += [
                _merge(i, 0, i + 1, nc),
                _bg(i, 0, i + 1, nc, "#1B2A4A"),
                _tf(i, 0, i + 1, nc, bold=True, size=12, color="#FFFFFF"),
            ]

        elif mtype == "col_header":
            nc = m.get("n_cols", max_cols)
            reqs += [
                _bg(i, 0, i + 1, nc, "#E8EEF7"),
                _tf(i, 0, i + 1, nc, bold=True, size=11, color="#1B2A4A"),
                _ha(i, 0, i + 1, nc, "CENTER"),
                _ha(i, 0, i + 1, 1, "LEFT"),   # first column stays left-aligned
                _border(i, 0, i + 1, nc, color="#1B2A4A"),
                # Thick bottom border under column headers
                {"updateBorders": {
                    "range": _gr(i, 0, i + 1, nc),
                    "bottom": {"style": "SOLID_MEDIUM", "color": _rgb("#1B2A4A")},
                }},
            ]

        elif mtype == "data_row":
            nc         = m.get("n_cols", max_cols)
            r_idx      = m.get("r_idx", 0)
            winner_col = m.get("winner_col")
            fin_bg     = m.get("fin_bg")
            row_data   = m.get("row_data", [])

            row_bg = fin_bg if fin_bg else ("#FFFFFF" if r_idx % 2 == 0 else "#F8F9FA")

            reqs += [
                _bg(i, 0, i + 1, nc, row_bg),
                _tf(i, 0, i + 1, nc, size=10, color="#2C2C2C"),
                _ha(i, 0, i + 1, 1, "LEFT"),
            ]
            if nc > 1:
                reqs.append(_ha(i, 1, i + 1, nc, "CENTER"))
            reqs.append(_border(i, 0, i + 1, nc, color="#E0E0E0"))

            # Winner cell: bold green text
            if winner_col is not None and winner_col < len(row_data):
                cell_val = row_data[winner_col].strip()
                if cell_val and cell_val.upper() not in ("WINNER", "N/A", ""):
                    reqs.append(
                        _tf(i, winner_col, i + 1, winner_col + 1,
                            bold=True, size=10, color="#1A7A4A")
                    )

    svc = _get_sheets_service()
    svc.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": reqs},
    ).execute()

    return tab_name


# ── Google Docs ────────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    return text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()


# ── colour / dimension helpers ─────────────────────────────────────────────────

def _rgb_doc(hex_color: str) -> dict:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return {"red": r / 255, "green": g / 255, "blue": b / 255}


def _pt_doc(n: float) -> dict:
    return {"magnitude": n, "unit": "PT"}


def _optcol(rgb: dict) -> dict:
    return {"color": {"rgbColor": rgb}}


_ORANGE = _rgb_doc("#FF6B35")   # orange — title (date/time + task name)
_BLACK  = _rgb_doc("#000000")   # black  — all headings, body, bullets


# ── inline markdown stripping ──────────────────────────────────────────────────

def _strip_inline_md(text: str):
    """
    Return (plain_text, bold_ranges) where bold_ranges is a list of (start, end)
    byte-offsets (character positions) within plain_text that should be bold.
    Strips ** and * markers; also strips leading - / • bullet characters.
    """
    plain = re.sub(r"^[\-•]\s*", "", text.strip())
    parts = re.split(r"\*\*(.+?)\*\*", plain)
    result_chars = []
    bold_ranges = []
    offset = 0
    for i, part in enumerate(parts):
        if i % 2 == 1:
            bold_ranges.append((offset, offset + len(part)))
        result_chars.append(part)
        offset += len(part)
    plain_out = "".join(result_chars)
    plain_out = re.sub(r"\*(.+?)\*", r"\1", plain_out)
    plain_out = re.sub(r"\*+", "", plain_out)
    return plain_out, bold_ranges


# ── markdown line classifier for Scout / Architect ────────────────────────────

def _parse_md_lines(text: str) -> list:
    """
    Classify each line of markdown text into one of:
      h2, h3, table_row, bullet, blank, body
    Returns list of dicts: {type, text, bolds}
    """
    lines = _clean(text).splitlines()
    result = []
    prev_blank = False
    for raw in lines:
        stripped = raw.strip()

        if not stripped:
            if not prev_blank:
                result.append({"type": "blank", "text": "", "bolds": []})
            prev_blank = True
            continue
        prev_blank = False

        if re.match(r'^-{2,}$', stripped):
            if not prev_blank:
                result.append({"type": "blank", "text": "", "bolds": []})
            prev_blank = True
            continue

        if stripped.startswith("### "):
            plain, bolds = _strip_inline_md(stripped[4:])
            result.append({"type": "h3", "text": plain, "bolds": bolds})
        elif stripped.startswith("## "):
            plain, bolds = _strip_inline_md(stripped[3:])
            result.append({"type": "h2", "text": plain, "bolds": bolds})
        elif stripped.startswith("#") and re.match(r"#{1,6}\s", stripped):
            level = len(re.match(r"(#+)", stripped).group(1))
            plain, bolds = _strip_inline_md(stripped[level:].lstrip())
            result.append({"type": "h2" if level <= 2 else "h3", "text": plain, "bolds": bolds})
        elif stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.match(r"^[-:]+$", c) for c in cells if c):
                continue  # separator row
            plain = "\t".join(re.sub(r"\*+(.+?)\*+", r"\1", c) for c in cells)
            plain = re.sub(r"\*+", "", plain)
            result.append({"type": "table_row", "text": plain, "bolds": []})
        elif re.match(r"^[\-•\*]\s+", stripped):
            plain, bolds = _strip_inline_md(stripped)
            result.append({"type": "bullet", "text": plain, "bolds": bolds})
        else:
            plain, bolds = _strip_inline_md(stripped)
            result.append({"type": "body", "text": plain, "bolds": bolds})

    return result


# ── Visual agent output classifier ────────────────────────────────────────────

def _parse_visual_lines(visual_text: str) -> list:
    """
    Classify lines from the Visual agent output into:
      slide_heading, section_label, bottom_line, bullet, body, blank
    """
    lines = _clean(visual_text).splitlines()
    result = []
    prev_blank = False
    for raw in lines:
        stripped = raw.strip()

        if not stripped or stripped == "---":
            if not prev_blank:
                result.append({"type": "blank", "text": "", "bolds": []})
            prev_blank = True
            continue
        prev_blank = False

        slide_m = re.match(r"(?:##\s+)?SLIDE\s+(\d+)\s*:\s*(.*)", stripped, re.IGNORECASE)
        if slide_m:
            plain = re.sub(r"\*+(.+?)\*+", r"\1", slide_m.group(2)).strip()
            plain = re.sub(r"\*+", "", plain)
            result.append({"type": "slide_heading",
                            "text": f"SLIDE {slide_m.group(1)}: {plain}", "bolds": []})
            continue

        bl_m = re.match(r"\*{0,2}BOTTOM\s*LINE:\*{0,2}\s*(.*)", stripped, re.IGNORECASE)
        if bl_m:
            plain = re.sub(r"\*+(.+?)\*+", r"\1", bl_m.group(1)).strip()
            plain = re.sub(r"\*+", "", plain)
            result.append({"type": "bottom_line", "text": f"BOTTOM LINE: {plain}", "bolds": []})
            continue

        sec_m = re.match(r"#{1,3}\s+(.+)", stripped)
        if sec_m:
            plain = re.sub(r"\*+(.+?)\*+", r"\1", sec_m.group(1)).strip()
            plain = re.sub(r"\*+", "", plain)
            result.append({"type": "section_label", "text": plain, "bolds": []})
            continue

        if re.match(r"^[\-•]\s+", stripped) or re.match(r"^\*\s+", stripped):
            plain, bolds = _strip_inline_md(stripped)
            result.append({"type": "bullet", "text": plain, "bolds": bolds})
            continue

        plain, bolds = _strip_inline_md(stripped)
        result.append({"type": "body", "text": plain, "bolds": bolds})

    return result


# ── request builder ────────────────────────────────────────────────────────────

def _make_text_style_req(start: int, end: int, **kwargs) -> dict:
    rng = {"startIndex": start, "endIndex": end}
    fields = ",".join(kwargs.keys())
    return {"updateTextStyle": {"range": rng, "textStyle": kwargs, "fields": fields}}


def _make_para_style_req(start: int, end: int, **kwargs) -> dict:
    rng = {"startIndex": start, "endIndex": end}
    fields = ",".join(kwargs.keys())
    return {"updateParagraphStyle": {"range": rng, "paragraphStyle": kwargs, "fields": fields}}


def _make_bullet_req(start: int, end: int) -> dict:
    rng = {"startIndex": start, "endIndex": end}
    return {"createParagraphBullets": {"range": rng, "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"}}


def _build_doc_requests(dt_str: str, task: str,
                        scout_text: str, architect_text: str,
                        visual_text: str,
                        run_n: int = 1,
                        insert_index: int = 1) -> list:
    """
    Build the full batchUpdate request list for a single doc insert.
    insert_index=1 → prepend at top (new task).
    insert_index=end_index → append at bottom (continuation).
    All formatting indices are offset from insert_index.
    """

    # ── classify lines for each section ───────────────────────────────────────
    scout_lines     = _parse_md_lines(scout_text)
    architect_lines = _parse_md_lines(architect_text)
    visual_lines    = _parse_visual_lines(visual_text) if visual_text else []

    sep = "─" * 60

    # ── build full plain text + layout map ────────────────────────────────────
    text_parts = []
    layout_map = []  # list of (start, end, meta)

    def emit(text: str, meta: dict):
        start = sum(len(p) for p in text_parts) + insert_index
        text_parts.append(text)
        end = start + len(text)
        layout_map.append((start, end, meta))

    run_sep = "━" * 40
    emit(run_sep + "\n",               {"type": "run_sep"})
    emit(f"RUN {run_n} | {dt_str}\n", {"type": "h_timestamp"})
    emit(f"Task: {task}\n",           {"type": "h2"})
    emit(run_sep + "\n",               {"type": "run_sep"})
    emit("\n",                         {"type": "blank"})

    def emit_section(label: str, classified_lines: list):
        emit(label + "\n", {"type": "section_head"})
        emit("\n", {"type": "blank"})
        bullet_group_start = None
        in_table = False
        table_buf = []  # list of (text, is_header) — flushed with padding when table ends

        def flush_table():
            if not table_buf:
                return
            split_rows = [row_text.split("\t") for row_text, _ in table_buf]
            num_cols = max(len(r) for r in split_rows)
            split_rows = [r + [""] * (num_cols - len(r)) for r in split_rows]
            col_widths = [max(len(r[i]) for r in split_rows) for i in range(num_cols)]
            for (row_text, is_hdr), cells in zip(table_buf, split_rows):
                padded = "| " + " | ".join(
                    cells[i].ljust(col_widths[i] + 2)
                    for i in range(num_cols)
                ) + " |"
                emit("    " + padded + "\n", {"type": "table_row", "is_header": is_hdr})
            table_buf.clear()

        for item in classified_lines:
            t    = item["type"]
            text = item["text"]
            bolds = item.get("bolds", [])

            if t == "blank":
                flush_table()
                if bullet_group_start is not None:
                    bullet_group_start = None
                in_table = False
                emit("\n", {"type": "blank"})
            elif t == "h2":
                flush_table()
                bullet_group_start = None
                in_table = False
                emit(text + "\n", {"type": "h2", "bolds": bolds})
            elif t == "h3":
                flush_table()
                bullet_group_start = None
                in_table = False
                emit(text + "\n", {"type": "h3", "bolds": bolds})
            elif t == "table_row":
                is_header = not in_table
                in_table = True
                bullet_group_start = None
                table_buf.append((text, is_header))
            elif t == "bullet":
                flush_table()
                in_table = False
                emit(text + "\n", {"type": "bullet", "bolds": bolds})
            else:  # body
                flush_table()
                bullet_group_start = None
                in_table = False
                emit(text + "\n", {"type": "body", "bolds": bolds})
        flush_table()
        emit("\n", {"type": "blank"})

    emit_section("RESEARCH FINDINGS", scout_lines)
    emit_section("STRATEGIC ANALYSIS", architect_lines)

    if visual_lines:
        emit("SLIDE DECK OUTLINE\n", {"type": "section_head"})
        emit("\n", {"type": "blank"})
        for item in visual_lines:
            t    = item["type"]
            text = item["text"]
            bolds = item.get("bolds", [])
            if t == "blank":
                emit("\n", {"type": "blank"})
            elif t == "slide_heading":
                emit(text + "\n", {"type": "slide_heading"})
            elif t == "section_label":
                emit(text + "\n", {"type": "h3", "bolds": bolds})
            elif t == "bottom_line":
                emit(text + "\n", {"type": "bottom_line"})
            elif t == "bullet":
                emit(text + "\n", {"type": "bullet", "bolds": bolds})
            else:
                emit(text + "\n", {"type": "body", "bolds": bolds})
        emit("\n", {"type": "blank"})

    emit(sep + "\n", {"type": "separator"})

    full_text = "".join(text_parts)

    # ── insert text at insert_index ────────────────────────────────────────────
    requests = [{"insertText": {"location": {"index": insert_index}, "text": full_text}}]

    # ── formatting pass ────────────────────────────────────────────────────────
    bullet_ranges = []  # collected and applied last (after all textStyle requests)

    for start, end, meta in layout_map:
        t     = meta["type"]
        bolds = meta.get("bolds", [])

        if t in ("h_timestamp", "h_task"):
            # RUN N | timestamp — Raleway 18pt bold orange
            requests.append(_make_para_style_req(start, end,
                spaceAbove=_pt_doc(0), spaceBelow=_pt_doc(6), lineSpacing=115))
            requests.append(_make_text_style_req(start, end,
                bold=True, fontSize=_pt_doc(18),
                foregroundColor=_optcol(_ORANGE),
                weightedFontFamily={"fontFamily": "Raleway", "weight": 700}))

        elif t == "section_head":
            # RESEARCH FINDINGS / STRATEGIC ANALYSIS / SLIDE DECK OUTLINE — Raleway 16pt bold black
            requests.append(_make_para_style_req(start, end,
                spaceAbove=_pt_doc(0), spaceBelow=_pt_doc(6), lineSpacing=115))
            requests.append(_make_text_style_req(start, end,
                bold=True, fontSize=_pt_doc(16),
                foregroundColor=_optcol(_BLACK),
                weightedFontFamily={"fontFamily": "Raleway", "weight": 700}))

        elif t in ("h2", "h3", "slide_heading"):
            # Sub-section headings — Raleway 14pt bold black
            requests.append(_make_para_style_req(start, end,
                spaceAbove=_pt_doc(0), spaceBelow=_pt_doc(6), lineSpacing=115))
            requests.append(_make_text_style_req(start, end,
                bold=True, fontSize=_pt_doc(14),
                foregroundColor=_optcol(_BLACK),
                weightedFontFamily={"fontFamily": "Raleway", "weight": 700}))

        elif t == "bottom_line":
            # BOTTOM LINE — Raleway 12pt bold black
            requests.append(_make_para_style_req(start, end,
                spaceAbove=_pt_doc(0), spaceBelow=_pt_doc(6), lineSpacing=115))
            requests.append(_make_text_style_req(start, end,
                bold=True, fontSize=_pt_doc(12),
                foregroundColor=_optcol(_BLACK),
                weightedFontFamily={"fontFamily": "Raleway", "weight": 700}))

        elif t == "table_row":
            # Table rows — Courier New 10pt, grey background; header row bold + darker bg
            is_header = meta.get("is_header", False)
            bg_color  = "#E8E8E8" if is_header else "#F5F5F5"
            requests.append(_make_para_style_req(start, end,
                spaceAbove=_pt_doc(0), spaceBelow=_pt_doc(2),
                indentFirstLine=_pt_doc(0), indentStart=_pt_doc(12),
                lineSpacing=115,
                shading={"backgroundColor": _optcol(_rgb_doc(bg_color))}))
            requests.append(_make_text_style_req(start, end,
                bold=is_header, fontSize=_pt_doc(10),
                foregroundColor=_optcol(_BLACK),
                weightedFontFamily={"fontFamily": "Courier New",
                                    "weight": 700 if is_header else 400}))

        elif t == "bullet":
            # Bullets — Raleway 12pt normal black
            requests.append(_make_para_style_req(start, end,
                spaceAbove=_pt_doc(0), spaceBelow=_pt_doc(6), lineSpacing=115))
            requests.append(_make_text_style_req(start, end,
                bold=False, fontSize=_pt_doc(12),
                foregroundColor=_optcol(_BLACK),
                weightedFontFamily={"fontFamily": "Raleway", "weight": 400}))
            bullet_ranges.append((start, end))
            for bs, be in bolds:
                if bs < be:
                    requests.append(_make_text_style_req(start + bs, start + be, bold=True))

        elif t == "body":
            # Body text — Raleway 12pt normal black
            requests.append(_make_para_style_req(start, end,
                spaceAbove=_pt_doc(0), spaceBelow=_pt_doc(6), lineSpacing=115))
            requests.append(_make_text_style_req(start, end,
                bold=False, fontSize=_pt_doc(12),
                foregroundColor=_optcol(_BLACK),
                weightedFontFamily={"fontFamily": "Raleway", "weight": 400}))
            for bs, be in bolds:
                if bs < be:
                    requests.append(_make_text_style_req(start + bs, start + be, bold=True))

        elif t == "run_sep":
            # ━━━ separator lines — Raleway 11pt bold black
            requests.append(_make_para_style_req(start, end,
                spaceAbove=_pt_doc(0), spaceBelow=_pt_doc(0), lineSpacing=115))
            requests.append(_make_text_style_req(start, end,
                bold=True, fontSize=_pt_doc(11),
                foregroundColor=_optcol(_BLACK),
                weightedFontFamily={"fontFamily": "Raleway", "weight": 700}))

        elif t == "separator":
            # ─── section end line — Raleway 12pt normal black
            requests.append(_make_para_style_req(start, end,
                spaceAbove=_pt_doc(0), spaceBelow=_pt_doc(6), lineSpacing=115))
            requests.append(_make_text_style_req(start, end,
                bold=False, fontSize=_pt_doc(12),
                foregroundColor=_optcol(_BLACK),
                weightedFontFamily={"fontFamily": "Raleway", "weight": 400}))

    # createParagraphBullets must come after all textStyle requests
    for bs, be in bullet_ranges:
        requests.append(_make_bullet_req(bs, be))

    return requests


def save_scout_architect_to_doc(scout_text: str, architect_text: str,
                                 user_task: str, visual_text: str = "",
                                 doc_id: str = DOC_ID,
                                 run_n: int = 1) -> str:
    """
    Write this run's outputs to the Google Doc main body.
    Run 1: insert at top (index 1) so the document starts fresh.
    Run 2+: append after all existing content so runs accumulate in order.
    Each run starts with a ━━━ / RUN N / Task header block.
    Returns dt_str.
    """
    service = _get_docs_service()
    dt_str  = datetime.now().strftime("%Y-%m-%d %H:%M")

    if run_n == 1:
        insert_index = 1
    else:
        doc = service.documents().get(documentId=doc_id).execute()
        insert_index = doc["body"]["content"][-1]["endIndex"] - 1

    all_reqs = _build_doc_requests(
        dt_str, _clean(user_task),
        scout_text, architect_text, visual_text,
        run_n=run_n,
        insert_index=insert_index,
    )
    insert_req = all_reqs[:1]   # insertText — must land first
    fmt_reqs   = all_reqs[1:]   # all style requests

    service.documents().batchUpdate(
        documentId=doc_id, body={"requests": insert_req},
    ).execute()

    if fmt_reqs:
        service.documents().batchUpdate(
            documentId=doc_id, body={"requests": fmt_reqs},
        ).execute()

    return dt_str
