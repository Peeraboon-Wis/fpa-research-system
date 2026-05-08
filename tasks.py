"""
Tool definitions and dispatcher for the Orchestrator-driven pipeline.
The Orchestrator calls these tools by name; execute_tool routes each call
to the right agent and stores the result in the shared context dict.
"""

from agents import run_scout, run_architect, run_analyst, run_visual

AGENT_TOOLS = [
    {
        "name": "call_scout",
        "description": (
            "Research market data for the user's task: pricing, delivery SLAs, "
            "coverage zones, platform subsidies, and any other quantitative data needed. "
            "Write a precise, task-specific research brief as the task string. "
            "Call this FIRST — all other agents depend on Scout's data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "Precise research brief for Scout. Specify: which companies, "
                        "which metrics, which routes or segments, and what label format to use."
                    ),
                }
            },
            "required": ["task"],
        },
    },
    {
        "name": "call_architect",
        "description": (
            "Analyze WHY the data from Scout matters: cost drivers, network structure, "
            "platform subsidies, and competitive moats relevant to the user's task. "
            "No new data — pure cause-effect analysis from Scout's findings. "
            "Call AFTER call_scout."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "Precise analysis brief for Architect. Specify: which insights "
                        "to develop, which cause-effect chains matter for the task."
                    ),
                }
            },
            "required": ["task"],
        },
    },
    {
        "name": "call_analyst",
        "description": (
            "Build 3 comparison tables from Scout and Architect outputs. "
            "TABLE 3 must be a scorecard with a WINNER column. "
            "If task involves financial analysis, also produce TABLE 4: Financial Model "
            "(key assumptions + unit economics + 3-scenario projection). "
            "Call AFTER call_scout and call_architect."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "Precise table spec for Analyst. Specify: table titles, "
                        "column headers, row categories, what the scorecard should judge, "
                        "AND whether to produce TABLE 4 financial model "
                        "(include when task involves unit economics, cost projections, "
                        "scenarios, margin, CAGR, or ROI analysis)."
                    ),
                }
            },
            "required": ["task"],
        },
    },
    {
        "name": "call_visual",
        "description": (
            "Create a 5-slide CFO executive deck outline synthesizing all prior agent outputs. "
            "Each slide must include: Headline (with specific number), Context (2-3 sentences), "
            "Key Data Points (exact numbers from named Analyst tables), "
            "Talking Points (4 full sentences), BOTTOM LINE verdict, and Design Note (chart type). "
            "Call AFTER call_analyst — Visual needs the tables."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "Precise deck brief for Visual. Specify: the narrative arc, "
                        "which Analyst tables to cite per slide, "
                        "what each slide should focus on, and what the overall CFO verdict should be. "
                        "Mention which table numbers contain the most important data."
                    ),
                }
            },
            "required": ["task"],
        },
    },
    {
        "name": "finalize_report",
        "description": (
            "Write your full Reality Check as plain text before calling this tool. "
            "This tool only captures memory fields. "
            "Call ONLY after all 4 agents have completed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": (
                        "3-5 sentence cumulative summary of ALL findings across all runs "
                        "for this task. Written to be read in the next session as context. "
                        "Be specific: include carrier names, key metrics, and main conclusions."
                    ),
                },
                "data_collected": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Flat list of distinct data points gathered in THIS run. "
                        "Each item is a short phrase, e.g. "
                        "'SPX base rates BKK-province 3 weight tiers', "
                        "'J&T COD fee structure', "
                        "'Delivery SLA comparison 5 routes'. "
                        "Do not repeat items already in the context brief."
                    ),
                },
                "lessons": {
                    "type": "object",
                    "description": (
                        "Quality flags from this run. Schema: "
                        "{\"run\": N, \"flags\": [\"string\"], "
                        "\"data_quality\": \"8/10\", \"gaps\": [\"string\"]}. "
                        "flags = data quality warnings. gaps = topics not yet covered."
                    ),
                    "properties": {
                        "run":          {"type": "integer"},
                        "flags":        {"type": "array", "items": {"type": "string"}},
                        "data_quality": {"type": "string"},
                        "gaps":         {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["run", "flags", "data_quality", "gaps"],
                },
            },
            "required": ["summary", "data_collected", "lessons"],
        },
    },
]

_AGENT_MAP = {
    "call_scout":     ("scout",     run_scout,     "SCOUT"),
    "call_architect": ("architect", run_architect, "ARCHITECT"),
    "call_analyst":   ("analyst",   run_analyst,   "ANALYST"),
    "call_visual":    ("visual",    run_visual,    "VISUAL"),
}


def execute_tool(tool_name: str, tool_input: dict, context: dict) -> str:
    if tool_name == "finalize_report":
        if not tool_input:
            print("[WARNING] finalize_report received empty input.")
            return "[ERROR] Input truncated."

        summary        = tool_input.get("summary", "")
        data_collected = tool_input.get("data_collected", [])
        lessons        = tool_input.get("lessons", {})

        context["summary"]        = summary
        context["data_collected"] = data_collected
        context["lessons"]        = lessons
        return "OK"

    if tool_name not in _AGENT_MAP:
        return f"[ERROR] Unknown tool: {tool_name}"

    context_key, agent_fn, label = _AGENT_MAP[tool_name]
    task         = tool_input.get("task", "")
    context_brief = context.get("_context_brief")

    preview = task[:120] + ("..." if len(task) > 120 else "")
    print(f"  Task brief: {preview}")
    text, usage = agent_fn(task, context, context_brief=context_brief)
    context[context_key] = text
    context.setdefault("usage", {})[context_key] = {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
    }
    print(f"  Output: {len(text):,} chars\n")
    return text
