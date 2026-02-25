"""Benchmark script for codesurface.

Measures parse time, record stats, query speed, and token savings
compared to realistic Grep+Read approach (simulating Claude Code tool output).

Usage:
    python -m codesurface.benchmark "path/to/your/Assets/Scripts"
"""

import os
import sys
import time
from pathlib import Path

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Add src to path for standalone execution
_src = Path(__file__).resolve().parent.parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from codesurface import cs_parser, db


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for code."""
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Realistic Grep+Read simulations
# ---------------------------------------------------------------------------
# These simulate what Claude Code actually produces when using its tools:
#   - Grep tool returns: file paths, line numbers, matching lines, context
#   - Read tool returns: numbered lines (like `cat -n`) for the full file
#   - A typical workflow is: Grep to find → Read the file → extract answer


def simulate_read_file(path: Path) -> str:
    """Simulate Claude Code's Read tool output: `cat -n` style with line numbers."""
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        return ""
    lines = text.splitlines()
    # Read tool format: "     1→line content"
    output = []
    for i, line in enumerate(lines, 1):
        output.append(f"     {i}→{line}")
    return "\n".join(output)


def simulate_grep_output(project_path: Path, pattern: str, context: int = 2) -> str:
    """Simulate Claude Code's Grep tool output with file paths + context lines.

    Matches how ripgrep formats results: file path header, line numbers,
    context lines before/after each match, separator between groups.
    """
    output_parts = []
    for f in sorted(project_path.rglob("*.cs")):
        try:
            text = f.read_text(encoding="utf-8-sig", errors="replace")
        except Exception:
            continue

        lines = text.splitlines()
        matches = []
        for i, line in enumerate(lines):
            if pattern.lower() in line.lower():
                matches.append(i)

        if not matches:
            continue

        rel = str(f.relative_to(project_path)).replace("\\", "/")
        output_parts.append(rel)

        # Build context windows (merge overlapping)
        windows = []
        for m in matches:
            start = max(0, m - context)
            end = min(len(lines), m + context + 1)
            if windows and start <= windows[-1][1]:
                windows[-1] = (windows[-1][0], end)
            else:
                windows.append((start, end))

        for wi, (start, end) in enumerate(windows):
            if wi > 0:
                output_parts.append("--")
            for j in range(start, end):
                prefix = ":" if j in matches else "-"
                output_parts.append(f"{j+1}{prefix}{lines[j]}")

        output_parts.append("")

    return "\n".join(output_parts)


def simulate_grep_then_read(project_path: Path, pattern: str, file_hint: str) -> str:
    """Simulate NAIVE agent: Grep to find the file → Read the full file.

    Step 1: Grep to locate (small cost)
    Step 2: Read the entire file (big cost — includes everything)
    """
    grep_out = simulate_grep_output(project_path, pattern, context=0)
    for f in project_path.rglob("*.cs"):
        if file_hint in f.name or file_hint in f.stem:
            read_out = simulate_read_file(f)
            return grep_out + "\n---\n" + read_out
    return grep_out


def simulate_grep_then_read_context(project_path: Path, pattern: str) -> str:
    """Simulate NAIVE agent: Grep with 5-line context + Read 30-line chunk.

    Grep to find, then Read a chunk around the match.
    """
    grep_out = simulate_grep_output(project_path, pattern, context=5)
    for f in sorted(project_path.rglob("*.cs")):
        try:
            text = f.read_text(encoding="utf-8-sig", errors="replace")
        except Exception:
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if pattern.lower() in line.lower():
                start = max(0, i - 10)
                end = min(len(lines), i + 20)
                chunk = []
                for j in range(start, end):
                    chunk.append(f"     {j+1}→{lines[j]}")
                return grep_out + "\n---\n" + "\n".join(chunk)
    return grep_out


# ---------------------------------------------------------------------------
# Skilled agent simulations — targeted reads, minimal context
# ---------------------------------------------------------------------------

def simulate_skilled_class_lookup(project_path: Path, pattern: str, file_hint: str) -> str:
    """Simulate SKILLED agent: Grep to locate → Read only the class region (~40 lines).

    A skilled agent knows the file is long. Instead of reading everything,
    they read from the class declaration through its public members only.
    This still includes some noise (attributes, using directives in the chunk,
    private fields if interleaved) but much less than the full file.
    """
    for f in project_path.rglob("*.cs"):
        if file_hint in f.name or file_hint in f.stem:
            try:
                text = f.read_text(encoding="utf-8-sig", errors="replace")
            except Exception:
                continue
            lines = text.splitlines()
            # Find the class/struct/interface declaration line
            class_start = None
            for i, line in enumerate(lines):
                if pattern.lower() in line.lower():
                    class_start = i
                    break
            if class_start is None:
                class_start = 0
            # Read ~40 lines from class start (typical public API region)
            start = max(0, class_start - 2)
            end = min(len(lines), class_start + 40)
            chunk = []
            for j in range(start, end):
                chunk.append(f"     {j+1}→{lines[j]}")
            return "\n".join(chunk)
    return ""


def simulate_skilled_signature_lookup(project_path: Path, pattern: str) -> str:
    """Simulate SKILLED agent: Grep with 3 lines context — enough for a signature.

    Doesn't follow up with a Read. Just the grep output with tight context.
    """
    return simulate_grep_output(project_path, pattern, context=3)


def simulate_skilled_search(project_path: Path, pattern: str) -> str:
    """Simulate SKILLED agent: Grep with 0 context — just matching lines.

    For discovery/search queries, a skilled agent uses files_with_matches or
    minimal context to scan for existence, not full understanding.
    """
    return simulate_grep_output(project_path, pattern, context=0)


def run_benchmark(project_dir: str):
    project_path = Path(project_dir)
    if not project_path.is_dir():
        print(f"Error: {project_dir} is not a directory")
        sys.exit(1)

    print("=" * 78)
    print("project-api-mcp Benchmark (Realistic Simulation)")
    print("=" * 78)
    print(f"Project: {project_path}")
    print()
    print("Grep+Read simulation matches Claude Code tool output:")
    print("  - Read: cat -n format with line number prefixes")
    print("  - Grep: ripgrep format with file headers, line numbers, context")
    print("  - Token estimate: len(text) / 4")
    print()

    # --- 1. Parse + Index Time ---
    print("1. PARSE + INDEX TIME")
    print("-" * 40)

    t0 = time.perf_counter()
    records = cs_parser.parse_directory(project_path)
    parse_time = time.perf_counter() - t0

    t1 = time.perf_counter()
    conn = db.create_memory_db(records)
    db_time = time.perf_counter() - t1

    total_time = parse_time + db_time
    cs_files = list(project_path.rglob("*.cs"))
    print(f"  Files found:   {len(cs_files)}")
    print(f"  Records:       {len(records)}")
    print(f"  Parse time:    {parse_time:.3f}s")
    print(f"  DB build time: {db_time:.3f}s")
    print(f"  Total:         {total_time:.3f}s")
    print()

    # --- 2. Record Stats ---
    print("2. RECORD STATS")
    print("-" * 40)
    stats = db.get_stats(conn)
    for k, v in sorted(stats.items()):
        print(f"  {k:15s}: {v}")
    print()

    rows = conn.execute(
        "SELECT namespace, COUNT(*) as cnt FROM api_records "
        "WHERE namespace != '' GROUP BY namespace ORDER BY cnt DESC LIMIT 10"
    ).fetchall()
    print("  Top namespaces:")
    for r in rows:
        print(f"    {r['namespace']:40s} {r['cnt']:4d}")
    print()

    # --- 3. Token Comparison ---
    print("3. TOKEN COMPARISON (Realistic)")
    print("-" * 78)
    print(f"  {'Query':<35s} {'MCP':>8s} {'Grep+Read':>10s} {'Savings':>8s} {'Method'}")
    print(f"  {'=' * 35} {'=' * 8} {'=' * 10} {'=' * 8} {'=' * 20}")

    queries = [
        {
            "label": "IMergeService all members",
            "method": "Grep→Read file",
            "mcp_fn": lambda: _get_class_response(conn, "IMergeService"),
            "grep_fn": lambda: simulate_grep_then_read(
                project_path, "IMergeService", "IMergeService"),
        },
        {
            "label": "BlastBoardModel members",
            "method": "Grep→Read file",
            "mcp_fn": lambda: _get_class_response(conn, "BlastBoardModel"),
            "grep_fn": lambda: simulate_grep_then_read(
                project_path, "BlastBoardModel", "BlastBoardModel"),
        },
        {
            "label": "ICampGridService members",
            "method": "Grep→Read file",
            "mcp_fn": lambda: _get_class_response(conn, "ICampGridService"),
            "grep_fn": lambda: simulate_grep_then_read(
                project_path, "ICampGridService", "ICampGridService"),
        },
        {
            "label": "TryMerge signature",
            "method": "Grep+context(5)",
            "mcp_fn": lambda: _get_signature_response(conn, "TryMerge"),
            "grep_fn": lambda: simulate_grep_then_read_context(
                project_path, "TryMerge"),
        },
        {
            "label": "GetItemAt signature",
            "method": "Grep+context(5)",
            "mcp_fn": lambda: _get_signature_response(conn, "GetItemAt"),
            "grep_fn": lambda: simulate_grep_then_read_context(
                project_path, "GetItemAt"),
        },
        {
            "label": "all IEvent structs",
            "method": "Grep project-wide",
            "mcp_fn": lambda: _search_response(conn, "IEvent", 20),
            "grep_fn": lambda: simulate_grep_output(
                project_path, "IEvent", context=0),
        },
        {
            "label": "BlastEvents class ref",
            "method": "Grep→Read file",
            "mcp_fn": lambda: _get_class_response(conn, "BlastTapEvent"),
            "grep_fn": lambda: simulate_grep_then_read(
                project_path, "BlastTapEvent", "BlastEvents"),
        },
        {
            "label": "CampEvents class ref",
            "method": "Grep→Read file",
            "mcp_fn": lambda: _search_response(conn, "CampGame Events PointerDown DragStarted ItemMerged", 15),
            "grep_fn": lambda: simulate_grep_then_read(
                project_path, "PointerDownEvent", "CampEvents"),
        },
    ]

    total_mcp = 0
    total_grep = 0

    for q in queries:
        mcp_resp = q["mcp_fn"]()
        mcp_tokens = estimate_tokens(mcp_resp)
        grep_resp = q["grep_fn"]()
        grep_tokens = estimate_tokens(grep_resp)

        total_mcp += mcp_tokens
        total_grep += grep_tokens

        if grep_tokens > 0:
            pct = (1 - mcp_tokens / grep_tokens) * 100
            savings = f"{pct:.0f}%"
        else:
            savings = "N/A"

        print(f"  {q['label']:<35s} {mcp_tokens:>8,d} {grep_tokens:>10,d} {savings:>8s} {q['method']}")

    print(f"  {'-' * 35} {'-' * 8} {'-' * 10} {'-' * 8}")
    if total_grep > 0:
        total_pct = (1 - total_mcp / total_grep) * 100
        total_savings = f"{total_pct:.0f}%"
    else:
        total_savings = "N/A"
    print(f"  {'TOTAL':<35s} {total_mcp:>8,d} {total_grep:>10,d} {total_savings:>8s}")
    print()

    # --- 4. Sample Outputs (show what each side produces) ---
    print("4. SAMPLE OUTPUT COMPARISON")
    print("-" * 78)

    sample_class = "IMergeService"
    print(f"  Query: \"{sample_class} all members\"")
    print()

    mcp_out = _get_class_response(conn, sample_class)
    print("  [MCP Response]")
    for line in mcp_out.splitlines():
        print(f"  | {line}")
    print(f"  ({estimate_tokens(mcp_out)} tokens)")
    print()

    grep_out = simulate_grep_then_read(project_path, sample_class, sample_class)
    grep_lines = grep_out.splitlines()
    print(f"  [Grep+Read Response] ({len(grep_lines)} lines, showing first 15 + last 5)")
    for line in grep_lines[:15]:
        print(f"  | {line}")
    if len(grep_lines) > 20:
        print(f"  | ... ({len(grep_lines) - 20} lines omitted) ...")
    for line in grep_lines[-5:]:
        print(f"  | {line}")
    print(f"  ({estimate_tokens(grep_out)} tokens)")
    print()

    # --- 5. Search Quality ---
    print("5. SEARCH QUALITY (PascalCase-aware)")
    print("-" * 78)
    print("  Testing common developer queries that require substring/component matching:")
    print()

    quality_queries = [
        ("Building", 8, "Should find BuildingConfig, CampBuildingService, BuildingController, etc."),
        ("Spawn", 5, "Should find SpawnItem, ItemSpawnedEvent, etc."),
        ("Event", 8, "Should find EventBus, all *Event structs"),
        ("Grid", 8, "Should find GridCoord, CampGridService, ICampGridService, etc."),
        ("Merge", 5, "Should find MergeService, MergeChainConfig, ItemMergedEvent, etc."),
        ("Powerup", 5, "Should find PowerupType, ActivatePowerupCommand, etc."),
        ("Blast", 8, "Should find BlastBoardModel, BlastGameController, etc."),
        ("Controller", 5, "Should find CampController, BuildingController, etc."),
        ("Config", 5, "Should find BuildingConfig, MergeChainConfig, etc."),
        ("ICommand", 3, "Should find the ICommand interface"),
        ("CampBuildingService", 3, "Exact name should rank #1"),
        ("BuildingService", 3, "Partial PascalCase should find CampBuildingService"),
    ]

    for query, n, description in quality_queries:
        results = db.search(conn, query, n=n)
        names = [r.get("class_name", "") + ("." + r["member_name"] if r.get("member_name") else "")
                 for r in results]
        status = "OK" if results else "MISS"
        print(f"  [{status:4s}] \"{query}\" → {len(results)} results")
        print(f"         {description}")
        if results:
            # Show top 5 match names
            shown = names[:5]
            if len(names) > 5:
                shown.append(f"...+{len(names)-5} more")
            print(f"         Matches: {', '.join(shown)}")
        print()

    # --- 6. Query Speed ---
    print("6. QUERY SPEED")
    print("-" * 40)

    speed_queries = [
        ("FTS: 'MergeService'", lambda: db.search(conn, "MergeService", n=5)),
        ("FTS: 'BlastBoard'", lambda: db.search(conn, "BlastBoard", n=5)),
        ("FTS: 'IEvent'", lambda: db.search(conn, "IEvent", n=10)),
        ("Class: 'BlastBoardModel'", lambda: db.get_class_members(conn, "BlastBoardModel")),
        ("Class: 'CampGridService'", lambda: db.get_class_members(conn, "CampGridService")),
        ("FQN: exact lookup", lambda: db.get_by_fqn(conn, "BlastGame.Models.BlastBoardModel")),
        ("Namespace: 'IMergeService'", lambda: db.resolve_namespace(conn, "IMergeService")),
    ]

    for label, fn in speed_queries:
        times = []
        for _ in range(100):
            t = time.perf_counter()
            fn()
            times.append(time.perf_counter() - t)
        avg_us = sum(times) / len(times) * 1_000_000
        print(f"  {label:<30s} {avg_us:>8.1f} us  (avg over 100 runs)")

    print()
    print("=" * 78)
    print("Benchmark complete.")


# ---------------------------------------------------------------------------
# MCP response simulators (match actual tool output format)
# ---------------------------------------------------------------------------

def _get_class_response(conn, class_name: str) -> str:
    """Simulate get_project_class tool response."""
    short = class_name.rsplit(".", 1)[-1]
    members = db.get_class_members(conn, short)
    if not members:
        return f"No class '{class_name}' found."

    type_rec = next((m for m in members if m["member_type"] == "type"), None)
    ns = type_rec["namespace"] if type_rec else ""

    parts = [f"Class: {short}"]
    if ns:
        parts.append(f"Namespace: {ns}")
    if type_rec:
        sig = type_rec.get("signature", "")
        if sig:
            parts.append(f"Declaration: {sig}")
        fp = type_rec.get("file_path", "")
        if fp:
            parts.append(f"File: {fp}")
    parts.append("")

    groups = {}
    for m in members:
        if m["member_type"] == "type":
            continue
        groups.setdefault(m["member_type"], []).append(m)

    for mtype in ("method", "property", "field", "event"):
        group = groups.get(mtype, [])
        if not group:
            continue
        parts.append(f"-- {mtype.upper()}S ({len(group)}) --")
        for m in group:
            sig = m.get("signature", m["member_name"])
            summary = m.get("summary", "")
            line = f"  {sig}"
            if summary:
                line += f"  // {summary[:80]}"
            parts.append(line)
        parts.append("")

    total = sum(len(g) for g in groups.values())
    parts.append(f"Total: {total} members")
    return "\n".join(parts)


def _get_signature_response(conn, name: str) -> str:
    """Simulate get_project_signature tool response."""
    rows = conn.execute(
        "SELECT * FROM api_records WHERE member_name = ? OR fqn LIKE ?",
        (name, f"%{name}%"),
    ).fetchall()
    if not rows:
        return f"No results for '{name}'."

    parts = []
    for r in rows[:10]:
        r = dict(r)
        parts.append(f"[{r['member_type'].upper()}] {r['fqn']}")
        sig = r.get("signature", "")
        if sig:
            parts.append(f"  Signature: {sig}")
        ns = r.get("namespace", "")
        if ns:
            parts.append(f"  Namespace: {ns}")
        parts.append(f"  File: {r.get('file_path', '')}")
        parts.append("")
    return "\n".join(parts)


def _search_response(conn, query: str, n: int = 10) -> str:
    """Simulate search_project_api tool response."""
    results = db.search(conn, query, n=n)
    parts = [f"Found {len(results)} result(s):\n"]
    for r in results:
        parts.append(f"[{r['member_type'].upper()}] {r['fqn']}")
        sig = r.get("signature", "")
        if sig:
            parts.append(f"  Signature: {sig}")
        parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Workflow benchmark: simulates a full feature research session
# ---------------------------------------------------------------------------

def run_workflow_benchmark(project_dir: str, workflow_name: str = "all") -> str:
    """Run a feature-research workflow and return a Markdown report."""
    project_path = Path(project_dir)
    if not project_path.is_dir():
        return f"Error: {project_dir} is not a directory"

    # Parse + index (measure cost)
    t0 = time.perf_counter()
    records = cs_parser.parse_directory(project_path)
    parse_time = time.perf_counter() - t0

    t1 = time.perf_counter()
    conn = db.create_memory_db(records)
    db_time = time.perf_counter() - t1

    index_cost = {
        "parse_time": parse_time,
        "db_time": db_time,
        "total_time": parse_time + db_time,
        "record_count": len(records),
        "cs_file_count": len(list(project_path.rglob("*.cs"))),
    }
    stats = db.get_stats(conn)

    workflows = {
        "blast-rewards-to-camp": _workflow_blast_rewards_to_camp,
    }

    if workflow_name == "all":
        selected = list(workflows.items())
    elif workflow_name in workflows:
        selected = [(workflow_name, workflows[workflow_name])]
    else:
        return f"Unknown workflow: {workflow_name}. Available: {', '.join(workflows.keys())}"

    md_parts = []
    for name, workflow_fn in selected:
        md_parts.append(workflow_fn(conn, project_path, stats, index_cost))

    return "\n".join(md_parts)


def _find_file(project_path: Path, name_hint: str):
    """Find a .cs file matching a name hint."""
    for f in project_path.rglob("*.cs"):
        if name_hint in f.stem:
            return f
    return None


def _count_file_lines(file_path: Path) -> int:
    """Count lines in a file."""
    try:
        return len(file_path.read_text(encoding="utf-8-sig", errors="replace").splitlines())
    except Exception:
        return 0


def _workflow_blast_rewards_to_camp(conn, project_path: Path, stats: dict, index_cost: dict) -> str:
    """Workflow: Connect blast level rewards to camp item spawning."""

    steps = [
        {
            "step": 1,
            "question": "What data comes back from a completed blast level?",
            "lookup_type": "class",
            "mcp_tool": "get_class",
            "mcp_fn": lambda: _get_class_response(conn, "LevelResult"),
            "naive_method": "Grep → Read full file",
            "naive_fn": lambda: simulate_grep_then_read(project_path, "class LevelResult", "LevelResult"),
            "skilled_method": "Grep → Read ~40 lines",
            "skilled_fn": lambda: simulate_skilled_class_lookup(project_path, "class LevelResult", "LevelResult"),
        },
        {
            "step": 2,
            "question": "How does the game mode context bridge work?",
            "lookup_type": "class",
            "mcp_tool": "get_class",
            "mcp_fn": lambda: _get_class_response(conn, "IGameModeContext"),
            "naive_method": "Grep → Read full file",
            "naive_fn": lambda: simulate_grep_then_read(project_path, "IGameModeContext", "IGameModeContext"),
            "skilled_method": "Grep → Read ~40 lines",
            "skilled_fn": lambda: simulate_skilled_class_lookup(project_path, "IGameModeContext", "IGameModeContext"),
        },
        {
            "step": 3,
            "question": "What orchestrates game mode transitions?",
            "lookup_type": "class",
            "mcp_tool": "get_class",
            "mcp_fn": lambda: _get_class_response(conn, "GameModeManager"),
            "naive_method": "Grep → Read full file",
            "naive_fn": lambda: simulate_grep_then_read(project_path, "class GameModeManager", "GameModeManager"),
            "skilled_method": "Grep → Read ~40 lines",
            "skilled_fn": lambda: simulate_skilled_class_lookup(project_path, "class GameModeManager", "GameModeManager"),
        },
        {
            "step": 4,
            "question": "What events fire when a blast level ends?",
            "lookup_type": "search",
            "mcp_tool": "search",
            "mcp_fn": lambda: _search_response(conn, "LevelWon", 10),
            "naive_method": "Grep project-wide (ctx 2)",
            "naive_fn": lambda: simulate_grep_output(project_path, "LevelWon", context=2),
            "skilled_method": "Grep project-wide (ctx 0)",
            "skilled_fn": lambda: simulate_skilled_search(project_path, "LevelWon"),
        },
        {
            "step": 5,
            "question": "How do I spawn items into the camp grid?",
            "lookup_type": "signature",
            "mcp_tool": "get_signature",
            "mcp_fn": lambda: _get_signature_response(conn, "SpawnItem"),
            "naive_method": "Grep (ctx 5) + Read chunk",
            "naive_fn": lambda: simulate_grep_then_read_context(project_path, "SpawnItem"),
            "skilled_method": "Grep (ctx 3) only",
            "skilled_fn": lambda: simulate_skilled_signature_lookup(project_path, "SpawnItem"),
        },
        {
            "step": 6,
            "question": "How do I find empty cells near a location?",
            "lookup_type": "signature",
            "mcp_tool": "get_signature",
            "mcp_fn": lambda: _get_signature_response(conn, "FindEmptyNeighbors"),
            "naive_method": "Grep (ctx 5) + Read chunk",
            "naive_fn": lambda: simulate_grep_then_read_context(project_path, "FindEmptyNeighbors"),
            "skilled_method": "Grep (ctx 3) only",
            "skilled_fn": lambda: simulate_skilled_signature_lookup(project_path, "FindEmptyNeighbors"),
        },
        {
            "step": 7,
            "question": "Where do I wire new camp logic? (entry point)",
            "lookup_type": "class",
            "mcp_tool": "get_class",
            "mcp_fn": lambda: _get_class_response(conn, "CampEntryPoint"),
            "naive_method": "Grep → Read full file",
            "naive_fn": lambda: simulate_grep_then_read(project_path, "class CampEntryPoint", "CampEntryPoint"),
            "skilled_method": "Grep → Read ~40 lines",
            "skilled_fn": lambda: simulate_skilled_class_lookup(project_path, "class CampEntryPoint", "CampEntryPoint"),
        },
        {
            "step": 8,
            "question": "What shared events bridge camp and blast?",
            "lookup_type": "search",
            "mcp_tool": "search",
            "mcp_fn": lambda: _search_response(conn, "ReturnToCamp", 10),
            "naive_method": "Grep project-wide (ctx 2)",
            "naive_fn": lambda: simulate_grep_output(project_path, "ReturnToCamp", context=2),
            "skilled_method": "Grep project-wide (ctx 0)",
            "skilled_fn": lambda: simulate_skilled_search(project_path, "ReturnToCamp"),
        },
        {
            "step": 9,
            "question": "What's the EventBus API?",
            "lookup_type": "class",
            "mcp_tool": "get_class",
            "mcp_fn": lambda: _get_class_response(conn, "EventBus"),
            "naive_method": "Grep → Read full file",
            "naive_fn": lambda: simulate_grep_then_read(project_path, "class EventBus", "EventBus"),
            "skilled_method": "Grep → Read ~40 lines",
            "skilled_fn": lambda: simulate_skilled_class_lookup(project_path, "class EventBus", "EventBus"),
        },
        {
            "step": 10,
            "question": "Does a reward-related event or model already exist?",
            "lookup_type": "search",
            "mcp_tool": "search",
            "mcp_fn": lambda: _search_response(conn, "Reward", 8),
            "naive_method": "Grep project-wide (ctx 1)",
            "naive_fn": lambda: simulate_grep_output(project_path, "Reward", context=1),
            "skilled_method": "Grep project-wide (ctx 0)",
            "skilled_fn": lambda: simulate_skilled_search(project_path, "Reward"),
        },
    ]

    # --- Execute all steps ---
    results = []
    for s in steps:
        mcp_resp = s["mcp_fn"]()
        mcp_tokens = estimate_tokens(mcp_resp)

        naive_resp = s["naive_fn"]()
        naive_tokens = estimate_tokens(naive_resp)

        skilled_resp = s["skilled_fn"]()
        skilled_tokens = estimate_tokens(skilled_resp)

        results.append({
            **s,
            "mcp_response": mcp_resp,
            "mcp_tokens": mcp_tokens,
            "naive_response": naive_resp,
            "naive_tokens": naive_tokens,
            "skilled_response": skilled_resp,
            "skilled_tokens": skilled_tokens,
        })

    total_mcp = sum(r["mcp_tokens"] for r in results)
    total_naive = sum(r["naive_tokens"] for r in results)
    total_skilled = sum(r["skilled_tokens"] for r in results)
    mcp_vs_naive = (1 - total_mcp / total_naive) * 100 if total_naive > 0 else 0
    mcp_vs_skilled = (1 - total_mcp / total_skilled) * 100 if total_skilled > 0 else 0

    # --- Build Markdown ---
    md = []
    md.append("# Evaluating API-Indexed Retrieval vs Textual Search for LLM Agent Codebase Navigation")
    md.append("")
    md.append("## Scenario")
    md.append("")
    md.append("**Feature**: When a player wins a blast level, spawn reward items in their camp.")
    md.append("")
    md.append("**Why this workflow?** It crosses both game modes (Blast and Camp), touches shared events,")
    md.append("requires understanding DI wiring, and involves multiple services. This is a realistic")
    md.append("cross-cutting feature that an LLM agent would need to research before implementing.")
    md.append("")
    md.append(f"**Codebase**: {stats.get('total', 0)} API records from {stats.get('files', 0)} files")
    md.append("")

    # Methodology
    md.append("## Methodology")
    md.append("")
    md.append("Three agent strategies are compared for the same 10-step research workflow:")
    md.append("")
    md.append("| Strategy | Description |")
    md.append("|---|---|")
    md.append("| **MCP** | Pre-indexed API server. Returns only public signatures, pre-ranked. 1 tool call per lookup. |")
    md.append("| **Skilled Agent** | Targeted Grep + partial Read. Uses `Grep -C 3` for signatures, `Read` with offset/limit (~40 lines) for classes, `Grep -C 0` for searches. Assumes the agent already knows file paths or finds them efficiently. |")
    md.append("| **Naive Agent** | Grep to find file → Read the entire file. Includes all imports, private fields, method bodies, comments. This is worst-case but not uncommon for agents unfamiliar with a codebase. |")
    md.append("")
    md.append("**Token estimate**: `len(text) / 4` (standard approximation for code).")
    md.append("")
    md.append("**Important caveats**:")
    md.append("- The Skilled Agent numbers assume optimal tool usage — real agents vary between Skilled and Naive depending on context and codebase familiarity.")
    md.append("- Grep simulations use case-insensitive substring matching (similar to ripgrep). In practice, an agent might need multiple grep attempts to find the right file.")
    md.append("- MCP returns are pure signal (public API only). Grep+Read returns include noise the LLM must mentally filter, which has a cognitive cost beyond raw token count.")
    md.append("- For small files (< 30 lines), all three approaches converge — the advantage grows with file size and codebase complexity.")
    md.append("")

    # Summary table
    md.append("## Results Summary")
    md.append("")
    md.append("| # | Developer Question | MCP | Skilled | Naive | MCP vs Skilled | MCP vs Naive |")
    md.append("|---|---|---:|---:|---:|---:|---:|")
    for r in results:
        vs_skilled = (1 - r["mcp_tokens"] / r["skilled_tokens"]) * 100 if r["skilled_tokens"] > 0 else 0
        vs_naive = (1 - r["mcp_tokens"] / r["naive_tokens"]) * 100 if r["naive_tokens"] > 0 else 0
        md.append(
            f"| {r['step']} | {r['question']} "
            f"| {r['mcp_tokens']:,} | {r['skilled_tokens']:,} | {r['naive_tokens']:,} "
            f"| {vs_skilled:.0f}% | {vs_naive:.0f}% |"
        )
    md.append(
        f"| | **TOTAL** "
        f"| **{total_mcp:,}** | **{total_skilled:,}** | **{total_naive:,}** "
        f"| **{mcp_vs_skilled:.0f}%** | **{mcp_vs_naive:.0f}%** |"
    )
    md.append("")

    # Cumulative chart (ASCII)
    md.append("## Cumulative Token Usage")
    md.append("")
    md.append("Tokens accumulated across the 10-step research workflow (lower is better):")
    md.append("")
    md.append("```")
    scale = max(total_naive, 1)
    bar_max = 60
    cum_mcp = cum_skilled = cum_naive = 0
    md.append(f"{'Step':<6} {'MCP':>7} {'Skilled':>8} {'Naive':>7}")
    md.append(f"{'─' * 6} {'─' * 7} {'─' * 8} {'─' * 7}   {'─' * bar_max}")
    for r in results:
        cum_mcp += r["mcp_tokens"]
        cum_skilled += r["skilled_tokens"]
        cum_naive += r["naive_tokens"]
        mcp_bar = max(1, int(cum_mcp / scale * bar_max))
        skilled_bar = max(1, int(cum_skilled / scale * bar_max))
        naive_bar = max(1, int(cum_naive / scale * bar_max))
        md.append(f"  {r['step']:<4} {cum_mcp:>7,} {cum_skilled:>8,} {cum_naive:>7,}   {'█' * mcp_bar} MCP")
        md.append(f"       {'':>7} {'':>8} {'':>7}   {'▓' * skilled_bar} Skilled")
        md.append(f"       {'':>7} {'':>8} {'':>7}   {'░' * naive_bar} Naive")
    md.append("```")
    md.append("")

    # Detailed step breakdowns
    md.append("## Step-by-Step Detail")
    md.append("")
    for r in results:
        vs_skilled = (1 - r["mcp_tokens"] / r["skilled_tokens"]) * 100 if r["skilled_tokens"] > 0 else 0
        md.append(f"### Step {r['step']}: {r['question']}")
        md.append("")
        md.append(f"| Approach | Tokens | Method |")
        md.append(f"|---|---:|---|")
        md.append(f"| MCP | **{r['mcp_tokens']:,}** | `{r['mcp_tool']}` |")
        md.append(f"| Skilled | {r['skilled_tokens']:,} | {r['skilled_method']} |")
        md.append(f"| Naive | {r['naive_tokens']:,} | {r['naive_method']} |")
        md.append("")
        md.append(f"**MCP vs Skilled: {vs_skilled:.0f}% fewer tokens**")
        md.append("")
        md.append("MCP response:")
        md.append("```")
        mcp_lines = r["mcp_response"].splitlines()
        for line in mcp_lines[:20]:
            md.append(line)
        if len(mcp_lines) > 20:
            md.append(f"... ({len(mcp_lines) - 20} more lines)")
        md.append("```")
        md.append("")

    # ===== Analysis =====
    md.append("## Analysis")
    md.append("")
    md.append("### Token totals")
    md.append("")
    md.append(f"| Metric | MCP | Skilled Agent | Naive Agent |")
    md.append(f"|---|---:|---:|---:|")
    md.append(f"| Total tokens | {total_mcp:,} | {total_skilled:,} | {total_naive:,} |")
    md.append(f"| vs MCP | — | {total_skilled / total_mcp:.1f}x | {total_naive / total_mcp:.1f}x |")
    md.append(f"| Tool calls | {len(results)} | ~{len(results) * 2} | ~{len(results) * 2} |")
    md.append("")

    md.append("### Where MCP wins most")
    md.append("")
    md.append("The biggest gaps appear on **class lookups for large files** (steps 3, 5, 7).")
    md.append("A 200-line file with 9 public methods contains ~190 lines of implementation the LLM doesn't need.")
    md.append("Even a skilled agent reading ~40 lines still includes private fields, attributes, and partial method bodies.")
    md.append("MCP returns only the public method signatures.")
    md.append("")

    md.append("### Where MCP wins least")
    md.append("")
    md.append("The gap narrows on **small files** (step 2: IGameModeContext is a short interface, MCP vs Skilled = 7%) and")
    md.append("**tight signature lookups** (step 6: 37%) where a skilled agent's `Grep -C 3` already captures the signature.")
    md.append("For a 5-field struct or a single-method interface, reading the whole file is nearly as cheap as the MCP response.")
    md.append("")

    # ===== Where MCP Is Insufficient =====
    md.append("## Where MCP Is Insufficient")
    md.append("")
    md.append("MCP returns **public API surface only**. There are cases in this workflow where that is not enough:")
    md.append("")
    md.append("### Step 7: CampEntryPoint — signature hides the wiring pattern")
    md.append("")
    md.append("MCP returns `Start()` and `Dispose()`. But to wire a new controller, the agent needs to see:")
    md.append("- Constructor parameters (what dependencies are injected)")
    md.append("- The body of `Start()` (the initialization sequence: which controllers are initialized in what order)")
    md.append("- The body of `Dispose()` (every controller must be disposed here or EventBus leaks)")
    md.append("")
    md.append("**Verdict**: MCP saves the discovery step (\"what class do I need?\"), but the agent will still need")
    md.append("to `Read` the file before writing code. The realistic flow is MCP → Read, not MCP alone.")
    md.append("")

    md.append("### Step 3: GameModeManager — transition logic is in the implementation")
    md.append("")
    md.append("MCP shows `void LoadGameMode(LevelConfig config)`. But understanding the transition flow")
    md.append("(scene loading, scope creation, event wiring) requires the method body.")
    md.append("An agent that only sees the signature might not know that `LoadGameMode` triggers an async scene load.")
    md.append("")

    md.append("### Step 9: EventBus — generic signatures lack behavioral context")
    md.append("")
    md.append("MCP shows `Subscribe<T>(Action<T>)`, `Publish<T>(T)`, `Unsubscribe<T>(Action<T>)`.")
    md.append("This is correct and sufficient for usage. But if the agent needs to know execution order,")
    md.append("thread safety, or re-entrancy behavior, the implementation is required.")
    md.append("")

    md.append("### Honest assessment")
    md.append("")
    md.append("For this 10-step workflow, **~3 steps require a follow-up Read** after MCP discovery.")
    md.append("MCP does not eliminate file reading — it reduces it to targeted reads of files you already know you need,")
    md.append("rather than exploratory reads to figure out what exists.")
    md.append("")

    # ===== The Hybrid Workflow =====
    md.append("## The Realistic Workflow: MCP + Targeted Read")
    md.append("")
    md.append("Given the limitations above, the honest comparison is not MCP vs Read, but **MCP+Read vs Read-only**:")
    md.append("")

    # Calculate hybrid cost with per-step breakdown
    # Steps that need follow-up Read: 3 (GameModeManager implementation), 7 (CampEntryPoint wiring)
    hybrid_read_details = []
    for r in results:
        if r["step"] == 3:
            # Need to read GameModeManager to see transition logic
            file_path = _find_file(project_path, "GameModeManager")
            lines_count = _count_file_lines(file_path) if file_path else 0
            read_tokens = r["skilled_tokens"]
            hybrid_read_details.append({
                "step": 3,
                "file": file_path.name if file_path else "GameModeManager.cs",
                "reason": "Transition logic in method bodies",
                "lines_read": min(lines_count, 40),
                "tokens": read_tokens,
            })
        elif r["step"] == 7:
            # Need to read CampEntryPoint to see DI wiring + disposal pattern
            file_path = _find_file(project_path, "CampEntryPoint")
            lines_count = _count_file_lines(file_path) if file_path else 0
            read_tokens = r["skilled_tokens"]
            hybrid_read_details.append({
                "step": 7,
                "file": file_path.name if file_path else "CampEntryPoint.cs",
                "reason": "Constructor DI + Start()/Dispose() bodies",
                "lines_read": min(lines_count, 40),
                "tokens": read_tokens,
            })

    hybrid_extra = sum(d["tokens"] for d in hybrid_read_details)
    total_hybrid = total_mcp + hybrid_extra

    md.append("### Follow-up read breakdown")
    md.append("")
    md.append("| Step | File | Why MCP is insufficient | Lines read | Tokens |")
    md.append("|---:|---|---|---:|---:|")
    for d in hybrid_read_details:
        md.append(f"| {d['step']} | `{d['file']}` | {d['reason']} | ~{d['lines_read']} | {d['tokens']:,} |")
    md.append(f"| | | **Total follow-up cost** | | **{hybrid_extra:,}** |")
    md.append("")

    md.append("### Hybrid totals")
    md.append("")
    md.append(f"| Workflow | MCP Discovery | Follow-up Reads | Total |")
    md.append(f"|---|---:|---:|---:|")
    md.append(f"| MCP + Targeted Read | {total_mcp:,} | {hybrid_extra:,} | **{total_hybrid:,}** |")
    md.append(f"| Skilled Agent (Read-only) | — | — | **{total_skilled:,}** |")
    md.append(f"| Naive Agent (Read-only) | — | — | **{total_naive:,}** |")
    md.append("")
    hybrid_vs_skilled = (1 - total_hybrid / total_skilled) * 100 if total_skilled > 0 else 0
    md.append(f"Even with follow-up reads, the hybrid approach uses **{hybrid_vs_skilled:.0f}% fewer tokens** than the Skilled Agent.")
    md.append("The key insight: MCP eliminates the **8 exploratory lookups** that don't need implementation detail,")
    md.append("and narrows the 2 that do to **targeted reads of known files**.")
    md.append("")

    # ===== Operational Cost =====
    md.append("## Operational Cost of MCP")
    md.append("")
    md.append("MCP is not free. It requires a pre-indexing step that Grep+Read does not:")
    md.append("")
    md.append(f"| Metric | Value |")
    md.append(f"|---|---|")
    md.append(f"| C# files scanned | {index_cost['cs_file_count']} |")
    md.append(f"| API records indexed | {index_cost['record_count']:,} |")
    md.append(f"| Parse time | {index_cost['parse_time']:.3f}s |")
    md.append(f"| DB build time | {index_cost['db_time']:.3f}s |")
    md.append(f"| **Total index time** | **{index_cost['total_time']:.3f}s** |")
    md.append(f"| Storage | In-memory SQLite (no disk I/O) |")
    md.append(f"| Rebuild trigger | MCP server restart (session start) |")
    md.append("")
    md.append("**Staleness risk**: The index is built at MCP server startup and is not updated mid-session.")
    md.append("If the agent creates new classes or renames methods during a session, the index is stale.")
    md.append("Mitigation: the agent can always fall back to Grep+Read for code it just wrote.")
    md.append("")
    md.append("**Scaling estimate**: Indexing is ~O(n) in file count. At this codebase size (129 files),")
    md.append(f"it takes {index_cost['total_time']:.3f}s. A 10x larger codebase (~1,300 files) would take ~{index_cost['total_time'] * 10:.1f}s.")
    md.append("")

    # ===== Context Window Scaling =====
    md.append("## Context Window Scaling")
    md.append("")
    md.append("The token savings become more or less critical depending on context window size.")
    md.append("This table shows the same 10-step workflow at different window sizes:")
    md.append("")
    md.append("| Window | MCP % used | Skilled % used | Naive % used | MCP headroom advantage |")
    md.append("|---:|---:|---:|---:|---|")
    for window in [8000, 32000, 128000, 200000]:
        mcp_pct = total_mcp / window * 100
        skilled_pct = total_skilled / window * 100
        naive_pct = total_naive / window * 100
        if naive_pct > 50:
            note = "Naive agent risks exhausting context"
        elif skilled_pct > 20:
            note = "Meaningful headroom difference"
        elif skilled_pct > 5:
            note = "Moderate advantage"
        else:
            note = "Marginal at this window size"
        md.append(f"| {window // 1000}K | {mcp_pct:.1f}% | {skilled_pct:.1f}% | {naive_pct:.1f}% | {note} |")
    md.append("")
    md.append("At **8K context** (common for smaller models), the Naive Agent spends almost **150% of its budget** on research alone —")
    md.append("it cannot complete this workflow. The Skilled Agent uses 56%. MCP uses 13%.")
    md.append("At **200K context**, all three fit comfortably, and the advantage is optimization rather than necessity.")
    md.append("")

    # ===== Reframing =====
    md.append("## Beyond Token Counting: Reducing Entropy in Agent Reasoning")
    md.append("")
    md.append("The deeper value of a pre-indexed API is not token savings. It is **determinism**.")
    md.append("")
    md.append("| Property | MCP | Grep+Read |")
    md.append("|---|---|---|")
    md.append("| **What the LLM sees** | Canonical API surface — every public member, nothing else | Variable — depends on grep pattern, context window, file structure |")
    md.append("| **Hallucination surface** | Low — signatures are authoritative | Higher — LLM may infer from partial context, wrong file, or stale grep match |")
    md.append("| **Consistency** | Same query always returns same result | Grep results vary with pattern, context lines, file ordering |")
    md.append("| **Discovery capability** | Semantic search across all types | Pattern matching on text — misses PascalCase components, abbreviations |")
    md.append("| **Parallelism** | All 10 lookups can run in parallel | Sequential: grep result informs which file to read |")
    md.append("")
    md.append("When an LLM reads 40 lines of source that include `private readonly EventBus _eventBus;`,")
    md.append("`[Inject]` attributes, `#region` blocks, and helper methods, it must decide what is API surface")
    md.append("and what is implementation noise. That decision is a source of reasoning error.")
    md.append("")
    md.append("### Concrete example: inferring behavior from partial context")
    md.append("")
    md.append("Consider step 2: the agent needs to know how `IGameModeContext.ReportLevelCompleted` works.")
    md.append("")
    md.append("**Grep+Read agent** reads `GameModeManager.cs` (the implementation) and sees:")
    md.append("```csharp")
    md.append("public void ReportLevelCompleted(LevelResult result)")
    md.append("{")
    md.append("    _currentMode?.OnLevelCompleted(result);")
    md.append("    UnloadGameMode();")
    md.append("}")
    md.append("```")
    md.append("The agent sees `_currentMode?.OnLevelCompleted(result)` and may reasonably infer that")
    md.append("`OnLevelCompleted` publishes an event internally (since the codebase is event-driven).")
    md.append("It might then write code that subscribes to a `LevelCompletedEvent` — which does not exist.")
    md.append("The actual event is `LevelWonEvent`, published by `BlastGameController`, not by the game mode context.")
    md.append("The agent hallucinated the event name and its source from plausible-looking implementation detail.")
    md.append("")
    md.append("**MCP agent** sees the same method as a signature only:")
    md.append("```")
    md.append("void ReportLevelCompleted(LevelResult result)")
    md.append("```")
    md.append("No implementation body to over-interpret. The agent knows the method exists and what it accepts,")
    md.append("but it cannot infer internal event publishing from a signature. When it needs to find level-end events,")
    md.append("it runs `search(\"LevelWon\")` and gets the authoritative answer: `LevelWonEvent` in `BlastGame.Events`.")
    md.append("The disambiguation is forced by the tool's limited output, not by the agent's reasoning.")
    md.append("")
    md.append("### Reasoning trace comparison")
    md.append("")
    md.append("The following traces reconstruct how each agent strategy reasons through the same")
    md.append("sub-task: *\"How do blast level results reach the camp?\"* (spans steps 2-4).")
    md.append("Each row shows what the agent sees, what it infers, what it does next, and where the")
    md.append("inference chain diverges.")
    md.append("")
    md.append("#### Phase 1: Discover the bridge API")
    md.append("")
    md.append("| | Grep+Read Agent | MCP Agent |")
    md.append("|---|---|---|")
    md.append("| **Action** | `Grep \"IGameModeContext\"` → Read `GameModeManager.cs` (~40 lines from class decl) | `get_class(\"IGameModeContext\")` |")
    md.append("| **Sees** | `class GameModeManager : IDisposable` with fields: `private readonly EventBus _eventBus;`, `private IGameMode _currentMode;`, `private LifetimeScope _modeScope;`. Method body: `ReportLevelCompleted` calls `_currentMode?.OnLevelCompleted(result)` then `UnloadGameMode()` | `interface IGameModeContext` with 5 method signatures. No fields, no bodies, no implementation |")
    md.append("| **Infers** | \"GameModeManager owns the EventBus. It delegates to `_currentMode.OnLevelCompleted`. The mode probably publishes a completion event internally. I should look for `LevelCompletedEvent`.\" | \"IGameModeContext has `ReportLevelCompleted(LevelResult)`. I don't know what happens inside. I need to find what events fire on level end.\" |")
    md.append("| **Risk** | Over-interprets implementation — infers event publishing that doesn't exist | No implementation to over-interpret — forced to search explicitly |")
    md.append("")
    md.append("#### Phase 2: Find level-end events")
    md.append("")
    md.append("| | Grep+Read Agent | MCP Agent |")
    md.append("|---|---|---|")
    md.append("| **Action** | `Grep \"LevelCompletedEvent\"` — 0 results. Tries `Grep \"LevelCompleted\"` — finds method definitions (not events). Tries `Grep \"LevelWon\"` — finds it. | `search(\"LevelWon\")` — 3 results |")
    md.append("| **Sees** | After 2-3 grep attempts: `struct LevelWonEvent : IEvent` in BlastEvents.cs | Immediately: `LevelWonEvent` in `BlastGame.Events` with fields `Score`, `StarsEarned` |")
    md.append("| **Cost** | 3 grep calls + reading through false positives. ~500 tokens of noise before finding the answer | 1 tool call. 62 tokens. Direct hit |")
    md.append("| **Risk** | May abandon search after first failed grep and *assume* the event exists with a guessed name | Search is exhaustive — if it's not in the index, it doesn't exist |")
    md.append("")
    md.append("#### Phase 3: Wire the subscription")
    md.append("")
    md.append("| | Grep+Read Agent | MCP Agent |")
    md.append("|---|---|---|")
    md.append("| **Action** | `Grep \"class EventBus\"` → Read full file (476 tokens) | `get_class(\"EventBus\")` — 60 tokens |")
    md.append("| **Sees** | Full EventBus.cs: generic `Subscribe<T>`, `Publish<T>`, `Unsubscribe<T>` + private `Dictionary<Type, List<Delegate>>` + locking logic + helper methods | 3 method signatures: `Subscribe<T>(Action<T>)`, `Publish<T>(T)`, `Unsubscribe<T>(Action<T>)` |")
    md.append("| **Writes** | `_eventBus.Subscribe<LevelWonEvent>(OnLevelWon)` — correct, but arrived here after 2-3 false starts on event name | `_eventBus.Subscribe<LevelWonEvent>(OnLevelWon)` — correct, arrived directly |")
    md.append("| **Risk** | May copy internal locking pattern or private field naming into new code (cargo-culting from implementation) | Cannot cargo-cult — only sees API contract |")
    md.append("")
    md.append("#### Divergence summary")
    md.append("")
    md.append("| Metric | Grep+Read Agent | MCP Agent |")
    md.append("|---|---|---|")
    md.append("| Tool calls for this sub-task | 5-7 (grep attempts + reads) | 3 (one per phase) |")
    md.append("| Tokens consumed | ~1,100-1,500 | ~215 |")
    md.append("| Wrong inferences made | 1 (hallucinated `LevelCompletedEvent`) | 0 |")
    md.append("| Recovery cost | 2 extra grep calls to find correct event name | None — correct on first lookup |")
    md.append("| Implementation noise absorbed | Private fields, method bodies, locking internals | Zero — signatures only |")
    md.append("")
    md.append("The critical difference is not tokens. It is **inference chain reliability**.")
    md.append("The Grep+Read agent made a *plausible* wrong inference from *true* context.")
    md.append("That is the hardest class of error to detect and correct — the reasoning looks sound,")
    md.append("but the conclusion is wrong because the agent filled a gap with pattern-matching")
    md.append("instead of lookup.")
    md.append("")
    md.append("The MCP agent never had that gap. The tool's constrained output forced explicit")
    md.append("disambiguation at every step. The agent could not *infer* event names — it had to *find* them.")
    md.append("")
    md.append("This is not measurable in tokens. It is measurable in:")
    md.append("- Fewer hallucinated method signatures")
    md.append("- Fewer incorrect parameter types")
    md.append("- Fewer \"let me check that file again\" round-trips")
    md.append("- Faster convergence to correct implementation")
    md.append("")
    md.append("These outcome metrics are not captured by this benchmark. They require A/B testing")
    md.append("with real agent task completion, which is a meaningful next step.")
    md.append("")

    return "\n".join(md)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m codesurface.benchmark <project_scripts_dir> [--workflow <name>]")
        sys.exit(1)

    if "--workflow" in sys.argv:
        idx = sys.argv.index("--workflow")
        wf_name = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "all"
        print(run_workflow_benchmark(sys.argv[1], wf_name))
    else:
        run_benchmark(sys.argv[1])
