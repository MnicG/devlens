"""Navegação no código: search_code, read_range, find_symbol, list_symbols,
find_references (docs/05, seção 2.2). Evidências do tipo `code`."""

from __future__ import annotations

import re
from typing import Annotated, Any

from app.evidence.candidates import code_candidate
from app.index.search import CodeSearch
from app.index.symbols import SymbolSearch
from app.schemas.contracts import Evidence, ToolResult
from app.tools.base import (
    INVALID_ARGS,
    MAX_SNIPPET_CHARS,
    PagedArgs,
    Tool,
    ToolArgs,
    ToolContext,
    ToolError,
    clip,
    clip_line,
    ensure_safe_path,
    fit_lines,
    json_size,
)
from pydantic import Field

MAX_READ_LINES = 120
SNIPPET_LINES = 3


def _code_evidence(
    ctx: ToolContext,
    path: str,
    line_start: int,
    line_end: int,
) -> Evidence | None:
    """Candidato `code` calculado sobre o arquivo original no commit fixado."""
    view = ctx.file_view(path)

    if not 1 <= line_start <= line_end <= len(view.lines):
        return None

    return code_candidate(
        repo=ctx.repo,
        commit=ctx.commit,
        path=path,
        content=view.content,
        line_start=line_start,
        line_end=line_end,
        hmac_key=ctx.hmac_key,
    )


def _snippet(ctx: ToolContext, path: str, line_start: int, line_end: int) -> str:
    view = ctx.file_view(path)
    last = min(line_end, line_start + SNIPPET_LINES - 1)
    return clip("\n".join(view.redacted[line_start - 1 : last]), MAX_SNIPPET_CHARS)


def _first_matching_line(
    ctx: ToolContext, path: str, line_start: int, line_end: int, query: str
) -> int:
    """Primeira linha do trecho indexado que contém algum termo da consulta.

    O chunk tem ~40 linhas; mostrar só o início dele esconderia a linha que
    casou. Sem correspondência literal, volta ao início do chunk.
    """
    terms = [term.lower() for term in re.findall(r"[A-Za-z0-9_]+", query)]
    lines = ctx.file_view(path).lines

    for number in range(line_start, min(line_end, len(lines)) + 1):
        lowered = lines[number - 1].lower()
        if any(term in lowered for term in terms):
            return number

    return line_start


class SearchCodeArgs(PagedArgs):
    query: Annotated[str, Field(min_length=1, max_length=200)]
    path_glob: Annotated[str | None, Field(max_length=200)] = None
    top_k: Annotated[int, Field(ge=1, le=20)] = 8


class SearchCode(Tool):
    name = "search_code"
    args_schema = SearchCodeArgs

    def run(self, ctx: ToolContext, args: SearchCodeArgs) -> ToolResult:
        ctx.require_index()

        hits = CodeSearch(ctx.index).search(
            ctx.repo, ctx.commit, args.query, args.top_k, args.path_glob
        )

        items: list[dict[str, Any]] = []
        candidates: list[Evidence] = []

        for hit in hits:
            path = str(hit["path"])
            chunk_start = int(str(hit["line_start"]))
            chunk_end = int(str(hit["line_end"]))

            line_start = _first_matching_line(
                ctx, path, chunk_start, chunk_end, args.query
            )
            line_end = min(chunk_end, line_start + SNIPPET_LINES - 1)

            candidate = _code_evidence(ctx, path, line_start, line_end)
            if candidate is None:
                continue

            candidates.append(candidate)
            items.append(
                {
                    "path": path,
                    "line_start": line_start,
                    "line_end": line_end,
                    "snippet": _snippet(ctx, path, line_start, line_end),
                    "_cand": len(candidates) - 1,
                }
            )

        return ToolResult(ok=True, data={"items": items}, candidates=candidates)


class ReadRangeArgs(ToolArgs):
    path: Annotated[str, Field(min_length=1, max_length=500)]
    line_start: Annotated[int, Field(ge=1)]
    line_end: Annotated[int, Field(ge=1)]


class ReadRange(Tool):
    name = "read_range"
    args_schema = ReadRangeArgs

    def run(self, ctx: ToolContext, args: ReadRangeArgs) -> ToolResult:
        path = ensure_safe_path(args.path)

        if args.line_end < args.line_start:
            raise ToolError(INVALID_ARGS, "line_end must not precede line_start")

        if args.line_end - args.line_start + 1 > MAX_READ_LINES:
            raise ToolError(INVALID_ARGS, f"at most {MAX_READ_LINES} lines per call")

        view = ctx.file_view(path)

        if args.line_start > len(view.lines):
            raise ToolError(INVALID_ARGS, "line_start is beyond the end of the file")

        end = min(args.line_end, len(view.lines))
        wanted = [clip_line(line) for line in view.redacted[args.line_start - 1 : end]]
        overhead = json_size(
            {"path": path, "line_start": 0, "line_end": 0, "lines": []}
        )
        shown = fit_lines(wanted, MAX_READ_LINES, ctx.max_output_chars, overhead + 600)
        last = args.line_start + shown - 1

        data: dict[str, Any] = {
            "path": path,
            "line_start": args.line_start,
            "line_end": last,
            "lines": [
                {"n": args.line_start + offset, "text": text}
                for offset, text in enumerate(wanted[:shown])
            ],
        }

        candidate = _code_evidence(ctx, path, args.line_start, last)
        assert candidate is not None

        truncated = last < end
        if truncated:
            data["cursor"] = str(last + 1)

        return ToolResult(
            ok=True,
            data=data,
            truncated=truncated,
            candidates=[candidate],
        )


class FindSymbolArgs(PagedArgs):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    kind: Annotated[str | None, Field(min_length=1, max_length=40)] = None


class FindSymbol(Tool):
    name = "find_symbol"
    args_schema = FindSymbolArgs

    def run(self, ctx: ToolContext, args: FindSymbolArgs) -> ToolResult:
        ctx.require_index()

        found = SymbolSearch(ctx.index).find_symbol(
            ctx.repo, ctx.commit, args.name, args.kind, 20
        )

        items: list[dict[str, Any]] = []
        candidates: list[Evidence] = []

        for row in found:
            path = str(row["path"])
            start = int(str(row["line_start"]))
            end = int(str(row["line_end"]))

            candidate = _code_evidence(ctx, path, start, end)
            if candidate is None:
                continue

            candidates.append(candidate)
            items.append(
                {
                    "path": path,
                    "kind": row["kind"],
                    "line_start": start,
                    "line_end": end,
                    "_cand": len(candidates) - 1,
                }
            )

        return ToolResult(ok=True, data={"items": items}, candidates=candidates)


class ListSymbolsArgs(PagedArgs):
    path: Annotated[str, Field(min_length=1, max_length=500)]


class ListSymbols(Tool):
    name = "list_symbols"
    args_schema = ListSymbolsArgs

    def run(self, ctx: ToolContext, args: ListSymbolsArgs) -> ToolResult:
        path = ensure_safe_path(args.path)
        ctx.require_index()

        rows = SymbolSearch(ctx.index).list_symbols(ctx.repo, ctx.commit, path, 100)

        return ToolResult(ok=True, data={"items": rows})


class FindReferencesArgs(PagedArgs):
    symbol: Annotated[str, Field(min_length=1, max_length=200)]
    top_k: Annotated[int, Field(ge=1, le=20)] = 10


class FindReferences(Tool):
    name = "find_references"
    args_schema = FindReferencesArgs

    def run(self, ctx: ToolContext, args: FindReferencesArgs) -> ToolResult:
        ctx.require_index()

        found = SymbolSearch(ctx.index).find_references(
            ctx.repo, ctx.commit, args.symbol, args.top_k
        )

        items: list[dict[str, Any]] = []
        candidates: list[Evidence] = []
        references: list[dict[str, Any]] = list(found["references"])  # type: ignore[call-overload]

        for row in references:
            path = str(row["path"])
            line = int(row["line"])

            candidate = _code_evidence(ctx, path, line, line)
            if candidate is None:
                continue

            candidates.append(candidate)
            items.append(
                {
                    "path": path,
                    "line": line,
                    "kind": row["kind"],
                    "_cand": len(candidates) - 1,
                }
            )

        # Aproximada (sem resolução de tipos): confirmar com read_range.
        return ToolResult(
            ok=True,
            data={"precision": "approximate", "items": items},
            candidates=candidates,
        )
