"""Abstract base class for language parsers."""

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ..filters import PathFilter


class BaseParser(ABC):
    """Base class that all language parsers must extend.

    Subclasses implement `file_extensions` and `parse_file`.
    The default `parse_directory` walks recursively for matching files.
    """

    @property
    @abstractmethod
    def file_extensions(self) -> list[str]:
        """File extensions this parser handles, e.g. ['.cs']."""

    @abstractmethod
    def parse_file(self, path: Path, base_dir: Path) -> list[dict]:
        """Parse a single file and return API records."""

    def parse_directory(
        self, directory: Path, path_filter: "PathFilter | None" = None,
        on_progress: "Callable[[Path], None] | None" = None,
    ) -> list[dict]:
        """Recursively parse all matching files under *directory*.

        Uses os.walk so excluded directories (worktrees, submodules) are
        pruned before descent — rglob cannot prune mid-walk.
        """
        exts = tuple(self.file_extensions)
        records = []

        for root, dirs, files in os.walk(directory):
            root_path = Path(root)

            if path_filter is not None:
                # Prune excluded directories IN PLACE so os.walk skips them
                dirs[:] = [
                    d for d in dirs
                    if not path_filter.is_dir_excluded(root_path / d)
                ]

            for filename in files:
                if not filename.endswith(exts):
                    continue
                f = root_path / filename
                if path_filter is not None and path_filter.is_file_excluded(f):
                    continue
                try:
                    records.extend(self.parse_file(f, directory))
                    if on_progress is not None:
                        on_progress(f)
                except Exception:
                    continue

        return records
