"""Abstract base class for language parsers."""

from abc import ABC, abstractmethod
from pathlib import Path


class BaseParser(ABC):
    """Base class that all language parsers must extend.

    Subclasses implement `file_extensions` and `parse_file`.
    The default `parse_directory` walks recursively for matching files.
    """

    @property
    @abstractmethod
    def file_extensions(self) -> list[str]:
        """File extensions this parser handles, e.g. [".cs"]."""

    @abstractmethod
    def parse_file(self, path: Path, base_dir: Path) -> list[dict]:
        """Parse a single file and return API records."""

    def parse_directory(self, directory: Path) -> list[dict]:
        """Recursively parse all matching files under *directory*."""
        records = []
        for ext in self.file_extensions:
            for f in sorted(directory.rglob(f"*{ext}")):
                try:
                    records.extend(self.parse_file(f, directory))
                except Exception:
                    continue
        return records
