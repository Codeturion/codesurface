"""Parser registry. Auto-registers built-in parsers on import."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..filters import PathFilter
    from .base import BaseParser

_REGISTRY: dict[str, type[BaseParser]] = {}
_EXT_TO_LANG: dict[str, str] = {}


def register(lang: str, parser_cls: type[BaseParser]) -> None:
    """Register a parser class for a language identifier (e.g. "csharp")."""
    _REGISTRY[lang] = parser_cls
    for ext in parser_cls().file_extensions:
        _EXT_TO_LANG[ext] = lang


def get_parser(lang: str) -> BaseParser:
    """Return an instance of the parser registered for *lang*.

    Raises KeyError if no parser is registered.
    """
    cls = _REGISTRY[lang]
    return cls()


def detect_languages(
    project_dir: Path,
    path_filter: "PathFilter | None" = None,
) -> list[str]:
    """Detect which registered languages are present in *project_dir*.

    Uses os.walk with *path_filter* pruning so vendored directories
    (node_modules, .git, etc.) are skipped during detection.
    """
    exts = tuple(_EXT_TO_LANG.keys())
    found: set[str] = set()

    for root, dirs, files in os.walk(project_dir):
        root_path = Path(root)
        if path_filter is not None:
            dirs[:] = [d for d in dirs if not path_filter.is_dir_excluded(root_path / d)]

        for filename in files:
            for ext in exts:
                if filename.endswith(ext):
                    found.add(_EXT_TO_LANG[ext])
                    break

        # Stop early once all registered languages are found
        if len(found) == len(_REGISTRY):
            break

    return sorted(found)


def get_parsers_for_project(
    project_dir: Path,
    path_filter: "PathFilter | None" = None,
) -> list[BaseParser]:
    """Return parser instances for every language detected in *project_dir*."""
    return [get_parser(lang) for lang in detect_languages(project_dir, path_filter)]


def all_extensions() -> list[str]:
    """Return all registered file extensions across all parsers."""
    return list(_EXT_TO_LANG.keys())


# --- Auto-register built-in parsers ---

from .cpp import CppParser  # noqa: E402
from .csharp import CSharpParser  # noqa: E402
from .go import GoParser  # noqa: E402
from .java import JavaParser  # noqa: E402
from .python_parser import PythonParser  # noqa: E402
from .typescript import TypeScriptParser  # noqa: E402

register("cpp", CppParser)
register("csharp", CSharpParser)
register("go", GoParser)
register("java", JavaParser)
register("python", PythonParser)
register("typescript", TypeScriptParser)
