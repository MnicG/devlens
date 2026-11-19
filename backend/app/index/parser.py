from __future__ import annotations

from dataclasses import dataclass

import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser


@dataclass(frozen=True)
class ParsedSymbol:
    name: str
    kind: str
    line_start: int
    line_end: int


@dataclass(frozen=True)
class ParsedReference:
    name: str
    kind: str
    line: int


@dataclass(frozen=True)
class ParseResult:
    symbols: list[ParsedSymbol]
    references: list[ParsedReference]


def _parser_for_path(path: str) -> Parser | None:
    if path.endswith(".py"):
        language = Language(tree_sitter_python.language())
    elif path.endswith((".js", ".jsx", ".mjs", ".cjs")):
        language = Language(tree_sitter_javascript.language())
    elif path.endswith(".tsx"):
        language = Language(tree_sitter_typescript.language_tsx())
    elif path.endswith(".ts"):
        language = Language(tree_sitter_typescript.language_typescript())
    else:
        return None

    return Parser(language)


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode(
        "utf-8",
        errors="replace",
    )


def _python_import_names(node: Node, source: bytes) -> set[str]:
    names: set[str] = set()

    imported_nodes = list(node.named_children)

    if node.type == "import_from_statement" and imported_nodes:
        imported_nodes = imported_nodes[1:]

    for imported in imported_nodes:
        if imported.type == "aliased_import":
            alias = imported.child_by_field_name("alias")

            if alias is None and imported.named_children:
                alias = imported.named_children[-1]

            if alias is not None:
                name = _node_text(alias, source).strip()
                if name:
                    names.add(name)

        elif imported.type == "dotted_name":
            identifiers = [
                child for child in imported.named_children if child.type == "identifier"
            ]

            if identifiers:
                name_node = (
                    identifiers[0]
                    if node.type == "import_statement"
                    else identifiers[-1]
                )
                name = _node_text(name_node, source).strip()
                if name:
                    names.add(name)

        else:
            for child in imported.named_children:
                if child.type in {"identifier", "property_identifier"}:
                    name = _node_text(child, source).strip()
                    if name:
                        names.add(name)

    return names


def _js_import_names(node: Node, source: bytes) -> set[str]:
    """Nomes locais ligados por um `import` ES ou `import x = require()` (TS)."""
    names: set[str] = set()

    def add(name_node: Node | None) -> None:
        if name_node is not None:
            name = _node_text(name_node, source).strip()
            if name:
                names.add(name)

    for clause in node.named_children:
        if clause.type == "import_require_clause":
            add(
                next((c for c in clause.named_children if c.type == "identifier"), None)
            )
        elif clause.type == "import_clause":
            for part in clause.named_children:
                if part.type == "identifier":
                    add(part)  # import padrao
                elif part.type == "namespace_import":
                    add(
                        next(
                            (c for c in part.named_children if c.type == "identifier"),
                            None,
                        )
                    )
                elif part.type == "named_imports":
                    for spec in part.named_children:
                        if spec.type == "import_specifier":
                            add(
                                spec.child_by_field_name("alias")
                                or spec.child_by_field_name("name")
                            )

    return names


def _js_require_names(node: Node, source: bytes) -> set[str]:
    """Nomes ligados por `const x = require(...)`, inclusive com desestruturação."""
    value = node.child_by_field_name("value")
    target = node.child_by_field_name("name")

    if value is None or target is None or value.type != "call_expression":
        return set()

    callee = value.child_by_field_name("function")
    if callee is None or _node_text(callee, source) != "require":
        return set()

    names: set[str] = set()

    def add(name_node: Node) -> None:
        name = _node_text(name_node, source).strip()
        if name:
            names.add(name)

    if target.type == "identifier":
        add(target)
    elif target.type == "object_pattern":
        for item in target.named_children:
            if item.type == "shorthand_property_identifier_pattern":
                add(item)
            elif item.type == "pair_pattern":
                bound = item.child_by_field_name("value")
                if bound is not None and bound.type == "identifier":
                    add(bound)

    return names


def _call_name(node: Node, source: bytes) -> str | None:
    for field in ("attribute", "property"):
        name_node = node.child_by_field_name(field)
        if name_node is not None:
            name = _node_text(name_node, source).strip()
            if name:
                return name

    identifiers = []
    pending = [node]

    while pending:
        current = pending.pop()

        if current.type in {"identifier", "property_identifier"}:
            identifiers.append(current)

        pending.extend(reversed(current.named_children))

    if not identifiers:
        return None

    return _node_text(identifiers[-1], source).strip() or None


def parse_source(path: str, content: str) -> ParseResult:
    parser = _parser_for_path(path)
    if parser is None:
        return ParseResult(symbols=[], references=[])

    source = content.encode("utf-8")
    tree = parser.parse(source)

    symbols: list[ParsedSymbol] = []
    references: list[ParsedReference] = []

    definition_types = {
        "function_definition",
        "function_declaration",
        "generator_function_declaration",
        "class_definition",
        "class_declaration",
        "abstract_class_declaration",
        "interface_declaration",
        "type_alias_declaration",
        "enum_declaration",
        "method_definition",
    }

    import_types = {
        "import_statement",
        "import_from_statement",
    }

    pending = [tree.root_node]

    while pending:
        node = pending.pop()

        if node.type in definition_types:
            name_node = node.child_by_field_name("name")

            if name_node is not None:
                name = _node_text(name_node, source).strip()

                if name:
                    kind = "class" if "class" in node.type else "function"

                    if node.type in {
                        "interface_declaration",
                        "type_alias_declaration",
                        "enum_declaration",
                    }:
                        kind = node.type.removesuffix("_declaration")

                    symbols.append(
                        ParsedSymbol(
                            name=name,
                            kind=kind,
                            line_start=node.start_point.row + 1,
                            line_end=node.end_point.row + 1,
                        )
                    )

        elif node.type in import_types or node.type == "variable_declarator":
            if path.endswith(".py"):
                imported_names = (
                    _python_import_names(node, source)
                    if node.type in import_types
                    else set()
                )
            elif node.type == "import_statement":
                imported_names = _js_import_names(node, source)
            elif node.type == "variable_declarator":
                imported_names = _js_require_names(node, source)
            else:
                imported_names = set()

            for name in sorted(imported_names):
                line = node.start_point.row + 1

                symbols.append(
                    ParsedSymbol(
                        name=name,
                        kind="import",
                        line_start=line,
                        line_end=node.end_point.row + 1,
                    )
                )

                references.append(
                    ParsedReference(
                        name=name,
                        kind="import",
                        line=line,
                    )
                )

        elif node.type in {"call", "call_expression"}:
            function_node = node.child_by_field_name("function")

            if function_node is not None:
                call_name = _call_name(function_node, source)

                if call_name:
                    references.append(
                        ParsedReference(
                            name=call_name,
                            kind="call",
                            line=node.start_point.row + 1,
                        )
                    )

        pending.extend(reversed(node.named_children))

    return ParseResult(
        symbols=symbols,
        references=references,
    )
