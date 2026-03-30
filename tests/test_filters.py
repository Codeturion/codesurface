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
    (wt / "src").mkdir()
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
