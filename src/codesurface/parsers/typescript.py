"""TypeScript parser that captures exported declarations.

Scans TypeScript/TSX files tracking brace-depth scope, captures exported
classes, interfaces, types, enums, namespaces, functions, and their members.
JSDoc comments (/** ... */) are extracted as summaries.
"""

import re
from pathlib import Path

from .base import BaseParser


# --- Skip patterns ---

_SKIP_DIRS = frozenset({
    "node_modules", "dist", "build", ".git", ".next",
    "__tests__", "__mocks__", "coverage", ".turbo", ".cache",
    ".tox", ".mypy_cache", ".venv", "venv",
})

_SKIP_SUFFIXES = (
    ".d.ts",
    ".test.ts", ".test.tsx",
    ".spec.ts", ".spec.tsx",
    ".stories.ts", ".stories.tsx",
)

# --- Regex patterns ---

# Re-export lines to skip entirely
_REEXPORT_RE = re.compile(
    r"^\s*export\s+(?:\{[^}]*\}\s+from|type\s+\{[^}]*\}\s+from|\*\s+from|\*\s+as\s+\w+\s+from)"
)

# export class/abstract class/interface/enum/const enum/namespace
_EXPORT_TYPE_RE = re.compile(
    r"^\s*export\s+"
    r"(?:default\s+)?"
    r"(?:declare\s+)?"
    r"(?:abstract\s+)?"
    r"(class|interface|enum|namespace)\s+"
    r"(\w+)"
    r"(?:<[^{]*>)?"                       # optional generics
    r"(?:\s+extends\s+([^{]+?))?"         # optional extends
    r"(?:\s+implements\s+([^{]+?))?"      # optional implements
    r"\s*\{?"
)

# export const enum Name {
_EXPORT_CONST_ENUM_RE = re.compile(
    r"^\s*export\s+(?:declare\s+)?const\s+enum\s+(\w+)"
)

# export type Name = ...
_EXPORT_TYPE_ALIAS_RE = re.compile(
    r"^\s*export\s+(?:declare\s+)?type\s+(\w+)(?:<[^=]*>)?\s*="
)

# export function name(
_EXPORT_FUNC_RE = re.compile(
    r"^\s*export\s+"
    r"(?:default\s+)?"
    r"(?:declare\s+)?"
    r"(?:async\s+)?"
    r"function\s+(\w+)\s*(?:<[^(]*>)?\s*\("
)

# export const name = (...) => ...  OR  export const name = value
_EXPORT_CONST_RE = re.compile(
    r"^\s*export\s+(?:declare\s+)?(?:const|let|var)\s+(\w+)"
)

# Class member: method, property, field, getter/setter, constructor
# Modifiers: public/private/protected/static/abstract/async/readonly/override/get/set
_MEMBER_PREFIX = (
    r"^\s*"
    r"(?:public\s+|private\s+|protected\s+|#)?"  # access modifier or #private
    r"(?:static\s+)?"
    r"(?:abstract\s+)?"
    r"(?:override\s+)?"
    r"(?:readonly\s+)?"
    r"(?:async\s+)?"
)

# constructor(
_CTOR_RE = re.compile(
    r"^\s*(?:public\s+|private\s+|protected\s+)?constructor\s*\("
)

# get name() / set name(
_ACCESSOR_RE = re.compile(
    _MEMBER_PREFIX + r"(get|set)\s+(\w+)\s*\("
)

# method: name( or name<T>(
_METHOD_RE = re.compile(
    _MEMBER_PREFIX + r"(\w+)\s*(?:<[^(]*>)?\s*\("
)

# field: name: type  or  name = value  or  name!: type (definite assignment)
_FIELD_RE = re.compile(
    _MEMBER_PREFIX + r"(\w+)\s*[!?]?\s*[;:=]"
)

# Interface method: name(params): RetType;  or  name<T>(params): RetType;
_IFACE_METHOD_RE = re.compile(
    r"^\s*(?:readonly\s+)?(\w+)\s*(?:<[^(]*>)?\s*\("
)

# Interface property: name: Type;  or  readonly name: Type;
_IFACE_PROP_RE = re.compile(
    r"^\s*(?:readonly\s+)?(\w+)\s*[?]?\s*:\s*(.+)"
)

# Enum member: Name = value,  or  Name,
_ENUM_MEMBER_RE = re.compile(
    r"^\s*(\w+)\s*(?:=\s*[^,}]+)?\s*,?\s*(?://.*)?$"
)

# JSDoc block delimiter
_JSDOC_START = "/**"
_JSDOC_END = "*/"

# Return type after closing paren: ): Type {  or  ): Type;
_RETURN_TYPE_RE = re.compile(r"\)\s*:\s*(.+?)(?:\s*\{|\s*;|\s*$)")

# Arrow function detection: = (...) =>  or  = async (...) =>
_ARROW_FUNC_RE = re.compile(
    r"=\s*(?:async\s+)?\("
)

# Overload signature: has return type but no { (ends with ; or just type)
_OVERLOAD_END_RE = re.compile(r"\)\s*:\s*[^{]+$")

# Skip names that could be false positives
_SKIP_NAMES = frozenset({
    "if", "else", "for", "while", "switch", "try", "catch",
    "return", "throw", "break", "continue", "do", "new",
    "typeof", "instanceof", "in", "of", "from", "import",
    "delete", "void", "yield", "await",
})


class TypeScriptParser(BaseParser):
    """Parser for TypeScript and TSX source files."""

    @property
    def file_extensions(self) -> list[str]:
        return [".ts", ".tsx"]

    def parse_directory(self, directory: Path) -> list[dict]:
        """Override to skip test/build/node_modules directories."""
        records = []
        for ext in self.file_extensions:
            for f in sorted(directory.rglob(f"*{ext}")):
                parts = f.relative_to(directory).parts
                if any(p in _SKIP_DIRS for p in parts):
                    continue
                fname = f.name
                if any(fname.endswith(s) for s in _SKIP_SUFFIXES):
                    continue
                try:
                    records.extend(self.parse_file(f, directory))
                except Exception as e:
                    import sys
                    print(f"codesurface: failed to parse {f}: {e}", file=sys.stderr)
                    continue
        return records

    def parse_file(self, path: Path, base_dir: Path) -> list[dict]:
        return _parse_ts_file(path, base_dir)


def _parse_ts_file(path: Path, base_dir: Path) -> list[dict]:
    """Parse a single TypeScript file and extract exported API members."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return []

    rel_path = str(path.relative_to(base_dir)).replace("\\", "/")
    lines = text.splitlines()
    records: list[dict] = []

    namespace = _file_to_module(path, base_dir)

    # State
    class_stack: list[tuple[str, str, int]] = []  # (name, kind, brace_depth)
    brace_depth = 0
    paren_depth = 0  # track () to skip multi-line parameter lists
    in_multiline_comment = False

    i = 0
    while i < len(lines):
        line = lines[i]

        # --- Multi-line comment tracking ---
        if in_multiline_comment:
            if "*/" in line:
                in_multiline_comment = False
            i += 1
            continue

        stripped = line.strip()

        # Start of multi-line comment (not JSDoc -- we extract those on demand)
        if "/*" in stripped and "*/" not in stripped:
            # Only enter multi-line mode for non-JSDoc or standalone block comments
            comment_start = stripped.find("/*")
            # Check this isn't inside a string (rough check)
            if not stripped.startswith("//"):
                in_multiline_comment = True
                # Still count braces before the comment
                pre_comment = line[:line.find("/*")]
                b, p = _count_braces_and_parens(pre_comment)
                brace_depth += b
                paren_depth = max(0, paren_depth + p)
                i += 1
                continue

        # Skip single-line comments
        if stripped.startswith("//"):
            i += 1
            continue

        # Skip JSDoc blocks (we'll look back for them)
        if stripped.startswith("/**"):
            if "*/" not in stripped[3:]:
                # Multi-line JSDoc, skip to end
                i += 1
                while i < len(lines) and "*/" not in lines[i]:
                    i += 1
                i += 1
                continue
            else:
                # Single-line JSDoc, skip
                i += 1
                continue

        # Count braces and parens (string-aware)
        open_braces, open_parens = _count_braces_and_parens(line)
        new_depth = brace_depth + open_braces
        new_paren = max(0, paren_depth + open_parens)

        # --- Skip re-exports ---
        if _REEXPORT_RE.match(line):
            brace_depth = new_depth
            paren_depth = new_paren
            i += 1
            continue

        # --- Exported const enum (check before generic const) ---
        if "export" in line and "const" in line and "enum" in line:
            ce_match = _EXPORT_CONST_ENUM_RE.match(line)
            if ce_match:
                enum_name = ce_match.group(1)
                while class_stack and class_stack[-1][2] >= brace_depth:
                    class_stack.pop()
                class_stack.append((enum_name, "enum", brace_depth))

                fqn = _make_fqn(namespace, class_stack)
                doc = _look_back_for_jsdoc(lines, i)
                records.append(_build_record(
                    fqn=fqn,
                    namespace=namespace,
                    class_name=enum_name,
                    member_name="",
                    member_type="type",
                    signature=f"const enum {enum_name}",
                    summary=doc,
                    file_path=rel_path,
                    line_start=i + 1,
                    line_end=i + 1,
                ))
                records.extend(_parse_enum_members(
                    lines, i, namespace, class_stack, rel_path
                ))
                brace_depth = new_depth
                paren_depth = new_paren
                i += 1
                continue

        # --- Exported type declarations (class/interface/enum/namespace) ---
        if "export" in line:
            type_match = _EXPORT_TYPE_RE.match(line)
            if type_match:
                kind = type_match.group(1)
                type_name = type_match.group(2)
                extends = (type_match.group(3) or "").strip()
                implements = (type_match.group(4) or "").strip()

                while class_stack and class_stack[-1][2] >= brace_depth:
                    class_stack.pop()
                class_stack.append((type_name, kind, brace_depth))

                fqn = _make_fqn(namespace, class_stack)
                doc = _look_back_for_jsdoc(lines, i)

                is_abstract = "abstract" in line.split(kind)[0]
                sig_parts = []
                if is_abstract and kind == "class":
                    sig_parts.append(f"abstract class {type_name}")
                else:
                    sig_parts.append(f"{kind} {type_name}")
                if extends:
                    sig_parts.append(f" extends {extends.rstrip('{').strip()}")
                if implements:
                    sig_parts.append(f" implements {implements.rstrip('{').strip()}")

                records.append(_build_record(
                    fqn=fqn,
                    namespace=namespace,
                    class_name=type_name,
                    member_name="",
                    member_type="type",
                    signature="".join(sig_parts),
                    summary=doc,
                    file_path=rel_path,
                    line_start=i + 1,
                    line_end=i + 1,
                ))

                if kind == "enum":
                    records.extend(_parse_enum_members(
                        lines, i, namespace, class_stack, rel_path
                    ))

                brace_depth = new_depth
                paren_depth = new_paren
                i += 1
                continue

        # --- Exported type alias ---
        if "export" in line and "type " in line and "=" in line:
            alias_match = _EXPORT_TYPE_ALIAS_RE.match(line)
            if alias_match:
                type_name = alias_match.group(1)
                fqn_prefix = _stack_prefix(namespace, class_stack)
                fqn = f"{fqn_prefix}.{type_name}" if fqn_prefix else type_name
                doc = _look_back_for_jsdoc(lines, i)

                # Collect the full type definition
                type_body = line[line.index("=") + 1:].strip()
                sig = f"type {type_name} = {type_body}"
                if len(sig) > 120:
                    sig = sig[:117] + "..."

                records.append(_build_record(
                    fqn=fqn,
                    namespace=namespace,
                    class_name=type_name,
                    member_name="",
                    member_type="type",
                    signature=sig,
                    summary=doc,
                    file_path=rel_path,
                    line_start=i + 1,
                    line_end=i + 1,
                ))
                brace_depth = new_depth
                paren_depth = new_paren
                i += 1
                continue

        # --- Exported function ---
        if "export" in line and "function" in line:
            func_match = _EXPORT_FUNC_RE.match(line)
            if func_match:
                func_name = func_match.group(1)
                # Check for overload (;-terminated)
                full_sig, end_i = _collect_signature(lines, i)
                if _OVERLOAD_END_RE.search(full_sig):
                    # Skip overload declaration
                    brace_depth = new_depth
                    paren_depth = new_paren
                    i = end_i + 1
                    continue

                params_str = _extract_params(full_sig)
                return_type = _extract_return_type(full_sig)
                doc = _look_back_for_jsdoc(lines, i)

                fqn_prefix = _stack_prefix(namespace, class_stack)
                is_async = "async" in line.split("function")[0]
                prefix = "async " if is_async else ""
                ret = f": {return_type}" if return_type else ""
                sig = f"{prefix}function {func_name}({params_str}){ret}"

                records.append(_build_record(
                    fqn=_method_fqn(fqn_prefix, func_name, params_str) if fqn_prefix else _method_fqn(namespace, func_name, params_str),
                    namespace=namespace,
                    class_name="",
                    member_name=func_name,
                    member_type="method",
                    signature=sig,
                    summary=doc,
                    file_path=rel_path,
                    line_start=i + 1,
                    line_end=end_i + 1,
                ))
                brace_depth = new_depth
                paren_depth = new_paren
                i = end_i + 1
                continue

        # --- Exported const/let/var ---
        if "export" in line and re.search(r"\b(?:const|let|var)\b", line):
            const_match = _EXPORT_CONST_RE.match(line)
            if const_match and not _REEXPORT_RE.match(line):
                const_name = const_match.group(1)
                if const_name not in _SKIP_NAMES:
                    # Determine if arrow function or value
                    rest = line[const_match.end():]
                    full_line = line
                    end_i = i

                    # Collect multi-line if needed
                    if _ARROW_FUNC_RE.search(rest):
                        full_line, end_i = _collect_signature(lines, i)
                        params_str = _extract_arrow_params(full_line)
                        return_type = _extract_arrow_return_type(full_line)
                        doc = _look_back_for_jsdoc(lines, i)

                        fqn_prefix = _stack_prefix(namespace, class_stack)
                        is_async = "async" in rest.split("(")[0] if "(" in rest else False
                        prefix = "async " if is_async else ""
                        ret = f": {return_type}" if return_type else ""
                        sig = f"const {prefix}{const_name}({params_str}){ret}"

                        records.append(_build_record(
                            fqn=_method_fqn(fqn_prefix, const_name, params_str) if fqn_prefix else _method_fqn(namespace, const_name, params_str),
                            namespace=namespace,
                            class_name="",
                            member_name=const_name,
                            member_type="method",
                            signature=sig,
                            summary=doc,
                            file_path=rel_path,
                            line_start=i + 1,
                            line_end=end_i + 1,
                        ))
                    else:
                        # Regular const value (field)
                        doc = _look_back_for_jsdoc(lines, i)
                        fqn_prefix = _stack_prefix(namespace, class_stack)
                        fqn = f"{fqn_prefix}.{const_name}" if fqn_prefix else f"{namespace}.{const_name}" if namespace else const_name

                        # Try to extract type annotation
                        type_ann = _extract_const_type(rest)
                        sig = f"const {const_name}: {type_ann}" if type_ann else f"const {const_name}"

                        records.append(_build_record(
                            fqn=fqn,
                            namespace=namespace,
                            class_name="",
                            member_name=const_name,
                            member_type="field",
                            signature=sig,
                            summary=doc,
                            file_path=rel_path,
                            line_start=i + 1,
                            line_end=i + 1,
                        ))

                    brace_depth = new_depth
                    paren_depth = new_paren
                    i = end_i + 1
                    continue

        # --- Members inside exported class/interface/enum/namespace ---
        # Skip if inside parentheses (multi-line parameter lists)
        if class_stack and paren_depth == 0:
            current_name = class_stack[-1][0]
            current_kind = class_stack[-1][1]
            class_brace = class_stack[-1][2]

            # Only parse members at the direct body level (class_depth + 1).
            # Deeper lines are inside decorator args, object literals, etc.
            at_body_level = brace_depth == class_brace + 1

            if current_kind == "interface" and at_body_level:
                record = _try_parse_interface_member(
                    line, lines, i, namespace, class_stack, rel_path
                )
                if record:
                    records.append(record)
                    brace_depth = new_depth
                    paren_depth = new_paren
                    i += 1
                    continue

            elif current_kind == "enum":
                # Enum members already parsed at declaration time
                brace_depth = new_depth
                paren_depth = new_paren
                i += 1
                # Pop if we're closing the enum
                while class_stack and new_depth <= class_stack[-1][2]:
                    class_stack.pop()
                continue

            elif current_kind == "namespace":
                # Inside namespace, look for nested exports
                # (handled by the export checks above, since class_stack
                # affects FQN construction)
                pass

            elif current_kind == "class" and at_body_level:
                record = _try_parse_class_member(
                    line, lines, i, namespace, class_stack, rel_path
                )
                if record:
                    records.append(record)
                    brace_depth = new_depth
                    paren_depth = new_paren
                    i += 1
                    continue

        brace_depth = new_depth
        paren_depth = new_paren

        # Pop class stack when we close their scope
        while class_stack and brace_depth <= class_stack[-1][2]:
            class_stack.pop()

        i += 1

    # Deduplicate within file: getter/setter pairs, declaration merging
    # (export const Foo + export type Foo), etc. Keep first occurrence.
    unique: list[dict] = []
    seen: set[str] = set()
    for rec in records:
        fqn = rec["fqn"]
        if fqn not in seen:
            seen.add(fqn)
            unique.append(rec)
    return unique


# --- Class member parsing ---

def _try_parse_class_member(
    line: str, lines: list[str], idx: int,
    namespace: str, class_stack: list[tuple[str, str, int]], file_path: str,
) -> dict | None:
    """Parse a member inside an exported class."""
    stripped = line.strip()
    if not stripped or stripped.startswith("//") or stripped in ("{", "}"):
        return None
    if stripped.startswith("/*") or stripped.startswith("*"):
        return None
    # Skip decorators
    if stripped.startswith("@"):
        return None

    # Skip private/protected members
    if _is_private_member(stripped):
        return None

    current_class = class_stack[-1][0]
    fqn_prefix = _make_fqn(namespace, class_stack)

    # Constructor
    ctor_match = _CTOR_RE.match(line)
    if ctor_match:
        full_sig, end_i = _collect_signature(lines, idx)
        # Skip overloads
        if _OVERLOAD_END_RE.search(full_sig):
            return None
        params_str = _extract_params(full_sig)
        doc = _look_back_for_jsdoc(lines, idx)
        sig = f"constructor({params_str})"
        return _build_record(
            fqn=_method_fqn(fqn_prefix, "constructor", params_str),
            namespace=namespace,
            class_name=current_class,
            member_name="constructor",
            member_type="method",
            signature=sig,
            summary=doc,
            file_path=file_path,
            line_start=idx + 1,
            line_end=end_i + 1,
        )

    # Getter / Setter
    acc_match = _ACCESSOR_RE.match(line)
    if acc_match:
        acc_kind = acc_match.group(1)  # get or set
        prop_name = acc_match.group(2)
        if prop_name in _SKIP_NAMES:
            return None
        full_sig, end_i = _collect_signature(lines, idx)
        return_type = _extract_return_type(full_sig) if acc_kind == "get" else ""
        doc = _look_back_for_jsdoc(lines, idx)
        mods = _extract_modifiers(stripped, acc_kind)
        prefix = "static " if "static" in mods else ""
        ret = f": {return_type}" if return_type else ""
        sig = f"{prefix}{prop_name}{ret}"
        return _build_record(
            fqn=f"{fqn_prefix}.{prop_name}",
            namespace=namespace,
            class_name=current_class,
            member_name=prop_name,
            member_type="property",
            signature=sig,
            summary=doc,
            file_path=file_path,
            line_start=idx + 1,
            line_end=end_i + 1,
        )

    # Method (has parentheses)
    if "(" in stripped:
        meth_match = _METHOD_RE.match(line)
        if meth_match:
            meth_name = meth_match.group(1)
            if meth_name in _SKIP_NAMES or meth_name in (
                "constructor", "if", "for", "while",
                "switch", "catch", "return", "throw",
            ):
                return None

            full_sig, end_i = _collect_signature(lines, idx)
            # Skip overloads
            if _OVERLOAD_END_RE.search(full_sig):
                return None
            params_str = _extract_params(full_sig)
            return_type = _extract_return_type(full_sig)
            doc = _look_back_for_jsdoc(lines, idx)

            mods = _extract_modifiers(stripped, meth_name)
            prefix = ""
            if "static" in mods:
                prefix += "static "
            if "abstract" in mods:
                prefix += "abstract "
            if "async" in mods:
                prefix += "async "
            ret = f": {return_type}" if return_type else ""
            sig = f"{prefix}{meth_name}({params_str}){ret}"

            return _build_record(
                fqn=_method_fqn(fqn_prefix, meth_name, params_str),
                namespace=namespace,
                class_name=current_class,
                member_name=meth_name,
                member_type="method",
                signature=sig,
                summary=doc,
                file_path=file_path,
                line_start=idx + 1,
                line_end=end_i + 1,
            )

    # Field (no parens, has : or = or ;)
    field_match = _FIELD_RE.match(line)
    if field_match:
        field_name = field_match.group(1)
        if field_name in _SKIP_NAMES:
            return None
        # Skip if it's a method-like line we missed
        if "(" in stripped:
            return None
        doc = _look_back_for_jsdoc(lines, idx)
        mods = _extract_modifiers(stripped, field_name)

        # Extract type
        field_type = _extract_field_type(stripped, field_name)

        prefix = ""
        if "static" in mods:
            prefix += "static "
        if "readonly" in mods:
            prefix += "readonly "
        type_part = f": {field_type}" if field_type else ""
        sig = f"{prefix}{field_name}{type_part}"

        return _build_record(
            fqn=f"{fqn_prefix}.{field_name}",
            namespace=namespace,
            class_name=current_class,
            member_name=field_name,
            member_type="field",
            signature=sig,
            summary=doc,
            file_path=file_path,
            line_start=idx + 1,
            line_end=idx + 1,
        )

    return None


# --- Interface member parsing ---

def _try_parse_interface_member(
    line: str, lines: list[str], idx: int,
    namespace: str, class_stack: list[tuple[str, str, int]], file_path: str,
) -> dict | None:
    """Parse a member inside an exported interface."""
    stripped = line.strip()
    if not stripped or stripped.startswith("//") or stripped in ("{", "}"):
        return None
    if stripped.startswith("/*") or stripped.startswith("*"):
        return None
    if stripped.startswith("["):  # index signatures
        return None

    current_class = class_stack[-1][0]
    fqn_prefix = _make_fqn(namespace, class_stack)

    # Method signature: name(params): Type;
    if "(" in stripped:
        meth_match = _IFACE_METHOD_RE.match(line)
        if meth_match:
            meth_name = meth_match.group(1)
            if meth_name in _SKIP_NAMES:
                return None
            full_sig, end_i = _collect_signature(lines, idx)
            params_str = _extract_params(full_sig)
            return_type = _extract_return_type(full_sig)
            doc = _look_back_for_jsdoc(lines, idx)
            ret = f": {return_type}" if return_type else ""
            sig = f"{meth_name}({params_str}){ret}"

            return _build_record(
                fqn=_method_fqn(fqn_prefix, meth_name, params_str),
                namespace=namespace,
                class_name=current_class,
                member_name=meth_name,
                member_type="method",
                signature=sig,
                summary=doc,
                file_path=file_path,
                line_start=idx + 1,
                line_end=end_i + 1,
            )

    # Property: name: Type; or readonly name: Type;
    prop_match = _IFACE_PROP_RE.match(line)
    if prop_match:
        prop_name = prop_match.group(1)
        prop_type = prop_match.group(2).rstrip(";").rstrip(",").strip()
        if prop_name in _SKIP_NAMES:
            return None
        # Skip if it has parens (method we missed)
        if "(" in prop_type and "=>" not in prop_type:
            return None
        doc = _look_back_for_jsdoc(lines, idx)
        is_readonly = "readonly" in stripped.split(prop_name)[0]
        prefix = "readonly " if is_readonly else ""
        sig = f"{prefix}{prop_name}: {prop_type}"

        return _build_record(
            fqn=f"{fqn_prefix}.{prop_name}",
            namespace=namespace,
            class_name=current_class,
            member_name=prop_name,
            member_type="property",
            signature=sig,
            summary=doc,
            file_path=file_path,
            line_start=idx + 1,
            line_end=idx + 1,
        )

    return None


# --- Enum member parsing ---

def _parse_enum_members(
    lines: list[str], type_line_idx: int,
    namespace: str, class_stack: list[tuple[str, str, int]], file_path: str,
) -> list[dict]:
    """Extract enum member names from lines after the enum declaration."""
    records = []
    enum_name = class_stack[-1][0]
    fqn_prefix = _make_fqn(namespace, class_stack)
    depth = 0
    started = False

    for j in range(type_line_idx, min(type_line_idx + 200, len(lines))):
        line = lines[j]
        if "{" in line:
            depth += line.count("{") - line.count("}")
            started = True
            continue
        if started:
            depth += line.count("{") - line.count("}")
            if depth <= 0:
                break
            stripped = line.strip()
            if not stripped or stripped.startswith("//") or stripped.startswith("/*"):
                continue
            em_match = _ENUM_MEMBER_RE.match(stripped)
            if em_match:
                name = em_match.group(1)
                if not name.startswith("//"):
                    records.append(_build_record(
                        fqn=f"{fqn_prefix}.{name}",
                        namespace=namespace,
                        class_name=enum_name,
                        member_name=name,
                        member_type="field",
                        signature=f"{enum_name}.{name}",
                        summary="",
                        file_path=file_path,
                        line_start=j + 1,
                        line_end=j + 1,
                    ))
    return records


# --- JSDoc extraction ---

def _look_back_for_jsdoc(lines: list[str], decl_idx: int) -> str:
    """Look backwards from a declaration for a /** ... */ JSDoc block."""
    i = decl_idx - 1

    # Skip decorator lines
    while i >= 0 and lines[i].strip().startswith("@"):
        i -= 1

    # Skip empty lines (at most 1)
    if i >= 0 and not lines[i].strip():
        i -= 1

    if i < 0:
        return ""

    # Check if the line ends a JSDoc block
    if "*/" not in lines[i]:
        return ""

    # Walk backward to find the opening /**
    end_i = i
    while i >= 0:
        if _JSDOC_START in lines[i]:
            break
        i -= 1
    else:
        return ""

    # Extract the JSDoc text
    doc_lines = []
    for j in range(i, end_i + 1):
        text = lines[j].strip()
        # Remove /** and */
        text = text.replace("/**", "").replace("*/", "")
        # Remove leading *
        if text.startswith("*"):
            text = text[1:]
        text = text.strip()
        if text:
            doc_lines.append(text)

    if not doc_lines:
        return ""

    # Return text before first @tag as summary
    summary_lines = []
    for dl in doc_lines:
        if dl.startswith("@"):
            break
        summary_lines.append(dl)

    return " ".join(summary_lines).strip()


# --- Signature collection ---

def _collect_signature(lines: list[str], start: int) -> tuple[str, int]:
    """Collect a signature that may span multiple lines (balanced parens)."""
    sig = lines[start]
    i = start

    _, depth = _count_braces_and_parens(sig)
    while depth > 0 and i + 1 < len(lines):
        i += 1
        sig += " " + lines[i].strip()
        _, paren_delta = _count_braces_and_parens(lines[i])
        depth += paren_delta

    return sig, i


def _extract_params(sig_text: str) -> str:
    """Extract parameter list from a function/method signature."""
    paren_start = sig_text.find("(")
    if paren_start == -1:
        return ""

    # Find matching closing paren
    depth = 0
    paren_end = -1
    for j in range(paren_start, len(sig_text)):
        if sig_text[j] == "(":
            depth += 1
        elif sig_text[j] == ")":
            depth -= 1
            if depth == 0:
                paren_end = j
                break

    if paren_end == -1:
        return ""

    params_str = sig_text[paren_start + 1:paren_end].strip()
    if not params_str:
        return ""

    # Clean up whitespace
    params_str = re.sub(r"\s+", " ", params_str)
    return params_str


def _extract_arrow_params(sig_text: str) -> str:
    """Extract parameters from an arrow function: const name = (params) => ..."""
    # Find the = sign, then look for (
    eq_idx = sig_text.find("=")
    if eq_idx == -1:
        return ""
    rest = sig_text[eq_idx + 1:]
    return _extract_params(rest)


def _extract_return_type(sig_text: str) -> str:
    """Extract return type from ): Type { or ): Type;"""
    match = _RETURN_TYPE_RE.search(sig_text)
    if match:
        ret = match.group(1).strip()
        # Clean trailing braces/semicolons
        ret = ret.rstrip("{;").strip()
        if ret:
            return ret
    return ""


def _extract_arrow_return_type(sig_text: str) -> str:
    """Extract return type from arrow function: ): Type => or ) => (no type)."""
    # Find the closing paren of params, then look for : before =>
    eq_idx = sig_text.find("=")
    if eq_idx == -1:
        return ""
    rest = sig_text[eq_idx + 1:]

    # Find ) then check for : before =>
    paren_close = -1
    depth = 0
    for j, ch in enumerate(rest):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                paren_close = j
                break

    if paren_close == -1:
        return ""

    after_paren = rest[paren_close + 1:].strip()
    if after_paren.startswith(":"):
        # Has return type annotation
        type_str = after_paren[1:].strip()
        # Find the => marker
        arrow_idx = type_str.find("=>")
        if arrow_idx > 0:
            return type_str[:arrow_idx].strip()
    return ""


def _extract_const_type(rest: str) -> str:
    """Extract type annotation from const declaration: : Type = value."""
    rest = rest.strip()
    if rest.startswith(":"):
        # Find = at depth 0
        depth = 0
        for j, ch in enumerate(rest[1:], 1):
            if ch in ("<", "(", "[", "{"):
                depth += 1
            elif ch in (">", ")", "]", "}"):
                depth -= 1
            elif ch == "=" and depth == 0:
                return rest[1:j].strip()
        # No = found, whole thing is the type
        type_str = rest[1:].rstrip(";").strip()
        return type_str if type_str else ""
    return ""


def _extract_field_type(stripped: str, field_name: str) -> str:
    """Extract type from a class field: name: Type = value."""
    # Find the field name, then look for : after it
    idx = stripped.find(field_name)
    if idx == -1:
        return ""
    after = stripped[idx + len(field_name):].strip()
    if after.startswith(("?", "!")):
        after = after[1:].strip()
    if after.startswith(":"):
        type_str = after[1:].strip()
        # Find = at depth 0
        depth = 0
        for j, ch in enumerate(type_str):
            if ch in ("<", "(", "[", "{"):
                depth += 1
            elif ch in (">", ")", "]", "}"):
                depth -= 1
            elif ch == "=" and depth == 0:
                return type_str[:j].strip()
            elif ch == ";" and depth == 0:
                return type_str[:j].strip()
        return type_str.rstrip(";").strip()
    return ""


# --- Brace counting ---

def _count_braces_and_parens(line: str) -> tuple[int, int]:
    """Count net brace and paren depth changes, skipping inside strings."""
    brace_depth = 0
    paren_depth = 0
    in_single = False
    in_double = False
    in_template = False
    escape = False

    for ch in line:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue

        if in_single:
            if ch == "'":
                in_single = False
            continue
        if in_double:
            if ch == '"':
                in_double = False
            continue
        if in_template:
            if ch == "`":
                in_template = False
            continue

        if ch == "'":
            in_single = True
        elif ch == '"':
            in_double = True
        elif ch == "`":
            in_template = True
        elif ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
        elif ch == "(":
            paren_depth += 1
        elif ch == ")":
            paren_depth -= 1

    return brace_depth, paren_depth


# --- Visibility ---

def _extract_modifiers(stripped: str, name: str) -> set[str]:
    """Extract modifier keywords appearing before *name* in a stripped line.

    Splits text before the first occurrence of *name* into words and returns
    those that are known TypeScript modifiers.  This replaces fragile
    ``"static " in text`` substring checks that can false-positive on names
    containing the modifier string.
    """
    _KNOWN_MODS = {"static", "abstract", "async", "readonly", "override", "declare"}
    idx = stripped.find(name)
    if idx <= 0:
        return set()
    prefix = stripped[:idx]
    return {w for w in prefix.split() if w in _KNOWN_MODS}


def _is_private_member(stripped: str) -> bool:
    """Check if a class member line is private or protected."""
    if stripped.startswith("#"):
        return True
    if stripped.startswith("private ") or stripped.startswith("private\t"):
        return True
    if stripped.startswith("protected ") or stripped.startswith("protected\t"):
        return True
    return False


# --- FQN helpers ---

def _make_fqn(namespace: str, class_stack: list[tuple[str, str, int]]) -> str:
    """Build full FQN from namespace + class stack."""
    parts = [namespace] if namespace else []
    for name, _, _ in class_stack:
        parts.append(name)
    return ".".join(parts)


def _stack_prefix(namespace: str, class_stack: list[tuple[str, str, int]]) -> str:
    """Build FQN prefix from namespace + class stack (for module-level items)."""
    if class_stack:
        return _make_fqn(namespace, class_stack)
    return namespace


def _method_fqn(base_fqn: str, name: str, params_str: str) -> str:
    """Build disambiguated FQN for functions by appending param types."""
    param_types = []
    if params_str.strip():
        for p in _split_params(params_str):
            p = p.strip()
            # TypeScript: name: Type = default  or  name?: Type  or  ...rest: Type
            if ":" in p:
                type_part = p.split(":", 1)[1].strip()
                # Remove default value
                type_part = _remove_default(type_part)
                param_types.append(type_part)
            else:
                # No type, use param name
                name_part = p.split("=")[0].strip().lstrip(".")
                param_types.append(name_part)

    if param_types:
        return f"{base_fqn}.{name}({','.join(param_types)})"
    return f"{base_fqn}.{name}"


def _remove_default(type_str: str) -> str:
    """Remove default value from a type annotation, respecting brackets."""
    depth = 0
    for j, ch in enumerate(type_str):
        if ch in ("<", "(", "[", "{"):
            depth += 1
        elif ch in (">", ")", "]", "}"):
            depth -= 1
        elif ch == "=" and depth == 0:
            return type_str[:j].strip()
    return type_str.strip()


def _split_params(params_str: str) -> list[str]:
    """Split parameter string by commas, respecting nested brackets."""
    params = []
    current = ""
    depth = 0

    for ch in params_str:
        if ch in ("(", "[", "{", "<"):
            depth += 1
            current += ch
        elif ch in (")", "]", "}", ">"):
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


# --- File path to module ---

def _file_to_module(path: Path, base_dir: Path) -> str:
    """Convert file path to a dot-separated module namespace.

    src/services/myService.ts -> src.services.myService
    """
    rel = path.relative_to(base_dir)
    parts = list(rel.parts)

    if not parts:
        return ""

    # Remove extension from last part
    last = parts[-1]
    for ext in (".tsx", ".ts"):
        if last.endswith(ext):
            parts[-1] = last[:-len(ext)]
            break

    # Drop index files (index.ts re-exports)
    if parts and parts[-1] == "index":
        parts = parts[:-1]

    return ".".join(parts)


def _build_record(**kwargs) -> dict:
    return kwargs
