"""
FP&A Multi-Agent Research System
Orchestrator-driven | Fully adaptive to any task | Quality-optimized

Run: python main.py
"""

import sys
import os
from datetime import datetime
from dotenv import load_dotenv

# Force UTF-8 on Windows terminals that default to a narrow locale (e.g. cp874)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

import anthropic
from tasks import AGENT_TOOLS, execute_tool
from sheets import (
    save_analyst_to_sheets,
    save_scout_architect_to_doc,
)
from drive import (
    get_doc_url,
    get_sheet_url,
)
from task_memory import (
    load_memory,
    add_task,
    list_tasks,
    build_context_brief,
    update_after_run,
    make_folder_name,
    delete_task,
)

client = anthropic.Anthropic()
ORCHESTRATOR_MODEL = "claude-sonnet-4-6"

ORCHESTRATOR_SYSTEM = """\
You are the lead research Orchestrator for an FP&A team at a Thailand logistics company.

YOUR JOB:
Read the user's task, plan how to decompose it across 4 specialist agents, execute the plan, \
then audit all outputs and deliver a final Reality Check verdict.

═══════════════════════════════════════════════
PHASE 1 — PLAN (think before your first tool call)
═══════════════════════════════════════════════
Decompose the user's task into 4 precise sub-tasks:
- SCOUT sub-task: what exact data to gather (companies, metrics, routes, weight tiers, time periods)
- ARCHITECT sub-task: which structural drivers and cause-effect chains to analyze from Scout's data
- ANALYST sub-task: what 3 tables to build, column headers, scorecard categories; whether TABLE 4 financial model is needed (unit economics + 3-scenario projection — only for financial analysis tasks)
- VISUAL sub-task: what narrative arc the 5-slide deck should tell, which Analyst table numbers contain the key data for each slide, and what the decisive CFO verdict should be

═══════════════════════════════════════════════
PHASE 2 — EXECUTE (call agents in this order)
═══════════════════════════════════════════════
1. call_scout    — research data
2. call_architect — explain the WHY behind the data
3. call_analyst  — build comparison tables + scorecard
4. call_visual   — create 5-slide CFO deck outline

IMPORTANT: Each agent task string must be SPECIFIC to the user's actual task.

EXAMPLE OF BAD task string (too vague):
"Research SPX and J&T pricing and delivery times."

EXAMPLE OF GOOD task string (specific):
"Research SPX Express and J&T Express base shipping rates (THB/parcel) for three weight \
tiers: 0-1kg, 1-3kg, 3-5kg. Focus on Bangkok-to-province routes only. Include: (1) standard \
rate per tier, (2) estimated delivery SLA in days, (3) any known platform subsidies or \
merchant discounts. Label each data point [VERIFIED] or [ESTIMATED] with source."

═══════════════════════════════════════════════
PHASE 3 — FINAL AUDIT (after all 4 agents complete)
═══════════════════════════════════════════════
Before calling finalize_report, audit all outputs:
1. DATA QUALITY: Are Scout's numbers specific and labeled [VERIFIED]/[ESTIMATED]?
2. LOGIC CHECK: Does Architect's analysis follow from Scout's actual data?
3. TABLE CONSISTENCY: Do Analyst's table numbers match Scout's findings?
4. NARRATIVE CHECK: Does Visual's deck use specific numbers from the Analyst tables?
5. REDUNDANCY: Note any significant overlaps between sections.

Write your full Reality Check as plain text FIRST, then call finalize_report with only the memory fields:
- summary: 3-5 sentence cumulative summary of ALL findings (written for the next session)
- data_collected: list of distinct data points gathered THIS run only
- lessons: {run, flags, data_quality, gaps} object

═══════════════════════════════════════════════
TASK MEMORY & CONTINUATION
═══════════════════════════════════════════════
For continued tasks: a CONTEXT BRIEF will appear at the start of the user message.
Read it carefully. Instruct agents to fill GAPS ONLY.
Warn agents about previous data quality flags from the brief.

QUALITY RULES
═══════════════════════════════════════════════
- Write precise, task-tailored agent instructions — vague instructions → vague outputs.
- Flag unsupported claims rather than accepting them.
- Your final verdict must give a clear, actionable business recommendation.
- Every finding must tie to a specific number."""


def run_orchestrator_session(user_task: str,
                             context_brief: str = None) -> dict:
    context: dict = {}

    # Store context brief so execute_tool can pass it to each agent
    if context_brief:
        context["_context_brief"] = context_brief

    # Build the initial user message
    if context_brief:
        initial_content = f"{context_brief}\n\nNEW REQUEST: {user_task}"
    else:
        initial_content = user_task

    messages = [{"role": "user", "content": initial_content}]

    print(f"\n{'-' * 70}")
    print(f"[ORCHESTRATOR] Analyzing task and building execution plan...")
    print(f"[MODEL] Orchestrator + Scout : {ORCHESTRATOR_MODEL}")
    print(f"[MODEL] Architect/Analyst/Visual: claude-haiku-4-5-20251001")
    print(f"{'-' * 70}\n")

    step = 0
    max_steps = 25

    while step < max_steps:
        step += 1

        response = client.messages.create(
            model=ORCHESTRATOR_MODEL,
            max_tokens=5000,
            system=[{
                "type": "text",
                "text": ORCHESTRATOR_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            tools=AGENT_TOOLS,
            messages=messages,
        )

        orc_usage = context.setdefault("_orc_usage", {"input_tokens": 0, "output_tokens": 0})
        orc_usage["input_tokens"]  += response.usage.input_tokens
        orc_usage["output_tokens"] += response.usage.output_tokens

        messages.append({"role": "assistant", "content": response.content})

        for block in response.content:
            if hasattr(block, "type") and block.type == "text" and block.text.strip():
                text = block.text.strip()
                print(f"[ORCHESTRATOR] {text[:300]}{'...' if len(text) > 300 else ''}\n")

        tool_use_blocks = [b for b in response.content
                           if hasattr(b, "type") and b.type == "tool_use"]

        if tool_use_blocks:
            tool_results = []
            finalize_called = False

            for block in tool_use_blocks:
                label = block.name.replace("call_", "").upper()
                if block.name != "finalize_report":
                    print(f"[{label}] Running...")

                result_text = execute_tool(block.name, block.input, context)

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })

                if block.name == "finalize_report":
                    finalize_called = True

            messages.append({"role": "user", "content": tool_results})

            if finalize_called:
                # Capture verdict from the Orchestrator's text output this turn
                for block in response.content:
                    if hasattr(block, "type") and block.type == "text" and block.text.strip():
                        context["verdict"] = block.text.strip()
                        break
                print("[ORCHESTRATOR] Reality Check complete. Report finalized.\n")
                return context

        else:
            print("[ORCHESTRATOR] Session complete.")
            break

    if step >= max_steps:
        print(f"[WARNING] Reached {max_steps}-step safety cap.")

    return context


def save_report(context: dict, user_task: str) -> str:
    filename = datetime.now().strftime("report_%Y-%m-%d_%H-%M.txt")

    sections = [
        ("1. SCOUT — MARKET DATA",                           "scout"),
        ("2. ARCHITECT — STRUCTURAL ANALYSIS",               "architect"),
        ("3. ANALYST — COMPARISON TABLES (pipe-separated)",  "analyst"),
        ("4. VISUAL — EXECUTIVE DECK OUTLINE (5 slides)",    "visual"),
        ("5. ORCHESTRATOR — REALITY CHECK & FINAL VERDICT",  "verdict"),
    ]

    with open(filename, "w", encoding="utf-8") as f:
        f.write("=" * 70 + "\n")
        f.write("FP&A MULTI-AGENT RESEARCH REPORT\n")
        f.write("=" * 70 + "\n\n")
        f.write(f"Task    : {user_task}\n")
        f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"Models  : Orchestrator/Scout = {ORCHESTRATOR_MODEL}\n")
        f.write(f"          Architect/Analyst/Visual = claude-haiku-4-5-20251001\n")
        f.write("\n")

        for section_name, key in sections:
            content = context.get(key, "[No output produced for this section]")
            f.write(f"\n{'=' * 70}\n")
            f.write(f"{section_name}\n")
            f.write(f"{'=' * 70}\n\n")
            f.write(content)
            f.write("\n")

    return filename


def _save_to_google(context: dict, user_task: str,
                    sheet_id: str, doc_id: str,
                    tab_name: str, run_n: int = 1) -> None:
    """Write analyst tables to Sheets and scout/architect/visual to Docs."""
    analyst_text   = context.get("analyst", "")
    scout_text     = context.get("scout", "")
    architect_text = context.get("architect", "")
    visual_text    = context.get("visual", "")

    if analyst_text:
        try:
            saved_tab = save_analyst_to_sheets(
                analyst_text, user_task,
                sheet_id=sheet_id,
                tab_name_override=tab_name,
            )
            print(f"\n[SHEETS] Analyst tables saved to tab '{saved_tab}'")
            print(f"[SHEETS] {get_sheet_url(sheet_id)}")
        except Exception as exc:
            print(f"\n[SHEETS] Warning: could not save to Google Sheets — {exc}")
    else:
        print("\n[SHEETS] No analyst output to save.")

    if scout_text and architect_text:
        try:
            dt_str = save_scout_architect_to_doc(
                scout_text, architect_text, user_task, visual_text,
                doc_id=doc_id,
                run_n=run_n,
            )
            print(f"\n[DOCS]   Scout + Architect + Visual saved → '{dt_str}'")
            print(f"[DOCS]   {get_doc_url(doc_id)}")
        except Exception as exc:
            print(f"\n[DOCS]   Warning: could not save to Google Docs — {exc}")
    else:
        print("\n[DOCS]   No scout/architect output to save.")


def _print_terminal_summary(task_entry: dict, run_n: int,
                             context: dict, tab_name: str) -> None:
    folder_name = task_entry.get("folder_name", "")
    doc_id      = task_entry.get("doc_id", "")
    sheet_id    = task_entry.get("sheet_id", "")

    data_this_run = context.get("data_collected", [])
    lessons       = context.get("lessons", {})
    gaps          = lessons.get("gaps", []) if isinstance(lessons, dict) else []

    print("\n" + "=" * 40)
    print(f"TASK: {folder_name} (Run {run_n})")
    print("-" * 40)
    print(f"DOC:    {get_doc_url(doc_id)}")
    print(f"SHEET:  {get_sheet_url(sheet_id)}")
    print(f"TAB:    {tab_name}")
    print("-" * 40)

    if data_this_run:
        print("DATA COLLECTED THIS SESSION:")
        for item in data_this_run:
            print(f"  - {item}")
    else:
        print("DATA COLLECTED THIS SESSION: (see verdict)")

    if gaps:
        print("-" * 40)
        print("GAPS REMAINING:")
        for gap in gaps:
            print(f"  - {gap}")

    print("=" * 40 + "\n")


def _print_verdict_preview(context: dict) -> None:
    verdict = context.get("verdict", "")
    print("=" * 70)
    print("ORCHESTRATOR REALITY CHECK (preview)")
    print("=" * 70)
    preview = verdict[-2000:] if len(verdict) > 2000 else verdict
    print(preview or "[No verdict produced]")


def _print_cost_summary(context: dict) -> None:
    PRICES = {
        "sonnet": {"input": 3.00, "output": 15.00},
        "haiku":  {"input": 1.00, "output": 5.00},
    }

    agents = [
        ("Orchestrator", "sonnet", context.get("_orc_usage", {})),
        ("Scout",        "sonnet", context.get("usage", {}).get("scout", {})),
        ("Architect",    "haiku",  context.get("usage", {}).get("architect", {})),
        ("Analyst",      "haiku",  context.get("usage", {}).get("analyst", {})),
        ("Visual",       "haiku",  context.get("usage", {}).get("visual", {})),
    ]

    print("\n" + "=" * 60)
    print(f"{'AGENT':<14} {'MODEL':<8} {'IN tok':>8} {'OUT tok':>8} {'COST (USD)':>12}")
    print("-" * 60)

    total_cost = 0.0
    for name, model, usage in agents:
        if not usage:
            continue
        in_tok  = usage.get("input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)
        p       = PRICES[model]
        cost    = (in_tok * p["input"] + out_tok * p["output"]) / 1_000_000
        total_cost += cost
        print(f"{name:<14} {model:<8} {in_tok:>8,} {out_tok:>8,} ${cost:>11.4f}")

    print("-" * 60)
    print(f"{'TOTAL':<14} {'':<8} {'':>8} {'':>8} ${total_cost:>11.4f}")
    print("=" * 60 + "\n")


def _print_stats(context: dict, filename: str) -> None:
    print("\n" + "=" * 70)
    agent_outputs = {k: context.get(k, "") for k in
                     ("scout", "architect", "analyst", "visual", "verdict")}
    total_chars = sum(len(v) for v in agent_outputs.values())
    print(f"Report saved : {filename}")
    print(f"Total output : {total_chars:,} characters across 5 sections")
    for key, label in [("scout","Scout"), ("architect","Architect"),
                       ("analyst","Analyst"), ("visual","Visual"),
                       ("verdict","Verdict")]:
        chars = len(context.get(key, ""))
        print(f"  {label:<12}: {chars:>6,} chars")
    print("=" * 70 + "\n")


# ── New Task ───────────────────────────────────────────────────────────────────

def handle_new_task() -> None:
    user_task = input("\nWhat is your task today? ").strip()
    if not user_task:
        print("[ERROR] No task entered.")
        return

    doc_id = input("\nGoogle Doc ID for this task: ").strip()
    if not doc_id:
        print("[ERROR] No Doc ID entered.")
        return

    sheet_id = input("Google Sheet ID for this task: ").strip()
    if not sheet_id:
        print("[ERROR] No Sheet ID entered.")
        return

    folder_name = make_folder_name(user_task)

    print(f"\n[TASK] Starting: {user_task}")
    print(f"[DOCS]   Using Doc   → {get_doc_url(doc_id)}")
    print(f"[SHEETS] Using Sheet → {get_sheet_url(sheet_id)}")
    print("=" * 70)

    # Save to task_memory.json
    task_entry = add_task(
        task_str    = user_task,
        folder_id   = "",
        folder_name = folder_name,
        doc_id      = doc_id,
        sheet_id    = sheet_id,
    )

    # Run orchestrator
    context = run_orchestrator_session(user_task)

    tab_name = f"Run 1 - {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    # Save to Google
    _save_to_google(context, user_task,
                    sheet_id=sheet_id, doc_id=doc_id,
                    tab_name=tab_name, run_n=1)

    # Save local txt report
    filename = save_report(context, user_task)

    # Update task_memory.json with run results
    summary        = context.get("summary", "")
    data_collected = context.get("data_collected", [])
    lessons        = context.get("lessons", {})
    if isinstance(lessons, dict) and "run" not in lessons:
        lessons["run"] = 1
    update_after_run(task_entry["id"], summary, data_collected, lessons)

    # Output
    _print_verdict_preview(context)
    _print_terminal_summary(task_entry, run_n=1, context=context, tab_name=tab_name)
    _print_cost_summary(context)
    _print_stats(context, filename)


# ── Continue Existing Task ─────────────────────────────────────────────────────

def handle_continue_task() -> None:
    data  = load_memory()
    tasks = list_tasks(data)

    if not tasks:
        print("\n[INFO] No previous tasks found. Starting a new task instead.\n")
        handle_new_task()
        return

    print("\nPrevious tasks:")
    for i, t in enumerate(tasks, start=1):
        runs_label = f"{t['runs']} run{'s' if t['runs'] != 1 else ''}"
        print(f"  {i}. {t['folder_name']}  ({runs_label})")

    while True:
        raw = input("\nEnter task number: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(tasks):
            task_entry = tasks[int(raw) - 1]
            break
        print(f"  Please enter a number between 1 and {len(tasks)}.")

    new_request = input("\nWhat additional detail do you need? ").strip()
    if not new_request:
        print("[ERROR] No follow-up entered.")
        return

    run_n = task_entry.get("runs", 1) + 1
    print(f"\n[TASK] {task_entry['folder_name']} — Run {run_n}")
    print("=" * 70)

    # Build context brief
    brief = build_context_brief(task_entry, new_request)
    print("\n[CONTEXT] Brief built from task memory. Agents will not repeat prior work.")

    tab_name = f"Run {run_n} - {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    # Run orchestrator with context brief
    context = run_orchestrator_session(new_request, context_brief=brief)

    # Save to Google (sheet tab auto-created; doc content appended at bottom)
    _save_to_google(context, task_entry["task"],
                    sheet_id=task_entry["sheet_id"],
                    doc_id=task_entry["doc_id"],
                    tab_name=tab_name,
                    run_n=run_n)

    # Save local txt report
    filename = save_report(context, new_request)

    # Update task_memory.json
    summary        = context.get("summary", "")
    data_collected = context.get("data_collected", [])
    lessons        = context.get("lessons", {})
    if isinstance(lessons, dict) and "run" not in lessons:
        lessons["run"] = run_n
    update_after_run(task_entry["id"], summary, data_collected, lessons)

    # Output
    _print_verdict_preview(context)
    _print_terminal_summary(task_entry, run_n=run_n, context=context, tab_name=tab_name)
    _print_cost_summary(context)
    _print_stats(context, filename)


# ── Delete Task ───────────────────────────────────────────────────────────────

def handle_delete_task() -> None:
    data  = load_memory()
    tasks = list_tasks(data)

    if not tasks:
        print("\n[INFO] No previous tasks found.\n")
        return

    print("\nPrevious tasks:")
    for i, t in enumerate(tasks, start=1):
        runs_label = f"{t['runs']} run{'s' if t['runs'] != 1 else ''}"
        print(f"  {i}. {t['folder_name']}  ({runs_label})")

    while True:
        raw = input("\nEnter task number: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(tasks):
            task_entry = tasks[int(raw) - 1]
            break
        print(f"  Please enter a number between 1 and {len(tasks)}.")

    confirm = input(f"Delete '{task_entry['folder_name']}'? This cannot be undone. (y/n): ").strip().lower()
    if confirm == "y":
        delete_task(task_entry["id"])
        print("Task deleted.")


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 40)
    print("FP&A Multi-Agent Research System")
    print("=" * 40)
    print("\n  1. New Task")
    print("  2. Continue Existing Task")
    print("  3. Delete Task")

    while True:
        choice = input("\nEnter 1, 2, or 3: ").strip()
        if choice == "1":
            handle_new_task()
            break
        elif choice == "2":
            handle_continue_task()
            break
        elif choice == "3":
            handle_delete_task()
            break
        else:
            print("  Please enter 1, 2, or 3.")


if __name__ == "__main__":
    main()
