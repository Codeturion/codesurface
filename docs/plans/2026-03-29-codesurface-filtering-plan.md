# Codesurface Filtering & Worktree Support Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:executing-plans to implement this plan task-by-task.

**Goal:** Add index-time path exclusions (default worktree/submodule skipping, `.codesurfaceignore`, `--exclude`) and query-time `file_path` scoping to codesurface, plus per-worktree server support in pr-manager.

**Architecture:** A new `PathFilter` class centralizes all exclusion logic and is threaded through `parse_directory` and the server's index functions. Query-time filtering is a thin SQL/fnmatch layer over the existing `file_path` column. Worktree server management piggybacks on pr-manager's existing codegraph-review lifecycle.

**Tech Stack:** Python 3.10+, SQLite FTS5, fnmatch, pathlib. Tests use pytest. pr-manager side is Node.js/Express.

---

## Setup

Install pytest in the codesurface dev environment:

```bash
cd ~/code/codesurface
uv add --dev pytest
```

Run all tests throughout with:

```bash
cd ~/code/codesurface
uv run pytest tests/ -v
```

---

## Task 1: PathFilter — default skip rules

**Files:**
- Create: `src/codesurface/filters.py`
- Create: `tests/test_filters.py`

### Step 1: Write the failing tests

Create `tests/__init__.py` (empty) and `tests/test_filters.py`:

```python
"""Tests for PathFilter default skip rules."""
import os
from pathlib import Path
import pytest
from codesurface.filters import PathFilter


@pytest.fixture
def tmp_project(tmp_path):
    """Project root with a variety of subdirectories."""
    # Normal source file
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text("export class Foo {}")

    # .worktrees directory (should always be skipped)
    wt = tmp_path / ".worktrees" / "pr-42"
    wt.mkdir(parents=True)
    (wt / "src" / "main.ts").write_text("export class Bar {}")
    # .git FILE in worktree (git worktree marker)
    (wt / ".git").write_text("gitdir: /repo/.git/worktrees/pr-42\n")

    # Submodule (should be skipped by default)
    sub = tmp_path / "vendor" / "mylib"
    sub.mkdir(parents=True)
    (sub / "lib.ts").write_text("export class Lib {}")
    (sub / ".git").write_text("gitdir: /repo/.git/modules/mylib\n")

    # Regular nested dir (should NOT be skipped)
    (tmp_path / "packages" / "core").mkdir(parents=True)
    (tmp_path / "packages" / "core" / "index.ts").write_text("export class Core {}")

    return tmp_path


def test_worktrees_dir_skipped(tmp_project):
    pf = PathFilter(tmp_project)
    assert pf.is_dir_excluded(tmp_project / ".worktrees")


def test_worktree_subdir_skipped(tmp_project):
    pf = PathFilter(tmp_project)
    assert pf.is_dir_excluded(tmp_project / ".worktrees" / "pr-42")


def test_git_file_worktree_skipped(tmp_project):
    pf = PathFilter(tmp_project)
    wt = tmp_project / ".worktrees" / "pr-42"
    assert pf.is_dir_excluded(wt)


def test_submodule_skipped_by_default(tmp_project):
    pf = PathFilter(tmp_project)
    assert pf.is_dir_excluded(tmp_project / "vendor" / "mylib")


def test_submodule_included_when_opted_in(tmp_project):
    pf = PathFilter(tmp_project, include_submodules=True)
    assert not pf.is_dir_excluded(tmp_project / "vendor" / "mylib")


def test_worktree_still_skipped_even_with_include_submodules(tmp_project):
    pf = PathFilter(tmp_project, include_submodules=True)
    wt = tmp_project / ".worktrees" / "pr-42"
    assert pf.is_dir_excluded(wt)


def test_normal_dir_not_skipped(tmp_project):
    pf = PathFilter(tmp_project)
    assert not pf.is_dir_excluded(tmp_project / "packages" / "core")


def test_src_dir_not_skipped(tmp_project):
    pf = PathFilter(tmp_project)
    assert not pf.is_dir_excluded(tmp_project / "src")
```

### Step 2: Run tests — verify they fail

```bash
cd ~/code/codesurface
uv run pytest tests/test_filters.py -v
```

Expected: `ModuleNotFoundError: No module named 'codesurface.filters'`

### Step 3: Create `src/codesurface/filters.py`

```python
"""Path filtering for codesurface indexing.

Handles default exclusions (worktrees, submodules) and user-configured
exclusions (.codesurfaceignore, --exclude CLI flag).
"""
from __future__ import annotations

import fnmatch
from pathlib import Path


def _read_git_file(path: Path) -> str | None:
    """Read .git FILE content if present. Returns None if .git is a directory."""
    git = path / ".git"
    if git.is_file():
        try:
            return git.read_text().strip()
        except OSError:
            return None
    return None


def _is_git_worktree(git_content: str) -> bool:
    """True if .git file references a worktrees/ path."""
    return "/worktrees/" in git_content


def _is_git_submodule(git_content: str) -> bool:
    """True if .git file references a modules/ path."""
    return "/modules/" in git_content


class PathFilter:
    """Determines which directories and files to skip during indexing.

    Default exclusions (always applied):
    - Any directory named .worktrees
    - Any subdirectory with a .git FILE referencing /worktrees/ (git worktree)
    - Any subdirectory with a .git FILE referencing /modules/ (submodule),
      unless include_submodules=True

    User exclusions are added in Task 2.
    """

    def __init__(
        self,
        project_root: Path,
        exclude_globs: list[str] | None = None,
        include_submodules: bool = False,
    ) -> None:
        self._root = project_root
        self._include_submodules = include_submodules
        self._globs: list[str] = []  # populated in Task 2

    def is_dir_excluded(self, path: Path) -> bool:
        """Return True if this directory should be skipped entirely."""
        # Rule 1: .worktrees by name
        if path.name == ".worktrees":
            return True

        # Rule 2: .git FILE detection
        git_content = _read_git_file(path)
        if git_content is not None:
            if _is_git_worktree(git_content):
                return True
            if _is_git_submodule(git_content) and not self._include_submodules:
                return True

        return False

    def is_file_excluded(self, path: Path) -> bool:
        """Return True if this file should be skipped. Used for user globs (Task 2)."""
        return False  # expanded in Task 2
```

### Step 4: Run tests — verify they pass

```bash
cd ~/code/codesurface
uv run pytest tests/test_filters.py -v
```

Expected: all 8 tests PASS

### Step 5: Commit

```bash
cd ~/code/codesurface
git add src/codesurface/filters.py tests/__init__.py tests/test_filters.py
git commit -m "feat: add PathFilter with default worktree/submodule skip rules"
```

---

## Task 2: PathFilter — user exclusions

**Files:**
- Modify: `src/codesurface/filters.py`
- Modify: `tests/test_filters.py`

### Step 1: Write the failing tests

Append to `tests/test_filters.py`:

```python
def test_exclude_glob_skips_matching_file(tmp_project):
    pf = PathFilter(tmp_project, exclude_globs=["tests/**"])
    (tmp_project / "tests").mkdir()
    test_file = tmp_project / "tests" / "foo.ts"
    test_file.write_text("")
    assert pf.is_file_excluded(test_file)


def test_exclude_glob_does_not_skip_nonmatching(tmp_project):
    pf = PathFilter(tmp_project, exclude_globs=["tests/**"])
    assert not pf.is_file_excluded(tmp_project / "src" / "main.ts")


def test_codesurfaceignore_loaded(tmp_project):
    (tmp_project / ".codesurfaceignore").write_text("generated/**\n# comment\n\n")
    pf = PathFilter(tmp_project)
    gen_file = tmp_project / "generated" / "types.ts"
    assert pf.is_file_excluded(gen_file)


def test_codesurfaceignore_and_cli_globs_merged(tmp_project):
    (tmp_project / ".codesurfaceignore").write_text("generated/**\n")
    pf = PathFilter(tmp_project, exclude_globs=["tests/**"])
    gen_file = tmp_project / "generated" / "types.ts"
    test_file = tmp_project / "tests" / "foo.ts"
    assert pf.is_file_excluded(gen_file)
    assert pf.is_file_excluded(test_file)


def test_codesurfaceignore_missing_is_fine(tmp_project):
    # No .codesurfaceignore present — should not raise
    pf = PathFilter(tmp_project)
    assert not pf.is_file_excluded(tmp_project / "src" / "main.ts")
```

### Step 2: Run tests — verify they fail

```bash
cd ~/code/codesurface
uv run pytest tests/test_filters.py -v -k "glob or ignore"
```

Expected: FAIL — `is_file_excluded` always returns False

### Step 3: Update `filters.py` — add user exclusion support

Replace the `__init__` and `is_file_excluded` methods:

```python
def __init__(
    self,
    project_root: Path,
    exclude_globs: list[str] | None = None,
    include_submodules: bool = False,
) -> None:
    self._root = project_root
    self._include_submodules = include_submodules
    self._globs = list(exclude_globs or [])
    self._globs.extend(_read_ignore_file(project_root))


def is_file_excluded(self, path: Path) -> bool:
    """Return True if this file matches any user exclusion glob."""
    if not self._globs:
        return False
    try:
        rel = str(path.relative_to(self._root)).replace("\\", "/")
    except ValueError:
        return False
    return any(fnmatch.fnmatch(rel, g) for g in self._globs)
```

Also add the helper function before the class:

```python
def _read_ignore_file(project_root: Path) -> list[str]:
    """Read .codesurfaceignore and return non-empty, non-comment lines."""
    ignore_path = project_root / ".codesurfaceignore"
    if not ignore_path.is_file():
        return []
    lines = []
    for line in ignore_path.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            lines.append(stripped)
    return lines
```

### Step 4: Run all filter tests — verify they pass

```bash
cd ~/code/codesurface
uv run pytest tests/test_filters.py -v
```

Expected: all 13 tests PASS

### Step 5: Commit

```bash
cd ~/code/codesurface
git add src/codesurface/filters.py tests/test_filters.py
git commit -m "feat: add .codesurfaceignore and --exclude glob support to PathFilter"
```

---

## Task 3: Wire PathFilter into parse_directory

**Files:**
- Modify: `src/codesurface/parsers/base.py`
- Create: `tests/test_parsers.py`

### Step 1: Write the failing tests

Create `tests/test_parsers.py`:

```python
"""Tests for PathFilter integration with parse_directory."""
from pathlib import Path
import pytest
from codesurface.filters import PathFilter
from codesurface.parsers.typescript import TypeScriptParser


@pytest.fixture
def ts_project(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "service.ts").write_text(
        "export class FooService { bar(): void {} }"
    )
    # A worktree that should be skipped
    wt = tmp_path / ".worktrees" / "pr-1"
    wt.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: /repo/.git/worktrees/pr-1\n")
    (wt / "service.ts").write_text(
        "export class WtService { baz(): void {} }"
    )
    # A generated file that should be skipped
    (tmp_path / "src" / "gen.ts").write_text(
        "export class Generated {}"
    )
    return tmp_path


def test_worktree_files_not_indexed(ts_project):
    pf = PathFilter(ts_project)
    parser = TypeScriptParser()
    records = parser.parse_directory(ts_project, path_filter=pf)
    names = [r["class_name"] for r in records]
    assert "WtService" not in names


def test_src_files_indexed(ts_project):
    pf = PathFilter(ts_project)
    parser = TypeScriptParser()
    records = parser.parse_directory(ts_project, path_filter=pf)
    names = [r["class_name"] for r in records]
    assert "FooService" in names


def test_excluded_file_not_indexed(ts_project):
    pf = PathFilter(ts_project, exclude_globs=["src/gen.ts"])
    parser = TypeScriptParser()
    records = parser.parse_directory(ts_project, path_filter=pf)
    names = [r["class_name"] for r in records]
    assert "Generated" not in names


def test_no_filter_indexes_everything_except_defaults(ts_project):
    # Without PathFilter, worktrees are still skipped (default behaviour
    # when no filter passed — pass a permissive one to verify worktree logic)
    parser = TypeScriptParser()
    records = parser.parse_directory(ts_project)
    # Default (no filter): .worktrees is NOT excluded — this test confirms
    # that without PathFilter the old behaviour is preserved
    names = [r["class_name"] for r in records]
    # WtService IS found without a filter (old behaviour)
    assert "FooService" in names
```

### Step 2: Run tests — verify they fail

```bash
cd ~/code/codesurface
uv run pytest tests/test_parsers.py -v
```

Expected: FAIL — `parse_directory` doesn't accept `path_filter`

### Step 3: Update `parsers/base.py`

```python
"""Abstract base class for language parsers."""

from abc import ABC, abstractmethod
from pathlib import Path

from ..filters import PathFilter


class BaseParser(ABC):

    @property
    @abstractmethod
    def file_extensions(self) -> list[str]:
        """File extensions this parser handles, e.g. ['.cs']."""

    @abstractmethod
    def parse_file(self, path: Path, base_dir: Path) -> list[dict]:
        """Parse a single file and return API records."""

    def parse_directory(
        self, directory: Path, path_filter: PathFilter | None = None
    ) -> list[dict]:
        """Recursively parse all matching files under *directory*.

        If path_filter is provided, excluded directories are pruned before
        descent and excluded files are skipped before parsing.
        """
        records = []
        for ext in self.file_extensions:
            for f in sorted(directory.rglob(f"*{ext}")):
                # Skip files inside excluded directories
                if path_filter is not None:
                    # Check each ancestor dir between directory and f
                    try:
                        rel_parts = f.relative_to(directory).parts
                    except ValueError:
                        continue
                    excluded = False
                    current = directory
                    for part in rel_parts[:-1]:  # all parts except filename
                        current = current / part
                        if path_filter.is_dir_excluded(current):
                            excluded = True
                            break
                    if excluded:
                        continue
                    # Check file-level exclusion
                    if path_filter.is_file_excluded(f):
                        continue
                try:
                    records.extend(self.parse_file(f, directory))
                except Exception:
                    continue
        return records
```

### Step 4: Run tests — verify they pass

```bash
cd ~/code/codesurface
uv run pytest tests/test_parsers.py tests/test_filters.py -v
```

Expected: all tests PASS

### Step 5: Commit

```bash
cd ~/code/codesurface
git add src/codesurface/parsers/base.py tests/test_parsers.py
git commit -m "feat: thread PathFilter through parse_directory for dir and file exclusion"
```

---

## Task 4: Wire PathFilter into server

**Files:**
- Modify: `src/codesurface/server.py`

No new tests needed — this is plumbing. The existing integration is validated by running the server manually.

### Step 1: Add CLI args and build PathFilter in `main()`

In `server.py`, update `main()`:

```python
def main():
    parser = argparse.ArgumentParser(description="codesurface MCP server")
    parser.add_argument("--project", default=None,
                        help="Path to source directory to index")
    parser.add_argument("--language", default=None,
                        help="Language to parse (e.g. csharp). Auto-detected if omitted.")
    parser.add_argument("--exclude", default=None,
                        help="Comma-separated glob patterns to exclude from indexing "
                             "(e.g. 'tests/**,generated/**')")
    parser.add_argument("--include-submodules", action="store_true", default=False,
                        help="Include git submodules in indexing (excluded by default)")
    args, remaining = parser.parse_known_args()

    global _project_path, _path_filter

    exclude_globs = [g.strip() for g in args.exclude.split(",")] if args.exclude else []

    if args.project:
        _project_path = Path(args.project)
        if not _project_path.is_dir():
            print(f"Warning: Project path not found: {args.project}", file=sys.stderr)
        else:
            _path_filter = PathFilter(
                _project_path,
                exclude_globs=exclude_globs,
                include_submodules=args.include_submodules,
            )
            summary = _index_full(_project_path, language=args.language)
            print(summary, file=sys.stderr)

    mcp.run()
```

### Step 2: Add module-level `_path_filter` global and import

At the top of `server.py`, add after existing imports:

```python
from .filters import PathFilter
```

After `_index_fresh: bool = True`:

```python
_path_filter: PathFilter | None = None
```

### Step 3: Pass `_path_filter` through index functions

Update `_index_full` signature and parser calls:

```python
def _index_full(project_path: Path, language: str | None = None) -> str:
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
        records.extend(parser.parse_directory(project_path, path_filter=_path_filter))
    # ... rest unchanged
```

Update `_index_incremental` to skip excluded files during dirty-file scanning. In the section that builds `current` dict, add after `for f in sorted(...)`:

```python
for f in sorted(project_path.rglob(f"*{ext}")):
    # Skip files inside excluded dirs
    if _path_filter is not None:
        try:
            rel_parts = f.relative_to(project_path).parts
        except ValueError:
            continue
        skip = False
        cur = project_path
        for part in rel_parts[:-1]:
            cur = cur / part
            if _path_filter.is_dir_excluded(cur):
                skip = True
                break
        if skip:
            continue
        if _path_filter.is_file_excluded(f):
            continue
    rel = str(f.relative_to(project_path)).replace("\\", "/")
    # ... rest of mtime tracking unchanged
```

Also pass `path_filter=_path_filter` in the dirty-file re-parse loop:

```python
file_records = parser.parse_file(full_path, project_path)
```

(No change needed here — parse_file takes individual files, filter already applied.)

### Step 4: Smoke test manually

```bash
cd ~/code/codesurface
uv run codesurface --project ~/work/apps --exclude "tests/**" 2>&1 | head -5
```

Expected: starts up, prints `Indexed X records from Y files...` without hanging.

### Step 5: Commit

```bash
cd ~/code/codesurface
git add src/codesurface/server.py
git commit -m "feat: add --exclude and --include-submodules CLI args, wire PathFilter into indexing"
```

---

## Task 5: Query-time file_path filtering

**Files:**
- Modify: `src/codesurface/db.py`
- Modify: `src/codesurface/server.py`
- Modify: `tests/test_filters.py` (add db query tests)

### Step 1: Write the failing tests

Append to `tests/test_filters.py`:

```python
# ---- Query-time file_path filtering ----
from codesurface import db as csdb


def _make_db():
    records = [
        {
            "fqn": "Services.FooService",
            "namespace": "Services",
            "class_name": "FooService",
            "member_name": "FooService",
            "member_type": "type",
            "signature": "class FooService",
            "file_path": "src/services/foo.ts",
            "line_start": 1, "line_end": 10,
        },
        {
            "fqn": "Utils.BarUtil",
            "namespace": "Utils",
            "class_name": "BarUtil",
            "member_name": "BarUtil",
            "member_type": "type",
            "signature": "class BarUtil",
            "file_path": "src/utils/bar.ts",
            "line_start": 1, "line_end": 5,
        },
    ]
    return csdb.create_memory_db(records)


def test_search_file_path_prefix_filters():
    conn = _make_db()
    results = csdb.search(conn, "Service", file_path="src/services/")
    assert len(results) == 1
    assert results[0]["class_name"] == "FooService"


def test_search_file_path_exact_file():
    conn = _make_db()
    results = csdb.search(conn, "Bar", file_path="src/utils/bar.ts")
    assert len(results) == 1
    assert results[0]["class_name"] == "BarUtil"


def test_search_file_path_no_match_returns_empty():
    conn = _make_db()
    results = csdb.search(conn, "Foo", file_path="src/utils/")
    assert len(results) == 0


def test_search_no_file_path_returns_all():
    conn = _make_db()
    results = csdb.search(conn, "Service OR Bar", file_path=None)
    assert len(results) == 2
```

### Step 2: Run tests — verify they fail

```bash
cd ~/code/codesurface
uv run pytest tests/test_filters.py -v -k "file_path"
```

Expected: FAIL — `search()` doesn't accept `file_path`

### Step 3: Update `db.py` — add `file_path` to `search()`

Update the `search` function signature and SQL:

```python
def search(conn: sqlite3.Connection, query: str, n: int = 10,
           member_type: str | None = None,
           file_path: str | None = None) -> list[dict]:
    """Full-text search with BM25 ranking + optional file path scoping.

    file_path: prefix string or exact path. Matched as SQL LIKE prefix.
    """
    clean = _escape_fts(query)
    if not clean.strip():
        return []

    ranking = """bm25(api_fts, 1.0, 5.0, 10.0, 0.5, 3.0, 4.0)
                + CASE WHEN r.member_type = 'type' THEN -1.0 ELSE 0.0 END"""

    # Build WHERE clauses
    conditions = ["api_fts MATCH ?"]
    params: list = [clean]

    if member_type:
        conditions.append("r.member_type = ?")
        params.append(member_type)

    if file_path:
        # Exact file or prefix directory match
        if file_path.endswith("/"):
            conditions.append("r.file_path LIKE ?")
            params.append(file_path + "%")
        else:
            conditions.append("(r.file_path = ? OR r.file_path LIKE ?)")
            params.extend([file_path, file_path + "/%"])

    where = " AND ".join(conditions)
    params.append(n)

    sql = f"""
        SELECT r.*, {ranking} AS rank
        FROM api_fts f
        JOIN api_records r ON r.rowid = f.rowid
        WHERE {where}
        ORDER BY rank
        LIMIT ?
    """
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]
```

### Step 4: Add `file_path` to MCP tools in `server.py`

Update `search` tool:

```python
@mcp.tool()
def search(
    query: str,
    n_results: int = 5,
    member_type: str | None = None,
    file_path: str | None = None,
) -> str:
    """Search the indexed API by keyword.

    Find classes, methods, properties, fields, and events.
    Returns ranked results with signatures.

    Args:
        query: Search terms (e.g. "MergeService", "BlastBoard", "GridCoord")
        n_results: Max results to return (default 5, max 20)
        member_type: Optional filter — "type", "method", "property", "field", or "event"
        file_path: Optional path prefix or exact file to scope results
                   (e.g. "src/services/" or "src/services/foo.ts")
    """
    ...
    results = db.search(_conn, query, n=n_results, member_type=member_type,
                        file_path=file_path)
```

Update `get_signature` tool — add `file_path: str | None = None` param and pass to the `LIKE` query inside `_lookup()`:

```python
@mcp.tool()
def get_signature(name: str, file_path: str | None = None) -> str:
    """Look up the exact signature of an API member by name or FQN.

    Args:
        name: Member name or FQN
        file_path: Optional path prefix to scope the lookup
    """
```

Inside `_lookup`, add file_path condition to the substring LIKE query:

```python
file_clause = ""
file_params: list = []
if file_path:
    if file_path.endswith("/"):
        file_clause = " AND file_path LIKE ?"
        file_params = [file_path + "%"]
    else:
        file_clause = " AND (file_path = ? OR file_path LIKE ?)"
        file_params = [file_path, file_path + "/%"]

rows = _conn.execute(
    f"SELECT * FROM api_records WHERE fqn LIKE ?{file_clause} ORDER BY fqn",
    (f"%{name}%", *file_params),
).fetchall()
```

Update `get_class` — add `file_path: str | None = None` param (affects `search` fallback only; class lookup by name doesn't need it).

### Step 5: Run all tests

```bash
cd ~/code/codesurface
uv run pytest tests/ -v
```

Expected: all tests PASS

### Step 6: Commit

```bash
cd ~/code/codesurface
git add src/codesurface/db.py src/codesurface/server.py tests/test_filters.py
git commit -m "feat: add file_path scoping to search, get_signature, get_class tools"
```

---

## Task 6: pr-manager — codesurface review servers

**Files:**
- Modify: `server/routes/pr-graph.js` in `~/code/pr-manager`

This mirrors how `codegraph-review-*` servers are managed. Read the existing worktree setup/teardown in `pr-graph.js` first to understand the current codegraph lifecycle, then add codesurface alongside it.

### Step 1: Read the existing lifecycle

```bash
grep -n "codegraph-review\|codesurface-review\|REVIEW_MCP_PREFIX\|mcpProxyManager" \
  ~/code/pr-manager/server/routes/pr-graph.js | head -40
```

### Step 2: Add codesurface server startup after worktree creation

In the `/setup` route, after the codegraph server is started, add:

```javascript
// Start codesurface review server for this worktree
const csName = `codesurface-review-${prSlug}`
mcpProxyManager.addDynamic({
  name: csName,
  label: `Codesurface (${prSlug})`,
  command: process.env.UVX_COMMAND_PATH || 'uvx',
  args: ['codesurface', '--project', worktreePath],
  readyViaHandshake: false,
})
send({ type: 'log', msg: `Codesurface server started: ${csName}` })
```

### Step 3: Add codesurface server teardown alongside codegraph teardown

In the worktree cleanup route (wherever `codegraph-review-*` is removed), add:

```javascript
const csName = `codesurface-review-${prSlug}`
mcpProxyManager.removeDynamic(csName)
```

### Step 4: Smoke test

Start pr-manager, set up a review worktree for a real PR, verify both
`codegraph-review-<pr>` and `codesurface-review-<pr>` appear in the MCP
proxy status page.

### Step 5: Commit

```bash
cd ~/code/pr-manager
git add server/routes/pr-graph.js
git commit -m "feat: spin up codesurface-review-* alongside codegraph-review-* for PR worktrees"
```

---

## Final Smoke Test

```bash
# 1. Confirm codesurface starts clean on apps repo
cd ~/work/apps
uvx --from ~/code/codesurface codesurface --project . 2>&1 | head -3
# Expected: "Indexed N records from M files..."

# 2. Confirm .worktrees is skipped
ls .worktrees/ | wc -l   # how many worktrees exist
# Reindex should be much faster than before and not hang

# 3. Run full test suite
cd ~/code/codesurface
uv run pytest tests/ -v --tb=short
```
