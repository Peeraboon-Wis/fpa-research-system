import anthropic

client = anthropic.Anthropic()

ORCHESTRATOR_MODEL = "claude-sonnet-4-6"
SCOUT_MODEL        = "claude-sonnet-4-6"
WORKER_MODEL       = "claude-haiku-4-5-20251001"

_QUALITY_RULES = """\
MANDATORY OUTPUT RULES:
- Zero filler sentences. Every line must carry information.
- Every claim requires a number or a named source.
- Use structured format: ## headers, bullets, pipe tables.
- Label all data: [VERIFIED] = confirmed rate/policy, [ESTIMATED] = market approximation.
- Do NOT repeat content already produced by another agent."""

_SCOUT_CONTINUATION = """\
CONTINUATION INSTRUCTIONS:
You MUST NOT research topics already listed in the CONTEXT BRIEF data_collected section.
Focus only on gaps and the new request.
Label every new finding clearly as NEW DATA: at the start of the line.
If a topic is already covered, write "ALREADY COVERED — see previous run" and move on."""

_ARCHITECT_CONTINUATION = """\
CONTINUATION INSTRUCTIONS:
Build on the previous analysis described in the CONTEXT BRIEF.
Reference previous findings where relevant — do not repeat prior cause-effect chains.
Only add new logic chains for data that is genuinely new this run."""

_ANALYST_CONTINUATION = """\
CONTINUATION INSTRUCTIONS:
Do not recreate tables that already exist in previous tabs of this spreadsheet.
Only add new rows or entirely new tables for genuinely new data.
Where data already exists in a prior tab, write: "See [previous tab name] — not duplicated here."
Add new rows to existing table structures rather than rebuilding them from scratch."""

_VISUAL_CONTINUATION = """\
CONTINUATION INSTRUCTIONS:
Update the slide outline to reflect ALL runs combined into one coherent deck.
For each slide, note: NEW DATA (added this run) vs CONFIRMED (validated from previous run).
Keep the deck self-contained — a reader should not need to see previous run outputs."""


def _format_context(context: dict) -> str:
    labels = {
        "scout":     "SCOUT — Market Data",
        "architect": "ARCHITECT — Structural Analysis",
        "analyst":   "ANALYST — Comparison Tables",
        "visual":    "VISUAL — Executive Deck Outline",
    }
    parts = []
    for key, label in labels.items():
        if key in context:
            parts.append(f"=== {label} ===\n{context[key]}")
    return "\n\n".join(parts)


def _build_user_blocks(task: str, context: dict,
                       context_brief: str | None,
                       continuation_instruction: str) -> list:
    """
    Assemble the user message blocks for a specialist agent.
    Order: context brief (if any) → prior agent work → task brief.
    """
    blocks = []

    if context_brief:
        blocks.append({
            "type": "text",
            "text": context_brief + "\n\n" + continuation_instruction,
            "cache_control": {"type": "ephemeral"},
        })

    shared = _format_context(context)
    if shared:
        label = "WORK ALREADY DONE — read before starting" if not context_brief \
                else "PRIOR AGENT OUTPUTS THIS RUN"
        blocks.append({
            "type": "text",
            "text": f"{label}:\n\n{shared}",
            "cache_control": {"type": "ephemeral"},
        })

    blocks.append({"type": "text", "text": f"YOUR TASK:\n\n{task}"})
    return blocks


def run_scout(task: str, context: dict,
              context_brief: str | None = None) -> str:
    blocks = _build_user_blocks(
        task, context, context_brief, _SCOUT_CONTINUATION
    )
    resp = client.messages.create(
        model=SCOUT_MODEL,
        max_tokens=3500,
        system=[{
            "type": "text",
            "text": (
                "You are a Thailand logistics market research specialist.\n"
                "You gather specific, verifiable data on Thai parcel delivery companies "
                "(SPX Express, J&T Express, Flash Express, Kerry, Ninja Van, etc.).\n\n"
                + _QUALITY_RULES + "\n\n"
                "RESEARCH FORMAT:\n"
                "## [CATEGORY]\n"
                "- Specific finding [VERIFIED/ESTIMATED] — source: [public rate card / industry estimate / etc.]\n\n"
                "Rules:\n"
                "- Use specific THB amounts, day counts, and km ranges.\n"
                "- If a data point is unknown, state 'Data unavailable' explicitly — never omit or invent.\n"
                "- Organize findings by category with clear ## headers."
            ),
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": blocks}],
    )
    return resp.content[0].text, resp.usage


def run_architect(task: str, context: dict,
                  context_brief: str | None = None) -> str:
    blocks = _build_user_blocks(
        task, context, context_brief, _ARCHITECT_CONTINUATION
    )
    resp = client.messages.create(
        model=WORKER_MODEL,
        max_tokens=2000,
        system=[{
            "type": "text",
            "text": (
                "You are a logistics strategy analyst for Southeast Asian last-mile economics.\n"
                "Your job is pure analysis — no new data. Use only what Scout provided.\n\n"
                + _QUALITY_RULES + "\n\n"
                "ANALYSIS FORMAT:\n"
                "## [INSIGHT TITLE]\n"
                "Cause: [Scout data point with specific number]\n"
                "Effect: [business consequence]\n"
                "Implication: [so what for the task]\n\n"
                "Rules:\n"
                "- Only reference [VERIFIED] or [ESTIMATED] data from Scout.\n"
                "- Explicitly flag any gap: 'DATA GAP: Scout did not provide X — this analysis assumes Y.'\n"
                "- Write for a CFO: clear conclusions, no jargon."
            ),
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": blocks}],
    )
    return resp.content[0].text, resp.usage


def run_analyst(task: str, context: dict,
                context_brief: str | None = None) -> str:
    blocks = _build_user_blocks(
        task, context, context_brief, _ANALYST_CONTINUATION
    )
    resp = client.messages.create(
        model=WORKER_MODEL,
        max_tokens=4000,
        system=[{
            "type": "text",
            "text": (
                "You are a financial analyst who creates structured comparison tables "
                "and financial models.\n"
                "Output pipe-separated tables ONLY — pasteable directly into Google Sheets.\n\n"
                + _QUALITY_RULES + "\n\n"
                "TABLE RULES (always produce TABLE 1–3):\n"
                "- Every cell must have a value: (N/A) = unknown, (E) = estimated.\n"
                "- TABLE 1: main metric comparison (use Scout numbers).\n"
                "- TABLE 2: secondary breakdown (cost breakdown, route split, etc.).\n"
                "- TABLE 3: SCORECARD — | Category | [Co A] | [Co B] | Winner |\n"
                "- After each table, list gaps: DATA GAP: [what is missing].\n"
                "- Keep all cell labels under 30 characters. Abbreviate where needed "
                "(e.g. \"Total Rev [Q1-Q4]\" not \"Sea Limited Total Revenue [Q1-Q4 preliminary]\", "
                "\"E-Comm Adj. EBITDA\" not \"E-Commerce Adjusted EBITDA estimate\"). "
                "Never wrap a label across lines.\n\n"
                "TABLE 4 — FINANCIAL MODEL (only when task involves financial analysis,\n"
                "unit economics, cost projections, scenarios, margin, CAGR, or ROI):\n"
                "If the task needs financial modelling, produce ## TABLE 4: Financial Model\n"
                "with THREE sub-sections, each as its own pipe table:\n\n"
                "A. Key Assumptions table — all input variables:\n"
                "   | Assumption | Value | Notes |\n"
                "   Start every data row first cell with 'Assumption: [name]'\n\n"
                "B. Unit Economics table — cost/revenue per parcel:\n"
                "   | Metric | [Scenario A] | [Scenario B] | [Scenario C] |\n"
                "   Start every data row first cell with the metric name only.\n\n"
                "C. 3-Scenario Projection — 12-month or volume-tier projection:\n"
                "   | Metric | Best Case | Base Case | Worst Case |\n"
                "   Start every data row first cell exactly with:\n"
                "   'Best Case: [metric]', 'Base Case: [metric]', or 'Worst Case: [metric]'\n\n"
                "If task does NOT require financial modelling, stop at TABLE 3."
            ),
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": blocks}],
    )
    return resp.content[0].text, resp.usage


def run_visual(task: str, context: dict,
               context_brief: str | None = None) -> str:
    blocks = _build_user_blocks(
        task, context, context_brief, _VISUAL_CONTINUATION
    )
    resp = client.messages.create(
        model=WORKER_MODEL,
        max_tokens=4000,
        system=[{
            "type": "text",
            "text": (
                "You are an executive communications specialist for CFO presentations.\n"
                "Create 5 comprehensive, self-contained slide outlines. Each slide must be\n"
                "detailed enough to hand to a presenter who has not read the underlying data.\n\n"
                + _QUALITY_RULES + "\n\n"
                "EXACT FORMAT FOR EVERY SLIDE (repeat all 6 elements for all 5 slides):\n\n"
                "## SLIDE N: [HEADLINE — one punchy sentence with a specific number]\n\n"
                "**Context:** [2-3 sentences. What is this slide about and why does it matter\n"
                "for the business right now? Reference the task and market situation.]\n\n"
                "**Key Data Points:**\n"
                "- [Exact number from Analyst TABLE X — label which table]\n"
                "- [Exact number from Analyst TABLE X — label which table]\n"
                "- [Exact number from Analyst TABLE X — label which table]\n\n"
                "**Talking Points:**\n"
                "• [Full sentence — finding + so-what for the business]\n"
                "• [Full sentence — comparative insight or trend]\n"
                "• [Full sentence — risk, opportunity, or recommendation]\n"
                "• [Full sentence — operational or financial implication]\n\n"
                "**BOTTOM LINE:** [One decisive sentence — what leadership must do or decide.]\n\n"
                "**Design Note:** [Specific chart/visual type suggestion, e.g. 'Grouped bar chart\n"
                "comparing rates by weight tier' or 'Waterfall chart showing cost breakdown'.]\n\n"
                "---\n\n"
                "Rules:\n"
                "- Pull every number directly from Analyst tables and cite by table number.\n"
                "- Context must reference the specific task — no generic filler.\n"
                "- Talking Points must be full sentences, not fragments.\n"
                "- Design Note must name a specific chart type relevant to that slide's data.\n"
                "- Written for CFO level: decisive verdict, zero jargon, all numbers visible."
            ),
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": blocks}],
    )
    return resp.content[0].text, resp.usage
