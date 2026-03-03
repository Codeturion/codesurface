"""In-memory SQLite + FTS5 database for project API records."""

import json
import re
import sqlite3

# ---------------------------------------------------------------------------
# Identifier splitter (PascalCase, camelCase, snake_case, mixed)
# ---------------------------------------------------------------------------

_PASCAL_SPLIT_RE = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])"   # camelCase boundary: aB → a B
    r"|(?<=[A-Z])(?=[A-Z][a-z])"  # acronym boundary: ABc → A Bc
)


def split_identifier(name: str) -> str:
    """Split any C#/code identifier into component words.

    Examples:
        CampBuildingService   → Camp Building Service
        ICampGridService      → I Camp Grid Service
        BFSFlood              → BFS Flood
        my_variable           → my variable
        MAX_HEALTH            → MAX HEALTH
        m_playerHealth        → m player Health
        kMaxRetries_perNode   → k Max Retries per Node
    """
    s = name.replace("_", " ")
    s = _PASCAL_SPLIT_RE.sub(" ", s)
    return " ".join(s.split())


def _build_search_text(record: dict) -> str:
    """Build searchable text by splitting PascalCase identifiers into words."""
    tokens = []
    for field in ("class_name", "member_name"):
        val = record.get(field, "")
        if val:
            tokens.append(split_identifier(val))
    # Last namespace segment (e.g. "Services" from "CampGame.Services",
    # or "Utils" from "MyLib::Utils")
    ns = record.get("namespace", "")
    if ns:
        last_part = re.split(r"[.:]", ns)[-1]
        tokens.append(split_identifier(last_part))
    return " ".join(tokens)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_records (
    fqn TEXT PRIMARY KEY,
    namespace TEXT NOT NULL DEFAULT '',
    class_name TEXT NOT NULL DEFAULT '',
    member_name TEXT NOT NULL DEFAULT '',
    member_type TEXT NOT NULL,
    signature TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    params_json TEXT NOT NULL DEFAULT '[]',
    returns_text TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL DEFAULT '',
    line_start INTEGER NOT NULL DEFAULT 0,
    line_end INTEGER NOT NULL DEFAULT 0,
    search_text TEXT NOT NULL DEFAULT ''
);

CREATE VIRTUAL TABLE IF NOT EXISTS api_fts USING fts5(
    fqn,
    class_name,
    member_name,
    summary,
    signature,
    search_text,
    content='api_records',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS api_records_ai AFTER INSERT ON api_records BEGIN
    INSERT INTO api_fts(rowid, fqn, class_name, member_name, summary, signature, search_text)
    VALUES (new.rowid, new.fqn, new.class_name, new.member_name, new.summary, new.signature, new.search_text);
END;

CREATE TRIGGER IF NOT EXISTS api_records_ad AFTER DELETE ON api_records BEGIN
    INSERT INTO api_fts(api_fts, rowid, fqn, class_name, member_name, summary, signature, search_text)
    VALUES ('delete', old.rowid, old.fqn, old.class_name, old.member_name, old.summary, old.signature, old.search_text);
END;
"""


def create_memory_db(records: list[dict]) -> sqlite3.Connection:
    """Create an in-memory SQLite DB and populate it with records."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    if records:
        insert_records(conn, records)
    return conn


def insert_records(conn: sqlite3.Connection, records: list[dict]) -> int:
    """Bulk-insert parsed records. Returns count inserted."""
    sql = """
        INSERT OR REPLACE INTO api_records
        (fqn, namespace, class_name, member_name, member_type,
         signature, summary, params_json, returns_text, file_path,
         line_start, line_end, search_text)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    rows = [
        (
            r["fqn"],
            r["namespace"],
            r["class_name"],
            r["member_name"],
            r["member_type"],
            r.get("signature", ""),
            r.get("summary", ""),
            json.dumps(r.get("params_json", [])),
            r.get("returns_text", ""),
            r.get("file_path", ""),
            r.get("line_start", 0),
            r.get("line_end", 0),
            _build_search_text(r),
        )
        for r in records
    ]
    conn.executemany(sql, rows)
    conn.commit()
    return len(rows)


def delete_by_files(conn: sqlite3.Connection, file_paths: list[str]) -> int:
    """Delete all records belonging to the given file paths. Returns count deleted."""
    if not file_paths:
        return 0
    placeholders = ",".join("?" for _ in file_paths)
    cursor = conn.execute(
        f"DELETE FROM api_records WHERE file_path IN ({placeholders})",
        file_paths,
    )
    conn.commit()
    return cursor.rowcount


def search(conn: sqlite3.Connection, query: str, n: int = 10,
           member_type: str | None = None) -> list[dict]:
    """Full-text search with BM25 ranking + PascalCase-aware matching.

    Column weights: member_name (10x) > class_name (5x) > search_text (4x) > signature (3x) > fqn/summary (1x)
    Type bonus: class/struct/enum defs rank higher than same-named members.
    """
    clean = _escape_fts(query)
    if not clean.strip():
        return []

    # BM25 column order: fqn, class_name, member_name, summary, signature, search_text
    ranking = """bm25(api_fts, 1.0, 5.0, 10.0, 0.5, 3.0, 4.0)
                + CASE WHEN r.member_type = 'type' THEN -1.0 ELSE 0.0 END"""

    if member_type:
        sql = f"""
            SELECT r.*, {ranking} AS rank
            FROM api_fts f
            JOIN api_records r ON r.rowid = f.rowid
            WHERE api_fts MATCH ? AND r.member_type = ?
            ORDER BY rank
            LIMIT ?
        """
        rows = conn.execute(sql, (clean, member_type, n)).fetchall()
    else:
        sql = f"""
            SELECT r.*, {ranking} AS rank
            FROM api_fts f
            JOIN api_records r ON r.rowid = f.rowid
            WHERE api_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """
        rows = conn.execute(sql, (clean, n)).fetchall()

    return [dict(row) for row in rows]


def get_by_fqn(conn: sqlite3.Connection, fqn: str) -> dict | None:
    """Exact FQN lookup."""
    row = conn.execute(
        "SELECT * FROM api_records WHERE fqn = ?", (fqn,)
    ).fetchone()
    return dict(row) if row else None


def get_class_members(conn: sqlite3.Connection, class_name: str,
                      namespace: str | None = None) -> list[dict]:
    """Get all members of a class by class name, optionally filtered by namespace."""
    if namespace is not None:
        rows = conn.execute(
            "SELECT * FROM api_records WHERE class_name = ? AND namespace = ? "
            "ORDER BY member_type, member_name",
            (class_name, namespace),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM api_records WHERE class_name = ? ORDER BY member_type, member_name",
            (class_name,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_class_namespaces(conn: sqlite3.Connection, class_name: str) -> list[str]:
    """Get all distinct namespaces that contain a class with this name."""
    rows = conn.execute(
        "SELECT DISTINCT namespace FROM api_records "
        "WHERE class_name = ? AND member_type = 'type' "
        "ORDER BY namespace",
        (class_name,),
    ).fetchall()
    return [row["namespace"] for row in rows]


def resolve_namespace(conn: sqlite3.Connection, name: str) -> list[dict]:
    """Find namespace for a class or member name."""
    rows = conn.execute(
        "SELECT DISTINCT namespace, class_name, member_type, fqn FROM api_records "
        "WHERE class_name = ? AND member_type = 'type' "
        "ORDER BY namespace",
        (name,),
    ).fetchall()
    if rows:
        return [dict(row) for row in rows]

    rows = conn.execute(
        "SELECT DISTINCT namespace, class_name, member_name, member_type, fqn FROM api_records "
        "WHERE member_name = ? "
        "ORDER BY namespace, class_name",
        (name,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_stats(conn: sqlite3.Connection) -> dict:
    """Return record counts by type."""
    rows = conn.execute(
        "SELECT member_type, COUNT(*) as cnt FROM api_records GROUP BY member_type"
    ).fetchall()
    stats = {row["member_type"]: row["cnt"] for row in rows}
    stats["total"] = sum(stats.values())

    file_count = conn.execute(
        "SELECT COUNT(DISTINCT file_path) FROM api_records WHERE file_path != ''"
    ).fetchone()[0]
    stats["files"] = file_count

    return stats


def _escape_fts(query: str) -> str:
    """Build an FTS5 query with prefix matching and PascalCase awareness.

    Strategy:
      1. Clean special characters
      2. Build original query with prefix on last term (handles exact + partial names)
      3. Split PascalCase and OR with original (handles component-word matches)

    Examples:
      "Building"            → Building*
      "CampBuildingService" → (CampBuildingService*) OR (Camp Building Service*)
      "BuildingService"     → (BuildingService*) OR (Building Service*)
      "Spawn item"          → Spawn item*
      "ICommand"            → (ICommand*) OR (I Command*)
    """
    q = query
    for ch in '."-*():,;{}[]!@#$%^&+|\\~`':
        q = q.replace(ch, " ")
    terms = [t for t in q.split() if t]
    if not terms:
        return ""

    # Original query with prefix on last term
    orig = list(terms)
    orig[-1] += "*"
    original_query = " ".join(orig)

    # Split each term by PascalCase
    split_terms = []
    for term in terms:
        split_terms.extend(split_identifier(term).split())

    # If splitting produced different tokens, OR both forms
    if split_terms != terms:
        split_copy = list(split_terms)
        split_copy[-1] += "*"
        split_query = " ".join(split_copy)
        return f"({original_query}) OR ({split_query})"

    return original_query
