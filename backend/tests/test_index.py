from pathlib import Path
from typing import cast

from app.index.indexer import LocalCodeIndexer
from app.index.parser import parse_source
from app.index.search import CodeSearch
from app.index.store import IndexStore
from app.index.symbols import SymbolSearch

COMMIT = "a" * 40


class FakeRepoSource:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def list_files(self, commit: str) -> list[str]:
        assert commit == COMMIT
        return sorted(self.files)

    def read_file(self, commit: str, path: str) -> str:
        assert commit == COMMIT
        return self.files[path]


def test_parser_extracts_python_symbols_and_references() -> None:
    source = """
import os

class Example:
    def calculate(self, value):
        return helper(value)

def helper(value):
    return value
"""
    result = parse_source("example.py", source)

    names = {symbol.name for symbol in result.symbols}
    assert {"Example", "calculate", "helper"} <= names

    references = {(reference.name, reference.kind) for reference in result.references}
    assert ("os", "import") in references
    assert ("helper", "call") in references


def test_parser_extracts_javascript_symbols() -> None:
    source = """
function calculate(value) {
    return helper(value);
}

class Example {}
"""
    result = parse_source("example.js", source)

    names = {symbol.name for symbol in result.symbols}
    assert {"calculate", "Example"} <= names

    references = {(reference.name, reference.kind) for reference in result.references}
    assert ("helper", "call") in references


def test_parser_ignores_unsupported_extensions() -> None:
    result = parse_source("image.png", "not source code")

    assert result.symbols == []
    assert result.references == []


def test_indexer_searches_code_and_symbols(tmp_path: Path) -> None:
    store = IndexStore(tmp_path / "index.sqlite")
    source = FakeRepoSource(
        {
            "src/example.py": ("def calculate_total(value):\n    return value + 10\n"),
            "README.md": "Project documentation\n",
        }
    )

    try:
        indexer = LocalCodeIndexer(store)
        result = indexer.build("repo-1", COMMIT, source)

        assert result == {
            "indexed": 2,
            "unchanged": 0,
            "skipped": 0,
        }

        search = CodeSearch(store)
        matches = search.search(
            "repo-1",
            COMMIT,
            "calculate_total",
        )

        assert matches
        assert matches[0]["path"] == "src/example.py"
        assert "calculate_total" in str(matches[0]["snippet"])

        symbols = SymbolSearch(store)
        found = symbols.find_symbol(
            "repo-1",
            COMMIT,
            "calculate_total",
        )

        assert len(found) == 1
        assert found[0]["kind"] == "function"
        assert found[0]["line_start"] == 1

        listed = symbols.list_symbols(
            "repo-1",
            COMMIT,
            "src/example.py",
        )
        assert any(item["name"] == "calculate_total" for item in listed)

    finally:
        store.close()


def test_indexer_does_not_duplicate_unchanged_files(
    tmp_path: Path,
) -> None:
    store = IndexStore(tmp_path / "index.sqlite")
    source = FakeRepoSource(
        {"src/example.py": "def stable_function():\n    return 1\n"}
    )

    try:
        indexer = LocalCodeIndexer(store)

        first = indexer.build("repo-1", COMMIT, source)
        second = indexer.build("repo-1", COMMIT, source)

        assert first["indexed"] == 1
        assert second["unchanged"] == 1

        rows = store.connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM symbols
            WHERE repo_id = ? AND commit_sha = ?
            """,
            ("repo-1", COMMIT),
        ).fetchone()

        assert rows["total"] == 1

    finally:
        store.close()


def test_symbol_search_returns_approximate_references(
    tmp_path: Path,
) -> None:
    store = IndexStore(tmp_path / "index.sqlite")
    source = FakeRepoSource(
        {
            "src/example.py": (
                "def helper():\n    return 1\n\ndef caller():\n    return helper()\n"
            )
        }
    )

    try:
        LocalCodeIndexer(store).build("repo-1", COMMIT, source)

        result = SymbolSearch(store).find_references(
            "repo-1",
            COMMIT,
            "helper",
        )

        assert result["precision"] == "approximate"
        references = cast(list[dict[str, object]], result["references"])
        assert any(reference["kind"] == "call" for reference in references)

    finally:
        store.close()


def test_indexer_skips_binary_files(tmp_path: Path) -> None:
    class BinaryRepoSource(FakeRepoSource):
        def read_file(self, commit: str, path: str) -> str:
            if path == "assets/image.png":
                raise ValueError("File is not valid UTF-8 text")
            return super().read_file(commit, path)

    store = IndexStore(tmp_path / "index.sqlite")
    source = BinaryRepoSource(
        {
            "src/example.py": "def valid_function():\\n    return 1\\n",
            "assets/image.png": "",
        }
    )

    try:
        result = LocalCodeIndexer(store).build("repo-1", COMMIT, source)

        assert result == {
            "indexed": 1,
            "unchanged": 0,
            "skipped": 1,
        }
    finally:
        store.close()


def test_list_symbols_includes_imports(tmp_path: Path) -> None:
    store = IndexStore(tmp_path / "index.sqlite")
    source = FakeRepoSource({"src/example.py": "import os\nfrom pathlib import Path\n"})

    try:
        LocalCodeIndexer(store).build("repo-1", COMMIT, source)

        symbols = SymbolSearch(store).list_symbols(
            "repo-1",
            COMMIT,
            "src/example.py",
        )

        imports = {item["name"] for item in symbols if item["kind"] == "import"}

        assert "os" in imports
        assert "Path" in imports
    finally:
        store.close()


def test_indexer_reuses_unchanged_file_across_commits(
    tmp_path: Path,
) -> None:
    second_commit = "b" * 40
    files = {
        COMMIT: {"src/example.py": ("def stable_function():\n    return 42\n")},
        second_commit: {"src/example.py": ("def stable_function():\n    return 42\n")},
    }

    class MultiCommitRepoSource:
        def list_files(self, commit: str) -> list[str]:
            return sorted(files[commit])

        def read_file(self, commit: str, path: str) -> str:
            return files[commit][path]

    store = IndexStore(tmp_path / "index.sqlite")

    try:
        indexer = LocalCodeIndexer(store)
        source = MultiCommitRepoSource()

        first = indexer.build("repo-1", COMMIT, source)
        second = indexer.build("repo-1", second_commit, source)

        assert first["indexed"] == 1
        assert second == {
            "indexed": 0,
            "unchanged": 1,
            "skipped": 0,
        }

        symbols = SymbolSearch(store).find_symbol(
            "repo-1",
            second_commit,
            "stable_function",
        )
        assert len(symbols) == 1

        matches = CodeSearch(store).search(
            "repo-1",
            second_commit,
            "stable_function",
        )
        assert matches
    finally:
        store.close()


def test_indexer_prefers_symbol_boundaries_for_chunks(tmp_path: Path) -> None:
    from app.index.indexer import LocalCodeIndexer
    from app.index.store import IndexStore

    indexer = LocalCodeIndexer(IndexStore(tmp_path / "index.sqlite"))
    content = "\n".join(f"line_{number}" for number in range(1, 51))
    symbols = [{"kind": "function", "line_start": 35}]

    chunks = indexer._make_chunks(content, symbols)

    assert chunks[0]["line_end"] == 34
    assert chunks[1]["line_start"] == 30
    assert str(chunks[0]["content"]).splitlines()[-1] == "line_34"
    assert str(chunks[1]["content"]).splitlines()[5] == "line_35"


def test_code_search_rejects_boolean_limit_and_invalid_path_glob(
    tmp_path: Path,
) -> None:
    import pytest
    from app.index.search import CodeSearch
    from app.index.store import IndexStore

    store = IndexStore(tmp_path / "index.sqlite")
    search = CodeSearch(store)

    with pytest.raises(ValueError):
        search.search("repo", "commit", "example", top_k=True)

    with pytest.raises(ValueError):
        search.search("repo", "commit", "example", path_glob="src\\\\file.py")

    with pytest.raises(ValueError):
        search.search("repo", "commit", "example", path_glob="src/\\x00file.py")

    store.close()


def test_symbol_search_rejects_boolean_limits(tmp_path: Path) -> None:
    import pytest
    from app.index.store import IndexStore
    from app.index.symbols import SymbolSearch

    store = IndexStore(tmp_path / "index.sqlite")
    search = SymbolSearch(store)

    with pytest.raises(ValueError):
        search.find_symbol("repo", "commit", "example", limit=True)

    with pytest.raises(ValueError):
        search.list_symbols("repo", "commit", "src/example.py", limit=True)

    with pytest.raises(ValueError):
        search.find_references("repo", "commit", "example", limit=True)

    store.close()


def test_parser_extracts_python_import_aliases_and_multiple_names() -> None:
    source = """
import os as sistema
from pathlib import Path, PurePath
import json, sys
"""
    result = parse_source("example.py", source)

    imports = {symbol.name for symbol in result.symbols if symbol.kind == "import"}

    assert imports == {"sistema", "Path", "PurePath", "json", "sys"}


def test_parser_extracts_attribute_calls_without_object_names() -> None:
    source = """
obj.metodo()
modulo.helper()
"""
    result = parse_source("example.py", source)

    calls = {
        reference.name for reference in result.references if reference.kind == "call"
    }

    assert calls == {"metodo", "helper"}
    assert "obj" not in calls
    assert "modulo" not in calls


def _import_names(path: str, source: str) -> set[str]:
    result = parse_source(path, source)
    return {symbol.name for symbol in result.symbols if symbol.kind == "import"}


def test_parser_extracts_js_es_imports_default_named_alias_and_namespace() -> None:
    source = """
import def, { a, b as c } from "m";
import * as ns from "n";
import "side-effect";
"""
    assert _import_names("example.js", source) == {"def", "a", "c", "ns"}


def test_parser_extracts_ts_imports_including_type_only_and_require() -> None:
    source = """
import type { T, U as V } from "m";
import { type W, X } from "n";
import fs = require("fs");
"""
    assert _import_names("example.ts", source) == {"T", "V", "W", "X", "fs"}
    assert _import_names("example.tsx", source) == {"T", "V", "W", "X", "fs"}


def test_parser_extracts_commonjs_require_bindings() -> None:
    source = """
const x = require("p");
const { q, r: s } = require("t");
const unrelated = compute("p");
"""
    assert _import_names("example.cjs", source) == {"x", "q", "s"}


def test_parser_js_import_references_carry_line_numbers() -> None:
    source = "\nimport { helper } from './util';\n"
    result = parse_source("example.js", source)

    assert ("helper", "import", 2) in {
        (ref.name, ref.kind, ref.line) for ref in result.references
    }


def test_js_imports_are_searchable_through_the_index(tmp_path: Path) -> None:
    store = IndexStore(tmp_path / "index.sqlite")
    source = FakeRepoSource(
        {"src/app.ts": "import { formatTotal } from './fmt';\nformatTotal(1);\n"}
    )

    try:
        LocalCodeIndexer(store).build("repo-1", COMMIT, source)
        symbols = SymbolSearch(store).list_symbols("repo-1", COMMIT, "src/app.ts")

        imports = {item["name"] for item in symbols if item["kind"] == "import"}
        assert imports == {"formatTotal"}
    finally:
        store.close()


HOSTILE_FTS_QUERIES = [
    '"',
    "total*",
    "NEAR(total x)",
    "total OR",
    "a AND",
    "-total",
    "col:total",
    "(",
    ")",
    "total'",
    'total" OR "x',
    "^total",
    "NOT total",
    "{a b}",
    "content:*",
    'total" ) OR (1',
]


def test_search_never_lets_fts_syntax_reach_the_engine(tmp_path: Path) -> None:
    store = IndexStore(tmp_path / "index.sqlite")
    source = FakeRepoSource(
        {"a.py": "def calculate_total(x):\n    return x  # NEAR OR AND total\n"}
    )

    try:
        LocalCodeIndexer(store).build("repo-1", COMMIT, source)
        search = CodeSearch(store)

        for query in HOSTILE_FTS_QUERIES:
            search.search("repo-1", COMMIT, query, top_k=5)

        # Operadores viram termos literais: "AND" existe no texto indexado.
        assert search.search("repo-1", COMMIT, "AND", top_k=5)
        assert search.search("repo-1", COMMIT, "!!!", top_k=5) == []
    finally:
        store.close()
