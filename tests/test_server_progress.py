"""Tests for _count_files and _index_full progress output."""
from pathlib import Path
import pytest


def test_count_files_basic(tmp_path):
    """_count_files counts matching source files, ignores other extensions."""
    from codesurface.server import _count_files
    from codesurface.parsers.python_parser import PythonParser

    (tmp_path / "a.py").write_text("x = 1")
    (tmp_path / "b.py").write_text("y = 2")
    (tmp_path / "c.txt").write_text("ignored")

    parsers = [PythonParser()]
    assert _count_files(tmp_path, parsers, path_filter=None) == 2


def test_count_files_prunes_excluded_dirs(tmp_path):
    """_count_files respects path_filter dir exclusions."""
    from codesurface.server import _count_files
    from codesurface.parsers.python_parser import PythonParser
    from codesurface.filters import PathFilter

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1")
    wt = tmp_path / ".worktrees" / "pr-1"
    wt.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: /repo/.git/worktrees/pr-1\n")
    (wt / "b.py").write_text("y = 2")

    pf = PathFilter(tmp_path)
    parsers = [PythonParser()]
    assert _count_files(tmp_path, parsers, path_filter=pf) == 1


def test_incremental_walk_applies_parser_skip_suffixes(tmp_path):
    """_index_incremental must apply per-parser skip_suffixes so _file_mtimes
    only tracks files the parser actually parses. Regression check: previously
    used a global os.walk that skipped only path_filter, so .test.ts/.spec.ts
    files leaked into _file_mtimes.
    """
    from codesurface import server
    from codesurface.filters import PathFilter

    (tmp_path / "foo.ts").write_text("export const x = 1;")
    (tmp_path / "foo.test.ts").write_text("test('x', () => {});")

    server._conn = None
    server._project_path = tmp_path
    server._path_filter = PathFilter(tmp_path)
    server._file_mtimes = {}

    server._index_full(tmp_path)
    assert set(server._file_mtimes) == {"foo.ts"}

    (tmp_path / "bar.spec.ts").write_text("test('y', () => {});")
    (tmp_path / "baz.ts").write_text("export const y = 2;")

    server._index_incremental(tmp_path)
    assert set(server._file_mtimes) == {"foo.ts", "baz.ts"}


def test_incremental_honors_language_pinning(tmp_path):
    """When --language pins a single parser, _index_incremental must use the
    same single parser. Regression check: previously called
    get_parsers_for_project which auto-detected all languages, so a
    polyglot project with --language=cpp would falsely report .py files
    as 'added' on first reindex.
    """
    from codesurface import server
    from codesurface.filters import PathFilter

    (tmp_path / "foo.py").write_text("x = 1\n")
    (tmp_path / "bar.ts").write_text("export const y = 2;")

    server._conn = None
    server._project_path = tmp_path
    server._path_filter = PathFilter(tmp_path)
    server._file_mtimes = {}
    server._language = "python"

    server._index_full(tmp_path, language="python")
    assert set(server._file_mtimes) == {"foo.py"}

    msg, changed = server._index_incremental(tmp_path)
    assert not changed
    assert "No changes detected" in msg
    assert set(server._file_mtimes) == {"foo.py"}

    server._language = None  # restore default for other tests


def test_index_full_emits_progress_to_stderr(tmp_path, capsys):
    """_index_full prints at least one progress line and a done line to stderr."""
    from codesurface import server
    from codesurface.filters import PathFilter

    for i in range(5):
        (tmp_path / f"m{i}.py").write_text(f"def f{i}(): pass\n")

    server._conn = None
    server._project_path = tmp_path
    server._path_filter = PathFilter(tmp_path)

    server._index_full(tmp_path)

    captured = capsys.readouterr()
    assert "[codesurface]" in captured.err
    assert "scanning" in captured.err       # e.g. "[codesurface] scanning 5 files..."
    assert "indexing:" in captured.err      # e.g. "[codesurface] indexing:   0% ..."
    assert "done:" in captured.err
