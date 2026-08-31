"""generic_extractors —— 多语言通用符号抽取（声明式 spec 驱动）。

与 indexer 里手写的 java/python/typescript 抽取器不同，本模块用
「节点类型表 + 递归容器表」描述每种语言，共享同一套遍历逻辑，
新增语言只需加一条 spec（节点类型名以实测 AST 为准，见 .worktrees 探测记录）。

约定：
- decl_kinds 值 'callable' 为动态 kind：容器（类）上下文内 → method，否则 → function
- container_kinds 仅递归不出符号（namespace/impl 等）；类状节点同时出现在
  decl_kinds 与 classlike_kinds，出符号后带 parent_class 递归
- 名字优先取 name 字段；缺失时回退第一个 *identifier 子节点（如 Kotlin class）；
  C/C++ function_definition 走 declarator 链
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tree_sitter import Node

from agent.codenav.models import Symbol

# Symbol.start_line 等由 node.start_point 得到（0-based → +1）


@dataclass(frozen=True)
class LangSpec:
    decl_kinds: dict[str, str]
    container_kinds: set[str] = field(default_factory=set)
    classlike_kinds: set[str] = field(default_factory=set)
    # impl 类容器：自身无 name 字段，parent_class 取指定字段文本（rust impl 的 type）
    parent_field: str | None = None


_SPECS: dict[str, LangSpec] = {
    "go": LangSpec(
        decl_kinds={
            "function_declaration": "callable",
            "method_declaration": "method",
            "type_spec": "class",
        },
        container_kinds={"type_declaration"},
    ),
    "c": LangSpec(
        decl_kinds={
            "struct_specifier": "class",
            "union_specifier": "class",
            "enum_specifier": "enum",
            "function_definition": "function",
        },
    ),
    "cpp": LangSpec(
        decl_kinds={
            "class_specifier": "class",
            "struct_specifier": "class",
            "enum_specifier": "enum",
            "function_definition": "callable",
        },
        container_kinds={"namespace_definition", "class_specifier", "struct_specifier"},
        classlike_kinds={"class_specifier", "struct_specifier"},
    ),
    "csharp": LangSpec(
        decl_kinds={
            "class_declaration": "class",
            "interface_declaration": "interface",
            "struct_declaration": "class",
            "enum_declaration": "enum",
            "method_declaration": "method",
        },
        container_kinds={
            "namespace_declaration",
            "class_declaration",
            "interface_declaration",
            "struct_declaration",
        },
        classlike_kinds={"class_declaration", "struct_declaration"},
    ),
    "php": LangSpec(
        decl_kinds={
            "class_declaration": "class",
            "interface_declaration": "interface",
            "function_definition": "callable",
            "method_declaration": "method",
        },
        container_kinds={
            "class_declaration",
            "interface_declaration",
            "namespace_definition",
        },
        classlike_kinds={"class_declaration"},
    ),
    "ruby": LangSpec(
        decl_kinds={
            "class": "class",
            "module": "class",
            "method": "callable",
            "singleton_method": "method",
        },
        container_kinds={"class", "module", "singleton_class"},
        classlike_kinds={"class", "module"},
    ),
    "rust": LangSpec(
        decl_kinds={
            "function_item": "callable",
            "struct_item": "class",
            "enum_item": "enum",
            "trait_item": "interface",
        },
        container_kinds={"impl_item"},
        parent_field="type",
    ),
    "kotlin": LangSpec(
        decl_kinds={
            "class_declaration": "class",
            "object_declaration": "class",
            "function_declaration": "callable",
        },
        container_kinds={"class_declaration", "object_declaration"},
        classlike_kinds={"class_declaration", "object_declaration"},
    ),
    "swift": LangSpec(
        decl_kinds={
            "class_declaration": "class",
            "protocol_declaration": "interface",
            "function_declaration": "callable",
        },
        container_kinds={"class_declaration", "protocol_declaration"},
        classlike_kinds={"class_declaration"},
    ),
    "scala": LangSpec(
        decl_kinds={
            "class_definition": "class",
            "object_definition": "class",
            "trait_definition": "interface",
            "function_definition": "callable",
        },
        container_kinds={"class_definition", "object_definition", "trait_definition"},
        classlike_kinds={"class_definition", "object_definition", "trait_definition"},
    ),
}


def supports(language: str) -> bool:
    return language in _SPECS


# 类体/块体包装节点：本身无符号，递归时透传上下文（实测各语言 body 容器名）
_BODY_CONTAINERS = frozenset(
    {
        "class_body",  # kotlin / swift
        "object_body",
        "template_body",  # scala
        "body_statement",  # ruby
        "field_declaration_list",  # c / cpp
        "declaration_list",  # csharp / php / cpp(namespace)
    }
)


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _decl_name(node: Node, source: bytes, language: str) -> str:
    """声明名：name 字段 → *identifier 子节点 → C 系 declarator 链。"""
    named = node.child_by_field_name("name")
    if named:
        return _node_text(named, source)
    for child in node.children:
        if child.type.endswith("identifier"):
            return _node_text(child, source)
    if language in ("c", "cpp") and node.type == "function_definition":
        return _c_function_name(node, source)
    return ""


def _c_function_name(node: Node, source: bytes) -> str:
    """function_definition → declarator（可能经指针包装）→ function_declarator → 名字。"""
    decl = node.child_by_field_name("declarator")
    seen = 0
    while decl is not None and decl.type != "function_declarator" and seen < 4:
        decl = decl.child_by_field_name("declarator")
        seen += 1
    if decl is not None and decl.type == "function_declarator":
        inner = decl.child_by_field_name("declarator")
        if inner:
            return _node_text(inner, source)
    return ""


def _signature(node: Node, source: bytes) -> str:
    text = _node_text(node, source)
    first_line = next((ln for ln in text.splitlines() if ln.strip()), "")
    return first_line.strip()[:120]


def _make_symbol(
    node: Node,
    source: bytes,
    name: str,
    kind: str,
    file_path: str,
    language: str,
    parent_class: str | None,
) -> Symbol:
    return Symbol(
        name=name,
        kind=kind,
        file_path=file_path,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        signature=_signature(node, source),
        parent_class=parent_class,
        language=language,
    )


def extract_generic(
    root: Node,
    source: bytes,
    file_path: str,
    language: str,
    last_mtime: int,  # 与 _extract_from_tree 分发签名对齐（mtime 由入库层处理，此处不用）
) -> list[Symbol]:
    """按语言 spec 遍历顶层声明（容器递归两层上下文）。"""
    spec = _SPECS.get(language)
    if spec is None:
        return []
    out: list[Symbol] = []

    def walk(node: Node, parent_class: str | None) -> None:
        for child in node.children:
            t = child.type
            if t in spec.decl_kinds:
                name = _decl_name(child, source, language)
                if not name:
                    continue
                kind = spec.decl_kinds[t]
                if kind == "callable":
                    kind = "method" if parent_class else "function"
                out.append(
                    _make_symbol(child, source, name, kind, file_path, language, parent_class)
                )
                if t in spec.classlike_kinds:
                    walk(child, name)
            elif t in spec.container_kinds:
                ctx = parent_class
                if spec.parent_field:
                    pf = child.child_by_field_name(spec.parent_field)
                    if pf:
                        ctx = _node_text(pf, source)
                walk(child, ctx)
            elif t in _BODY_CONTAINERS:
                # 类体包装层：透传上下文继续下探（方法在 body 里再套一层）
                walk(child, parent_class)

    walk(root, None)
    return out
