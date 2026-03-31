# Startup Progress Reporting — Design

**Date:** 2026-03-30

**Goal:** Stream concise progress lines to stderr during `_index_full` so users can see how far along indexing is, especially during dynamic worktree MCP server startups.

---

## Architecture

Two files change, cleanly separated.

### 1. `src/codesurface/parsers/base.py`

Add an optional `on_progress` callback to `parse_directory`:

```python
def parse_directory(
    self,
    directory: Path,
    path_filter: "PathFilter | None" = None,
    on_progress: "Callable[[Path], None] | None" = None,
) -> list[dict]:
```

After each successful `parse_file` call, invoke `on_progress(f)` if provided. All subclass overrides (TypeScript, Python, Java, Go) receive and forward the same parameter.

### 2. `src/codesurface/server.py`

**`_count_files(project_path, parsers, path_filter)`** — new helper. Single `os.walk` pass using the same dir-pruning logic as `_index_full`. Returns the total count of matching source files.

**`_index_full`** — updated flow:

1. Pre-scan: call `_count_files` → `total`
2. Print: `[codesurface] scanning N files...`
3. Create throttled `on_progress` closure (see below)
4. Pass `on_progress` to each `parser.parse_directory`
5. Final line unchanged: `done: N records from M files in X.XXs`

**Throttle logic:** emit a progress line when either condition is true since the last print:
- ≥ 5% of total files parsed, OR
- ≥ 3 seconds elapsed

## Output Format

```
[codesurface] scanning 1,234 files...
[codesurface] indexing:   0% (    0 / 1,234)
[codesurface] indexing:  41% (  500 / 1,234)  9.4s
[codesurface] done: 8,412 records from 1,234 files in 22.1s
```

- Lines go to `sys.stderr` (same as today's summary line)
- Concise — typically 3–6 lines total for a large repo
- No ANSI escape codes (compatible with all log viewers)

## Edge Cases

| Scenario | Handling |
|----------|----------|
| Zero files detected | Existing `"No supported source files"` message; no progress lines |
| `total = 0` (race) | `max(total, 1)` prevents division by zero |
| `parsed > total` (race) | Percentage can exceed 100% gracefully; final line always accurate |
| Single-file repos | One `0%` line + `done` line |

## Scope

**Changes:** `base.py`, subclass `parse_directory` overrides in `typescript.py`, `python_parser.py`, `java.py`, `go.py`, `server.py`

**No changes:** `db.py`, MCP tools, `filters.py`, `__init__.py`, CLI interface

## Testing

- Unit test: `on_progress` called once per successfully parsed file
- Unit test: `on_progress=None` (default) works unchanged
- Unit test: `_count_files` returns correct count with dir pruning
- Integration test: `_index_full` emits at least one progress line to stderr for a non-empty directory
