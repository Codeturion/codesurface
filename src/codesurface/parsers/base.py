"""Abstract base class for language parsers."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

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
        self, directory: Path, path_filter: "PathFilter | None" = None
    ) -> list[dict]:
        """Recursively parse all matching files under *directory*.

        If path_filter is provided, excluded directories are pruned before
        descent and excluded files are skipped before parsing.
        """
        records = []
        for ext in self.file_extensions:
            for f in sorted(directory.rglob(f"*{ext}")):
                if path_filter is not None:
                    # Check each ancestor directory between root and file
                    try:
                        rel_parts = f.relative_to(directory).parts
                    except ValueError:
                        continue
                    excluded = False
                    current = directory
                    for part in rel_parts[:-1]:  # all parts except the filename
                        current = current / part
                        if path_filter.is_dir_excluded(current):
                            excluded = True
                            break
                    if excluded:
                        continue
                    # Check file-level exclusion
                    if path_filter.is_file_excluded(f):
                        continue
                try:
                    records.extend(self.parse_file(f, directory))
                except Exception:
                    continue
        return records
