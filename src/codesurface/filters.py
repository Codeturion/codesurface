"""Path filtering for codesurface indexing.

Handles default exclusions (worktrees, submodules, vendored/build dirs)
and user-configured exclusions (.codesurfaceignore, --exclude CLI flag).
"""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path

# Directories excluded by name in every project — vendored deps, build
# output, VCS internals, and IDE config that never contain user source.
_DEFAULT_EXCLUDED_DIRS: frozenset[str] = frozenset({
    # JS / Node
    "node_modules", "bower_components",
    # Python
    ".venv", "venv", "env", "__pycache__", ".tox", ".mypy_cache",
    ".pytest_cache", "site-packages",
    # Go
    "vendor", "testdata", "third_party", "examples", "example",
    # .NET / Java
    "bin", "obj", "packages", ".gradle", ".mvn",
    "generated", "generated-sources", "generated-test-sources",
    # Build output / caches
    "dist", "build", "out", "target", ".next", ".nuxt", ".nx",
    # VCS / IDE
    ".git", ".hg", ".svn",
    ".idea", ".vscode", ".vs",
    # Misc
    ".yarn", ".pnp", "coverage", ".turbo", ".cache", ".worktrees",
})


def _read_git_file(path: Path) -> str | None:
    """Read .git FILE content if present. Returns None if .git is a directory."""
    return _read_git_file_str(str(path))


def _read_git_file_str(dir_path: str) -> str | None:
    """String-path version of _read_git_file. Avoids Path object churn in walks."""
    git = os.path.join(dir_path, ".git")
    if os.path.isfile(git):
        try:
            with open(git, "r", encoding="utf-8") as f:
                return f.read().strip()
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

    User exclusions via exclude_globs (CLI) and .codesurfaceignore (project file).
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

    def is_dir_excluded_name(self, name: str) -> bool:
        """Fast check using only the directory basename (no I/O)."""
        return name in _DEFAULT_EXCLUDED_DIRS

    def is_dir_excluded(self, path: Path) -> bool:
        """Return True if this directory should be skipped entirely."""
        name = path.name
        if name in _DEFAULT_EXCLUDED_DIRS:
            return True
        return self._git_file_excludes(str(path))

    def is_dir_excluded_git(self, root: str, name: str) -> bool:
        """String-path companion to is_dir_excluded for the .git FILE check.

        Caller is expected to have already filtered by is_dir_excluded_name,
        so this only handles worktree/submodule detection. Avoids Path()
        construction and an extra str() round-trip in tight walk loops.
        """
        return self._git_file_excludes(os.path.join(root, name))

    def _git_file_excludes(self, dir_path: str) -> bool:
        git_content = _read_git_file_str(dir_path)
        if git_content is None:
            return False
        if _is_git_worktree(git_content):
            return True
        if _is_git_submodule(git_content) and not self._include_submodules:
            return True
        return False

    def is_file_excluded(self, path: Path) -> bool:
        """Return True if this file matches any user exclusion glob.

        Path-based variant retained for callers that already have a Path.
        Hot walk loops should prefer is_file_excluded_rel with a string slice.
        """
        if not self._globs:
            return False
        try:
            rel = str(path.relative_to(self._root)).replace("\\", "/")
        except ValueError:
            return False
        return self.is_file_excluded_rel(rel)

    def is_file_excluded_rel(self, rel_path: str) -> bool:
        """Return True if a relative path matches any user exclusion glob."""
        if not self._globs:
            return False
        return any(fnmatch.fnmatch(rel_path, g) for g in self._globs)
