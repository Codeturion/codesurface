"""Python parser that captures public API declarations.

Scans Python files tracking indentation-based scope, captures classes,
functions, methods, properties, and module-level constants.
Docstrings are extracted as summaries.
"""

import re
from pathlib import Path

from .base import BaseParser


# --- Skip patterns ---

_SKIP_DIRS = frozenset({
    "__pycache__", ".git", ".venv", "venv", "env",
    "node_modules", ".tox", ".mypy_cache", ".pytest_cache",
    "dist", "build", "egg-info",
})

_SKIP_FILES = frozenset({
    "setup.py", "conftest.py",
})

# --- Regex patterns ---

# Class declaration: class Name(bases):
_CLASS_RE = re.compile(
    r"^(\s*)class\s+(\w+)"
    r"(?:\s*\(([^)]*)\))?"  # optional bases
    r"\s*:"
)

# Function/method declaration: def name(params) -> return:
_DEF_RE = re.compile(
    r"^(\s*)(?:async\s+)?def\s+(\w+)\s*\("
)

# Module-level constant: UPPER_CASE = value  or  _UPPER_CASE = value
_CONST_RE = re.compile(
    r"^([A-Z][A-Z0-9_]*)\s*(?::\s*[^=]+)?\s*="
)

# Module-level typed variable: name: Type = value
_TYPED_VAR_RE = re.compile(
    r"^(\w+)\s*:\s*(.+?)\s*="
)

# Decorator line
_DECORATOR_RE = re.compile(r"^(\s*)@(\w+(?:\.\w+)*)")

# Docstring openers
_DOCSTRING_TRIPLE_DQ = '"""'
_DOCSTRING_TRIPLE_SQ = "'''"

# __all__ list
_ALL_RE = re.compile(r"^__all__\s*=\s*\[")

# Return type annotation: ) -> Type:
_RETURN_TYPE_RE = re.compile(r"\)\s*->\s*(.+?)\s*:")


class PythonParser(BaseParser):
    """Parser for Python source files."""

    @property
    def file_extensions(self) -> list[str]:
        return [".py"]

    def parse_directory(self, directory: Path) -> list[dict]:
        """Override to skip common non-source directories."""
        records = []
        for f in sorted(directory.rglob("*.py")):
            # Skip files in excluded directories
            parts = f.relative_to(directory).parts
            if any(p in _SKIP_DIRS for p in parts):
                continue
            if any(p.endswith(".egg-info") for p in parts):
                continue
            if f.name in _SKIP_FILES:
                continue
            try:
                records.extend(self.parse_file(f, directory))
            except Exception:
                continue
        return records

    def parse_file(self, path: Path, base_dir: Path) -> list[dict]:
        return _parse_py_file(path, base_dir)


def _parse_py_file(path: Path, base_dir: Path) -> list[dict]:
    """Parse a single .py file and extract public API members."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return []

    rel_path = str(path.relative_to(base_dir)).replace("\\", "/")
    lines = text.splitlines()
    records = []

    # Derive module path from file path
    module = _file_to_module(path, base_dir)

    # Check for __all__ to determine explicit exports
    all_names = _extract_all(lines)

    # Collect decorators, then parse declarations
    i = 0
    class_stack: list[tuple[str, int]] = []  # (class_name, indent_level)
    pending_decorators: list[str] = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Skip empty lines and comments
        if not stripped or stripped.startswith("#"):
            i += 1
            pending_decorators = []
            continue

        # Track indentation to pop class stack
        if stripped and not stripped.startswith("#"):
            indent = _indent_level(line)
            while class_stack and indent <= class_stack[-1][1]:
                class_stack.pop()

        # Decorator
        dec_match = _DECORATOR_RE.match(line)
        if dec_match:
            pending_decorators.append(dec_match.group(2))
            i += 1
            continue

        # Class declaration
        cls_match = _CLASS_RE.match(line)
        if cls_match:
            indent = len(cls_match.group(1))
            class_name = cls_match.group(2)
            bases = cls_match.group(3) or ""

            class_stack.append((class_name, indent))

            if _is_public(class_name, all_names):
                fqn = f"{module}.{class_name}" if module else class_name
                docstring = _extract_docstring(lines, i)

                sig_parts = [f"class {class_name}"]
                if bases:
                    sig_parts.append(f"({bases.strip()})")
                signature = "".join(sig_parts)

                records.append(_build_record(
                    fqn=fqn,
                    namespace=module,
                    class_name=class_name,
                    member_name="",
                    member_type="type",
                    signature=signature,
                    summary=docstring,
                    file_path=rel_path,
                ))

            pending_decorators = []
            i += 1
            continue

        # Function/method declaration
        def_match = _DEF_RE.match(line)
        if def_match:
            indent = len(def_match.group(1))
            func_name = def_match.group(2)

            # Collect full signature (may span multiple lines)
            sig_text, end_line = _collect_signature(lines, i)

            in_class = bool(class_stack) and indent > class_stack[-1][1]
            current_class = class_stack[-1][0] if in_class else None

            is_property = "property" in pending_decorators
            is_static = "staticmethod" in pending_decorators
            is_classmethod = "classmethod" in pending_decorators
            is_abstract = "abstractmethod" in pending_decorators
            is_async = stripped.startswith("async ")

            # Determine visibility
            visible = False
            if in_class:
                # Inside class: skip dunder (except __init__) and _private
                if func_name == "__init__":
                    visible = True
                elif not func_name.startswith("_"):
                    visible = True
            else:
                # Module-level function
                visible = _is_public(func_name, all_names)

            if visible:
                params_str = _extract_params(sig_text, skip_self=in_class)
                return_type = _extract_return_type(sig_text)
                docstring = _extract_docstring(lines, end_line)

                if in_class:
                    base_fqn = f"{module}.{current_class}" if module else current_class

                    if func_name == "__init__":
                        # Constructor
                        sig = f"{current_class}({params_str})"
                        records.append(_build_record(
                            fqn=_method_fqn(base_fqn, current_class, params_str),
                            namespace=module,
                            class_name=current_class,
                            member_name=current_class,
                            member_type="method",
                            signature=sig,
                            summary=docstring,
                            file_path=rel_path,
                        ))
                    elif is_property:
                        # Property
                        sig = f"{return_type} {func_name}" if return_type else func_name
                        sig += " { get; }"
                        records.append(_build_record(
                            fqn=f"{base_fqn}.{func_name}",
                            namespace=module,
                            class_name=current_class,
                            member_name=func_name,
                            member_type="property",
                            signature=sig,
                            summary=docstring,
                            file_path=rel_path,
                        ))
                    else:
                        # Method
                        prefix = ""
                        if is_static:
                            prefix = "static "
                        elif is_classmethod:
                            prefix = "classmethod "
                        elif is_abstract:
                            prefix = "abstract "
                        if is_async:
                            prefix += "async "

                        ret = f" -> {return_type}" if return_type else ""
                        sig = f"{prefix}{func_name}({params_str}){ret}"
                        records.append(_build_record(
                            fqn=_method_fqn(base_fqn, func_name, params_str),
                            namespace=module,
                            class_name=current_class,
                            member_name=func_name,
                            member_type="method",
                            signature=sig,
                            summary=docstring,
                            file_path=rel_path,
                        ))
                else:
                    # Module-level function
                    prefix = "async " if is_async else ""
                    ret = f" -> {return_type}" if return_type else ""
                    sig = f"{prefix}{func_name}({params_str}){ret}"
                    fqn = f"{module}.{func_name}" if module else func_name

                    records.append(_build_record(
                        fqn=_method_fqn(module, func_name, params_str) if module else func_name,
                        namespace=module,
                        class_name="",
                        member_name=func_name,
                        member_type="method",
                        signature=sig,
                        summary=docstring,
                        file_path=rel_path,
                    ))

            pending_decorators = []
            i = end_line + 1
            continue

        # Module-level constant (UPPER_CASE) -- only at indent 0, outside classes
        if not class_stack and not line[0].isspace():
            const_match = _CONST_RE.match(stripped)
            if const_match:
                const_name = const_match.group(1)
                if _is_public(const_name, all_names):
                    fqn = f"{module}.{const_name}" if module else const_name
                    records.append(_build_record(
                        fqn=fqn,
                        namespace=module,
                        class_name="",
                        member_name=const_name,
                        member_type="field",
                        signature=const_name,
                        summary="",
                        file_path=rel_path,
                    ))
                pending_decorators = []
                i += 1
                continue

        pending_decorators = []
        i += 1

    return records


def _indent_level(line: str) -> int:
    """Return the indentation level (number of leading spaces)."""
    return len(line) - len(line.lstrip())


def _is_public(name: str, all_names: set[str] | None) -> bool:
    """Determine if a name is public.

    If __all__ is defined, only names in __all__ are public.
    Otherwise, names not starting with _ are public.
    """
    if all_names is not None:
        return name in all_names
    return not name.startswith("_")


def _extract_all(lines: list[str]) -> set[str] | None:
    """Extract __all__ list if present."""
    text = "\n".join(lines)
    match = _ALL_RE.search(text)
    if not match:
        return None

    # Find the closing bracket
    start = match.start()
    bracket_depth = 0
    content = ""
    for ch in text[start:]:
        content += ch
        if ch == "[":
            bracket_depth += 1
        elif ch == "]":
            bracket_depth -= 1
            if bracket_depth == 0:
                break

    # Extract quoted names
    names = set(re.findall(r'["\'](\w+)["\']', content))
    return names if names else None


def _collect_signature(lines: list[str], start: int) -> tuple[str, int]:
    """Collect a function signature that may span multiple lines.

    Returns (full_sig_text, last_line_index).
    """
    sig = lines[start]
    i = start

    # Count parens to handle multi-line signatures
    depth = sig.count("(") - sig.count(")")
    while depth > 0 and i + 1 < len(lines):
        i += 1
        sig += " " + lines[i].strip()
        depth += lines[i].count("(") - lines[i].count(")")

    return sig, i


def _extract_params(sig_text: str, skip_self: bool = False) -> str:
    """Extract parameter list from a function signature string."""
    # Find content between first ( and last )
    paren_start = sig_text.find("(")
    paren_end = sig_text.rfind(")")
    if paren_start == -1 or paren_end == -1:
        return ""

    params_str = sig_text[paren_start + 1:paren_end].strip()
    if not params_str:
        return ""

    # Split params, handling nested brackets/parens
    params = _split_params(params_str)

    if skip_self and params:
        first = params[0].strip()
        if first in ("self", "cls") or first.startswith("self:") or first.startswith("cls:"):
            params = params[1:]

    # Clean up whitespace
    cleaned = []
    for p in params:
        p = re.sub(r"\s+", " ", p).strip()
        if p:
            cleaned.append(p)

    return ", ".join(cleaned)


def _split_params(params_str: str) -> list[str]:
    """Split parameter string by commas, respecting nested brackets."""
    params = []
    current = ""
    depth = 0

    for ch in params_str:
        if ch in ("(", "[", "{"):
            depth += 1
            current += ch
        elif ch in (")", "]", "}"):
            depth -= 1
            current += ch
        elif ch == "," and depth == 0:
            params.append(current)
            current = ""
        else:
            current += ch

    if current.strip():
        params.append(current)

    return params


def _extract_return_type(sig_text: str) -> str:
    """Extract return type annotation from signature."""
    match = _RETURN_TYPE_RE.search(sig_text)
    if match:
        return match.group(1).strip()
    return ""


def _extract_docstring(lines: list[str], decl_line: int) -> str:
    """Extract docstring from the line(s) after a declaration."""
    i = decl_line + 1
    while i < len(lines) and not lines[i].strip():
        i += 1

    if i >= len(lines):
        return ""

    stripped = lines[i].strip()

    # Check for triple-quote docstring
    for quote in (_DOCSTRING_TRIPLE_DQ, _DOCSTRING_TRIPLE_SQ):
        if stripped.startswith(quote):
            # Single-line docstring?
            if stripped.endswith(quote) and len(stripped) > 6:
                return stripped[3:-3].strip()

            # Multi-line docstring
            doc_lines = [stripped[3:]]
            i += 1
            while i < len(lines):
                line = lines[i]
                if quote in line:
                    end_idx = line.find(quote)
                    doc_lines.append(line[:end_idx].strip())
                    break
                doc_lines.append(line.strip())
                i += 1

            full = "\n".join(doc_lines).strip()
            # Return first paragraph as summary
            paragraphs = re.split(r"\n\s*\n", full)
            return paragraphs[0].replace("\n", " ").strip() if paragraphs else full

    return ""


def _file_to_module(path: Path, base_dir: Path) -> str:
    """Convert a file path to a Python module path.

    Walks up from the file looking for __init__.py to determine package boundaries.
    """
    rel = path.relative_to(base_dir)
    parts = list(rel.parts)

    # Remove .py extension from last part
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]

    # Remove __init__ (package init files)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]

    # Find the highest directory that contains __init__.py (package root)
    # Walk from top to bottom, only include parts that are packages
    module_parts = []
    current = base_dir
    for part in parts[:-1]:  # Check directories (not the file itself)
        current = current / part
        init = current / "__init__.py"
        if init.exists():
            module_parts.append(part)
        else:
            # Non-package directory, reset
            module_parts = []

    # Add the final module name
    if parts:
        module_parts.append(parts[-1])

    return ".".join(module_parts)


def _method_fqn(base_fqn: str, name: str, params_str: str) -> str:
    """Build a disambiguated FQN for functions by appending param types."""
    param_types = []
    if params_str.strip():
        for p in params_str.split(","):
            p = p.strip()
            # Extract type from "name: type = default" or just "name"
            if ":" in p:
                type_part = p.split(":")[1].strip()
                # Remove default value
                if "=" in type_part:
                    type_part = type_part.split("=")[0].strip()
                param_types.append(type_part)
            else:
                # No type hint, use parameter name
                name_part = p.split("=")[0].strip()
                if name_part.startswith("*"):
                    param_types.append(name_part)
                else:
                    param_types.append(name_part)

    if param_types:
        return f"{base_fqn}.{name}({','.join(param_types)})"
    return f"{base_fqn}.{name}"


def _build_record(
    fqn: str,
    namespace: str,
    class_name: str,
    member_name: str,
    member_type: str,
    signature: str,
    summary: str,
    file_path: str,
) -> dict:
    return {
        "fqn": fqn,
        "namespace": namespace,
        "class_name": class_name,
        "member_name": member_name,
        "member_type": member_type,
        "signature": signature,
        "summary": summary,
        "params_json": [],
        "returns_text": "",
        "file_path": file_path,
    }
