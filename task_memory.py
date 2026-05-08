"""
FP&A Multi-Agent System — task memory backed by Google Sheets.
All task data is stored in a 'TaskMemory' tab in the configured sheet.
Every public function signature is identical to the original JSON-file version.
"""

import json
import os
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

TAB_NAME = "TaskMemory"
HEADERS = [
    "id", "task", "folder_id", "folder_name", "doc_id", "sheet_id",
    "created", "runs", "summary", "data_collected", "lessons",
]
_END_COL = chr(ord("A") + len(HEADERS) - 1)   # "K" for 11 columns


# ── Auth + sheet helpers ───────────────────────────────────────────────────────

def _get_credentials() -> Credentials:
    try:
        import streamlit as st
        sa_info = dict(st.secrets["gcp_service_account"])
        return Credentials.from_service_account_info(sa_info, scopes=SCOPES)
    except Exception:
        pass
    creds_file = (
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        or os.getenv("GOOGLE_CREDENTIALS")
    )
    if creds_file and os.path.exists(creds_file):
        return Credentials.from_service_account_file(creds_file, scopes=SCOPES)
    raise RuntimeError(
        "Cannot connect to Google Sheets. Check your secrets.toml credentials "
        "and that the sheet is shared with your service account email."
    )


def _get_sheet_id() -> str:
    try:
        import streamlit as st
        sid = st.secrets.get("MEMORY_SHEET_ID")
        if sid:
            return sid
    except Exception:
        pass
    sheet_id = os.getenv("MEMORY_SHEET_ID")
    if sheet_id:
        return sheet_id
    raise RuntimeError(
        "MEMORY_SHEET_ID not found. Set it in .streamlit/secrets.toml "
        "or as MEMORY_SHEET_ID environment variable."
    )


def _get_worksheet() -> gspread.Worksheet:
    try:
        gc = gspread.authorize(_get_credentials())
        sh = gc.open_by_key(_get_sheet_id())
        try:
            return sh.worksheet(TAB_NAME)
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title=TAB_NAME, rows=1000, cols=len(HEADERS))
            ws.append_row(HEADERS)
            return ws
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(
            "Cannot connect to Google Sheets. Check your secrets.toml credentials "
            "and that the sheet is shared with your service account email."
        ) from e


# ── Row serialisation helpers ─────────────────────────────────────────────────

def _row_to_dict(row: list) -> dict:
    d: dict = {}
    for i, header in enumerate(HEADERS):
        val = row[i] if i < len(row) else ""
        if header == "id":
            try:
                d[header] = int(val)
            except (ValueError, TypeError):
                d[header] = 0
        elif header == "runs":
            try:
                d[header] = int(val)
            except (ValueError, TypeError):
                d[header] = 1
        elif header in ("data_collected", "lessons"):
            try:
                d[header] = json.loads(val) if val else []
            except (json.JSONDecodeError, TypeError):
                d[header] = []
        else:
            d[header] = str(val) if val is not None else ""
    return d


def _dict_to_row(task_dict: dict) -> list:
    row = []
    for header in HEADERS:
        val = task_dict.get(header, "")
        if header in ("data_collected", "lessons"):
            row.append(json.dumps(val if val is not None else []))
        else:
            row.append(str(val) if val is not None else "")
    return row


# ── Public API (signatures unchanged from original) ───────────────────────────

def load_memory() -> dict:
    ws = _get_worksheet()
    all_rows = ws.get_all_values()
    if len(all_rows) <= 1:
        return {"tasks": []}
    tasks = [
        _row_to_dict(row)
        for row in all_rows[1:]
        if any(c.strip() for c in row)
    ]
    return {"tasks": tasks}


def save_memory(data: dict) -> None:
    ws = _get_worksheet()
    ws.clear()
    ws.append_row(HEADERS)
    for task in data.get("tasks", []):
        ws.append_row(_dict_to_row(task))


def add_task(task_str: str, folder_id: str, folder_name: str,
             doc_id: str, sheet_id: str) -> dict:
    data   = load_memory()
    next_id = max((t["id"] for t in data["tasks"]), default=0) + 1
    entry = {
        "id":             next_id,
        "task":           task_str,
        "folder_id":      folder_id,
        "folder_name":    folder_name,
        "doc_id":         doc_id,
        "sheet_id":       sheet_id,
        "created":        datetime.now().strftime("%Y-%m-%d %H:%M"),
        "runs":           1,
        "summary":        "",
        "data_collected": [],
        "lessons":        [],
    }
    ws = _get_worksheet()
    ws.append_row(_dict_to_row(entry))
    return entry


def get_task(task_id: int) -> dict | None:
    data = load_memory()
    for t in data["tasks"]:
        if t["id"] == task_id:
            return t
    return None


def list_tasks(data: dict | None = None) -> list:
    if data is None:
        data = load_memory()
    return sorted(data["tasks"], key=lambda t: t["created"], reverse=True)


def update_after_run(task_id: int, summary: str,
                     data_collected: list, lesson: dict) -> None:
    ws       = _get_worksheet()
    all_rows = ws.get_all_values()
    if len(all_rows) <= 1:
        return
    for row_idx, row in enumerate(all_rows[1:], start=2):
        task = _row_to_dict(row)
        if task["id"] != task_id:
            continue
        task["runs"] = task.get("runs", 1) + 1
        if summary:
            task["summary"] = summary
        existing = set(task.get("data_collected", []))
        for item in data_collected:
            if item not in existing:
                task["data_collected"].append(item)
                existing.add(item)
        if lesson:
            task.setdefault("lessons", []).append(lesson)
        ws.update(
            values=[_dict_to_row(task)],
            range_name=f"A{row_idx}:{_END_COL}{row_idx}",
        )
        break


def build_context_brief(task_entry: dict, new_request: str) -> str:
    original_task  = task_entry.get("task", "")
    data_collected = task_entry.get("data_collected", [])
    summary        = task_entry.get("summary", "")
    lessons        = task_entry.get("lessons", [])

    all_flags: list = []
    all_gaps:  list = []
    for lesson in lessons:
        all_flags.extend(lesson.get("flags", []))
        all_gaps.extend(lesson.get("gaps", []))

    lines = [
        "=" * 53,
        "CONTEXT BRIEF",
        "=" * 53,
        f"ORIGINAL TASK: {original_task}",
        f"NEW REQUEST: {new_request}",
        "",
    ]
    if data_collected:
        lines.append("WHAT WAS ALREADY RESEARCHED (do not duplicate):")
        for item in data_collected:
            lines.append(f"  - {item}")
        lines.append("")
    if summary:
        lines.append("PREVIOUS FINDINGS SUMMARY:")
        lines.append(summary)
        lines.append("")
    if all_flags:
        lines.append("ORCHESTRATOR FLAGS FROM PREVIOUS RUNS:")
        for flag in all_flags:
            lines.append(f"  - {flag}")
        lines.append("")
    if all_gaps:
        lines.append("KNOWN GAPS (prioritise filling these):")
        for gap in all_gaps:
            lines.append(f"  - {gap}")
        lines.append("")
    lines += [
        "INSTRUCTION: Build on existing findings.",
        "Do not repeat data already collected.",
        "Focus only on the new request and known gaps.",
        "=" * 53,
    ]
    return "\n".join(lines)


def delete_task(task_id: int) -> None:
    data = load_memory()
    data["tasks"] = [t for t in data["tasks"] if t["id"] != task_id]
    save_memory(data)


def make_folder_name(task_str: str) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    words = task_str.split()[:8]
    short = " ".join(words)
    return f"[{today}] {short}"
