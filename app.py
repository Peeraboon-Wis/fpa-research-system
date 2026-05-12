"""
Streamlit front-end for the FP&A Multi-Agent Research System.
"""

import os
import threading
import traceback
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv
import streamlit as st

st.set_page_config(
    page_title="FP&A Research System",
    page_icon="📊",
    layout="wide",
)

load_dotenv()

# Set API key BEFORE importing main — main.py creates anthropic.Anthropic() at module level.
_api_key = None
try:
    _api_key = st.secrets["ANTHROPIC_API_KEY"]
except Exception:
    pass
if not _api_key:
    _api_key = os.getenv("ANTHROPIC_API_KEY")
if _api_key:
    os.environ["ANTHROPIC_API_KEY"] = _api_key

# Patch main's module-level execute_tool reference (not tasks.execute_tool, which is
# only the definition — main.py binds its own local name via `from tasks import execute_tool`).
import main as main_module
from main import run_orchestrator_session, _save_to_google, save_report, _print_cost_summary
from task_memory import (
    load_memory, add_task, list_tasks, get_task,
    update_after_run, build_context_brief, delete_task, make_folder_name,
)
from drive import get_doc_url, get_sheet_url

# ── Session state ──────────────────────────────────────────────────────────────
st.session_state.setdefault("running", False)
st.session_state.setdefault("delete_confirm_id", None)
st.session_state.setdefault("last_result", None)

# ── Password gate ──────────────────────────────────────────────────────────────
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False

if not st.session_state["authenticated"]:
    st.title("🔒 FP&A Research System")
    password = st.text_input("Enter password", type="password")
    if st.button("Login"):
        correct = st.secrets.get("APP_PASSWORD", "") or "b0onyasup"
        if password == correct:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password")
    st.stop()

# ── Header ─────────────────────────────────────────────────────────────────────
st.title("📊 FP&A Multi-Agent Research System")
st.caption("Orchestrator → Scout → Architect → Analyst → Visual → Google Workspace")
st.divider()

# ── Agent labels for live status ───────────────────────────────────────────────
_AGENT_CONFIG: dict[str, tuple[str, str]] = {
    "call_scout":      ("🔍 SCOUT",        "Researching market data..."),
    "call_architect":  ("🏗️ ARCHITECT",    "Analyzing structural drivers..."),
    "call_analyst":    ("📊 ANALYST",      "Building comparison tables..."),
    "call_visual":     ("🎨 VISUAL",       "Crafting CFO executive deck..."),
    "finalize_report": ("✅ ORCHESTRATOR", "Writing reality check..."),
}

_PRICES: dict[str, dict[str, float]] = {
    "sonnet": {"input": 3.00, "output": 15.00},
    "haiku":  {"input": 1.00, "output": 5.00},
}


def _build_cost_data(context: dict) -> list[dict]:
    agents = [
        ("Orchestrator", "sonnet", context.get("_orc_usage", {})),
        ("Scout",        "sonnet", context.get("usage", {}).get("scout", {})),
        ("Architect",    "haiku",  context.get("usage", {}).get("architect", {})),
        ("Analyst",      "haiku",  context.get("usage", {}).get("analyst", {})),
        ("Visual",       "haiku",  context.get("usage", {}).get("visual", {})),
    ]
    rows: list[dict] = []
    total_cost = 0.0
    for name, model, usage in agents:
        if not usage:
            continue
        in_tok  = usage.get("input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)
        p       = _PRICES[model]
        cost    = (in_tok * p["input"] + out_tok * p["output"]) / 1_000_000
        total_cost += cost
        rows.append({
            "Agent":         name,
            "Model":         model,
            "Input tokens":  f"{in_tok:,}",
            "Output tokens": f"{out_tok:,}",
            "Cost (USD)":    f"${cost:.4f}",
        })
    rows.append({
        "Agent": "TOTAL", "Model": "",
        "Input tokens": "", "Output tokens": "",
        "Cost (USD)": f"${total_cost:.4f}",
    })
    return rows


# ── Core run function ──────────────────────────────────────────────────────────

def run_with_live_progress(
    user_task: str,
    doc_id: str,
    sheet_id: str,
    task_entry: dict | None = None,
    context_brief: str | None = None,
) -> None:
    # A — Validate inputs
    if not user_task.strip():
        st.error("Please enter a task.")
        return
    if not doc_id.strip():
        st.error("Please enter a Google Doc ID.")
        return
    if not sheet_id.strip():
        st.error("Please enter a Google Sheet ID.")
        return

    # B — New or continuation
    is_new = task_entry is None
    if is_new:
        task_entry = add_task(
            task_str=user_task,
            folder_id="",
            folder_name=make_folder_name(user_task),
            doc_id=doc_id,
            sheet_id=sheet_id,
        )
    run_n     = 1 if is_new else task_entry.get("runs", 1) + 1
    tab_name  = f"Run {run_n} - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    save_task = user_task if is_new else task_entry.get("task", user_task)

    original_execute_tool = main_module.execute_tool

    try:
        # C — Live status display + monkey-patch execute_tool
        with st.status(label="🤖 Crew is running...", expanded=True) as status:

            def patched_execute_tool(tool_name, tool_input, ctx):
                if tool_name in _AGENT_CONFIG:
                    label, action = _AGENT_CONFIG[tool_name]
                    status.write(f"⏳ [{label}] {action}")
                result = original_execute_tool(tool_name, tool_input, ctx)
                if tool_name in _AGENT_CONFIG:
                    label, _ = _AGENT_CONFIG[tool_name]
                    if tool_name == "finalize_report":
                        status.write(f"✓ [{label}] Reality check written")
                    else:
                        status.write(f"✓ [{label}] Done — {len(result):,} chars")
                return result

            main_module.execute_tool = patched_execute_tool

            # D — Run orchestrator in thread; pass Streamlit script context so
            #     st calls (status.write) work correctly from within the thread.
            result_box: dict = {"context": None, "error": None}

            def _run() -> None:
                try:
                    result_box["context"] = run_orchestrator_session(
                        user_task, context_brief
                    )
                except Exception as exc:
                    result_box["error"] = exc

            _st_ctx = None
            _add_ctx = None
            try:
                from streamlit.runtime.scriptrunner import (
                    add_script_run_ctx, get_script_run_ctx,
                )
                _st_ctx  = get_script_run_ctx()
                _add_ctx = add_script_run_ctx
            except Exception:
                pass

            t = threading.Thread(target=_run, daemon=True)
            if _st_ctx is not None and _add_ctx is not None:
                _add_ctx(t, _st_ctx)
            t.start()
            t.join()

            if result_box["error"]:
                raise result_box["error"]

            context: dict = result_box["context"]

            # E — Save outputs
            _save_to_google(
                context, save_task,
                sheet_id=sheet_id, doc_id=doc_id,
                tab_name=tab_name, run_n=run_n,
            )
            save_report(context, user_task)

            summary        = context.get("summary", "")
            data_collected = context.get("data_collected", [])
            lessons        = context.get("lessons", {})
            if isinstance(lessons, dict) and "run" not in lessons:
                lessons["run"] = run_n
            update_after_run(task_entry["id"], summary, data_collected, lessons)

            status.update(label="✅ Crew complete!", state="complete")

        # F — Completion UI (rendered after st.status block closes)
        st.success(
            f"**Run {run_n} complete** — {task_entry.get('folder_name', user_task[:60])}\n\n"
            f"📄 [Google Doc]({get_doc_url(doc_id)})  |  "
            f"📊 [Google Sheet]({get_sheet_url(sheet_id)})  |  "
            f"Tab: `{tab_name}`"
        )

        with st.expander("📋 Orchestrator Reality Check"):
            st.markdown(context.get("verdict", "No verdict produced."))

        with st.expander("💰 Cost Summary"):
            cost_rows = _build_cost_data(context)
            if cost_rows:
                st.dataframe(
                    pd.DataFrame(cost_rows),
                    use_container_width=True,
                    hide_index=True,
                )

        st.session_state["last_result"] = context

    except Exception as exc:
        st.error(f"Run failed: {str(exc)}")
        with st.expander("Error details"):
            st.code(traceback.format_exc())
    finally:
        main_module.execute_tool = original_execute_tool


# ── Tabs ───────────────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["🆕 New Task", "▶️ Continue Task", "🗑️ Delete Task"])

# ── Tab 1: New Task ────────────────────────────────────────────────────────────
with tab1:
    col_left, col_right = st.columns([2, 1])

    with col_left:
        user_task_new = st.text_area(
            "What is your task today?",
            height=120,
            placeholder="e.g. Benchmark SPX vs J&T Express on delivery speed...",
            key="new_task_input",
        )
        c1, c2 = st.columns(2)
        doc_id_new   = c1.text_input(
            "Google Doc ID",   placeholder="Paste Doc ID from URL",   key="new_doc_id"
        )
        sheet_id_new = c2.text_input(
            "Google Sheet ID", placeholder="Paste Sheet ID from URL", key="new_sheet_id"
        )
        st.caption("Find the ID in your Google Doc/Sheet URL between /d/ and /edit")

        run_btn = st.button(
            "🚀 Run Crew",
            type="primary",
            disabled=st.session_state.running,
            use_container_width=True,
            key="run_btn_new",
        )
        if run_btn:
            st.session_state.running = True
            run_with_live_progress(user_task_new, doc_id_new, sheet_id_new)
            st.session_state.running = False

    with col_right:
        st.info(
            "**How it works:**\n\n"
            "1. Scout researches market data\n"
            "2. Architect analyzes the WHY\n"
            "3. Analyst builds comparison tables\n"
            "4. Visual creates CFO deck outline\n\n"
            "Results written to your Google Doc and Sheet."
        )

# ── Tab 2: Continue Task ───────────────────────────────────────────────────────
with tab2:
    tasks_cont = list_tasks()
    if not tasks_cont:
        st.info("No previous tasks. Create one first.")
    else:
        task_labels_cont = [
            f"{t['folder_name']}  ({t['runs']} run{'s' if t['runs'] != 1 else ''})"
            for t in tasks_cont
        ]
        sel_cont = st.selectbox(
            "Select a task to continue",
            range(len(tasks_cont)),
            format_func=lambda i: task_labels_cont[i],
            key="cont_select",
        )
        te_cont = tasks_cont[sel_cont]

        st.markdown(
            f"📄 [Google Doc]({get_doc_url(te_cont['doc_id'])})  |  "
            f"📊 [Google Sheet]({get_sheet_url(te_cont['sheet_id'])})"
        )
        if te_cont.get("summary"):
            with st.expander("📝 Previous findings summary"):
                st.markdown(te_cont["summary"])

        new_request = st.text_area(
            "What additional detail do you need?",
            height=100,
            key="cont_request",
        )
        cont_btn = st.button(
            "▶️ Continue Run",
            type="primary",
            disabled=st.session_state.running,
            use_container_width=True,
            key="cont_btn",
        )
        if cont_btn:
            brief = build_context_brief(te_cont, new_request)
            st.session_state.running = True
            run_with_live_progress(
                user_task=new_request,
                doc_id=te_cont["doc_id"],
                sheet_id=te_cont["sheet_id"],
                task_entry=te_cont,
                context_brief=brief,
            )
            st.session_state.running = False

# ── Tab 3: Delete Task ─────────────────────────────────────────────────────────
with tab3:
    tasks_del = list_tasks()
    if not tasks_del:
        st.info("No tasks to delete.")
    else:
        task_labels_del = [
            f"{t['folder_name']}  ({t['runs']} run{'s' if t['runs'] != 1 else ''})"
            for t in tasks_del
        ]
        sel_del = st.selectbox(
            "Select a task to delete",
            range(len(tasks_del)),
            format_func=lambda i: task_labels_del[i],
            key="del_select",
        )
        te_del = tasks_del[sel_del]

        st.markdown(
            f"**Created:** {te_del.get('created', 'N/A')}  |  "
            f"**Runs:** {te_del.get('runs', 0)}  |  "
            f"📄 [Google Doc]({get_doc_url(te_del['doc_id'])})  |  "
            f"📊 [Google Sheet]({get_sheet_url(te_del['sheet_id'])})"
        )

        del_btn = st.button(
            "🗑️ Delete This Task",
            type="secondary",
            key="del_btn",
        )
        if del_btn:
            st.session_state.delete_confirm_id = te_del["id"]

        if st.session_state.delete_confirm_id == te_del["id"]:
            st.warning(
                "This removes from task memory only. Doc and Sheet are NOT deleted."
            )
            conf1, conf2 = st.columns(2)
            with conf1:
                if st.button("✅ Yes, delete", type="primary", key="confirm_del"):
                    delete_task(te_del["id"])
                    st.session_state.delete_confirm_id = None
                    st.success("Task deleted.")
                    st.rerun()
            with conf2:
                if st.button("❌ Cancel", key="cancel_del"):
                    st.session_state.delete_confirm_id = None
                    st.rerun()
