"""Google Drive URL helpers for the FP&A Multi-Agent System."""


def get_folder_url(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}"


def get_doc_url(doc_id: str) -> str:
    return f"https://docs.google.com/document/d/{doc_id}"


def get_sheet_url(sheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}"
