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


def test_no_filter_indexes_worktrees_too(ts_project):
    # Without PathFilter, worktrees are NOT excluded — old behaviour preserved
    parser = TypeScriptParser()
    records = parser.parse_directory(ts_project)
    names = [r["class_name"] for r in records]
    assert "FooService" in names
    # WtService IS found without a filter (old behaviour)
    assert "WtService" in names
