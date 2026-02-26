"""Go parser that captures exported API declarations.

Scans Go files tracking brace-depth scope, captures exported types (struct,
interface, defined, alias), functions, methods (with receivers), struct fields,
interface methods, and var/const declarations (including grouped blocks).

Go visibility rule: capitalized first letter = exported (public).
Doc comments are consecutive // lines immediately before a declaration.
"""

import re
from pathlib import Path

from .base import BaseParser


# --- Skip patterns ---

_SKIP_DIRS = frozenset({
    "vendor", "testdata", ".git", "node_modules", "third_party",
    "examples", "example",
})

_SKIP_FILE_SUFFIX = "_test.go"

# Go reserved words that can't be identifiers
_GO_KEYWORDS = frozenset({
    "func", "type", "var", "const", "import", "package",
    "return", "if", "else", "for", "range", "switch",
    "case", "default", "go", "defer", "select", "chan",
    "map", "struct", "interface", "true", "false", "nil",
    "iota", "break", "continue", "goto", "fallthrough",
})

# Max length for const/var values in signatures before truncation
_MAX_VALUE_LEN = 60

# --- Regex patterns ---

# Package declaration: package main
_PACKAGE_RE = re.compile(r"^\s*package\s+(\w+)")

# Function: func FuncName(params) returns
_FUNC_RE = re.compile(
    r"^\s*func\s+"
    r"(\w+)"                            # function name
    r"(?:\[[^\]]*\])?"                  # optional type params [T any]
    r"\s*\("                            # open paren
)

# Method: func (r *Type) MethodName(params) returns
_METHOD_RE = re.compile(
    r"^\s*func\s+"
    r"\(\s*\w+\s+"                      # receiver param name
    r"(\*?)"                            # optional pointer
    r"(\w+)"                            # receiver type name
    r"(?:\[[^\]]*\])?"                  # optional type params on receiver
    r"\s*\)\s*"
    r"(\w+)"                            # method name
    r"(?:\[[^\]]*\])?"                  # optional type params on method
    r"\s*\("                            # open paren
)

# Type declaration (single): type Name struct/interface/underlying
_TYPE_DECL_RE = re.compile(
    r"^\s*type\s+"
    r"(\w+)"                            # type name
    r"(?:\[[^\]]*\])?"                  # optional type params
    r"\s+"
    r"(.*)"                             # rest: struct/interface/underlying
)

# Type alias: type Name = underlying
_TYPE_ALIAS_RE = re.compile(
    r"^\s*type\s+"
    r"(\w+)"                            # type name
    r"\s*=\s*"
    r"(.*)"                             # underlying type
)

# Grouped type entry (inside type(...)): Name struct/interface/underlying
_GROUP_TYPE_ENTRY_RE = re.compile(
    r"^\s*(\w+)"                        # type name
    r"(?:\[[^\]]*\])?"                  # optional type params
    r"\s+"
    r"(.*)"                             # rest
)

# Grouped type alias entry: Name = underlying
_GROUP_TYPE_ALIAS_RE = re.compile(
    r"^\s*(\w+)"                        # type name
    r"\s*=\s*"
    r"(.*)"                             # underlying type
)

# Single var: var Name Type [= value]
_VAR_RE = re.compile(
    r"^\s*var\s+"
    r"(\w+)\s+"
    r"([\w.*\[\]]+(?:\[[^\]]*\])?)"     # type (possibly generic/slice/pointer)
)

# Single const: const Name [Type] = value
_CONST_RE = re.compile(
    r"^\s*const\s+"
    r"(\w+)"                            # name
    r"(?:\s+([\w.*\[\]]+))?"            # optional type
    r"\s*="                             # = sign
)

# Grouped var/const entry: Name Type = value  OR  Name = value  OR  Name (iota/implicit)
_GROUP_VAR_CONST_RE = re.compile(
    r"^\s*(\w+)"                        # name
    r"(?:\s+([\w.*\[\]]+(?:\[[^\]]*\])?))?"  # optional type
    r"(?:\s*=\s*(.*))?"                 # optional = value
)

# Struct field: FieldName Type `tags`
_STRUCT_FIELD_RE = re.compile(
    r"^\s*(\w+)\s+"                     # field name
    r"(.+?)"                            # type (greedy-ish)
    r"(?:\s*`[^`]*`)?"                  # optional struct tags
    r"\s*(?://.*)?$"                    # optional line comment
)

# Interface method: MethodName(params) returns
_IFACE_METHOD_RE = re.compile(
    r"^\s*(\w+)"                        # method name
    r"(?:\[[^\]]*\])?"                  # optional type params
    r"\s*\("                            # open paren
)

# Grouped declaration opener: type(  var(  const(
_GROUP_OPEN_RE = re.compile(r"^\s*(type|var|const)\s*\(\s*$")


class GoParser(BaseParser):
    """Parser for Go source files."""

    @property
    def file_extensions(self) -> list[str]:
        return [".go"]

    def parse_directory(self, directory: Path) -> list[dict]:
        """Override to skip vendor/testdata/test files."""
        records: list[dict] = []
        for f in sorted(directory.rglob("*.go")):
            parts = f.relative_to(directory).parts
            # Skip dirs
            if any(p in _SKIP_DIRS or p.startswith("_") for p in parts):
                continue
            # Skip test files
            if f.name.endswith(_SKIP_FILE_SUFFIX):
                continue
            try:
                records.extend(self.parse_file(f, directory))
            except Exception as e:
                import sys
                print(f"codesurface: failed to parse {f}: {e}", file=sys.stderr)
                continue
        return records

    def parse_file(self, path: Path, base_dir: Path) -> list[dict]:
        return _parse_go_file(path, base_dir)


def _is_exported(name: str) -> bool:
    """Go visibility: exported if first letter is uppercase."""
    return bool(name) and name[0].isupper()


def _parse_go_file(path: Path, base_dir: Path) -> list[dict]:
    """Parse a single .go file and extract exported API members."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return []

    rel_path = path.relative_to(base_dir).as_posix()
    lines = text.splitlines()

    # Skip generated files (Go standard: "Code generated ... DO NOT EDIT")
    for line in lines[:20]:
        if "Code generated" in line and "DO NOT EDIT" in line:
            return []

    records: list[dict] = []

    # State
    package = ""
    brace_depth = 0
    in_multiline_comment = False
    in_backtick = False

    # Current type body tracking (at most one, Go has no nested types)
    current_type: str = ""          # type name (e.g. "Server")
    current_type_kind: str = ""     # "struct" or "interface"
    type_brace_depth: int = -1      # brace depth when type body opened

    # Grouped declaration state
    in_group: str | None = None     # "type", "var", or "const"
    group_const_type: str = ""      # remembered type for iota const blocks
    # For type groups with nested struct/interface bodies:
    group_type_name: str = ""
    group_type_kind: str = ""
    group_type_brace_depth: int = -1

    i = 0
    while i < len(lines):
        line = lines[i]

        # --- Backtick raw string continuation ---
        if in_backtick:
            if "`" in line:
                # Count backticks to determine state
                count = line.count("`")
                if count % 2 == 1:
                    in_backtick = False
                # Still need to count braces outside backtick portions
                brace_delta = _count_braces(line, in_backtick=False)
                brace_depth += brace_delta
            i += 1
            continue

        # --- Multi-line comment continuation ---
        if in_multiline_comment:
            if "*/" in line:
                in_multiline_comment = False
                # Count braces after the comment end
                after_comment = line[line.index("*/") + 2:]
                brace_depth += _count_braces(after_comment)
            i += 1
            continue

        stripped = line.strip()

        # Empty line
        if not stripped:
            i += 1
            continue

        # Start of multi-line comment
        if "/*" in stripped and "*/" not in stripped:
            if not stripped.startswith("//"):
                in_multiline_comment = True
                pre_comment = line[:line.find("/*")]
                brace_depth += _count_braces(pre_comment)
                i += 1
                continue

        # Single-line comment (skip but don't consume -- doc comments read on demand)
        if stripped.startswith("//"):
            i += 1
            continue

        # Count braces for this line (string-aware)
        brace_delta = _count_braces(line)

        # Check if line starts a backtick string that doesn't close
        backtick_count = _count_raw_backticks(line)
        if backtick_count % 2 == 1:
            in_backtick = True

        new_depth = brace_depth + brace_delta

        # === GROUPED DECLARATION BLOCK ===
        if in_group is not None:
            # Check for close of group
            if ")" in stripped:
                # Closing paren for group block
                in_group = None
                group_const_type = ""
                # Handle any type body that was open inside group
                if group_type_name:
                    group_type_name = ""
                    group_type_kind = ""
                    group_type_brace_depth = -1
                # Also close any current_type that was opened inside group
                if current_type:
                    current_type = ""
                    current_type_kind = ""
                    type_brace_depth = -1
                brace_depth = new_depth
                i += 1
                continue

            # Inside a struct/interface body within a type group
            if group_type_name and group_type_kind:
                if brace_depth > group_type_brace_depth:
                    # Parse members inside this grouped type body
                    if group_type_kind == "struct":
                        _try_parse_struct_field(
                            stripped, lines, i, package,
                            group_type_name, rel_path, records,
                        )
                    elif group_type_kind == "interface":
                        _try_parse_interface_method(
                            stripped, lines, i, package,
                            group_type_name, rel_path, records,
                        )
                    brace_depth = new_depth
                    # Check if type body closed
                    if new_depth <= group_type_brace_depth:
                        group_type_name = ""
                        group_type_kind = ""
                        group_type_brace_depth = -1
                    i += 1
                    continue
                else:
                    # Type body closed
                    group_type_name = ""
                    group_type_kind = ""
                    group_type_brace_depth = -1
                    # Fall through to parse next entry

            if in_group == "type":
                _parse_group_type_entry(
                    stripped, lines, i, package, rel_path, records,
                )
                # Check if this entry opens a struct/interface body
                alias_m = _GROUP_TYPE_ALIAS_RE.match(stripped)
                if alias_m:
                    brace_depth = new_depth
                    i += 1
                    continue
                entry_m = _GROUP_TYPE_ENTRY_RE.match(stripped)
                if entry_m:
                    name = entry_m.group(1)
                    rest = entry_m.group(2).strip()
                    if _is_exported(name):
                        if rest.startswith("struct") and "{" in line:
                            group_type_name = name
                            group_type_kind = "struct"
                            group_type_brace_depth = brace_depth
                        elif rest.startswith("interface") and "{" in line:
                            group_type_name = name
                            group_type_kind = "interface"
                            group_type_brace_depth = brace_depth
                brace_depth = new_depth
                i += 1
                continue

            elif in_group in ("var", "const"):
                _parse_group_var_const_entry(
                    stripped, lines, i, package, rel_path, records,
                    in_group, group_const_type,
                )
                # Track type for subsequent iota entries in const blocks
                if in_group == "const":
                    m = _GROUP_VAR_CONST_RE.match(stripped)
                    if m and m.group(2):
                        group_const_type = m.group(2)
                brace_depth = new_depth
                i += 1
                continue

            brace_depth = new_depth
            i += 1
            continue

        # === INSIDE TYPE BODY (struct/interface fields) ===
        if current_type and current_type_kind:
            if brace_depth > type_brace_depth:
                if current_type_kind == "struct":
                    _try_parse_struct_field(
                        stripped, lines, i, package,
                        current_type, rel_path, records,
                    )
                elif current_type_kind == "interface":
                    _try_parse_interface_method(
                        stripped, lines, i, package,
                        current_type, rel_path, records,
                    )
                brace_depth = new_depth
                if new_depth <= type_brace_depth:
                    current_type = ""
                    current_type_kind = ""
                    type_brace_depth = -1
                i += 1
                continue
            else:
                # Type body closed
                current_type = ""
                current_type_kind = ""
                type_brace_depth = -1

        # === TOP-LEVEL DECLARATIONS ===

        # Package
        pkg_m = _PACKAGE_RE.match(line)
        if pkg_m:
            package = pkg_m.group(1)
            brace_depth = new_depth
            i += 1
            continue

        # Import (skip)
        if stripped.startswith("import ") or stripped == "import (":
            if "(" in stripped and ")" not in stripped:
                # Multi-line import block, skip to closing paren
                brace_depth = new_depth
                i += 1
                while i < len(lines):
                    if ")" in lines[i]:
                        brace_depth += _count_braces(lines[i])
                        i += 1
                        break
                    brace_depth += _count_braces(lines[i])
                    i += 1
                continue
            brace_depth = new_depth
            i += 1
            continue

        # Grouped declaration opener: type( / var( / const(
        group_m = _GROUP_OPEN_RE.match(line)
        if group_m:
            in_group = group_m.group(1)
            group_const_type = ""
            brace_depth = new_depth
            i += 1
            continue

        # Method with receiver: func (r *Type) Name(...)
        method_m = _METHOD_RE.match(line)
        if method_m:
            pointer = method_m.group(1)
            receiver_type = method_m.group(2)
            method_name = method_m.group(3)

            if _is_exported(method_name) and _is_exported(receiver_type):
                full_sig, _ = _collect_signature(lines, i)
                params_str, returns_str = _extract_func_parts(full_sig, method_name)
                doc = _look_back_for_doc_comment(lines, i)

                returns_part = f" {returns_str}" if returns_str else ""
                sig = f"{method_name}({params_str}){returns_part}"

                fqn = f"{package}.{receiver_type}.{method_name}"
                records.append(_build_record(
                    fqn=fqn,
                    namespace=package,
                    class_name=receiver_type,
                    member_name=method_name,
                    member_type="method",
                    signature=sig,
                    summary=doc,
                    file_path=rel_path,
                ))

            brace_depth = new_depth
            i += 1
            continue

        # Function: func Name(...)
        func_m = _FUNC_RE.match(line)
        if func_m:
            func_name = func_m.group(1)

            if _is_exported(func_name):
                full_sig, _ = _collect_signature(lines, i)
                params_str, returns_str = _extract_func_parts(full_sig, func_name)
                doc = _look_back_for_doc_comment(lines, i)

                returns_part = f" {returns_str}" if returns_str else ""
                sig = f"func {func_name}({params_str}){returns_part}"

                fqn = f"{package}.{func_name}"
                records.append(_build_record(
                    fqn=fqn,
                    namespace=package,
                    class_name="",
                    member_name=func_name,
                    member_type="method",
                    signature=sig,
                    summary=doc,
                    file_path=rel_path,
                ))

            brace_depth = new_depth
            i += 1
            continue

        # Type alias: type Name = underlying
        alias_m = _TYPE_ALIAS_RE.match(line)
        if alias_m:
            type_name = alias_m.group(1)
            underlying = alias_m.group(2).strip()

            if _is_exported(type_name):
                doc = _look_back_for_doc_comment(lines, i)
                sig = f"type {type_name} = {underlying}"
                fqn = f"{package}.{type_name}"
                records.append(_build_record(
                    fqn=fqn,
                    namespace=package,
                    class_name=type_name,
                    member_name="",
                    member_type="type",
                    signature=sig,
                    summary=doc,
                    file_path=rel_path,
                ))

            brace_depth = new_depth
            i += 1
            continue

        # Type declaration: type Name struct/interface/underlying
        type_m = _TYPE_DECL_RE.match(line)
        if type_m:
            type_name = type_m.group(1)
            rest = type_m.group(2).strip()

            if _is_exported(type_name):
                doc = _look_back_for_doc_comment(lines, i)

                if rest.startswith("struct"):
                    sig = f"type {type_name} struct"
                    fqn = f"{package}.{type_name}"
                    records.append(_build_record(
                        fqn=fqn,
                        namespace=package,
                        class_name=type_name,
                        member_name="",
                        member_type="type",
                        signature=sig,
                        summary=doc,
                        file_path=rel_path,
                    ))
                    if "{" in line:
                        current_type = type_name
                        current_type_kind = "struct"
                        type_brace_depth = brace_depth

                elif rest.startswith("interface"):
                    sig = f"type {type_name} interface"
                    fqn = f"{package}.{type_name}"
                    records.append(_build_record(
                        fqn=fqn,
                        namespace=package,
                        class_name=type_name,
                        member_name="",
                        member_type="type",
                        signature=sig,
                        summary=doc,
                        file_path=rel_path,
                    ))
                    if "{" in line:
                        current_type = type_name
                        current_type_kind = "interface"
                        type_brace_depth = brace_depth

                else:
                    # Defined type or type with complex underlying
                    underlying = rest.rstrip("{").strip()
                    sig = f"type {type_name} {underlying}"
                    fqn = f"{package}.{type_name}"
                    records.append(_build_record(
                        fqn=fqn,
                        namespace=package,
                        class_name=type_name,
                        member_name="",
                        member_type="type",
                        signature=sig,
                        summary=doc,
                        file_path=rel_path,
                    ))

            brace_depth = new_depth
            i += 1
            continue

        # Single var: var Name Type
        var_m = _VAR_RE.match(line)
        if var_m:
            var_name = var_m.group(1)
            var_type = var_m.group(2)

            if _is_exported(var_name):
                doc = _look_back_for_doc_comment(lines, i)
                sig = f"var {var_name} {var_type}"
                fqn = f"{package}.{var_name}"
                records.append(_build_record(
                    fqn=fqn,
                    namespace=package,
                    class_name="",
                    member_name=var_name,
                    member_type="field",
                    signature=sig,
                    summary=doc,
                    file_path=rel_path,
                ))

            brace_depth = new_depth
            i += 1
            continue

        # Single const: const Name [Type] = value
        const_m = _CONST_RE.match(line)
        if const_m:
            const_name = const_m.group(1)
            const_type = const_m.group(2) or ""

            if _is_exported(const_name):
                doc = _look_back_for_doc_comment(lines, i)
                # Extract value after =
                eq_idx = line.index("=")
                value = line[eq_idx + 1:].strip().rstrip("{").strip()
                # Truncate long values
                if len(value) > _MAX_VALUE_LEN:
                    value = value[:_MAX_VALUE_LEN - 3] + "..."
                type_part = f" {const_type}" if const_type else ""
                sig = f"const {const_name}{type_part} = {value}"
                fqn = f"{package}.{const_name}"
                records.append(_build_record(
                    fqn=fqn,
                    namespace=package,
                    class_name="",
                    member_name=const_name,
                    member_type="field",
                    signature=sig,
                    summary=doc,
                    file_path=rel_path,
                ))

            brace_depth = new_depth
            i += 1
            continue

        brace_depth = new_depth
        i += 1

    # Deduplicate within file (keep first occurrence)
    unique: list[dict] = []
    seen: set[str] = set()
    for rec in records:
        fqn = rec["fqn"]
        if fqn not in seen:
            seen.add(fqn)
            unique.append(rec)
    return unique


# --- Struct field parsing ---

def _try_parse_struct_field(
    stripped: str, lines: list[str], idx: int,
    package: str, struct_name: str, file_path: str,
    records: list[dict],
) -> None:
    """Parse an exported field inside a struct body."""
    if not stripped or stripped.startswith("//") or stripped in ("{", "}"):
        return
    if stripped.startswith("/*") or stripped.startswith("*"):
        return

    # Skip embedded types (single word on a line, no explicit field name + type pair)
    # Embedded: just a type name like `sync.Mutex` or `*Foo`
    # We want "FieldName Type" patterns
    field_m = _STRUCT_FIELD_RE.match(stripped)
    if not field_m:
        return

    field_name = field_m.group(1)
    field_type = field_m.group(2).strip()

    # Clean up: remove struct tags from type
    if "`" in field_type:
        field_type = field_type[:field_type.index("`")].strip()
    # Remove trailing comments
    if "//" in field_type:
        field_type = field_type[:field_type.index("//")].strip()

    if not _is_exported(field_name):
        return

    # Skip if field_name is a Go keyword
    if field_name in _GO_KEYWORDS:
        return

    doc = _look_back_for_doc_comment(lines, idx)
    sig = f"{field_name} {field_type}"
    fqn = f"{package}.{struct_name}.{field_name}"

    records.append(_build_record(
        fqn=fqn,
        namespace=package,
        class_name=struct_name,
        member_name=field_name,
        member_type="field",
        signature=sig,
        summary=doc,
        file_path=file_path,
    ))


# --- Interface method parsing ---

def _try_parse_interface_method(
    stripped: str, lines: list[str], idx: int,
    package: str, iface_name: str, file_path: str,
    records: list[dict],
) -> None:
    """Parse a method inside an interface body."""
    if not stripped or stripped.startswith("//") or stripped in ("{", "}"):
        return
    if stripped.startswith("/*") or stripped.startswith("*"):
        return

    # Interface can embed other types -- single word lines like `io.Reader`
    # or constraint unions like `~int | ~string`. Skip those.
    if "(" not in stripped:
        return

    method_m = _IFACE_METHOD_RE.match(stripped)
    if not method_m:
        return

    method_name = method_m.group(1)
    if not _is_exported(method_name):
        return

    full_sig, _ = _collect_signature(lines, idx)
    params_str, returns_str = _extract_iface_method_parts(full_sig, method_name)
    doc = _look_back_for_doc_comment(lines, idx)

    returns_part = f" {returns_str}" if returns_str else ""
    sig = f"{method_name}({params_str}){returns_part}"
    fqn = f"{package}.{iface_name}.{method_name}"

    records.append(_build_record(
        fqn=fqn,
        namespace=package,
        class_name=iface_name,
        member_name=method_name,
        member_type="method",
        signature=sig,
        summary=doc,
        file_path=file_path,
    ))


# --- Grouped type entry parsing ---

def _parse_group_type_entry(
    stripped: str, lines: list[str], idx: int,
    package: str, file_path: str, records: list[dict],
) -> None:
    """Parse a single entry inside a type(...) block."""
    if not stripped or stripped.startswith("//") or stripped in ("{", "}", ")"):
        return
    if stripped.startswith("/*") or stripped.startswith("*"):
        return

    # Type alias entry: Name = underlying
    alias_m = _GROUP_TYPE_ALIAS_RE.match(stripped)
    if alias_m:
        name = alias_m.group(1)
        underlying = alias_m.group(2).strip()
        if _is_exported(name):
            doc = _look_back_for_doc_comment(lines, idx)
            sig = f"type {name} = {underlying}"
            fqn = f"{package}.{name}"
            records.append(_build_record(
                fqn=fqn,
                namespace=package,
                class_name=name,
                member_name="",
                member_type="type",
                signature=sig,
                summary=doc,
                file_path=file_path,
            ))
        return

    # Normal type entry: Name struct/interface/underlying
    entry_m = _GROUP_TYPE_ENTRY_RE.match(stripped)
    if not entry_m:
        return

    name = entry_m.group(1)
    rest = entry_m.group(2).strip()

    if not _is_exported(name):
        return

    doc = _look_back_for_doc_comment(lines, idx)

    if rest.startswith("struct"):
        sig = f"type {name} struct"
        fqn = f"{package}.{name}"
        records.append(_build_record(
            fqn=fqn,
            namespace=package,
            class_name=name,
            member_name="",
            member_type="type",
            signature=sig,
            summary=doc,
            file_path=file_path,
        ))
    elif rest.startswith("interface"):
        sig = f"type {name} interface"
        fqn = f"{package}.{name}"
        records.append(_build_record(
            fqn=fqn,
            namespace=package,
            class_name=name,
            member_name="",
            member_type="type",
            signature=sig,
            summary=doc,
            file_path=file_path,
        ))
    else:
        underlying = rest.rstrip("{").strip()
        sig = f"type {name} {underlying}"
        fqn = f"{package}.{name}"
        records.append(_build_record(
            fqn=fqn,
            namespace=package,
            class_name=name,
            member_name="",
            member_type="type",
            signature=sig,
            summary=doc,
            file_path=file_path,
        ))


# --- Grouped var/const entry parsing ---

def _parse_group_var_const_entry(
    stripped: str, lines: list[str], idx: int,
    package: str, file_path: str, records: list[dict],
    group_kind: str, remembered_type: str,
) -> None:
    """Parse a single entry inside a var(...) or const(...) block."""
    if not stripped or stripped.startswith("//") or stripped in ("{", "}", ")"):
        return
    if stripped.startswith("/*") or stripped.startswith("*"):
        return

    m = _GROUP_VAR_CONST_RE.match(stripped)
    if not m:
        return

    name = m.group(1)
    explicit_type = m.group(2) or ""
    value = m.group(3) or ""

    if not _is_exported(name):
        return

    # Skip if name is a Go keyword
    if name in _GO_KEYWORDS:
        return

    doc = _look_back_for_doc_comment(lines, idx)
    the_type = explicit_type or remembered_type

    if group_kind == "const":
        value_str = value.strip() if value else ""
        if len(value_str) > _MAX_VALUE_LEN:
            value_str = value_str[:_MAX_VALUE_LEN - 3] + "..."
        type_part = f" {the_type}" if the_type else ""
        if value_str:
            sig = f"const {name}{type_part} = {value_str}"
        else:
            sig = f"const {name}{type_part}"
    else:
        # var
        type_part = f" {the_type}" if the_type else ""
        sig = f"var {name}{type_part}"

    fqn = f"{package}.{name}"
    records.append(_build_record(
        fqn=fqn,
        namespace=package,
        class_name="",
        member_name=name,
        member_type="field",
        signature=sig,
        summary=doc,
        file_path=file_path,
    ))


# --- Doc comment extraction ---

def _look_back_for_doc_comment(lines: list[str], decl_idx: int) -> str:
    """Look backwards from a declaration for consecutive // comment lines."""
    doc_lines: list[str] = []
    i = decl_idx - 1

    while i >= 0:
        stripped = lines[i].strip()
        if stripped.startswith("//"):
            text = stripped[2:].strip()
            doc_lines.append(text)
            i -= 1
        else:
            break

    if not doc_lines:
        return ""

    # Reverse since we collected bottom-up
    doc_lines.reverse()

    # Return first sentence as summary
    full = " ".join(doc_lines)
    # Find first sentence boundary (period followed by space or end)
    for j, ch in enumerate(full):
        if ch == "." and (j + 1 >= len(full) or full[j + 1] == " "):
            return full[:j + 1]

    return full


# --- Signature collection ---

def _collect_signature(lines: list[str], start: int) -> tuple[str, int]:
    """Collect a signature that may span multiple lines (balanced parens)."""
    sig = lines[start]
    i = start
    paren_depth = _count_parens(sig)

    while paren_depth > 0 and i + 1 < len(lines):
        i += 1
        next_line = lines[i]
        sig += " " + next_line.strip()
        paren_depth += _count_parens(next_line)

    return sig, i


def _count_parens(line: str) -> int:
    """Count net parenthesis depth change, skipping inside strings."""
    depth = 0
    in_double = False
    in_backtick = False
    in_rune = False
    escape = False

    for ch in line:
        if escape:
            escape = False
            continue
        if ch == "\\" and not in_backtick:
            escape = True
            continue

        if in_rune:
            if ch == "'":
                in_rune = False
            continue
        if in_double:
            if ch == '"':
                in_double = False
            continue
        if in_backtick:
            if ch == '`':
                in_backtick = False
            continue

        if ch == "'":
            in_rune = True
        elif ch == '"':
            in_double = True
        elif ch == '`':
            in_backtick = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1

    return depth


# --- Function/method signature parsing ---

def _extract_func_parts(full_sig: str, func_name: str) -> tuple[str, str]:
    """Extract params and return type from a func/method signature.

    Returns (params_str, returns_str).
    """
    # Find the parameter list: the first balanced (...) after func_name
    name_idx = full_sig.find(func_name)
    if name_idx == -1:
        return "", ""

    search_from = name_idx + len(func_name)
    # Skip optional type params [...]
    if search_from < len(full_sig) and full_sig[search_from] == "[":
        bracket_depth = 0
        for j in range(search_from, len(full_sig)):
            if full_sig[j] == "[":
                bracket_depth += 1
            elif full_sig[j] == "]":
                bracket_depth -= 1
                if bracket_depth == 0:
                    search_from = j + 1
                    break

    paren_start = full_sig.find("(", search_from)
    if paren_start == -1:
        return "", ""

    # Find matching close
    paren_end = _find_matching_paren(full_sig, paren_start)
    if paren_end == -1:
        return "", ""

    params = full_sig[paren_start + 1:paren_end].strip()
    params = re.sub(r"\s+", " ", params)

    # Return type is everything between closing paren and opening brace or end
    after = full_sig[paren_end + 1:].strip()
    # Strip opening brace and everything after
    if "{" in after:
        after = after[:after.index("{")].strip()

    returns = after
    # Clean up whitespace
    returns = re.sub(r"\s+", " ", returns).strip()

    return params, returns


def _extract_iface_method_parts(full_sig: str, method_name: str) -> tuple[str, str]:
    """Extract params and return type from an interface method signature."""
    name_idx = full_sig.strip().find(method_name)
    if name_idx == -1:
        return "", ""

    search_from = name_idx + len(method_name)
    # Skip optional type params [...]
    trimmed = full_sig.strip()
    if search_from < len(trimmed) and trimmed[search_from] == "[":
        bracket_depth = 0
        for j in range(search_from, len(trimmed)):
            if trimmed[j] == "[":
                bracket_depth += 1
            elif trimmed[j] == "]":
                bracket_depth -= 1
                if bracket_depth == 0:
                    search_from = j + 1
                    break

    paren_start = trimmed.find("(", search_from)
    if paren_start == -1:
        return "", ""

    paren_end = _find_matching_paren(trimmed, paren_start)
    if paren_end == -1:
        return "", ""

    params = trimmed[paren_start + 1:paren_end].strip()
    params = re.sub(r"\s+", " ", params)

    after = trimmed[paren_end + 1:].strip()
    # Remove trailing comment
    if "//" in after:
        after = after[:after.index("//")].strip()

    return params, after


def _find_matching_paren(text: str, start: int) -> int:
    """Find the index of the matching closing paren."""
    depth = 0
    for j in range(start, len(text)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    return -1


# --- Brace counting (string-aware, with backtick support) ---

def _count_braces(line: str, in_backtick: bool = False) -> int:
    """Count net brace depth changes, skipping inside strings.

    Handles Go's three string types: "double", 'rune', `backtick`.
    """
    depth = 0
    in_double = False
    in_bt = in_backtick
    in_rune = False
    escape = False

    for ch in line:
        if escape:
            escape = False
            continue
        if ch == "\\" and not in_bt:
            escape = True
            continue

        if in_rune:
            if ch == "'":
                in_rune = False
            continue
        if in_double:
            if ch == '"':
                in_double = False
            continue
        if in_bt:
            if ch == '`':
                in_bt = False
            continue

        if ch == "'":
            in_rune = True
        elif ch == '"':
            in_double = True
        elif ch == '`':
            in_bt = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1

    return depth


def _count_raw_backticks(line: str) -> int:
    """Count backticks that are not inside double-quote or rune strings.

    Used to track multi-line raw string state across lines.
    """
    count = 0
    in_double = False
    in_rune = False
    escape = False

    for ch in line:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue

        if in_rune:
            if ch == "'":
                in_rune = False
            continue
        if in_double:
            if ch == '"':
                in_double = False
            continue

        if ch == "'":
            in_rune = True
        elif ch == '"':
            in_double = True
        elif ch == '`':
            count += 1

    return count


# --- Record builder ---

def _build_record(**kwargs) -> dict:
    return kwargs
