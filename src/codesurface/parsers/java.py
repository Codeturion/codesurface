"""Java parser that captures public API declarations.

Scans Java files tracking brace-depth scope, captures public classes,
interfaces, enums, records, annotation types, and their members.
Javadoc comments (/** ... */) are extracted as summaries.
"""

import re
from pathlib import Path

from .base import BaseParser


# --- Skip patterns ---

_SKIP_DIRS = frozenset({
    "test", "tests", "target", "build", ".gradle", ".git",
    "node_modules", ".mvn", "out", "generated",
    "generated-sources", "generated-test-sources",
    ".idea", "bin",
})

_SKIP_SUFFIXES = (
    "Test.java",
    "Tests.java",
    "TestCase.java",
    "IT.java",
)

_SKIP_FILES = frozenset({
    "module-info.java",
    "package-info.java",
})

# --- Regex patterns ---

# Package declaration: package com.example.service;
_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;")

# Type declaration: public [modifiers] class/interface/enum/record Name [generics] [extends] [implements] [permits] {
_TYPE_DECL_RE = re.compile(
    r"^\s*(?:public\s+)?"
    r"(?:static\s+)?(?:abstract\s+)?(?:sealed\s+)?(?:final\s+)?(?:strictfp\s+)?(?:non-sealed\s+)?"
    r"(class|interface|enum|record)\s+"
    r"(\w+)"
    r"(?:<[^{]*?>)?"                       # optional generics (non-greedy to not eat the brace)
    r"(?:\s*\([^)]*\))?"                   # optional record components
    r"(?:\s+extends\s+([^{]+?))?"          # optional extends
    r"(?:\s+implements\s+([^{]+?))?"       # optional implements
    r"(?:\s+permits\s+([^{]+?))?"          # optional permits
    r"\s*\{?"
)

# Annotation type: public @interface Name {
_ANNOTATION_TYPE_RE = re.compile(
    r"^\s*(?:public\s+)?"
    r"@interface\s+"
    r"(\w+)"
    r"\s*\{?"
)

# Constructor: public ClassName(params) [throws ...] {
_CTOR_RE = re.compile(
    r"^\s*(?:public\s+)"
    r"(\w+)\s*\("
)

# Method: public [modifiers] [<T>] ReturnType methodName(params) [throws ...] {;
_METHOD_RE = re.compile(
    r"^\s*(?:public\s+)"
    r"(?:static\s+)?(?:final\s+)?(?:abstract\s+)?(?:synchronized\s+)?(?:native\s+)?"
    r"(?:<[^>]+>\s+)?"                     # optional type parameters
    r"([\w<>\[\],.\s?]+?)\s+"              # return type
    r"(\w+)\s*\("                          # method name + open paren
)

# Interface default/static method: default/static ReturnType name(
_IFACE_METHOD_RE = re.compile(
    r"^\s*(?:default\s+|static\s+)?"
    r"(?:<[^>]+>\s+)?"                     # optional type parameters
    r"([\w<>\[\],.\s?]+?)\s+"              # return type
    r"(\w+)\s*\("                          # method name + open paren
)

# Interface constant: Type NAME = value;
_IFACE_CONST_RE = re.compile(
    r"^\s*([\w<>\[\],.\s?]+?)\s+"          # type
    r"([A-Z][A-Z0-9_]*)\s*="              # CONSTANT_NAME =
)

# Field: public [static] [final] Type name [= value];
_FIELD_RE = re.compile(
    r"^\s*(?:public\s+)"
    r"(?:static\s+)?(?:final\s+)?(?:volatile\s+)?(?:transient\s+)?"
    r"([\w<>\[\],.\s?]+?)\s+"              # type
    r"(\w+)\s*[;=,]"                       # name + semicolon/assignment/comma
)

# Enum constant: NAME or NAME(args) or NAME { ... }
_ENUM_CONST_RE = re.compile(
    r"^\s*([A-Z][A-Z0-9_]*)\s*(?:\(|,|;|\{|$)")

# Annotation element: Type name() [default value];
_ANNOTATION_ELEM_RE = re.compile(
    r"^\s*([\w<>\[\],.\s?]+?)\s+"          # type
    r"(\w+)\s*\(\s*\)"                     # name()
    r"(?:\s+default\s+.+?)?"              # optional default
    r"\s*;"
)

# Javadoc
_JAVADOC_START = "/**"
_JAVADOC_END = "*/"

# Skip names that could be false positives
_SKIP_NAMES = frozenset({
    "if", "else", "for", "while", "switch", "try", "catch",
    "return", "throw", "break", "continue", "do", "new",
    "instanceof", "import", "package", "class", "interface",
    "enum", "extends", "implements", "throws", "this", "super",
    "final", "static", "abstract", "synchronized", "native",
    "void", "boolean", "byte", "char", "short", "int", "long",
    "float", "double",
})


class JavaParser(BaseParser):
    """Parser for Java source files."""

    @property
    def file_extensions(self) -> list[str]:
        return [".java"]

    def parse_directory(self, directory: Path) -> list[dict]:
        """Override to skip test/build directories."""
        records = []
        for f in sorted(directory.rglob("*.java")):
            parts = f.relative_to(directory).parts
            if any(p in _SKIP_DIRS for p in parts):
                continue
            fname = f.name
            if fname in _SKIP_FILES:
                continue
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
        return _parse_java_file(path, base_dir)


def _parse_java_file(path: Path, base_dir: Path) -> list[dict]:
    """Parse a single .java file and extract public API members."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return []

    rel_path = str(path.relative_to(base_dir)).replace("\\", "/")
    lines = text.splitlines()
    records: list[dict] = []

    # State
    package = ""
    class_stack: list[tuple[str, str, int]] = []  # (name, kind, brace_depth)
    brace_depth = 0
    paren_depth = 0
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

        # Start of multi-line comment (not Javadoc -- we extract those on demand)
        if "/*" in stripped and "*/" not in stripped:
            if not stripped.startswith("//"):
                in_multiline_comment = True
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

        # Skip Javadoc blocks (we look back for them on demand)
        if stripped.startswith("/**"):
            if "*/" not in stripped[3:]:
                i += 1
                while i < len(lines) and "*/" not in lines[i]:
                    i += 1
                i += 1
                continue
            else:
                i += 1
                continue

        # Skip annotation lines (but track braces in them)
        if stripped.startswith("@") and "interface" not in stripped:
            b, p = _count_braces_and_parens(line)
            brace_depth += b
            paren_depth = max(0, paren_depth + p)
            i += 1
            continue

        # Count braces and parens (string-aware)
        open_braces, open_parens = _count_braces_and_parens(line)
        new_depth = brace_depth + open_braces
        new_paren = max(0, paren_depth + open_parens)

        # Skip if we're inside parentheses (multi-line parameter lists, annotations)
        if paren_depth > 0:
            brace_depth = new_depth
            paren_depth = new_paren
            i += 1
            continue

        # --- Package declaration ---
        pkg_match = _PACKAGE_RE.match(line)
        if pkg_match:
            package = pkg_match.group(1)
            brace_depth = new_depth
            paren_depth = new_paren
            i += 1
            continue

        # Skip import statements
        if stripped.startswith("import "):
            brace_depth = new_depth
            paren_depth = new_paren
            i += 1
            continue

        # --- Annotation type declaration (@interface) ---
        if "@interface" in line:
            ann_match = _ANNOTATION_TYPE_RE.match(line)
            if ann_match:
                type_name = ann_match.group(1)

                while class_stack and class_stack[-1][2] >= brace_depth:
                    class_stack.pop()
                class_stack.append((type_name, "@interface", brace_depth))

                if re.match(r"^\s*public\s+", line):
                    fqn = _make_fqn(package, class_stack)
                    doc = _look_back_for_javadoc(lines, i)
                    records.append(_build_record(
                        fqn=fqn,
                        namespace=package,
                        class_name=type_name,
                        member_name="",
                        member_type="type",
                        signature=f"@interface {type_name}",
                        summary=doc,
                        file_path=rel_path,
                    ))

                brace_depth = new_depth
                paren_depth = new_paren
                i += 1
                continue

        # --- Type declarations (class/interface/enum/record) ---
        if re.search(r"\b(class|interface|enum|record)\b", line):
            type_match = _TYPE_DECL_RE.match(line)
            if type_match:
                kind = type_match.group(1)
                type_name = type_match.group(2)
                extends = (type_match.group(3) or "").strip()
                implements = (type_match.group(4) or "").strip()
                permits = (type_match.group(5) or "").strip()

                while class_stack and class_stack[-1][2] >= brace_depth:
                    class_stack.pop()
                class_stack.append((type_name, kind, brace_depth))

                # Only record public types; skip package-private (no modifier)
                is_public = bool(re.match(r"^\s*public\s+", line))
                if is_public:
                    fqn = _make_fqn(package, class_stack)
                    doc = _look_back_for_javadoc(lines, i)

                    is_abstract = "abstract " in line.split(kind)[0]
                    is_sealed = "sealed " in line.split(kind)[0]
                    sig_parts = []
                    if is_abstract:
                        sig_parts.append("abstract ")
                    if is_sealed:
                        sig_parts.append("sealed ")
                    sig_parts.append(f"{kind} {type_name}")
                    if extends:
                        sig_parts.append(f" extends {extends.rstrip('{').strip()}")
                    if implements:
                        sig_parts.append(f" implements {implements.rstrip('{').strip()}")
                    if permits:
                        sig_parts.append(f" permits {permits.rstrip('{').strip()}")

                    records.append(_build_record(
                        fqn=fqn,
                        namespace=package,
                        class_name=type_name,
                        member_name="",
                        member_type="type",
                        signature="".join(sig_parts),
                        summary=doc,
                        file_path=rel_path,
                    ))

                    # For enums, extract constants
                    if kind == "enum":
                        records.extend(_parse_enum_constants(
                            lines, i, fqn, package, type_name, rel_path
                        ))

                    # For records, extract components as fields
                    if kind == "record":
                        # Collect full declaration in case components span multiple lines
                        record_decl = line
                        local_paren = line.count("(") - line.count(")")
                        j = i + 1
                        while local_paren > 0 and j < len(lines):
                            record_decl += " " + lines[j].strip()
                            local_paren += lines[j].count("(") - lines[j].count(")")
                            j += 1
                        records.extend(_parse_record_components(
                            record_decl, fqn, package, type_name, rel_path
                        ))

                brace_depth = new_depth
                paren_depth = new_paren
                i += 1
                continue

        # --- Members inside a type ---
        if class_stack and paren_depth == 0:
            current_name = class_stack[-1][0]
            current_kind = class_stack[-1][1]
            class_brace = class_stack[-1][2]
            at_body_level = brace_depth == class_brace + 1
            base_fqn = _make_fqn(package, class_stack)

            # Interface members (all implicitly public)
            if current_kind == "interface" and at_body_level:
                record = _try_parse_interface_member(
                    line, lines, i, package, current_name, rel_path,
                    class_stack=class_stack,
                )
                if record:
                    records.append(record)
                    brace_depth = new_depth
                    paren_depth = new_paren
                    i += 1
                    continue

            # Annotation type elements
            elif current_kind == "@interface" and at_body_level:
                record = _try_parse_annotation_element(
                    line, lines, i, package, current_name, rel_path,
                    class_stack=class_stack,
                )
                if record:
                    records.append(record)
                    brace_depth = new_depth
                    paren_depth = new_paren
                    i += 1
                    continue

            # Enum body: skip constants area, parse public methods/fields
            elif current_kind == "enum" and at_body_level:
                # Enum constants are already parsed at declaration time.
                # After the constants (terminated by ;), we can have methods/fields.
                # Check if this is a public method/field
                if "public" in line:
                    record = _try_parse_class_member(
                        line, lines, i, package, class_stack, rel_path
                    )
                    if record:
                        records.append(record)
                        brace_depth = new_depth
                        paren_depth = new_paren
                        i += 1
                        continue

            # Class/record members (public only)
            elif current_kind in ("class", "record") and at_body_level:
                if "public" in line:
                    record = _try_parse_class_member(
                        line, lines, i, package, class_stack, rel_path
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

    # Deduplicate within file (keep first occurrence)
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
    package: str, class_stack: list[tuple[str, str, int]], file_path: str,
) -> dict | None:
    """Parse a public member inside a class, enum, or record."""
    stripped = line.strip()
    if not stripped or stripped.startswith("//") or stripped in ("{", "}"):
        return None
    if stripped.startswith("/*") or stripped.startswith("*"):
        return None
    if stripped.startswith("@"):
        return None

    if "public" not in line:
        return None

    current_class = class_stack[-1][0]
    base_fqn = _make_fqn(package, class_stack)

    # Constructor: public ClassName(
    ctor_match = _CTOR_RE.match(line)
    if ctor_match and ctor_match.group(1) == current_class:
        full_sig, end_i = _collect_signature(lines, idx)
        params_str = _extract_params(full_sig)
        throws = _extract_throws(full_sig)
        doc = _look_back_for_javadoc(lines, idx)

        throws_part = f" throws {throws}" if throws else ""
        sig = f"{current_class}({params_str}){throws_part}"
        return _build_record(
            fqn=_method_fqn(base_fqn, current_class, params_str),
            namespace=package,
            class_name=current_class,
            member_name=current_class,
            member_type="method",
            signature=sig,
            summary=doc,
            file_path=file_path,
        )

    # Method: public [modifiers] ReturnType name(
    if "(" in stripped:
        meth_match = _METHOD_RE.match(line)
        if meth_match:
            ret_type = meth_match.group(1).strip()
            meth_name = meth_match.group(2)

            if meth_name in _SKIP_NAMES or meth_name == current_class:
                return None

            full_sig, end_i = _collect_signature(lines, idx)
            params_str = _extract_params(full_sig)
            throws = _extract_throws(full_sig)
            doc = _look_back_for_javadoc(lines, idx)

            mods = _extract_modifiers(stripped, ret_type)
            prefix = ""
            if "static" in mods:
                prefix += "static "
            if "abstract" in mods:
                prefix += "abstract "
            if "synchronized" in mods:
                prefix += "synchronized "

            throws_part = f" throws {throws}" if throws else ""
            sig = f"{prefix}{ret_type} {meth_name}({params_str}){throws_part}"

            return _build_record(
                fqn=_method_fqn(base_fqn, meth_name, params_str),
                namespace=package,
                class_name=current_class,
                member_name=meth_name,
                member_type="method",
                signature=sig,
                summary=doc,
                file_path=file_path,
            )

    # Field: public [static] [final] Type name [= value];
    if "(" not in stripped:
        field_match = _FIELD_RE.match(line)
        if field_match:
            field_type = field_match.group(1).strip()
            field_name = field_match.group(2)

            if field_name in _SKIP_NAMES:
                return None

            doc = _look_back_for_javadoc(lines, idx)
            mods = _extract_modifiers(stripped, field_type)

            prefix = ""
            if "static" in mods:
                prefix += "static "
            if "final" in mods:
                prefix += "final "

            sig = f"{prefix}{field_type} {field_name}"

            return _build_record(
                fqn=f"{base_fqn}.{field_name}",
                namespace=package,
                class_name=current_class,
                member_name=field_name,
                member_type="field",
                signature=sig,
                summary=doc,
                file_path=file_path,
            )

    return None


# --- Interface member parsing ---

def _try_parse_interface_member(
    line: str, lines: list[str], idx: int,
    package: str, class_name: str, file_path: str,
    class_stack: list[tuple[str, str, int]] | None = None,
) -> dict | None:
    """Parse a member inside an interface (all implicitly public)."""
    stripped = line.strip()
    if not stripped or stripped.startswith("//") or stripped in ("{", "}"):
        return None
    if stripped.startswith("/*") or stripped.startswith("*"):
        return None
    if stripped.startswith("@"):
        return None

    # Skip nested type declarations inside interface
    if re.search(r"\b(class|interface|enum|record)\s+\w+", stripped):
        return None

    base_fqn = _make_fqn(package, class_stack) if class_stack else (f"{package}.{class_name}" if package else class_name)

    # Method signature: [default|static] [<T>] ReturnType name(params) [throws ...];/{
    if "(" in stripped:
        meth_match = _IFACE_METHOD_RE.match(line)
        if meth_match:
            ret_type = meth_match.group(1).strip()
            meth_name = meth_match.group(2)

            if meth_name in _SKIP_NAMES:
                return None

            full_sig, end_i = _collect_signature(lines, idx)
            params_str = _extract_params(full_sig)
            throws = _extract_throws(full_sig)
            doc = _look_back_for_javadoc(lines, idx)

            prefix = ""
            if stripped.startswith("default "):
                prefix = "default "
            elif stripped.startswith("static "):
                prefix = "static "

            throws_part = f" throws {throws}" if throws else ""
            sig = f"{prefix}{ret_type} {meth_name}({params_str}){throws_part}"

            return _build_record(
                fqn=_method_fqn(base_fqn, meth_name, params_str),
                namespace=package,
                class_name=class_name,
                member_name=meth_name,
                member_type="method",
                signature=sig,
                summary=doc,
                file_path=file_path,
            )

    # Interface constant: Type CONSTANT_NAME = value;
    const_match = _IFACE_CONST_RE.match(line)
    if const_match:
        const_type = const_match.group(1).strip()
        const_name = const_match.group(2)

        if const_name in _SKIP_NAMES:
            return None

        doc = _look_back_for_javadoc(lines, idx)
        sig = f"static final {const_type} {const_name}"

        return _build_record(
            fqn=f"{base_fqn}.{const_name}",
            namespace=package,
            class_name=class_name,
            member_name=const_name,
            member_type="field",
            signature=sig,
            summary=doc,
            file_path=file_path,
        )

    return None


# --- Annotation element parsing ---

def _try_parse_annotation_element(
    line: str, lines: list[str], idx: int,
    package: str, class_name: str, file_path: str,
    class_stack: list[tuple[str, str, int]] | None = None,
) -> dict | None:
    """Parse an element inside an @interface (annotation type)."""
    stripped = line.strip()
    if not stripped or stripped.startswith("//") or stripped in ("{", "}"):
        return None
    if stripped.startswith("/*") or stripped.startswith("*"):
        return None
    if stripped.startswith("@"):
        return None

    base_fqn = _make_fqn(package, class_stack) if class_stack else (f"{package}.{class_name}" if package else class_name)

    elem_match = _ANNOTATION_ELEM_RE.match(stripped)
    if elem_match:
        elem_type = elem_match.group(1).strip()
        elem_name = elem_match.group(2)
        doc = _look_back_for_javadoc(lines, idx)
        sig = f"{elem_type} {elem_name}()"

        return _build_record(
            fqn=f"{base_fqn}.{elem_name}",
            namespace=package,
            class_name=class_name,
            member_name=elem_name,
            member_type="method",
            signature=sig,
            summary=doc,
            file_path=file_path,
        )

    return None


# --- Enum constant parsing ---

def _parse_enum_constants(
    lines: list[str], type_line_idx: int,
    base_fqn: str, package: str, enum_name: str, file_path: str,
) -> list[dict]:
    """Extract enum constant names from lines after the enum declaration."""
    records = []
    depth = 0
    started = False
    # Enum constants end at the first ; at depth 1 (enum body level)

    for j in range(type_line_idx, min(type_line_idx + 500, len(lines))):
        line = lines[j]
        b, _ = _count_braces_and_parens(line)
        if "{" in line and not started:
            depth += b
            started = True
            # Check if there are constants on the same line as the opening brace
            after_brace = line[line.index("{") + 1:].strip()
            if after_brace:
                const_match = _ENUM_CONST_RE.match(after_brace)
                if const_match:
                    name = const_match.group(1)
                    records.append(_build_record(
                        fqn=f"{base_fqn}.{name}",
                        namespace=package,
                        class_name=enum_name,
                        member_name=name,
                        member_type="field",
                        signature=f"{enum_name}.{name}",
                        summary="",
                        file_path=file_path,
                    ))
            continue
        if started:
            depth += b
            if depth <= 0:
                break
            stripped = line.strip()
            if not stripped or stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
                continue
            # End of constants section
            if stripped == ";":
                break
            # Skip if line starts with method/field keywords (we're past constants)
            if re.match(r"^\s*(?:public|private|protected|static|final|abstract|@)", line):
                break
            const_match = _ENUM_CONST_RE.match(stripped)
            if const_match:
                name = const_match.group(1)
                if not name.startswith("//"):
                    # Look back for Javadoc on this constant
                    doc = _look_back_for_javadoc(lines, j)
                    records.append(_build_record(
                        fqn=f"{base_fqn}.{name}",
                        namespace=package,
                        class_name=enum_name,
                        member_name=name,
                        member_type="field",
                        signature=f"{enum_name}.{name}",
                        summary=doc,
                        file_path=file_path,
                    ))
            # If we hit a semicolon on this line (after constants), stop
            if ";" in stripped and "(" not in stripped:
                # Only stop if the semicolon is after all constants on this line
                # (e.g., "VALUE1, VALUE2;")
                break

    return records


# --- Record component parsing ---

def _parse_record_components(
    decl_line: str, base_fqn: str, package: str, record_name: str, file_path: str,
) -> list[dict]:
    """Extract record components from the record declaration."""
    records = []

    # Find the parenthesized component list
    paren_start = decl_line.find("(")
    if paren_start == -1:
        return []

    # Find matching closing paren
    depth = 0
    paren_end = -1
    for j in range(paren_start, len(decl_line)):
        if decl_line[j] == "(":
            depth += 1
        elif decl_line[j] == ")":
            depth -= 1
            if depth == 0:
                paren_end = j
                break

    if paren_end == -1:
        return []

    components_str = decl_line[paren_start + 1:paren_end].strip()
    if not components_str:
        return []

    # Split components by comma (respecting generics)
    components = _split_params(components_str)
    for comp in components:
        comp = comp.strip()
        if not comp:
            continue
        # Remove annotations from component
        comp = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", comp).strip()
        # Type name  (last word is name, rest is type)
        parts = comp.rsplit(None, 1)
        if len(parts) == 2:
            comp_type, comp_name = parts
            records.append(_build_record(
                fqn=f"{base_fqn}.{comp_name}",
                namespace=package,
                class_name=record_name,
                member_name=comp_name,
                member_type="field",
                signature=f"{comp_type} {comp_name}",
                summary="",
                file_path=file_path,
            ))

    return records


# --- Javadoc extraction ---

def _look_back_for_javadoc(lines: list[str], decl_idx: int) -> str:
    """Look backwards from a declaration for a /** ... */ Javadoc block."""
    i = decl_idx - 1

    # Skip annotation lines
    while i >= 0 and lines[i].strip().startswith("@"):
        i -= 1

    # Skip empty lines (at most 1)
    if i >= 0 and not lines[i].strip():
        i -= 1

    if i < 0:
        return ""

    # Check if the line ends a Javadoc block
    if "*/" not in lines[i]:
        return ""

    # Walk backward to find the opening /**
    end_i = i
    while i >= 0:
        if _JAVADOC_START in lines[i]:
            break
        i -= 1
    else:
        return ""

    # Extract the Javadoc text
    doc_lines = []
    for j in range(i, end_i + 1):
        text = lines[j].strip()
        text = text.replace("/**", "").replace("*/", "")
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

    # Use string-aware counting to avoid miscounting parens inside literals
    _, paren_depth = _count_braces_and_parens(sig)
    while paren_depth > 0 and i + 1 < len(lines):
        i += 1
        next_line = lines[i]
        sig += " " + next_line.strip()
        _, delta = _count_braces_and_parens(next_line)
        paren_depth += delta

    return sig, i


def _extract_params(sig_text: str) -> str:
    """Extract parameter list from a method/constructor signature."""
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

    # Clean up whitespace and annotations
    params_str = re.sub(r"\s+", " ", params_str)
    # Remove parameter annotations like @NonNull, @Nullable
    params_str = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", params_str)
    return params_str.strip()


def _extract_throws(sig_text: str) -> str:
    """Extract throws clause from a method signature."""
    # Find 'throws' after the closing paren
    paren_start = sig_text.find("(")
    if paren_start == -1:
        return ""

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

    after_paren = sig_text[paren_end + 1:]
    throws_match = re.search(r"\bthrows\s+(.+?)(?:\s*\{|\s*;|\s*$)", after_paren)
    if throws_match:
        return throws_match.group(1).strip()
    return ""


# --- Brace counting (string-aware) ---

def _count_braces_and_parens(line: str) -> tuple[int, int]:
    """Count net brace and paren depth changes, skipping inside strings."""
    brace_depth = 0
    paren_depth = 0
    in_single = False
    in_double = False
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

        if ch == "'":
            in_single = True
        elif ch == '"':
            in_double = True
        elif ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
        elif ch == "(":
            paren_depth += 1
        elif ch == ")":
            paren_depth -= 1

    return brace_depth, paren_depth


# --- Modifier extraction ---

def _extract_modifiers(stripped: str, first_type_word: str) -> set[str]:
    """Extract modifier keywords from before the type/return type."""
    _KNOWN_MODS = {
        "static", "final", "abstract", "synchronized",
        "native", "default", "volatile", "transient",
    }
    idx = stripped.find(first_type_word)
    if idx <= 0:
        return set()
    prefix = stripped[:idx]
    return {w for w in prefix.split() if w in _KNOWN_MODS}


# --- FQN helpers ---

def _method_fqn(base_fqn: str, name: str, params_str: str) -> str:
    """Build a disambiguated FQN for methods by appending param types.

    Java params: "Type name" pairs. Extract the type part (everything before last word).
    Handles varargs (Type... name), generics (List<Item> items), arrays (int[] arr).
    """
    param_types = []
    if params_str.strip():
        for p in _split_params(params_str):
            p = p.strip()
            if not p:
                continue
            # Split into words; last word is param name, rest is type
            parts = p.rsplit(None, 1)
            if len(parts) == 2:
                param_types.append(parts[0])
            elif len(parts) == 1:
                param_types.append(parts[0])

    if param_types:
        return f"{base_fqn}.{name}({','.join(param_types)})"
    return f"{base_fqn}.{name}"


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


def _make_fqn(package: str, class_stack: list[tuple[str, str, int]]) -> str:
    """Build full FQN from package + class stack."""
    parts = [package] if package else []
    for name, _, _ in class_stack:
        parts.append(name)
    return ".".join(parts)


def _build_record(**kwargs) -> dict:
    return kwargs
