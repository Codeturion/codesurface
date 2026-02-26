"""Parser registry. Auto-registers built-in parsers on import."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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


def detect_languages(project_dir: Path) -> list[str]:
    """Detect which registered languages are present in *project_dir*."""
    found: set[str] = set()
    for ext, lang in _EXT_TO_LANG.items():
        # Quick check: does at least one file with this extension exist?
        try:
            next(project_dir.rglob(f"*{ext}"))
            found.add(lang)
        except StopIteration:
            pass
    return sorted(found)


def get_parsers_for_project(project_dir: Path) -> list[BaseParser]:
    """Return parser instances for every language detected in *project_dir*."""
    return [get_parser(lang) for lang in detect_languages(project_dir)]


def all_extensions() -> list[str]:
    """Return all registered file extensions across all parsers."""
    return list(_EXT_TO_LANG.keys())


# --- Auto-register built-in parsers ---

from .csharp import CSharpParser  # noqa: E402
from .go import GoParser  # noqa: E402
from .java import JavaParser  # noqa: E402
from .python_parser import PythonParser  # noqa: E402
from .typescript import TypeScriptParser  # noqa: E402

register("csharp", CSharpParser)
register("go", GoParser)
register("java", JavaParser)
register("python", PythonParser)
register("typescript", TypeScriptParser)
