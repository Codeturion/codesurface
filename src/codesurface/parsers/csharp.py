"""C# parser that captures all public declarations.

Scans every line tracking namespace/class context, captures public members
with their full signatures. Doc comments (///) are extracted as bonus data.
"""

import re
from pathlib import Path

from .base import BaseParser


# --- Regex patterns ---

_NAMESPACE_RE = re.compile(r"^\s*namespace\s+([\w.]+)")

# Type declarations: class, struct, interface, enum
_TYPE_DECL_RE = re.compile(
    r"^\s*(?:public|internal)\s+"
    r"(?:static\s+)?(?:abstract\s+)?(?:sealed\s+)?(?:partial\s+)?(?:readonly\s+)?"
    r"(class|struct|interface|enum)\s+"
    r"(\w+)(?:<[^>]+>)?"           # name + optional generic params
    r"(?:\s*:\s*(.+?))?"           # optional base types
    r"\s*(?:\{|$|//|where\s)"      # opening brace, EOL, comment, or generic constraint
)

# Method declarations (including constructors via class name match)
_METHOD_RE = re.compile(
    r"^\s*(?:public)\s+"
    r"(?:static\s+)?(?:virtual\s+)?(?:override\s+)?(?:abstract\s+)?(?:new\s+)?(?:async\s+)?"
    r"([\w<>\[\],\s?]+?)\s+"       # return type
    r"(\w+)\s*"                     # method name
    r"(?:<[^>]+>)?\s*"             # optional generic params
    r"\(([^)]*)\)"                 # parameters (may be empty)
)

# Constructor (public ClassName(...)  -- closing paren may be on a later line)
_CTOR_RE = re.compile(
    r"^\s*(?:public)\s+"
    r"(\w+)\s*\(([^)]*)\)?"        # ClassName(params -- closing paren optional
)

# Property declarations
_PROPERTY_RE = re.compile(
    r"^\s*(?:public)\s+"
    r"(?:static\s+)?(?:virtual\s+)?(?:override\s+)?(?:abstract\s+)?(?:new\s+)?"
    r"([\w<>\[\],\s?]+?)\s+"       # type
    r"(\w+)\s*\{"                   # name + opening brace
)

# Field declarations
_FIELD_RE = re.compile(
    r"^\s*(?:public)\s+"
    r"(?:static\s+)?(?:readonly\s+)?(?:const\s+)?"
    r"([\w<>\[\],\s?]+?)\s+"       # type
    r"(\w+)\s*[;=]"                # name + semicolon or assignment
)

# Event declarations
_EVENT_RE = re.compile(
    r"^\s*(?:public)\s+"
    r"(?:static\s+)?event\s+"
    r"([\w<>\[\],\s?]+?)\s+"       # delegate type
    r"(\w+)\s*[;{]"                # name
)

# Interface method (no access modifier, inside interface)
_INTERFACE_METHOD_RE = re.compile(
    r"^\s*([\w<>\[\],\s?]+?)\s+"   # return type
    r"(\w+)\s*"                     # method name
    r"(?:<[^>]+>)?\s*"
    r"\(([^)]*)\)\s*;"             # params + semicolon
)

# Interface property
_INTERFACE_PROP_RE = re.compile(
    r"^\s*([\w<>\[\],\s?]+?)\s+"   # type
    r"(\w+)\s*\{"                   # name + brace
)

# Doc comment
_DOC_COMMENT_LINE = re.compile(r"^\s*///\s?(.*)")
_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.DOTALL)
_PARAM_RE = re.compile(r"<param\s+name=[\"'](\w+)[\"']>(.*?)</param>")
_RETURNS_RE = re.compile(r"<returns>(.*?)</returns>")
_SEE_CREF_RE = re.compile(r"<see\s+cref=[\"']([^\"']+)[\"']\s*/>")
_XML_TAG_RE = re.compile(r"<[^>]+>")

# Enum member
_ENUM_MEMBER_RE = re.compile(r"^\s*(\w+)\s*(?:=\s*\S+)?\s*,?\s*(?://.*)?$")

# Skip patterns -- lines that look like declarations but aren't
_SKIP_NAMES = frozenset({
    "class", "struct", "interface", "enum", "namespace",
    "if", "else", "for", "while", "switch", "try", "catch",
    "return", "throw", "yield", "break", "continue",
    "get", "set", "add", "remove", "value", "init",
})


class CSharpParser(BaseParser):
    """Parser for C# source files."""

    @property
    def file_extensions(self) -> list[str]:
        return [".cs"]

    def parse_file(self, path: Path, base_dir: Path) -> list[dict]:
        return _parse_cs_file(path, base_dir)


def _parse_cs_file(path: Path, base_dir: Path) -> list[dict]:
    """Parse a single .cs file and extract all public members."""
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except (OSError, UnicodeDecodeError):
        return []

    rel_path = str(path.relative_to(base_dir)).replace("\\", "/")
    lines = text.splitlines()
    records = []

    namespace = ""
    class_stack = []     # [(name, kind, depth)] where kind = class/struct/interface/enum
    brace_depth = 0
    in_multiline_comment = False

    i = 0
    while i < len(lines):
        line = lines[i]

        # Track multi-line comments
        if in_multiline_comment:
            if "*/" in line:
                in_multiline_comment = False
            i += 1
            continue
        if "/*" in line and "*/" not in line:
            # Only enter multi-line mode if not closed on same line
            stripped = line.lstrip()
            if not stripped.startswith("//"):
                in_multiline_comment = True
                i += 1
                continue

        # Skip single-line comments (but not doc comments -- we extract those)
        stripped = line.strip()
        if stripped.startswith("//") and not stripped.startswith("///"):
            i += 1
            continue

        # Skip doc comment lines (we'll look backwards for them)
        if stripped.startswith("///"):
            i += 1
            continue

        # Track brace depth for class stack management
        open_braces = line.count("{") - line.count("}")
        new_depth = brace_depth + open_braces

        # Track namespace
        ns_match = _NAMESPACE_RE.match(line)
        if ns_match:
            namespace = ns_match.group(1)
            brace_depth = new_depth
            i += 1
            continue

        # Track type declarations (class/struct/interface/enum)
        type_match = _TYPE_DECL_RE.match(line)
        if type_match:
            kind = type_match.group(1)       # class/struct/interface/enum
            type_name = type_match.group(2)
            bases = type_match.group(3) or ""

            # Pop any classes at same or deeper depth
            while class_stack and class_stack[-1][2] >= brace_depth:
                class_stack.pop()

            class_stack.append((type_name, kind, brace_depth))

            # Only record public types
            if re.match(r"^\s*public\s+", line):
                doc = _look_back_for_doc(lines, i)
                fqn = f"{namespace}.{type_name}" if namespace else type_name

                sig_parts = [f"{kind} {type_name}"]
                if bases:
                    sig_parts.append(f" : {bases.strip()}")
                signature = "".join(sig_parts)

                records.append(_build_record(
                    fqn=fqn,
                    namespace=namespace,
                    class_name=type_name,
                    member_name="",
                    member_type="type",
                    signature=signature,
                    summary=doc.get("summary", ""),
                    params_json=doc.get("params", []),
                    returns_text="",
                    file_path=rel_path,
                ))

                # For enums, extract members
                if kind == "enum":
                    records.extend(_parse_enum_members(
                        lines, i, namespace, type_name, rel_path
                    ))

            brace_depth = new_depth
            i += 1
            continue

        # --- Member declarations (only inside a type) ---
        if class_stack:
            current_class = class_stack[-1][0]
            current_kind = class_stack[-1][1]
            base_fqn = f"{namespace}.{current_class}" if namespace else current_class

            # Inside an interface -- members have no access modifier
            if current_kind == "interface":
                record = _try_parse_interface_member(
                    line, lines, i, namespace, current_class, rel_path
                )
                if record:
                    records.append(record)
                    brace_depth = new_depth
                    i += 1
                    continue

            # Public event (check before property/method)
            if "public" in line and "event " in line:
                ev_match = _EVENT_RE.match(line)
                if ev_match:
                    ev_type = ev_match.group(1).strip()
                    ev_name = ev_match.group(2)
                    if ev_name not in _SKIP_NAMES:
                        doc = _look_back_for_doc(lines, i)
                        records.append(_build_record(
                            fqn=f"{base_fqn}.{ev_name}",
                            namespace=namespace,
                            class_name=current_class,
                            member_name=ev_name,
                            member_type="event",
                            signature=f"event {ev_type} {ev_name}",
                            summary=doc.get("summary", ""),
                            params_json=[],
                            returns_text="",
                            file_path=rel_path,
                        ))
                    brace_depth = new_depth
                    i += 1
                    continue

            # Public method
            if "public" in line and "(" in line:
                # Try constructor first
                ctor_match = _CTOR_RE.match(line)
                if ctor_match and ctor_match.group(1) == current_class:
                    params_str = _collect_params(lines, i, ctor_match.group(2))
                    doc = _look_back_for_doc(lines, i)
                    sig = f"{current_class}({params_str})"
                    records.append(_build_record(
                        fqn=_method_fqn(base_fqn, current_class, params_str),
                        namespace=namespace,
                        class_name=current_class,
                        member_name=current_class,
                        member_type="method",
                        signature=sig,
                        summary=doc.get("summary", ""),
                        params_json=doc.get("params", []),
                        returns_text="",
                        file_path=rel_path,
                    ))
                    brace_depth = new_depth
                    i += 1
                    continue

                method_match = _METHOD_RE.match(line)
                if method_match:
                    ret_type = method_match.group(1).strip()
                    meth_name = method_match.group(2)
                    params_str = _collect_params(lines, i, method_match.group(3))
                    if meth_name not in _SKIP_NAMES:
                        doc = _look_back_for_doc(lines, i)
                        sig = f"{ret_type} {meth_name}({params_str})"
                        records.append(_build_record(
                            fqn=_method_fqn(base_fqn, meth_name, params_str),
                            namespace=namespace,
                            class_name=current_class,
                            member_name=meth_name,
                            member_type="method",
                            signature=sig,
                            summary=doc.get("summary", ""),
                            params_json=doc.get("params", []),
                            returns_text=doc.get("returns", ""),
                            file_path=rel_path,
                        ))
                    brace_depth = new_depth
                    i += 1
                    continue

            # Public property (has { after name)
            if "public" in line and "{" in line and "(" not in line and "event " not in line:
                prop_match = _PROPERTY_RE.match(line)
                if prop_match:
                    prop_type = prop_match.group(1).strip()
                    prop_name = prop_match.group(2)
                    if prop_name not in _SKIP_NAMES:
                        # Extract accessor info
                        accessors = _extract_accessors(line)
                        doc = _look_back_for_doc(lines, i)
                        sig = f"{prop_type} {prop_name} {{ {accessors} }}"
                        records.append(_build_record(
                            fqn=f"{base_fqn}.{prop_name}",
                            namespace=namespace,
                            class_name=current_class,
                            member_name=prop_name,
                            member_type="property",
                            signature=sig,
                            summary=doc.get("summary", ""),
                            params_json=[],
                            returns_text="",
                            file_path=rel_path,
                        ))
                    brace_depth = new_depth
                    i += 1
                    continue

            # Public field (ends with ; or =, no { and no ()
            if "public" in line and "(" not in line and "{" not in line:
                field_match = _FIELD_RE.match(line)
                if field_match:
                    field_type = field_match.group(1).strip()
                    field_name = field_match.group(2)
                    if field_name not in _SKIP_NAMES:
                        doc = _look_back_for_doc(lines, i)
                        readonly = "readonly " if "readonly" in line else ""
                        const = "const " if " const " in line else ""
                        static = "static " if " static " in line else ""
                        sig = f"{static}{readonly}{const}{field_type} {field_name}"
                        records.append(_build_record(
                            fqn=f"{base_fqn}.{field_name}",
                            namespace=namespace,
                            class_name=current_class,
                            member_name=field_name,
                            member_type="field",
                            signature=sig.strip(),
                            summary=doc.get("summary", ""),
                            params_json=[],
                            returns_text="",
                            file_path=rel_path,
                        ))
                    brace_depth = new_depth
                    i += 1
                    continue

        brace_depth = new_depth

        # Pop class stack when we close their scope
        while class_stack and brace_depth <= class_stack[-1][2]:
            class_stack.pop()

        i += 1

    return records


def _try_parse_interface_member(
    line: str, lines: list[str], idx: int,
    namespace: str, class_name: str, file_path: str,
) -> dict | None:
    """Parse a member inside an interface (no access modifier)."""
    stripped = line.strip()

    # Skip non-declaration lines
    if not stripped or stripped.startswith("//") or stripped.startswith("{") or stripped.startswith("}"):
        return None
    if stripped.startswith("["):  # attributes
        return None

    base_fqn = f"{namespace}.{class_name}" if namespace else class_name

    # Interface method
    meth_match = _INTERFACE_METHOD_RE.match(line)
    if meth_match:
        ret_type = meth_match.group(1).strip()
        meth_name = meth_match.group(2)
        params_str = meth_match.group(3).strip()
        if meth_name not in _SKIP_NAMES:
            doc = _look_back_for_doc(lines, idx)
            sig = f"{ret_type} {meth_name}({params_str})"
            return _build_record(
                fqn=_method_fqn(base_fqn, meth_name, params_str),
                namespace=namespace,
                class_name=class_name,
                member_name=meth_name,
                member_type="method",
                signature=sig,
                summary=doc.get("summary", ""),
                params_json=doc.get("params", []),
                returns_text=doc.get("returns", ""),
                file_path=file_path,
            )

    # Interface property
    prop_match = _INTERFACE_PROP_RE.match(line)
    if prop_match:
        prop_type = prop_match.group(1).strip()
        prop_name = prop_match.group(2)
        if prop_name not in _SKIP_NAMES:
            accessors = _extract_accessors(line)
            doc = _look_back_for_doc(lines, idx)
            sig = f"{prop_type} {prop_name} {{ {accessors} }}"
            return _build_record(
                fqn=f"{base_fqn}.{prop_name}",
                namespace=namespace,
                class_name=class_name,
                member_name=prop_name,
                member_type="property",
                signature=sig,
                summary=doc.get("summary", ""),
                params_json=[],
                returns_text="",
                file_path=file_path,
            )

    return None


def _parse_enum_members(
    lines: list[str], type_line_idx: int,
    namespace: str, enum_name: str, file_path: str,
) -> list[dict]:
    """Extract enum member names from the lines after the enum declaration."""
    records = []
    base_fqn = f"{namespace}.{enum_name}" if namespace else enum_name
    depth = 0
    started = False

    for i in range(type_line_idx, min(type_line_idx + 100, len(lines))):
        line = lines[i]
        if "{" in line:
            depth += line.count("{") - line.count("}")
            started = True
            continue
        if started:
            depth += line.count("{") - line.count("}")
            if depth <= 0:
                break
            stripped = line.strip()
            if not stripped or stripped.startswith("//") or stripped.startswith("["):
                continue
            em_match = _ENUM_MEMBER_RE.match(stripped)
            if em_match:
                name = em_match.group(1)
                if name not in ("None",) and not name.startswith("//"):
                    records.append(_build_record(
                        fqn=f"{base_fqn}.{name}",
                        namespace=namespace,
                        class_name=enum_name,
                        member_name=name,
                        member_type="field",
                        signature=f"{enum_name}.{name}",
                        summary="",
                        params_json=[],
                        returns_text="",
                        file_path=file_path,
                    ))
    return records


def _look_back_for_doc(lines: list[str], decl_idx: int) -> dict:
    """Look backwards from a declaration for /// doc comments."""
    doc_lines = []
    i = decl_idx - 1

    # Skip attributes
    while i >= 0 and lines[i].strip().startswith("["):
        i -= 1

    while i >= 0:
        match = _DOC_COMMENT_LINE.match(lines[i])
        if match:
            doc_lines.insert(0, match.group(1))
            i -= 1
        else:
            break

    if not doc_lines:
        return {}

    doc_text = "\n".join(doc_lines)
    result = {}

    # Summary
    summary_match = _SUMMARY_RE.search(doc_text)
    if summary_match:
        result["summary"] = _clean_xml_text(summary_match.group(1).strip())
    else:
        # Use entire doc text as summary
        result["summary"] = _clean_xml_text(doc_text)

    # Params
    params = [
        {"name": m.group(1), "description": _clean_xml_text(m.group(2))}
        for m in _PARAM_RE.finditer(doc_text)
    ]
    if params:
        result["params"] = params

    # Returns
    returns_match = _RETURNS_RE.search(doc_text)
    if returns_match:
        result["returns"] = _clean_xml_text(returns_match.group(1))

    return result


def _collect_params(lines: list[str], line_idx: int, initial: str) -> str:
    """Collect parameters that may span multiple lines."""
    params = initial.strip()
    if ")" in lines[line_idx]:
        return params

    # Multi-line params -- collect until closing paren
    for j in range(line_idx + 1, min(line_idx + 50, len(lines))):
        part = lines[j].strip()
        params += " " + part
        if ")" in part:
            # Trim at closing paren
            paren_idx = params.index(")")
            params = params[:paren_idx]
            break

    return re.sub(r"\s+", " ", params).strip()


def _extract_accessors(line: str) -> str:
    """Extract get/set from a property line."""
    parts = []
    if "get;" in line or "get ;" in line or "{ get" in line:
        parts.append("get;")
    if "set;" in line or "set ;" in line:
        parts.append("set;")
    if not parts:
        # Auto-property or complex accessor -- just say get/set
        if "=>" in line:
            parts.append("get;")
        else:
            parts.append("get; set;")
    return " ".join(parts)


def _method_fqn(base_fqn: str, name: str, params_str: str) -> str:
    """Build a disambiguated FQN for methods by appending param types."""
    param_types = []
    if params_str.strip():
        for p in params_str.split(","):
            parts = p.strip().split()
            if len(parts) >= 2:
                # Last token is param name, everything before is type
                param_types.append(" ".join(parts[:-1]))
            elif len(parts) == 1:
                param_types.append(parts[0])
    if param_types:
        return f"{base_fqn}.{name}({','.join(param_types)})"
    return f"{base_fqn}.{name}"


def _build_record(**kwargs) -> dict:
    return kwargs


def _clean_xml_text(text: str) -> str:
    """Strip XML tags and normalize whitespace."""
    text = _SEE_CREF_RE.sub(lambda m: m.group(1).split(".")[-1], text)
    text = _XML_TAG_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
