"""Tests for PathFilter integration with parse_directory."""
from pathlib import Path
import pytest
from codesurface.filters import PathFilter
from codesurface.parsers.typescript import TypeScriptParser
from codesurface.parsers.python_parser import PythonParser
from codesurface.parsers.go import GoParser
from codesurface.parsers.java import JavaParser


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


def test_no_filter_indexes_worktrees_too(ts_project):
    # Without PathFilter, worktrees are NOT excluded — old behaviour preserved
    parser = TypeScriptParser()
    records = parser.parse_directory(ts_project)
    names = [r["class_name"] for r in records]
    assert "FooService" in names
    # WtService IS found without a filter (old behaviour)
    assert "WtService" in names


def test_on_progress_called_per_file(ts_project):
    """on_progress is called once per successfully parsed file."""
    parser = TypeScriptParser()
    visited = []
    parser.parse_directory(ts_project, on_progress=lambda f: visited.append(f))
    # ts_project has service.ts, gen.ts, and a worktree service.ts (3 .ts files total without filter)
    assert len(visited) == 3
    assert all(isinstance(f, Path) for f in visited)


def test_on_progress_none_is_default(ts_project):
    """Omitting on_progress works exactly as before."""
    parser = TypeScriptParser()
    records = parser.parse_directory(ts_project)
    assert len(records) > 0


@pytest.fixture
def py_project(tmp_path):
    (tmp_path / "mod.py").write_text("def hello(): pass\n")
    return tmp_path


def test_typescript_on_progress(ts_project):
    parser = TypeScriptParser()
    visited = []
    parser.parse_directory(ts_project, on_progress=lambda f: visited.append(f))
    assert len(visited) >= 1


def test_python_on_progress(py_project):
    parser = PythonParser()
    visited = []
    parser.parse_directory(py_project, on_progress=lambda f: visited.append(f))
    assert len(visited) == 1


def test_go_on_progress(tmp_path):
    (tmp_path / "main.go").write_text("package main\nfunc Hello() {}\n")
    parser = GoParser()
    visited = []
    parser.parse_directory(tmp_path, on_progress=lambda f: visited.append(f))
    assert len(visited) == 1


def test_java_on_progress(tmp_path):
    (tmp_path / "Foo.java").write_text("public class Foo { public void bar() {} }\n")
    parser = JavaParser()
    visited = []
    parser.parse_directory(tmp_path, on_progress=lambda f: visited.append(f))
    assert len(visited) == 1
