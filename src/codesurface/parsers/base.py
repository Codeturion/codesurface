"""Abstract base class for language parsers."""

import os
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ..filters import PathFilter


class BaseParser(ABC):
    """Base class that all language parsers must extend.

    Subclasses implement `file_extensions` and `parse_file`.
    The default `parse_directory` walks recursively for matching files,
    using raw str paths internally to avoid pathlib overhead at scale.

    Subclasses may override `skip_suffixes` or `skip_files` to filter
    out files by suffix or exact name (e.g. ".d.ts", "module-info.java").
    """

    @property
    @abstractmethod
    def file_extensions(self) -> list[str]:
        """File extensions this parser handles, e.g. ['.cs']."""

    @property
    def skip_suffixes(self) -> tuple[str, ...]:
        """File suffixes to skip (e.g. ('.d.ts',)). Override in subclass."""
        return ()

    @property
    def skip_files(self) -> frozenset[str]:
        """Exact filenames to skip (e.g. frozenset({'conftest.py'})). Override in subclass."""
        return frozenset()

    @abstractmethod
    def parse_file(self, path: Path, base_dir: Path) -> list[dict]:
        """Parse a single file and return API records."""

    def _should_skip_dir(self, name: str) -> bool:
        """Extra per-parser directory skip logic. Override if needed."""
        return False

    def parse_directory(
        self, directory: Path, path_filter: "PathFilter | None" = None,
        on_progress: "Callable[[Path], None] | None" = None,
    ) -> list[dict]:
        """Recursively parse all matching files under *directory*.

        Uses os.walk with str paths to avoid pathlib overhead.
        PathFilter handles all default exclusions (node_modules, .git, etc.).
        """
        exts = tuple(self.file_extensions)
        skip_suf = self.skip_suffixes
        skip_fn = self.skip_files
        dir_str = str(directory)
        records: list[dict] = []

        for root, dirs, files in os.walk(dir_str):
            # Prune excluded directories IN PLACE so os.walk skips them
            if path_filter is not None:
                dirs[:] = [
                    d for d in dirs
                    if not path_filter.is_dir_excluded_name(d)
                    and not self._should_skip_dir(d)
                    and not path_filter.is_dir_excluded(Path(os.path.join(root, d)))
                ]
            else:
                dirs[:] = [d for d in dirs if not self._should_skip_dir(d)]

            for filename in files:
                if not filename.endswith(exts):
                    continue
                if skip_suf and filename.endswith(skip_suf):
                    continue
                if skip_fn and filename in skip_fn:
                    continue

                filepath = os.path.join(root, filename)

                if path_filter is not None and path_filter.is_file_excluded_rel(
                    filepath[len(dir_str) + 1:].replace("\\", "/")
                ):
                    continue

                f = Path(filepath)
                try:
                    records.extend(self.parse_file(f, directory))
                except Exception as e:
                    print(f"codesurface: failed to parse {filepath}: {e}", file=sys.stderr)
                finally:
                    if on_progress is not None:
                        on_progress(f)

        return records
