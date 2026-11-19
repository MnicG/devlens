"""Testes relacionados: find_tests, check_test_scenario (docs/05, seção 2.4).

`find_tests` usa convenções (diretórios `tests/`, `test_*.py`, `*.test.ts`,
`__tests__`) mais busca por imports/chamadas do alvo no índice.
`check_test_scenario` é heurística: devolve `covered=None` quando não consegue
decidir, nunca um palpite.
"""

from __future__ import annotations

import math
import re
from pathlib import PurePosixPath
from typing import Annotated, Any

from app.evidence.candidates import code_candidate
from app.index.symbols import SymbolSearch
from app.schemas.contracts import Evidence, EvidenceKind, ToolResult
from app.tools.base import (
    PagedArgs,
    Tool,
    ToolArgs,
    ToolContext,
    is_sensitive_name,
)
from pydantic import Field

MAX_TESTS = 20
MAX_CANDIDATES = 10
MAX_TARGET_SYMBOLS = 50

_TEST_DIRECTORIES = {"tests", "test", "__tests__"}
_TEST_FILE = re.compile(
    r"^(?:test_.+\.py|.+_test\.py|.+\.(?:test|spec)\.(?:js|jsx|ts|tsx|mjs|cjs))$"
)
_JS_TEST_CALL = re.compile(
    r"^\s*(?:it|test)(?:\.\w+)?\(\s*(?P<quote>['\"`])(?P<name>.+?)(?P=quote)"
)
_WORD = re.compile(r"[a-z0-9]{4,}")
_STOPWORDS = {
    "that",
    "this",
    "with",
    "when",
    "should",
    "must",
    "from",
    "para",
    "quando",
    "deve",
    "dever",
    "ser",
    "uma",
    "como",
    "com",
    "nao",
    "sem",
}


def is_test_path(path: str) -> bool:
    pure = PurePosixPath(path)

    if is_sensitive_name(pure.name):
        return False

    return bool(_TEST_FILE.match(pure.name)) or any(
        part in _TEST_DIRECTORIES for part in pure.parts[:-1]
    )


def _stems_match(test_name: str, stem: str) -> bool:
    lowered = test_name.lower()
    return any(
        marker in lowered
        for marker in (
            f"test_{stem}",
            f"{stem}_test",
            f"{stem}.test.",
            f"{stem}.spec.",
        )
    )


class TestRef:
    """Um teste localizado: caminho, nome e intervalo de linhas."""

    __test__ = False  # evita que o pytest tente coletar a classe

    def __init__(self, path: str, name: str, line_start: int, line_end: int) -> None:
        self.path = path
        self.name = name
        self.line_start = line_start
        self.line_end = line_end


def _tests_in_file(ctx: ToolContext, path: str) -> list[TestRef]:
    refs: list[TestRef] = []

    if path.endswith(".py"):
        for row in SymbolSearch(ctx.index).list_symbols(ctx.repo, ctx.commit, path):
            name = str(row["name"])
            if row["kind"] == "function" and name.startswith("test"):
                refs.append(
                    TestRef(
                        path,
                        name,
                        int(str(row["line_start"])),
                        int(str(row["line_end"])),
                    )
                )
        return refs

    view = ctx.file_view(path)
    for number, line in enumerate(view.lines, start=1):
        match = _JS_TEST_CALL.match(line)
        if match:
            refs.append(TestRef(path, match.group("name"), number, number))

    return refs


def _target_names(ctx: ToolContext, target: str) -> tuple[str | None, set[str]]:
    """Devolve (stem do arquivo, nomes procurados)."""
    if target in set(ctx.source.list_files(ctx.commit)):
        stem = PurePosixPath(target).stem
        symbols = SymbolSearch(ctx.index).list_symbols(
            ctx.repo, ctx.commit, target, MAX_TARGET_SYMBOLS
        )
        names = {stem} | {
            str(row["name"]) for row in symbols if row["kind"] != "import"
        }
        return stem, names

    return None, {target}


def related_tests(ctx: ToolContext, target: str) -> list[TestRef]:
    ctx.require_index()

    stem, names = _target_names(ctx, target)
    test_files = [p for p in ctx.source.list_files(ctx.commit) if is_test_path(p)]

    by_name = [
        p for p in test_files if stem and _stems_match(p.rsplit("/", 1)[-1], stem)
    ]

    placeholders = ",".join("?" for _ in names)
    rows = ctx.index.connection.execute(
        f"""
        SELECT DISTINCT path FROM references_index
        WHERE repo_id = ? AND commit_sha = ? AND name IN ({placeholders})
        """,
        (ctx.repo, ctx.commit, *sorted(names)),
    ).fetchall()
    referenced = {row["path"] for row in rows}
    by_reference = [p for p in test_files if p in referenced and p not in by_name]

    refs: list[TestRef] = []
    for path in sorted(by_name) + sorted(by_reference):
        refs.extend(_tests_in_file(ctx, path))

    return refs


def _ref_candidate(ctx: ToolContext, ref: TestRef) -> Evidence:
    view = ctx.file_view(ref.path)
    return code_candidate(
        repo=ctx.repo,
        commit=ctx.commit,
        path=ref.path,
        content=view.content,
        line_start=ref.line_start,
        line_end=ref.line_end,
        hmac_key=ctx.hmac_key,
        kind=EvidenceKind.TEST,
    )


def _items_for(
    ctx: ToolContext, refs: list[TestRef]
) -> tuple[list[dict[str, Any]], list[Evidence]]:
    items: list[dict[str, Any]] = []
    candidates: list[Evidence] = []

    for ref in refs:
        candidates.append(_ref_candidate(ctx, ref))
        items.append(
            {
                "path": ref.path,
                "name": ref.name,
                "line_start": ref.line_start,
                "line_end": ref.line_end,
                "_cand": len(candidates) - 1,
            }
        )

    return items, candidates


class FindTestsArgs(PagedArgs):
    target: Annotated[str, Field(min_length=1, max_length=300)]


class FindTests(Tool):
    name = "find_tests"
    args_schema = FindTestsArgs

    def run(self, ctx: ToolContext, args: FindTestsArgs) -> ToolResult:
        refs = related_tests(ctx, args.target)[:MAX_TESTS]
        items, candidates = _items_for(ctx, refs)
        return ToolResult(ok=True, data={"items": items}, candidates=candidates)


class CheckTestScenarioArgs(ToolArgs):
    description: Annotated[str, Field(min_length=1, max_length=500)]
    target: Annotated[str, Field(min_length=1, max_length=300)]


def _terms(description: str) -> list[str]:
    seen: dict[str, None] = {}
    for word in _WORD.findall(description.lower()):
        if word not in _STOPWORDS:
            seen[word] = None
    return list(seen)


class CheckTestScenario(Tool):
    """Cobertura provável de um cenário.

    - nenhum teste relacionado ao alvo -> `covered=False`;
    - algum teste casa a maior parte dos termos do cenário -> `covered=True`;
    - demais casos -> `covered=None` (indeterminado).
    """

    name = "check_test_scenario"
    args_schema = CheckTestScenarioArgs

    def run(self, ctx: ToolContext, args: CheckTestScenarioArgs) -> ToolResult:
        refs = related_tests(ctx, args.target)

        if not refs:
            return ToolResult(ok=True, data={"covered": False, "items": []})

        terms = _terms(args.description)
        needed = max(2, math.ceil(0.6 * len(terms))) if len(terms) > 1 else 1
        strong: list[TestRef] = []
        partial: list[TestRef] = []

        for ref in refs:
            view = ctx.file_view(ref.path)
            body = "\n".join(view.lines[ref.line_start - 1 : ref.line_end])
            haystack = f"{ref.name}\n{body}".lower()
            matched = sum(1 for term in terms if term in haystack)

            if terms and matched >= needed:
                strong.append(ref)
            elif matched:
                partial.append(ref)

        covered: bool | None = True if strong else None
        chosen = (strong or partial)[:MAX_CANDIDATES]
        items, candidates = _items_for(ctx, chosen)

        return ToolResult(
            ok=True,
            data={"covered": covered, "items": items},
            candidates=candidates,
        )
