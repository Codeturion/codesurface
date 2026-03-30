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
        self._globs: list[str] = list(exclude_globs or [])
        self._globs.extend(_read_ignore_file(project_root))

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
        """Return True if this file matches any user exclusion glob."""
        if not self._globs:
            return False
        try:
            rel = str(path.relative_to(self._root)).replace("\\", "/")
        except ValueError:
            return False
        return any(fnmatch.fnmatch(rel, g) for g in self._globs)
