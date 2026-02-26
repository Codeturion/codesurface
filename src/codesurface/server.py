"""MCP server that indexes a codebase's public API on startup."""

import argparse
import json
import sys
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import db
from .parsers import all_extensions, detect_languages, get_parser, get_parsers_for_project

mcp = FastMCP(
    "codesurface",
    instructions=(
        "Codebase API server. Use these tools to look up classes, methods, "
        "properties, and fields instead of reading source files."
    ),
)

_conn = None
_project_path: Path | None = None
_file_mtimes: dict[str, float] = {}  # rel_path → mtime


def _index_full(project_path: Path, language: str | None = None) -> str:
    """Full parse + rebuild. Used on startup."""
    global _conn, _file_mtimes
    t0 = time.perf_counter()

    if language:
        parsers = [get_parser(language)]
    else:
        parsers = get_parsers_for_project(project_path)

    if not parsers:
        return "No supported source files detected in project directory."

    records = []
    for parser in parsers:
        records.extend(parser.parse_directory(project_path))
    parse_time = time.perf_counter() - t0

    t1 = time.perf_counter()
    _conn = db.create_memory_db(records)
    db_time = time.perf_counter() - t1

    # Snapshot mtimes for all registered extensions used
    extensions = set()
    for parser in parsers:
        extensions.update(parser.file_extensions)

    _file_mtimes = {}
    for ext in extensions:
        for f in sorted(project_path.rglob(f"*{ext}")):
            rel = str(f.relative_to(project_path)).replace("\\", "/")
            try:
                _file_mtimes[rel] = f.stat().st_mtime
            except OSError:
                pass

    stats = db.get_stats(_conn)
    langs = ", ".join(type(p).__name__.replace("Parser", "") for p in parsers)
    return (
        f"Indexed {stats['total']} records from {stats.get('files', 0)} files "
        f"({langs}) in {parse_time + db_time:.2f}s "
        f"(parse: {parse_time:.2f}s, db: {db_time:.2f}s)"
    )


def _index_incremental(project_path: Path) -> str:
    """Re-parse only changed/new/deleted files. Updates existing DB in-place."""
    global _file_mtimes
    if _conn is None:
        return _index_full(project_path)

    t0 = time.perf_counter()

    # Collect all registered extensions
    extensions = set(all_extensions())

    # Scan current files
    current: dict[str, float] = {}
    for ext in extensions:
        for f in sorted(project_path.rglob(f"*{ext}")):
            rel = str(f.relative_to(project_path)).replace("\\", "/")
            try:
                current[rel] = f.stat().st_mtime
            except OSError:
                pass

    old_keys = set(_file_mtimes)
    new_keys = set(current)

    deleted = old_keys - new_keys
    added = new_keys - old_keys
    changed = {k for k in old_keys & new_keys if current[k] != _file_mtimes[k]}

    dirty = added | changed
    stale = deleted | changed  # records from these files need removal

    if not dirty and not stale:
        elapsed = time.perf_counter() - t0
        stats = db.get_stats(_conn)
        return (
            f"No changes detected ({len(current)} files scanned in {elapsed:.3f}s). "
            f"Index: {stats['total']} records"
        )

    # Remove stale records
    if stale:
        db.delete_by_files(_conn, list(stale))

    # Build extension-to-parser map for dirty files
    parsers = get_parsers_for_project(project_path)
    ext_to_parser: dict[str, object] = {}
    for parser in parsers:
        for ext in parser.file_extensions:
            ext_to_parser[ext] = parser

    # Parse dirty files
    new_records = []
    for rel in sorted(dirty):
        full_path = project_path / rel.replace("/", "\\")
        suffix = full_path.suffix
        parser = ext_to_parser.get(suffix)
        if parser is None:
            continue
        try:
            file_records = parser.parse_file(full_path, project_path)
            new_records.extend(file_records)
        except Exception:
            pass

    if new_records:
        db.insert_records(_conn, new_records)

    # Update snapshot
    _file_mtimes = current

    elapsed = time.perf_counter() - t0
    stats = db.get_stats(_conn)
    parts = [f"Incremental reindex in {elapsed:.3f}s:"]
    if added:
        parts.append(f"  added {len(added)} file(s)")
    if changed:
        parts.append(f"  updated {len(changed)} file(s)")
    if deleted:
        parts.append(f"  removed {len(deleted)} file(s)")
    parts.append(f"  parsed {len(new_records)} records from {len(dirty)} file(s)")
    parts.append(f"  index total: {stats['total']} records from {stats.get('files', 0)} files")
    return "\n".join(parts)


def _format_record(r: dict) -> str:
    """Format a single API record into readable text."""
    lines = []

    type_label = r.get("member_type", "").upper()
    fqn = r.get("fqn", "")
    lines.append(f"[{type_label}] {fqn}")

    ns = r.get("namespace", "")
    if ns:
        lines.append(f"  Namespace: {ns}")

    cls = r.get("class_name", "")
    if cls and r.get("member_type") != "type":
        lines.append(f"  Class: {cls}")

    sig = r.get("signature", "")
    if sig:
        lines.append(f"  Signature: {sig}")

    summary = r.get("summary", "")
    if summary:
        lines.append(f"  Summary: {summary}")

    params_raw = r.get("params_json", "[]")
    if isinstance(params_raw, str):
        params = json.loads(params_raw)
    else:
        params = params_raw
    if params:
        lines.append("  Parameters:")
        for p in params:
            lines.append(f"    - {p['name']}: {p.get('description', '')}")

    returns = r.get("returns_text", "")
    if returns:
        lines.append(f"  Returns: {returns}")

    fp = r.get("file_path", "")
    if fp:
        lines.append(f"  File: {fp}")

    return "\n".join(lines)


@mcp.tool()
def search(
    query: str,
    n_results: int = 5,
    member_type: str | None = None,
) -> str:
    """Search the indexed API by keyword.

    Find classes, methods, properties, fields, and events.
    Returns ranked results with signatures.

    Args:
        query: Search terms (e.g. "MergeService", "BlastBoard", "GridCoord")
        n_results: Max results to return (default 5, max 20)
        member_type: Optional filter — "type", "method", "property", "field", or "event"
    """
    if _conn is None:
        return "No codebase indexed. Start the server with --project <path>."

    n_results = min(max(n_results, 1), 20)
    results = db.search(_conn, query, n=n_results, member_type=member_type)

    if not results:
        return f"No results found for '{query}'. Try broader search terms."

    parts = [f"Found {len(results)} result(s) for '{query}':\n"]
    for i, r in enumerate(results, 1):
        parts.append(f"--- Result {i} ---")
        parts.append(_format_record(r))
        parts.append("")

    return "\n".join(parts)


@mcp.tool()
def get_signature(name: str) -> str:
    """Look up the exact signature of an API member by name or FQN.

    Use when you need exact parameter types, return types, or method signatures
    without reading the full source file.

    Args:
        name: Member name or FQN, e.g. "TryMerge", "CampGame.Services.IMergeService.TryMerge"
    """
    if _conn is None:
        return "No codebase indexed. Start the server with --project <path>."

    # 1. Exact FQN match
    record = db.get_by_fqn(_conn, name)
    if record:
        return _format_record(record)

    # 2. Prefix match (overloads or partial FQN)
    rows = _conn.execute(
        "SELECT * FROM api_records WHERE fqn LIKE ? ORDER BY fqn",
        (f"%{name}%",),
    ).fetchall()
    if rows:
        parts = [f"Found {len(rows)} match(es) for '{name}':\n"]
        for r in rows[:10]:
            parts.append(_format_record(dict(r)))
            parts.append("")
        if len(rows) > 10:
            parts.append(f"... and {len(rows) - 10} more")
        return "\n".join(parts)

    # 3. FTS fallback
    results = db.search(_conn, name, n=5)
    if results:
        parts = [f"No exact match for '{name}'. Did you mean:\n"]
        for r in results:
            parts.append(_format_record(r))
            parts.append("")
        return "\n".join(parts)

    return f"No results found for '{name}'."


@mcp.tool()
def get_class(class_name: str) -> str:
    """Get a complete reference card for a class — all public members.

    Shows every method, property, field, and event with signatures.
    Replaces reading the entire source file.

    Args:
        class_name: Class name, e.g. "BlastBoardModel", "IMergeService", "CampGridService"
    """
    if _conn is None:
        return "No codebase indexed. Start the server with --project <path>."

    short_name = class_name.rsplit(".", 1)[-1]
    members = db.get_class_members(_conn, short_name)

    if not members:
        results = db.search(_conn, class_name, n=5, member_type="type")
        if results:
            parts = [f"No class '{class_name}' found. Did you mean:\n"]
            for r in results:
                parts.append(f"  {r['fqn']} — {r.get('signature', '')}")
            return "\n".join(parts)
        return f"No class '{class_name}' found."

    type_record = next((m for m in members if m["member_type"] == "type"), None)
    ns = type_record["namespace"] if type_record else members[0].get("namespace", "")

    parts = [f"Class: {short_name}"]
    if ns:
        parts.append(f"Namespace: {ns}")
    if type_record:
        sig = type_record.get("signature", "")
        if sig:
            parts.append(f"Declaration: {sig}")
        summary = type_record.get("summary", "")
        if summary:
            parts.append(f"Summary: {summary}")
        fp = type_record.get("file_path", "")
        if fp:
            parts.append(f"File: {fp}")
    parts.append("")

    groups: dict[str, list[dict]] = {}
    for m in members:
        if m["member_type"] == "type":
            continue
        # Separate constructors from regular methods
        if m["member_type"] == "method" and m.get("member_name") == short_name:
            groups.setdefault("constructor", []).append(m)
        else:
            groups.setdefault(m["member_type"], []).append(m)

    for mtype in ("constructor", "method", "property", "field", "event"):
        group = groups.get(mtype, [])
        if not group:
            continue
        label = mtype.upper() + "S"
        if mtype == "constructor":
            label = "CONSTRUCTORS" if len(group) > 1 else "CONSTRUCTOR"
        parts.append(f"-- {label} ({len(group)}) --")
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


@mcp.tool()
def get_stats() -> str:
    """Get a quick overview of the indexed codebase.

    Shows file count, record counts by type, and namespace breakdown.
    """
    if _conn is None:
        return "No codebase indexed. Start the server with --project <path>."

    stats = db.get_stats(_conn)

    parts = [
        "Project API Index Stats:",
        f"  Files indexed: {stats.get('files', 0)}",
        f"  Total records: {stats.get('total', 0)}",
        "",
        "  By type:",
    ]
    for mtype in ("type", "method", "property", "field", "event"):
        count = stats.get(mtype, 0)
        if count:
            parts.append(f"    {mtype}: {count}")

    # Top namespaces
    rows = _conn.execute(
        "SELECT namespace, COUNT(*) as cnt FROM api_records "
        "WHERE namespace != '' GROUP BY namespace ORDER BY cnt DESC LIMIT 10"
    ).fetchall()
    if rows:
        parts.append("")
        parts.append("  Top namespaces:")
        for r in rows:
            parts.append(f"    {r['namespace']}: {r['cnt']} records")

    return "\n".join(parts)


@mcp.tool()
def reindex() -> str:
    """Incrementally update the index by re-parsing only changed, new, or deleted files.

    Uses file modification times to detect changes. Fast on large codebases —
    only touches files that actually changed since the last index.
    """
    if _project_path is None:
        return "No project path configured. Start the server with --project <path>."
    if not _project_path.is_dir():
        return f"Project path not found: {_project_path}"

    return _index_incremental(_project_path)


def main():
    """Entry point for the MCP server."""
    parser = argparse.ArgumentParser(description="codesurface MCP server")
    parser.add_argument("--project", default=None,
                        help="Path to source directory to index")
    parser.add_argument("--language", default=None,
                        help="Language to parse (e.g. csharp). Auto-detected if omitted.")
    args, remaining = parser.parse_known_args()

    global _project_path

    if args.project:
        _project_path = Path(args.project)
        if not _project_path.is_dir():
            print(f"Warning: Project path not found: {args.project}", file=sys.stderr)
        else:
            summary = _index_full(_project_path, language=args.language)
            print(summary, file=sys.stderr)

    mcp.run()


if __name__ == "__main__":
    main()
