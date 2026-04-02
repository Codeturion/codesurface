"""C++ header parser that captures public API declarations.

Scans C++ header files (.h, .hpp, .hxx, .h++) tracking brace-depth scope,
namespace nesting, class/struct/union hierarchy with access specifier state,
and emits only public members.

Captures: classes, structs, unions, methods, constructors, destructors,
operator overloads, free functions, public fields, enums, enum class,
typedef, and using aliases.

Doc comments: Doxygen /** */ blocks and /// lines with @brief/@param/@return
extraction.
"""

import re
from pathlib import Path

from .base import BaseParser

# ---------------------------------------------------------------------------
# Skip patterns
# ---------------------------------------------------------------------------

_SKIP_DIRS = frozenset({
    "build", ".git", "third_party", "vendor", "test", "tests",
    "examples", "node_modules", ".cache", "obj", "out",
    "Debug", "Release", "x64", "x86", ".vs",
})

_SKIP_DIR_PREFIXES = ("cmake-build-",)

# C++ keywords that can't be member names
_CPP_KEYWORDS = frozenset({
    "if", "else", "for", "while", "do", "switch", "case", "default",
    "break", "continue", "return", "goto", "try", "catch", "throw",
    "new", "delete", "this", "sizeof", "alignof", "decltype", "typeid",
    "static_cast", "dynamic_cast", "const_cast", "reinterpret_cast",
    "true", "false", "nullptr", "void", "auto", "register", "volatile",
    "extern", "mutable", "inline", "constexpr", "consteval", "constinit",
    "public", "protected", "private", "friend", "using", "namespace",
    "class", "struct", "union", "enum", "typedef", "template",
    "virtual", "override", "final", "static", "const", "noexcept",
    "explicit", "operator", "typename", "concept", "requires",
})

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Trailing qualifier keywords that can follow the closing paren of a signature
_TRAILING_QUAL_STARTS = (
    "noexcept", "override", "final", "volatile",
    "->", "= 0", "= default", "= delete", "[[", "requires", "throw(",
)

# Regex for matching "const" as a trailing qualifier of a method signature.
# Must be followed by another known qualifier, semicolon, brace, =, or end-of-line.
# This prevents "const Type& foo()" from being swallowed as a trailing qualifier.
_TRAILING_CONST_RE = re.compile(
    r"^const\s*(?:noexcept|override|final|volatile|;|\{|=|\[|\->|&|\s*$)"
)

# extern "C" { block — transparent scope (declarations inside are file-scope)
_EXTERN_C_RE = re.compile(r'^\s*extern\s+"C"\s*\{')
# extern "C" without brace (may be on next line, or single-decl form)
_EXTERN_C_NOBRACE_RE = re.compile(r'^\s*extern\s+"C"\s*$')

# Export/API macros: SFML_API, IMGUI_API, MY_EXPORT, CV_EXPORTS, CV_EXPORTS_W, etc.
_EXPORT_MACRO_RE = re.compile(r"\b\w+_(?:API|EXPORTS?|DLL|SHARED)(?:_\w+)*\b")

# Namespace: namespace foo { or namespace foo::bar {
_NAMESPACE_RE = re.compile(
    r"^\s*(?:inline\s+)?namespace\s+"
    r"(\w+(?:::\w+)*)"          # namespace name (possibly nested)
    r"\s*\{?"
)

# Anonymous namespace
_ANON_NAMESPACE_RE = re.compile(r"^\s*(?:inline\s+)?namespace\s*\{")

# Access specifier: public: / protected: / private:
_ACCESS_RE = re.compile(r"^\s*(public|protected|private)\s*:")

# Class/struct/union declaration
# Handles: template<...> class EXPORT_API ClassName : public Base {
# Handles: struct [[nodiscard]] ClassName : public Base {
_CLASS_RE = re.compile(
    r"^\s*(?:template\s*<[^>]*>\s*)?"       # optional template<...>
    r"(class|struct|union)\s+"
    r"(?:\[\[[^\]]*\]\]\s*)?"               # optional [[attribute]]
    r"(?:\w+_(?:API|EXPORTS?|DLL|SHARED)(?:_\w+)*\s+)?"  # optional export macro
    r"(\w+)"                                 # class name
    r"(?:\s+final)?"                         # optional final
    r"(.*)"                                  # rest: inheritance, {, ;
)

# Forward declaration: class Foo; or struct Foo;
_FORWARD_DECL_RE = re.compile(
    r"^\s*(?:class|struct|union)\s+"
    r"(?:\[\[[^\]]*\]\]\s*)?"               # optional [[attribute]]
    r"(?:\w+_(?:API|EXPORTS?|DLL|SHARED)(?:_\w+)*\s+)?"
    r"\w+\s*;"
)

# Friend declaration
_FRIEND_RE = re.compile(r"^\s*friend\s+")

# Enum: enum Foo { or enum class Foo : int {
_ENUM_RE = re.compile(
    r"^\s*(enum\s+class|enum\s+struct|enum)\s+"
    r"(\w+)"                                 # enum name
    r"(?:\s*:\s*([\w:]+(?:\s+\w+)*))?"       # optional underlying type
    r"(.*)"                                  # rest
)

# Enum value: NAME = value, or NAME,
_ENUM_VALUE_RE = re.compile(
    r"^\s*(\w+)"                             # enumerator name
    r"(?:\s*=\s*([^,}]+))?"                  # optional = value
    r"\s*[,}]?"
)

# Typedef: typedef old_type new_name;
_TYPEDEF_RE = re.compile(
    r"^\s*typedef\s+"
    r"(.+?)\s+"                              # original type
    r"(\w+)\s*;"                             # new name
)

# Using alias: using Name = type;
_USING_ALIAS_RE = re.compile(
    r"^\s*(?:template\s*<[^>]*>\s*)?"
    r"using\s+(\w+)\s*=\s*(.+?)\s*;"
)

# Method/function declaration (very broad, refined in code)
# Captures: optional qualifiers, return type, name, params
# Return type is greedy and must end at a ptr/ref char or whitespace boundary,
# which correctly handles all C++ pointer styles:
#   Type name(     — ends at space
#   Type *name(    — ends at * (Godot/Linux style)
#   Type* name(    — ends at space after *
#   Type * name(   — ends at space after *
_FUNC_RE = re.compile(
    r"^\s*"
    r"((?:(?:static|virtual|inline|explicit|constexpr|consteval|"
    r"friend|extern|nodiscard|\[\[nodiscard\]\]|"
    r"\w+_(?:API|EXPORTS?|DLL|SHARED)(?:_\w+)*|"
    r"[A-Z_][A-Z_0-9]+)\s+)*)"              # leading qualifiers (incl. ALL_CAPS macros)
    r"([\w:*&<>,\s]+(?:[*&]\s*|\s))"         # return type (greedy, ends at ptr/ref or space)
    r"(\w+)"                                 # function/method name
    r"\s*\("                                 # open paren
)

# Constructor: ClassName(params)
# Accepts C++ keywords + ALL_CAPS macro qualifiers (SIMD_FORCE_INLINE, _FORCE_INLINE_, etc.)
# Safe to be permissive: name must match current class (checked in code at line ~896)
_CTOR_RE = re.compile(
    r"^\s*"
    r"((?:(?:explicit|inline|constexpr|consteval|"
    r"\w+_(?:API|EXPORTS?|DLL|SHARED)(?:_\w+)*|"  # export macros (CV_EXPORTS_W, etc.)
    r"[A-Z_][A-Z_0-9]+)\s+)*)"              # ALL_CAPS macros (SIMD_FORCE_INLINE, etc.)
    r"(\w+)"                                 # class name (must match current)
    r"\s*\("                                 # open paren
)

# Destructor: ~ClassName(), virtual ~ClassName(), EXPORT_API ~ClassName()
_DTOR_RE = re.compile(
    r"^\s*"
    r"(?:(?:virtual|inline|"
    r"\w+_(?:API|EXPORTS?|DLL|SHARED)(?:_\w+)*|"
    r"[A-Z_][A-Z_0-9]+)\s+)*"               # optional qualifiers
    r"~(\w+)"                                # class name
    r"\s*\("
)

# Operator overload: ReturnType operator+(...) or operator Type()
_OPERATOR_RE = re.compile(
    r"^\s*"
    r"((?:(?:static|virtual|inline|explicit|constexpr|friend|"
    r"\w+_(?:API|EXPORTS?|DLL|SHARED)(?:_\w+)*)\s+)*)"  # leading qualifiers
    r"([\w:*&<>,\s]*?)\s*"                   # return type (may be empty for conversion)
    r"(operator\s*(?:\(\)|"                   # operator() — call operator
    r"\[\]|"                                  # operator[] — subscript
    r"->|"                                    # operator-> — member access
    r"<<|>>|"                                 # shift operators
    r"\+\+|--|"                              # increment/decrement operators
    r"[+\-*/%^&|~!=<>]=?|"                   # arithmetic/comparison ops
    r"&&|\|\||"                              # logical ops
    r",|"                                    # comma operator
    r"\w[\w:*&<> ]*?"                        # conversion operator
    r"))"
    r"\s*\("                                 # open paren
)

# Field declaration: type name; or type name = value;
# Only matched inside class/struct bodies when access is public
# Type uses greedy match ending at ptr/ref or space (handles Type*Name style)
_FIELD_RE = re.compile(
    r"^\s*"
    r"((?:(?:static|const|constexpr|inline|mutable|volatile)\s+)*)"  # qualifiers
    r"([\w:*&<>,\s]+(?:[*&]\s*|\s))"          # type (greedy, ends at ptr/ref or space)
    r"(\w+)"                                  # field name
    r"(?:\s*(?:=\s*[^;]+|{[^}]*}|\[[^\]]*\]))?"  # optional init
    r"\s*;"
)

# Macro-wrapped class: ATTRIBUTE_ALIGNED16(class) or MY_MACRO(struct)
# The class/struct keyword is inside a macro call, name comes on next line
_MACRO_CLASS_RE = re.compile(
    r"^\s*\w+\s*\(\s*(class|struct|union)\s*\)\s*$"
)

# Bare class name on its own line (follows a MACRO(class) line)
_BARE_NAME_RE = re.compile(r"^\s*(\w+)\s*$")

# Class name with inheritance on same line (follows a MACRO(class) line)
# e.g., "btTypedConstraint : public btTypedObject"
_BARE_NAME_INHERIT_RE = re.compile(
    r"^\s*(\w+)"                              # class name
    r"(?:\s+final)?"                          # optional final
    r"\s*:\s*(.+)"                            # : inheritance...
)

# Template prefix: template<...> (possibly multi-line)
_TEMPLATE_RE = re.compile(r"^\s*template\s*<")

# Preprocessor directive
_PREPROCESSOR_RE = re.compile(r"^\s*#")

# Macro continuation (line ending with \)
_MACRO_CONT_RE = re.compile(r"\\\s*$")


class CppParser(BaseParser):
    """Parser for C++ header files."""

    @property
    def file_extensions(self) -> list[str]:
        return [".h", ".hpp", ".hxx", ".h++"]

    def parse_directory(self, directory: Path) -> list[dict]:
        """Override to skip build/vendor/test directories."""
        records: list[dict] = []
        ext_set = set(self.file_extensions)
        for f in sorted(directory.rglob("*")):
            if f.suffix not in ext_set:
                continue
            parts = f.relative_to(directory).parts
            if any(
                p in _SKIP_DIRS
                or any(p.startswith(pfx) for pfx in _SKIP_DIR_PREFIXES)
                for p in parts
            ):
                continue
            try:
                records.extend(self.parse_file(f, directory))
            except Exception as e:
                import sys
                print(f"codesurface: failed to parse {f}: {e}", file=sys.stderr)
                continue
        return records

    def parse_file(self, path: Path, base_dir: Path) -> list[dict]:
        return _parse_cpp_file(path, base_dir)


# ---------------------------------------------------------------------------
# Core parser
# ---------------------------------------------------------------------------

def _parse_cpp_file(path: Path, base_dir: Path) -> list[dict]:
    """Parse a single C++ header file and extract public API members."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return []

    rel_path = path.relative_to(base_dir).as_posix()
    lines = text.splitlines()

    # Skip generated files
    for line in lines[:20]:
        if "DO NOT EDIT" in line or "GENERATED" in line.upper():
            return []

    records: list[dict] = []

    # State
    namespace_stack: list[tuple[str, int]] = []   # (name, brace_depth_when_opened)
    class_stack: list[list] = []                   # [name, kind, depth, access]
    brace_depth = 0
    in_multiline_comment = False
    pending_template = ""
    in_enum: str = ""           # enum name if inside enum body
    enum_class_name: str = ""   # owning class for the enum, if any
    enum_brace_depth = -1
    pending_enum: str = ""      # enum name when { is on next line
    pending_enum_class: str = ""
    # Deferred class push: when class decl has no { on its line
    # Stored as [name, kind, inheritance, decl_line_idx, already_emitted]
    pending_class: list | None = None
    # MACRO(class) on previous line — kind stored, waiting for name on next line
    pending_macro_class_kind: str = ""
    # extern "C" { transparent scopes — track brace depths so declarations
    # inside are treated as file-scope rather than nested
    extern_c_depths: list[int] = []
    pending_extern_c: bool = False

    i = 0
    while i < len(lines):
        line = lines[i]

        # --- Multi-line comment continuation ---
        if in_multiline_comment:
            if "*/" in line:
                in_multiline_comment = False
                after = line[line.index("*/") + 2:]
                brace_depth += _count_braces(after)
            i += 1
            continue

        stripped = line.strip()

        # Empty line
        if not stripped:
            i += 1
            continue

        # Start of multi-line comment (not doc comment — those are read on demand)
        if "/*" in stripped and "*/" not in stripped:
            # Check if it's NOT a doc comment (we handle those via lookback)
            if not stripped.startswith("/**") and not stripped.startswith("/*!"):
                if not stripped.startswith("//"):
                    in_multiline_comment = True
                    pre = line[:line.find("/*")]
                    brace_depth += _count_braces(pre)
                    i += 1
                    continue
            else:
                # Doc comment block — skip lines until */
                in_multiline_comment = True
                i += 1
                continue

        # Single-line comment
        if stripped.startswith("//"):
            i += 1
            continue

        # Constructor initializer list lines (: member(val) or , member(val))
        # Only skip when inside a class body (not at namespace/file scope)
        if class_stack and brace_depth > class_stack[-1][2]:
            if stripped.startswith(":") and not stripped.startswith("::"):
                brace_depth += _count_braces(line)
                i += 1
                continue
            if stripped.startswith(",") and "(" in stripped:
                brace_depth += _count_braces(line)
                i += 1
                continue

        # Preprocessor
        if _PREPROCESSOR_RE.match(line):
            # Skip continuation lines
            while _MACRO_CONT_RE.search(line) and i + 1 < len(lines):
                i += 1
                line = lines[i]
            i += 1
            continue

        # --- extern "C" { transparent scope ---
        if _EXTERN_C_RE.match(stripped):
            # extern "C" { on this line — record brace depth, consume the {
            extern_c_depths.append(brace_depth)
            brace_depth += _count_braces(line)
            i += 1
            continue
        if _EXTERN_C_NOBRACE_RE.match(stripped):
            # extern "C" without { — brace may be on next line
            pending_extern_c = True
            i += 1
            continue
        if pending_extern_c:
            pending_extern_c = False
            if stripped.startswith("{"):
                extern_c_depths.append(brace_depth)
                brace_depth += _count_braces(line)
                i += 1
                continue
            # No brace — single-decl form (extern "C" void foo();)
            # Fall through to normal parsing

        # Count braces
        brace_delta = _count_braces(line)
        new_depth = brace_depth + brace_delta

        # Close extern "C" scopes when brace depth drops
        while extern_c_depths and new_depth <= extern_c_depths[-1]:
            extern_c_depths.pop()

        # --- MACRO(class) pattern (e.g. ATTRIBUTE_ALIGNED16(class)) ---
        if pending_macro_class_kind:
            # Expecting the class name on this line (bare or with inheritance)
            bare_m = _BARE_NAME_RE.match(stripped)
            if bare_m:
                macro_name = bare_m.group(1)
                if macro_name not in _CPP_KEYWORDS:
                    pending_class = [macro_name, pending_macro_class_kind, "", i, False]
                    pending_macro_class_kind = ""
                    brace_depth = new_depth
                    i += 1
                    continue
            # Also try name with inheritance: "Name : public Base"
            inherit_m = _BARE_NAME_INHERIT_RE.match(stripped)
            if inherit_m:
                macro_name = inherit_m.group(1)
                macro_inherit = inherit_m.group(2).strip()
                # Strip opening brace from inheritance if present
                brace_pos = macro_inherit.find("{")
                if brace_pos != -1:
                    macro_inherit = macro_inherit[:brace_pos].strip()
                if macro_name not in _CPP_KEYWORDS:
                    pending_class = [macro_name, pending_macro_class_kind, macro_inherit, i, False]
                    pending_macro_class_kind = ""
                    brace_depth = new_depth
                    i += 1
                    continue
            pending_macro_class_kind = ""
            # Fall through to normal parsing

        macro_class_m = _MACRO_CLASS_RE.match(stripped)
        if macro_class_m:
            pending_macro_class_kind = macro_class_m.group(1)
            brace_depth = new_depth
            i += 1
            continue

        # --- Deferred class push: previous class decl had no { ---
        if pending_class and "{" in line:
            pc_name, pc_kind, pc_inherit, pc_decl_line, pc_emitted = pending_class
            default_access = "public" if pc_kind in ("struct", "union") else "private"
            class_stack.append([pc_name, pc_kind, brace_depth, default_access])
            # Emit type record for the deferred class (only if not already emitted)
            is_pub = _is_public(class_stack[:-1])  # check enclosing context
            if not pc_emitted and is_pub and pc_name not in _CPP_KEYWORDS:
                ns = _build_ns(namespace_stack)
                owning_class = class_stack[-2][0] if len(class_stack) > 1 else ""
                doc = _look_back_for_doc(lines, pc_decl_line)
                sig = f"{pc_kind} {pc_name}"
                if pc_inherit:
                    sig += f" : {pc_inherit}"
                if pending_template:
                    sig = pending_template + " " + sig
                fqn_parts = [p for p in [ns, owning_class, pc_name] if p]
                fqn = "::".join(fqn_parts)
                records.append(_build_record(
                    fqn=fqn,
                    namespace=ns,
                    class_name=owning_class or pc_name,
                    member_name="" if not owning_class else pc_name,
                    member_type="type",
                    signature=sig,
                    summary=doc.get("brief", ""),
                    file_path=rel_path,
                    line_start=pc_decl_line + 1,
                    line_end=pc_decl_line + 1,
                ))
            pending_class = None
            pending_template = ""
            brace_depth = new_depth
            i += 1
            continue

        # --- Deferred enum push: previous enum decl had no { ---
        if pending_enum:
            if "{" in stripped and stripped.startswith("{"):
                in_enum = pending_enum
                enum_class_name = pending_enum_class
                enum_brace_depth = brace_depth
                pending_enum = ""
                pending_enum_class = ""
                brace_depth = new_depth
                i += 1
                continue
            else:
                # Next line wasn't a standalone { — cancel deferred enum
                pending_enum = ""
                pending_enum_class = ""

        # --- Template accumulation ---
        if _TEMPLATE_RE.match(line) and not _has_declaration_after_template(stripped):
            pending_template = stripped
            # Multi-line template: balance angle brackets
            angle_depth = _count_angles(stripped)
            while angle_depth > 0 and i + 1 < len(lines):
                i += 1
                next_stripped = lines[i].strip()
                pending_template += " " + next_stripped
                angle_depth += _count_angles(next_stripped)
                new_depth += _count_braces(lines[i])
            brace_depth = new_depth
            i += 1
            continue

        # --- Inside enum body ---
        if in_enum:
            if new_depth <= enum_brace_depth:
                # Enum body closed
                in_enum = ""
                enum_class_name = ""
                enum_brace_depth = -1
                brace_depth = new_depth
                i += 1
                continue

            # Parse enum values
            val_m = _ENUM_VALUE_RE.match(stripped)
            if val_m and stripped not in ("{", "}"):
                val_name = val_m.group(1)
                if val_name not in _CPP_KEYWORDS and not val_name.startswith("//"):
                    val_value = val_m.group(2)
                    ns = _build_ns(namespace_stack)
                    sig = val_name
                    if val_value:
                        sig += f" = {val_value.strip()}"

                    fqn_parts = [p for p in [ns, enum_class_name, in_enum, val_name] if p]
                    fqn = "::".join(fqn_parts)

                    records.append(_build_record(
                        fqn=fqn,
                        namespace=ns,
                        class_name=in_enum,
                        member_name=val_name,
                        member_type="field",
                        signature=sig,
                        file_path=rel_path,
                        line_start=i + 1,
                        line_end=i + 1,
                    ))

            brace_depth = new_depth
            i += 1
            continue

        # --- Close class/struct/union scope ---
        while class_stack and new_depth <= class_stack[-1][2]:
            class_stack.pop()

        # --- Close namespace scope ---
        while namespace_stack and new_depth <= namespace_stack[-1][1]:
            namespace_stack.pop()

        # --- Anonymous namespace (skip tracking) ---
        if _ANON_NAMESPACE_RE.match(line):
            brace_depth = new_depth
            i += 1
            continue

        # --- Namespace declaration ---
        ns_m = _NAMESPACE_RE.match(line)
        if ns_m and "=" not in line:
            ns_name = ns_m.group(1)
            # Handle inline namespaces and nested namespace::name
            if "{" in line:
                # For nested ns like namespace a::b::c {
                parts = ns_name.split("::")
                base_depth = brace_depth
                for j, part in enumerate(parts):
                    # Each nested namespace segment gets its own depth level
                    # but they all open at the same brace
                    if j == len(parts) - 1:
                        namespace_stack.append((part, base_depth))
                    else:
                        namespace_stack.append((part, base_depth))
            else:
                # namespace without brace on same line — will come on next line
                namespace_stack.append((ns_name, brace_depth))
            pending_template = ""
            brace_depth = new_depth
            i += 1
            continue

        # --- Forward declaration (skip) ---
        if _FORWARD_DECL_RE.match(line):
            pending_template = ""
            brace_depth = new_depth
            i += 1
            continue

        # --- Friend declaration (skip) ---
        if _FRIEND_RE.match(stripped):
            pending_template = ""
            brace_depth = new_depth
            i += 1
            continue

        # --- Access specifier ---
        access_m = _ACCESS_RE.match(line)
        if access_m and class_stack:
            class_stack[-1][3] = access_m.group(1)
            brace_depth = new_depth
            i += 1
            continue

        # Determine if we're in a public context
        is_public = _is_public(class_stack)

        # Determine if we're at declaration level (not inside a function body)
        # Class members: depth == class_depth + 1
        # Namespace-level: depth == namespace_depth + 1 (or 0 if no namespace)
        # Deeper means we're inside a function body — skip declarations
        if class_stack:
            at_decl_level = brace_depth == class_stack[-1][2] + 1
        elif namespace_stack:
            at_decl_level = brace_depth == namespace_stack[-1][1] + 1
        else:
            at_decl_level = brace_depth == len(extern_c_depths)

        # --- Enum declaration ---
        enum_m = _ENUM_RE.match(line)
        if enum_m:
            enum_keyword = enum_m.group(1)
            enum_name = enum_m.group(2)
            enum_underlying = enum_m.group(3) or ""
            enum_rest = enum_m.group(4)

            if is_public and enum_name not in _CPP_KEYWORDS:
                ns = _build_ns(namespace_stack)
                owning_class = class_stack[-1][0] if class_stack else ""
                doc = _look_back_for_doc(lines, i)

                sig = f"{enum_keyword} {enum_name}"
                if enum_underlying:
                    sig += f" : {enum_underlying}"
                if pending_template:
                    sig = pending_template + " " + sig

                fqn_parts = [p for p in [ns, owning_class, enum_name] if p]
                fqn = "::".join(fqn_parts)

                records.append(_build_record(
                    fqn=fqn,
                    namespace=ns,
                    class_name=owning_class or enum_name,
                    member_name="" if not owning_class else enum_name,
                    member_type="type",
                    signature=sig,
                    summary=doc.get("brief", ""),
                    file_path=rel_path,
                    line_start=i + 1,
                    line_end=i + 1,
                ))

                # Track enum body for value extraction
                if "{" in line and "}" in line:
                    # Single-line enum: enum Foo { A, B, C };
                    brace_open = line.index("{")
                    brace_close = line.index("}")
                    body = line[brace_open + 1:brace_close].strip()
                    if body:
                        for val_part in body.split(","):
                            val_part = val_part.strip()
                            if not val_part:
                                continue
                            val_m2 = _ENUM_VALUE_RE.match(val_part)
                            if val_m2:
                                val_name = val_m2.group(1)
                                if val_name not in _CPP_KEYWORDS:
                                    val_value = val_m2.group(2)
                                    sig2 = val_name
                                    if val_value:
                                        sig2 += f" = {val_value.strip()}"
                                    fqn_parts2 = [p for p in [ns, owning_class, enum_name, val_name] if p]
                                    fqn2 = "::".join(fqn_parts2)
                                    records.append(_build_record(
                                        fqn=fqn2,
                                        namespace=ns,
                                        class_name=enum_name,
                                        member_name=val_name,
                                        member_type="field",
                                        signature=sig2,
                                        file_path=rel_path,
                                        line_start=i + 1,
                                        line_end=i + 1,
                                    ))
                elif "{" in line:
                    in_enum = enum_name
                    enum_class_name = owning_class
                    enum_brace_depth = brace_depth
                elif not enum_rest.rstrip().endswith(";"):
                    # Brace on next line — defer (skip forward declarations)
                    pending_enum = enum_name
                    pending_enum_class = owning_class

            pending_template = ""
            brace_depth = new_depth
            i += 1
            continue

        # --- Typedef ---
        td_m = _TYPEDEF_RE.match(line)
        if td_m:
            if is_public:
                orig_type = td_m.group(1).strip()
                new_name = td_m.group(2)
                if new_name not in _CPP_KEYWORDS:
                    ns = _build_ns(namespace_stack)
                    owning_class = class_stack[-1][0] if class_stack else ""
                    doc = _look_back_for_doc(lines, i)

                    sig = f"typedef {orig_type} {new_name}"
                    fqn_parts = [p for p in [ns, owning_class, new_name] if p]
                    fqn = "::".join(fqn_parts)

                    records.append(_build_record(
                        fqn=fqn,
                        namespace=ns,
                        class_name=owning_class or new_name,
                        member_name="" if not owning_class else new_name,
                        member_type="type",
                        signature=sig,
                        summary=doc.get("brief", ""),
                        file_path=rel_path,
                        line_start=i + 1,
                        line_end=i + 1,
                    ))

            pending_template = ""
            brace_depth = new_depth
            i += 1
            continue

        # --- Using alias ---
        using_m = _USING_ALIAS_RE.match(line)
        if using_m:
            if is_public:
                alias_name = using_m.group(1)
                alias_type = using_m.group(2).strip()
                if alias_name not in _CPP_KEYWORDS:
                    ns = _build_ns(namespace_stack)
                    owning_class = class_stack[-1][0] if class_stack else ""
                    doc = _look_back_for_doc(lines, i)

                    sig = f"using {alias_name} = {alias_type}"
                    if pending_template:
                        sig = pending_template + " " + sig
                    fqn_parts = [p for p in [ns, owning_class, alias_name] if p]
                    fqn = "::".join(fqn_parts)

                    records.append(_build_record(
                        fqn=fqn,
                        namespace=ns,
                        class_name=owning_class or alias_name,
                        member_name="" if not owning_class else alias_name,
                        member_type="type",
                        signature=sig,
                        summary=doc.get("brief", ""),
                        file_path=rel_path,
                        line_start=i + 1,
                        line_end=i + 1,
                    ))

            pending_template = ""
            brace_depth = new_depth
            i += 1
            continue

        # --- Class / struct / union declaration ---
        class_m = _CLASS_RE.match(line)
        if class_m:
            kind = class_m.group(1)       # class, struct, or union
            name = class_m.group(2)
            rest = class_m.group(3).strip()

            # Skip forward declarations (ending with ;)
            if rest.endswith(";") and "{" not in rest:
                pending_template = ""
                brace_depth = new_depth
                i += 1
                continue

            if name not in _CPP_KEYWORDS and is_public:
                ns = _build_ns(namespace_stack)
                owning_class = class_stack[-1][0] if class_stack else ""
                doc = _look_back_for_doc(lines, i)

                # Build signature with inheritance
                sig = f"{kind} {name}"
                # Extract inheritance
                inheritance = _extract_inheritance(rest)
                if inheritance:
                    sig += f" : {inheritance}"
                if pending_template:
                    sig = pending_template + " " + sig

                fqn_parts = [p for p in [ns, owning_class, name] if p]
                fqn = "::".join(fqn_parts)

                records.append(_build_record(
                    fqn=fqn,
                    namespace=ns,
                    class_name=owning_class or name,
                    member_name="" if not owning_class else name,
                    member_type="type",
                    signature=sig,
                    summary=doc.get("brief", ""),
                    file_path=rel_path,
                    line_start=i + 1,
                    line_end=i + 1,
                ))

            # Push onto class stack with default access
            if "{" in line:
                default_access = "public" if kind in ("struct", "union") else "private"
                class_stack.append([name, kind, brace_depth, default_access])
            else:
                # Brace on next line — defer the push
                inheritance = _extract_inheritance(rest)
                # Type record was already emitted above
                pending_class = [name, kind, inheritance, i, True]

            pending_template = ""
            brace_depth = new_depth
            i += 1
            continue

        # --- Destructor ---
        dtor_m = _DTOR_RE.match(line)
        if dtor_m and class_stack and at_decl_level:
            class_name_match = dtor_m.group(1)
            full_sig, end_i = _collect_signature(lines, i)
            if class_name_match == class_stack[-1][0] and is_public:
                ns = _build_ns(namespace_stack)
                owning_class = class_stack[-1][0]
                doc = _look_back_for_doc(lines, i)

                params_str = _extract_params_str(full_sig, f"~{class_name_match}")
                sig = _clean_sig(f"~{class_name_match}({params_str})")

                # Add qualifiers
                quals = _extract_trailing_qualifiers(full_sig)
                if quals:
                    sig += " " + quals

                if "virtual" in stripped:
                    sig = "virtual " + sig

                fqn = "::".join([p for p in [ns, owning_class, f"~{owning_class}"] if p])
                records.append(_build_record(
                    fqn=fqn,
                    namespace=ns,
                    class_name=owning_class,
                    member_name=f"~{owning_class}",
                    member_type="method",
                    signature=sig,
                    summary=doc.get("brief", ""),
                    params_json=doc.get("params", []),
                    file_path=rel_path,
                    line_start=i + 1,
                    line_end=end_i + 1,
                ))

            pending_template = ""
            new_depth += sum(_count_braces(lines[j]) for j in range(i + 1, end_i + 1))
            brace_depth = new_depth
            i = end_i + 1
            continue

        # --- Operator overload ---
        op_m = _OPERATOR_RE.match(line)
        if op_m and is_public and at_decl_level:
            qualifiers = op_m.group(1).strip()
            ret_type = op_m.group(2).strip()
            op_name = op_m.group(3).strip()

            # Skip friend declarations
            if "friend" in qualifiers:
                pending_template = ""
                brace_depth = new_depth
                i += 1
                continue

            ns = _build_ns(namespace_stack)
            owning_class = class_stack[-1][0] if class_stack else ""
            doc = _look_back_for_doc(lines, i)

            full_sig, end_i = _collect_signature(lines, i)
            params_str = _extract_params_str(full_sig, op_name)

            sig_parts = []
            if qualifiers:
                sig_parts.append(_strip_export_macros(qualifiers))
            if ret_type:
                sig_parts.append(_strip_export_macros(ret_type))
            sig_parts.append(f"{op_name}({params_str})")
            sig = _clean_sig(" ".join(sig_parts))

            quals = _extract_trailing_qualifiers(full_sig)
            if quals:
                sig += " " + quals

            if pending_template:
                sig = pending_template + " " + sig

            fqn_parts = [p for p in [ns, owning_class, op_name] if p]
            fqn = "::".join(fqn_parts)

            # Handle overloads: add param types to FQN
            param_types = _extract_param_types(params_str)
            if param_types:
                fqn += f"({param_types})"
            # Distinguish const vs non-const overloads
            if _quals_have_const(quals):
                fqn += " const"

            records.append(_build_record(
                fqn=fqn,
                namespace=ns,
                class_name=owning_class,
                member_name=op_name,
                member_type="method",
                signature=sig,
                summary=doc.get("brief", ""),
                params_json=doc.get("params", []),
                returns_text=doc.get("returns", ""),
                file_path=rel_path,
                line_start=i + 1,
                line_end=end_i + 1,
            ))

            pending_template = ""
            new_depth += sum(_count_braces(lines[j]) for j in range(i + 1, end_i + 1))
            brace_depth = new_depth
            i = end_i + 1
            continue

        # --- Constructor ---
        if class_stack and at_decl_level:
            ctor_m = _CTOR_RE.match(line)
            if ctor_m:
                ctor_name = ctor_m.group(2)
                full_sig, end_i = _collect_signature(lines, i)
                if ctor_name == class_stack[-1][0] and is_public:
                    qualifiers = ctor_m.group(1).strip()
                    ns = _build_ns(namespace_stack)
                    owning_class = class_stack[-1][0]
                    doc = _look_back_for_doc(lines, i)

                    params_str = _extract_params_str(full_sig, ctor_name)

                    sig_parts = []
                    if qualifiers:
                        sig_parts.append(_strip_export_macros(qualifiers))
                    sig_parts.append(f"{ctor_name}({params_str})")
                    sig = _clean_sig(" ".join(sig_parts))

                    # Add trailing qualifiers (noexcept, = default, = delete, etc.)
                    quals = _extract_trailing_qualifiers(full_sig)
                    if quals:
                        sig += " " + quals

                    if pending_template:
                        sig = pending_template + " " + sig

                    fqn_parts = [p for p in [ns, owning_class, ctor_name] if p]
                    fqn = "::".join(fqn_parts)

                    # Handle overloads
                    param_types = _extract_param_types(params_str)
                    if param_types:
                        fqn += f"({param_types})"

                    records.append(_build_record(
                        fqn=fqn,
                        namespace=ns,
                        class_name=owning_class,
                        member_name=ctor_name,
                        member_type="method",
                        signature=sig,
                        summary=doc.get("brief", ""),
                        params_json=doc.get("params", []),
                        file_path=rel_path,
                        line_start=i + 1,
                        line_end=end_i + 1,
                    ))

                pending_template = ""
                new_depth += sum(_count_braces(lines[j]) for j in range(i + 1, end_i + 1))
                brace_depth = new_depth
                i = end_i + 1
                continue

        # --- Method / Free function ---
        func_m = _FUNC_RE.match(line)
        if func_m and is_public and at_decl_level:
            qualifiers = func_m.group(1).strip()
            ret_type = func_m.group(2).strip()
            func_name = func_m.group(3)

            # Skip if func_name is a keyword or matches current class (that's a ctor)
            if func_name in _CPP_KEYWORDS:
                pending_template = ""
                brace_depth = new_depth
                i += 1
                continue

            # Skip friend functions
            if "friend" in qualifiers:
                pending_template = ""
                brace_depth = new_depth
                i += 1
                continue

            # Skip if this is actually a constructor (name matches class)
            if class_stack and func_name == class_stack[-1][0]:
                pending_template = ""
                brace_depth = new_depth
                i += 1
                continue

            # Skip macro-style variable declarations: TYPE MACRO(name) = value
            body_pos = _find_body_brace(stripped)
            after_parens = stripped[stripped.find(")") + 1:body_pos if body_pos != -1 else len(stripped)].strip() if ")" in stripped else ""
            if after_parens.startswith("=") and not any(after_parens.startswith(p) for p in ("= 0", "= default", "= delete")):
                pending_template = ""
                brace_depth = new_depth
                i += 1
                continue

            ns = _build_ns(namespace_stack)
            owning_class = class_stack[-1][0] if class_stack else ""
            doc = _look_back_for_doc(lines, i)

            full_sig, end_i = _collect_signature(lines, i)
            params_str = _extract_params_str(full_sig, func_name)

            sig_parts = []
            clean_quals = _strip_export_macros(qualifiers)
            if clean_quals:
                sig_parts.append(clean_quals)
            clean_ret = _strip_export_macros(ret_type)
            if clean_ret:
                sig_parts.append(clean_ret)
            sig_parts.append(f"{func_name}({params_str})")
            sig = _clean_sig(" ".join(sig_parts))

            # Add trailing qualifiers (const, noexcept, override, = 0, etc.)
            quals = _extract_trailing_qualifiers(full_sig)
            if quals:
                sig += " " + quals

            if pending_template:
                sig = pending_template + " " + sig

            fqn_parts = [p for p in [ns, owning_class, func_name] if p]
            fqn = "::".join(fqn_parts)

            # Handle overloads
            param_types = _extract_param_types(params_str)
            if param_types:
                fqn += f"({param_types})"
            # Distinguish const vs non-const overloads
            if _quals_have_const(quals):
                fqn += " const"

            records.append(_build_record(
                fqn=fqn,
                namespace=ns,
                class_name=owning_class,
                member_name=func_name,
                member_type="method",
                signature=sig,
                summary=doc.get("brief", ""),
                params_json=doc.get("params", []),
                returns_text=doc.get("returns", ""),
                file_path=rel_path,
                line_start=i + 1,
                line_end=end_i + 1,
            ))

            pending_template = ""
            new_depth += sum(_count_braces(lines[j]) for j in range(i + 1, end_i + 1))
            brace_depth = new_depth
            i = end_i + 1
            continue

        # --- Field (inside class/struct body, public only, at class body level) ---
        # Only match fields at class body depth (depth == class_depth + 1),
        # not inside method bodies (depth >= class_depth + 2)
        if class_stack and is_public and brace_depth == class_stack[-1][2] + 1:
            field_m = _FIELD_RE.match(line)
            if field_m:
                field_quals = field_m.group(1).strip()
                field_type = field_m.group(2).strip()
                field_name = field_m.group(3)

                if field_name not in _CPP_KEYWORDS:
                    # Skip if field_type looks like a keyword-only thing
                    if field_type and field_type not in _CPP_KEYWORDS:
                        ns = _build_ns(namespace_stack)
                        owning_class = class_stack[-1][0]
                        doc = _look_back_for_doc(lines, i)

                        sig_parts = []
                        if field_quals:
                            sig_parts.append(field_quals)
                        sig_parts.append(field_type)
                        sig_parts.append(field_name)
                        sig = _clean_sig(" ".join(sig_parts))

                        fqn_parts = [p for p in [ns, owning_class, field_name] if p]
                        fqn = "::".join(fqn_parts)

                        records.append(_build_record(
                            fqn=fqn,
                            namespace=ns,
                            class_name=owning_class,
                            member_name=field_name,
                            member_type="field",
                            signature=sig,
                            summary=doc.get("brief", ""),
                            file_path=rel_path,
                            line_start=i + 1,
                            line_end=i + 1,
                        ))

        pending_template = ""
        brace_depth = new_depth
        i += 1

    # Deduplicate — keep first occurrence
    unique: list[dict] = []
    seen: set[str] = set()
    for rec in records:
        fqn = rec["fqn"]
        if fqn not in seen:
            seen.add(fqn)
            unique.append(rec)
    return unique


# ---------------------------------------------------------------------------
# Helper: public access check
# ---------------------------------------------------------------------------

def _is_public(class_stack: list[list]) -> bool:
    """Check if the current position is in a public context.

    Returns True if we're at namespace scope (no class) or all enclosing
    classes have public access for the current section.
    """
    if not class_stack:
        return True
    # All enclosing classes must grant public visibility
    return all(cs[3] == "public" for cs in class_stack)


# ---------------------------------------------------------------------------
# Namespace builder
# ---------------------------------------------------------------------------

def _build_ns(namespace_stack: list[tuple[str, int]]) -> str:
    """Build the current namespace string from the stack."""
    if not namespace_stack:
        return ""
    return "::".join(name for name, _ in namespace_stack)


# ---------------------------------------------------------------------------
# Inheritance extraction
# ---------------------------------------------------------------------------

def _extract_inheritance(rest: str) -> str:
    """Extract inheritance clause from the text after class name."""
    # rest looks like ": public Base, private Other {" or just "{"
    colon_idx = rest.find(":")
    if colon_idx == -1:
        return ""
    after_colon = rest[colon_idx + 1:]
    # Strip opening brace and beyond
    brace_idx = after_colon.find("{")
    if brace_idx != -1:
        after_colon = after_colon[:brace_idx]
    return after_colon.strip()


# ---------------------------------------------------------------------------
# Doc comment extraction (Doxygen)
# ---------------------------------------------------------------------------

def _look_back_for_doc(lines: list[str], decl_idx: int) -> dict:
    """Look backwards from a declaration for Doxygen doc comments.

    Handles both /// line comments and /** block comments.
    Returns dict with 'brief', 'params' (list), 'returns'.
    """
    result: dict = {"brief": "", "params": [], "returns": ""}
    doc_lines: list[str] = []
    i = decl_idx - 1

    # First, try to collect /// comments
    while i >= 0:
        stripped = lines[i].strip()
        if stripped.startswith("///"):
            text = stripped[3:].strip()
            # Strip leading < (used for member docs in some styles)
            if text.startswith("<"):
                text = text[1:].strip()
            doc_lines.append(text)
            i -= 1
        elif stripped.startswith("//!"):
            text = stripped[3:].strip()
            if text.startswith("<"):
                text = text[1:].strip()
            doc_lines.append(text)
            i -= 1
        elif not stripped:
            # Allow one blank line gap
            if i > 0 and (lines[i - 1].strip().startswith("///") or
                          lines[i - 1].strip().startswith("//!")):
                i -= 1
                continue
            break
        else:
            break

    if doc_lines:
        doc_lines.reverse()
        return _parse_doxygen_lines(doc_lines)

    # Try /** ... */ block comment
    i = decl_idx - 1
    # Skip blank lines
    while i >= 0 and not lines[i].strip():
        i -= 1
    if i < 0:
        return result

    # Check if previous line ends a block comment
    last_stripped = lines[i].strip()
    if not (last_stripped.endswith("*/") or last_stripped == "*/"):
        return result

    # Collect block comment lines
    block_lines: list[str] = []
    found_marker = False
    while i >= 0:
        stripped = lines[i].strip()
        block_lines.append(stripped)
        if stripped.startswith("/**") or stripped.startswith("/*!"):
            found_marker = True
            break
        i -= 1

    if not found_marker or not block_lines:
        return result

    block_lines.reverse()

    # Reject copyright/license block comments (not doc comments)
    raw_text = " ".join(block_lines)
    if any(kw in raw_text for kw in (
        "Copyright", "copyright", "LICENSE", "License", "license",
        "SPDX-License", "Permission is hereby granted",
        "All rights reserved", "WARRANTY",
        "#pragma", "#include", "#ifndef",
    )):
        return result

    # Reject very large block comments (likely file-level headers, not doc comments)
    if len(block_lines) > 40:
        return result

    # Clean up block comment markers
    cleaned: list[str] = []
    for bline in block_lines:
        # Remove leading /**, /*!, trailing */
        text = bline
        if text.startswith("/**") or text.startswith("/*!"):
            text = text[3:].strip()
        if text.endswith("*/"):
            text = text[:-2].strip()
        # Remove leading * from middle lines
        if text.startswith("*"):
            text = text[1:].strip()
        if text:
            cleaned.append(text)

    return _parse_doxygen_lines(cleaned)


def _parse_doxygen_lines(doc_lines: list[str]) -> dict:
    """Parse Doxygen tags from collected doc comment lines."""
    brief = ""
    params: list[dict] = []
    returns = ""
    brief_lines: list[str] = []

    i = 0
    while i < len(doc_lines):
        line = doc_lines[i]

        # @brief or \brief
        if line.startswith("@brief ") or line.startswith("\\brief "):
            brief = line[7:].strip()
            i += 1
            continue

        # @param or \param
        if line.startswith("@param") or line.startswith("\\param"):
            param_text = line[6:].strip()
            # Handle @param[in], @param[out], @param[in,out]
            if param_text.startswith("["):
                bracket_end = param_text.find("]")
                if bracket_end != -1:
                    param_text = param_text[bracket_end + 1:].strip()
            parts = param_text.split(None, 1)
            if parts:
                pname = parts[0]
                pdesc = parts[1] if len(parts) > 1 else ""
                params.append({"name": pname, "description": pdesc})
            i += 1
            continue

        # @return or \return or @returns or \returns
        if (line.startswith("@return") or line.startswith("\\return")):
            # Match longest tag first to avoid stripping 's' from description
            for tag in ("@returns", "\\returns", "@return", "\\return"):
                if line.startswith(tag):
                    rest = line[len(tag):].strip()
                    break
            returns = rest
            i += 1
            continue

        # @see, @note, @warning, @deprecated, @throws, etc. — skip
        if line.startswith("@") or line.startswith("\\"):
            i += 1
            continue

        # Regular text — part of brief if no @brief tag found yet
        if not brief:
            brief_lines.append(line)

        i += 1

    if not brief and brief_lines:
        full = " ".join(brief_lines)
        # First sentence
        for j, ch in enumerate(full):
            if ch == "." and (j + 1 >= len(full) or full[j + 1] == " "):
                brief = full[:j + 1]
                break
        if not brief:
            brief = full

    return {"brief": brief, "params": params, "returns": returns}


# ---------------------------------------------------------------------------
# Signature collection
# ---------------------------------------------------------------------------

def _collect_signature(lines: list[str], start: int) -> tuple[str, int]:
    """Collect a function/method signature that may span multiple lines."""
    sig = lines[start]
    i = start
    paren_depth = _count_parens(sig)

    # Collect until parens balanced (max 50 lines lookahead)
    limit = min(start + 50, len(lines))
    while paren_depth > 0 and i + 1 < limit:
        i += 1
        next_line = lines[i].strip()
        sig += " " + next_line
        paren_depth += _count_parens(next_line)

    # After parens balance, collect trailing qualifiers on subsequent lines
    # (const, noexcept, override, final, ->, = 0, = default, = delete, etc.)
    while i + 1 < limit:
        next_line = lines[i + 1].strip()
        if not next_line or next_line.startswith("//"):
            break
        if next_line.startswith("{") or next_line.startswith(";"):
            break
        if any(next_line.startswith(q) for q in _TRAILING_QUAL_STARTS) or \
                _TRAILING_CONST_RE.match(next_line):
            i += 1
            sig += " " + next_line
            # Stop if this line ends the declaration
            if ";" in next_line or "{" in next_line:
                break
        else:
            break

    return sig, i


def _extract_params_str(full_sig: str, func_name: str) -> str:
    """Extract the parameter string from a collected signature."""
    # Find the function name, then the opening paren
    name_idx = full_sig.find(func_name)
    if name_idx == -1:
        return ""

    search_from = name_idx + len(func_name)
    paren_start = full_sig.find("(", search_from)
    if paren_start == -1:
        return ""

    paren_end = _find_matching_paren(full_sig, paren_start)
    if paren_end == -1:
        return ""

    params = full_sig[paren_start + 1:paren_end].strip()
    params = re.sub(r"\s+", " ", params)
    return _strip_export_macros(params)


def _quals_have_const(quals: str) -> bool:
    """Check if trailing qualifiers contain a top-level 'const' (method constness)."""
    if not quals:
        return False
    # Match 'const' as a whole word, not inside noexcept(...) or other tokens
    return bool(re.search(r"\bconst\b", quals))


def _extract_trailing_qualifiers(full_sig: str) -> str:
    """Extract trailing qualifiers after the closing paren (const, noexcept, etc.)."""
    # Truncate at method body start to avoid inline body content
    # polluting the paren depth search (e.g., { IM_ASSERT(...) })
    sig = full_sig
    body_start = _find_body_brace(sig)
    if body_start != -1:
        sig = sig[:body_start]

    # Find the last closing paren at depth 0
    depth = 0
    last_close = -1
    for j, ch in enumerate(sig):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                last_close = j

    if last_close == -1:
        return ""

    after = sig[last_close + 1:].strip()

    # Strip inline comments (// ...)
    comment_idx = after.find("//")
    if comment_idx != -1:
        after = after[:comment_idx].strip()

    # Strip semicolons
    after = after.rstrip(";").strip()

    # Also strip initializer lists (starting with :)
    # but keep const, noexcept, override, final, = 0, = default, = delete
    colon_idx = after.find(":")
    if colon_idx != -1:
        # Check if it's an initializer list (not part of =)
        before_colon = after[:colon_idx].strip()
        if not before_colon.endswith("="):
            after = before_colon

    # Validate: if remaining text doesn't start with a known trailing qualifier,
    # it's likely leaked comment text (e.g., ". Dead-zones should be handled...")
    if after and not any(after.startswith(q) for q in _TRAILING_QUAL_STARTS) \
            and not _TRAILING_CONST_RE.match(after):
        after = ""

    return after.strip()


def _find_body_brace(sig: str) -> int:
    """Find the first '{' that's not inside parens, strings, or comments."""
    depth = 0
    in_double = False
    in_single = False
    escape = False
    for j, ch in enumerate(sig):
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
        if ch == "/" and j + 1 < len(sig) and sig[j + 1] == "/":
            break
        if ch == "'":
            in_single = True
        elif ch == '"':
            in_double = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "{" and depth == 0:
            return j
    return -1


def _extract_param_types(params_str: str) -> str:
    """Extract just the type names from a parameter string for FQN overload disambiguation."""
    if not params_str.strip():
        return ""

    types: list[str] = []
    # Split on commas, respecting angle brackets and parens
    parts = _split_params(params_str)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Remove default values
        eq_idx = _find_default_eq(part)
        if eq_idx != -1:
            part = part[:eq_idx].strip()
        # Last word is the param name, rest is type.
        # Handle pointer/ref attached to name: "int *p" → type "int*"
        tokens = part.rsplit(None, 1)
        if len(tokens) >= 2:
            type_part = tokens[0].strip()
            name_part = tokens[1]
            # Move leading *& from name back to type
            while name_part and name_part[0] in "*&":
                type_part += name_part[0]
                name_part = name_part[1:]
            types.append(type_part)
        elif tokens:
            types.append(tokens[0].strip())

    return ",".join(types)


def _split_params(params: str) -> list[str]:
    """Split parameter string on commas, respecting nested <>, (), []."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []

    for ch in params:
        if ch in "<([":
            depth += 1
            current.append(ch)
        elif ch in ">)]":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)

    if current:
        parts.append("".join(current))
    return parts


def _find_default_eq(param: str) -> int:
    """Find the = sign for a default value, respecting nested brackets."""
    depth = 0
    for j, ch in enumerate(param):
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth -= 1
        elif ch == "=" and depth == 0:
            return j
    return -1


# ---------------------------------------------------------------------------
# Brace / paren / angle counting
# ---------------------------------------------------------------------------

def _count_braces(line: str) -> int:
    """Count net brace depth change, skipping strings and comments."""
    depth = 0
    in_double = False
    in_single = False
    escape = False
    i = 0

    while i < len(line):
        ch = line[i]

        if escape:
            escape = False
            i += 1
            continue
        if ch == "\\":
            escape = True
            i += 1
            continue

        if in_single:
            if ch == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if ch == '"':
                in_double = False
            i += 1
            continue

        # Check for line comment
        if ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
            break

        # Check for block comment
        if ch == "/" and i + 1 < len(line) and line[i + 1] == "*":
            end = line.find("*/", i + 2)
            if end != -1:
                i = end + 2
                continue
            else:
                break  # unclosed block comment, skip rest of line

        if ch == "'":
            in_single = True
        elif ch == '"':
            in_double = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1

        i += 1

    return depth


def _count_parens(line: str) -> int:
    """Count net parenthesis depth change."""
    depth = 0
    in_double = False
    in_single = False
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
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1

    return depth


def _count_angles(line: str) -> int:
    """Count net angle bracket depth for template<...> matching."""
    depth = 0
    in_double = False
    in_single = False

    for ch in line:
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
        elif ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1

    return depth


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


def _has_declaration_after_template(stripped: str) -> bool:
    """Check if a template<...> line also contains the declaration on the same line."""
    # Balance angle brackets
    depth = 0
    for j, ch in enumerate(stripped):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
            if depth == 0:
                # Check if there's a declaration keyword after
                rest = stripped[j + 1:].strip()
                if rest and any(rest.startswith(kw) for kw in
                               ("class ", "struct ", "union ", "enum ",
                                "typename ", "using ", "void ", "int ",
                                "auto ", "const ", "static ", "virtual ",
                                "inline ", "explicit ", "constexpr ")):
                    return True
                # Also check for return type + function pattern
                if rest and re.match(r"[\w:*&<>]+\s+\w+\s*\(", rest):
                    return True
                return bool(rest and not rest.startswith("//"))
    return False


# ---------------------------------------------------------------------------
# Cleaning helpers
# ---------------------------------------------------------------------------

def _strip_export_macros(text: str) -> str:
    """Remove export/API macros from text."""
    return _EXPORT_MACRO_RE.sub("", text).strip()


def _clean_sig(sig: str) -> str:
    """Clean up whitespace in a signature."""
    return re.sub(r"\s+", " ", sig).strip()


# ---------------------------------------------------------------------------
# Record builder
# ---------------------------------------------------------------------------

def _build_record(**kwargs) -> dict:
    """Build a standard API record dict."""
    record = {
        "fqn": kwargs.get("fqn", ""),
        "namespace": kwargs.get("namespace", ""),
        "class_name": kwargs.get("class_name", ""),
        "member_name": kwargs.get("member_name", ""),
        "member_type": kwargs.get("member_type", ""),
        "signature": kwargs.get("signature", ""),
        "summary": kwargs.get("summary", ""),
        "params_json": kwargs.get("params_json", []),
        "returns_text": kwargs.get("returns_text", ""),
        "file_path": kwargs.get("file_path", ""),
        "line_start": kwargs.get("line_start", 0),
        "line_end": kwargs.get("line_end", 0),
    }
    return record
