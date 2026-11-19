"""Infraestrutura das Repo Tools (docs/05).

Todas as ferramentas passam pelo mesmo wrapper (`ToolRunner`), que aplica:
validação de argumentos, orçamento, cache por commit, redação de segredos,
truncamento com cursor e registro de evidências no Evidence Store.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Protocol

from app.evidence.hashing import split_lines
from app.evidence.store import EvidenceStore
from app.gateway.redaction import redact_text
from app.index.indexer import LocalCodeIndexer
from app.index.store import IndexStore
from app.schemas.contracts import Budget, ToolResult, Usage
from app.tools.repo_source import is_git_metadata_path
from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_OUTPUT_CHARS = 4000
MAX_SNIPPET_CHARS = 240
# Teto por linha devolvida: uma linha enorme (ex.: JS minificado) não pode
# estourar o limite de saída (RNF-02).
MAX_LINE_CHARS = 400
# `, "ev_id": "ev_<16 hex>"` no item + `, "ev_<16 hex>"` em evidence_ids.
EVIDENCE_REFERENCE_OVERHEAD = 30 + 23
SHA_ARG_PATTERN = r"^[0-9a-f]{7,40}$"

# Erros padronizados (docs/05, seção 5)
INVALID_ARGS = "INVALID_ARGS"
PATH_DENIED = "PATH_DENIED"
NOT_FOUND = "NOT_FOUND"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
RATE_LIMITED = "RATE_LIMITED"
INDEX_MISSING = "INDEX_MISSING"
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
# Falha inesperada de ferramenta: não derruba a análise (RNF-20).
TOOL_FAILURE = "TOOL_FAILURE"

# Erros que contam no orçamento de chamadas; os demais não contam.
COUNTED_ERRORS = {INVALID_ARGS, PATH_DENIED, NOT_FOUND, TOOL_FAILURE}


class ToolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class IndexMissing(Exception):
    """O índice do commit ainda não foi construído."""


def error_result(code: str, message: str) -> ToolResult:
    return ToolResult(ok=False, error=f"{code}: {message}")


def error_code(result: ToolResult) -> str | None:
    if result.error is None:
        return None
    return result.error.split(":", 1)[0]


# Argumentos


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PagedArgs(ToolArgs):
    cursor: str | None = Field(default=None, max_length=20)


# Caminhos


def is_sensitive_name(filename: str) -> bool:
    name = filename.lower()
    return (
        name.startswith(".env")
        or name.endswith((".pem", ".key"))
        or name.startswith("id_rsa")
        or name in {"credentials", "credentials.json", "service-account.json"}
    )


def ensure_safe_path(path: str) -> str:
    """Valida e normaliza um caminho relativo à raiz do repositório."""
    if (
        not path
        or "\x00" in path
        or path.startswith("/")
        or "\\" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or is_git_metadata_path(path)
    ):
        raise ToolError(PATH_DENIED, "path is not allowed")

    normalized = posixpath.normpath(path)

    if is_sensitive_name(normalized.rsplit("/", 1)[-1]):
        raise ToolError(PATH_DENIED, "sensitive files are not readable")

    return normalized


# Fonte e contexto


class RepoSource(Protocol):
    def resolve_commit(self, ref: str | None = None) -> str: ...

    def read_file(self, commit: str, path: str) -> str: ...

    def list_files(self, commit: str) -> list[str]: ...

    def git_log(
        self, commit: str, path: str | None, limit: int
    ) -> list[dict[str, str]]: ...

    def git_blame(
        self, commit: str, path: str, start: int, end: int
    ) -> list[dict[str, str | int]]: ...

    def diff(self, base: str, head: str, path: str | None) -> str: ...


@dataclass(frozen=True)
class FileView:
    """Arquivo no commit fixado: linhas originais e linhas já redigidas."""

    lines: list[str]
    redacted: list[str]

    @property
    def content(self) -> str:
        return "\n".join(self.lines)


def build_file_view(content: str) -> FileView:
    lines = split_lines(content)
    # A redação roda no arquivo inteiro, para pegar blocos multilinha (PEM)
    # mesmo quando o trecho devolvido começa no meio do bloco.
    redacted = redact_text(content).text.splitlines()

    if len(redacted) != len(lines):
        redacted = [redact_text(line).text for line in lines]

    return FileView(lines=lines, redacted=redacted)


class ToolCache:
    """Cache por (ferramenta, argumentos normalizados, commit) — tabela tool_cache."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(str(database_path))
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS tool_cache (
              key TEXT PRIMARY KEY,
              commit_sha TEXT NOT NULL,
              tool TEXT NOT NULL,
              result_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    @staticmethod
    def make_key(tool: str, args: dict[str, Any], commit: str, limit: int) -> str:
        # O limite de saída entra na chave: ferramentas que cortam a própria
        # saída (read_range, git_show, get_diff) dependem dele, e ele muda
        # quando os limites são calibrados.
        payload = json.dumps([tool, args, commit, limit], sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, key: str) -> ToolResult | None:
        row = self.connection.execute(
            "SELECT result_json FROM tool_cache WHERE key = ?", (key,)
        ).fetchone()
        return ToolResult.model_validate_json(row[0]) if row else None

    def put(self, key: str, commit: str, tool: str, result: ToolResult) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO tool_cache
                    (key, commit_sha, tool, result_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    key,
                    commit,
                    tool,
                    result.model_dump_json(),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def close(self) -> None:
        self.connection.close()


@dataclass(frozen=True)
class CallInfo:
    tool: str
    cache_hit: bool
    counted: bool
    error_code: str | None


@dataclass
class ToolContext:
    repo: str
    commit: str
    source: RepoSource
    index: IndexStore
    evidence: EvidenceStore
    cache: ToolCache
    hmac_key: bytes
    budget: Budget = field(default_factory=Budget)
    usage: Usage = field(default_factory=Usage)
    max_output_chars: int = MAX_OUTPUT_CHARS
    last_call: CallInfo | None = None
    _views: dict[str, FileView] = field(default_factory=dict, repr=False)

    def file_view(self, path: str) -> FileView:
        safe = ensure_safe_path(path)

        if safe not in self._views:
            if len(self._views) >= 64:
                self._views.clear()
            self._views[safe] = build_file_view(
                self.source.read_file(self.commit, safe)
            )

        return self._views[safe]

    def require_index(self) -> None:
        row = self.index.connection.execute(
            "SELECT 1 FROM indexed_files WHERE repo_id = ? AND commit_sha = ? LIMIT 1",
            (self.repo, self.commit),
        ).fetchone()

        if row is None:
            raise IndexMissing

    def build_index(self) -> None:
        LocalCodeIndexer(self.index).build(self.repo, self.commit, self.source)


def clip(text: str, limit: int = MAX_SNIPPET_CHARS) -> str:
    return text if len(text) <= limit else text[:limit]


def clip_line(text: str, limit: int = MAX_LINE_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + " [truncated]"


def json_size(data: object) -> int:
    return len(json.dumps(data, ensure_ascii=False))


def fit_lines(
    lines: list[str],
    max_lines: int,
    max_chars: int,
    overhead: int = 0,
) -> int:
    """Quantas linhas cabem nos limites de linhas e de caracteres (mínimo 1)."""
    used = overhead
    count = 0

    for line in lines[:max_lines]:
        used += len(json.dumps(line, ensure_ascii=False)) + 2
        if count >= 1 and used > max_chars:
            break
        count += 1

    return count


def clip_lines(
    lines: list[str],
    max_lines: int,
    max_chars: int,
    overhead: int = 0,
) -> list[str]:
    """As primeiras linhas que cabem nos limites (ao menos uma, se houver)."""
    return lines[: fit_lines(lines, max_lines, max_chars, overhead)]


class Tool:
    name: ClassVar[str]
    args_schema: ClassVar[type[ToolArgs]]

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        raise NotImplementedError


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value).text
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    return value


def _validation_message(exc: ValidationError) -> str:
    # Sem `input`: o valor recebido pode conter texto arbitrário.
    details = [
        f"{'.'.join(str(part) for part in error['loc']) or 'args'}: {error['msg']}"
        for error in exc.errors()
    ]
    return "; ".join(details)[:300]


_CURSOR = re.compile(r"^[0-9]{1,9}$")


class ToolRunner:
    def __init__(self, tools: list[Tool]) -> None:
        self.tools = {tool.name: tool for tool in tools}

    def call(
        self,
        ctx: ToolContext,
        name: str,
        raw_args: dict[str, Any] | None = None,
    ) -> ToolResult:
        result, cache_hit = self._execute(ctx, name, raw_args or {})
        code = error_code(result)
        counted = not cache_hit and (result.ok or code in COUNTED_ERRORS)

        if counted:
            ctx.usage.tool_calls += 1

        ctx.last_call = CallInfo(name, cache_hit, counted, code)
        return result

    def _execute(
        self,
        ctx: ToolContext,
        name: str,
        raw_args: dict[str, Any],
    ) -> tuple[ToolResult, bool]:
        tool = self.tools.get(name)

        if tool is None:
            return error_result(INVALID_ARGS, "unknown tool"), False

        try:
            args = tool.args_schema.model_validate(raw_args)
        except ValidationError as exc:
            return error_result(INVALID_ARGS, _validation_message(exc)), False

        # O cache guarda o resultado completo; o cursor só fatia a saída.
        paged = isinstance(args, PagedArgs)
        cursor = args.cursor if isinstance(args, PagedArgs) else None
        cache_args = args.model_dump(exclude={"cursor"} if paged else set())
        key = ToolCache.make_key(name, cache_args, ctx.commit, ctx.max_output_chars)

        cached = ctx.cache.get(key)
        cache_hit = cached is not None
        result = cached

        if result is None:
            if ctx.usage.tool_calls >= ctx.budget.max_tool_calls:
                return error_result(
                    BUDGET_EXCEEDED, "tool call budget exhausted"
                ), False

            result = self._run_tool(ctx, tool, args)

            if not result.ok:
                return result, False

            result = result.model_copy(update={"data": _redact_value(result.data)})
            ctx.cache.put(key, ctx.commit, name, result)

        try:
            return self._finalize(ctx, result, cursor), cache_hit
        except ToolError as exc:
            return error_result(exc.code, exc.message), False

    def _run_tool(self, ctx: ToolContext, tool: Tool, args: ToolArgs) -> ToolResult:
        try:
            try:
                return tool.run(ctx, args)
            except IndexMissing:
                ctx.build_index()
                return tool.run(ctx, args)
        except IndexMissing:
            return error_result(INDEX_MISSING, "index is not available for the commit")
        except ToolError as exc:
            return error_result(exc.code, exc.message)
        except PermissionError:
            return error_result(PATH_DENIED, "path is not allowed")
        except FileNotFoundError:
            return error_result(NOT_FOUND, "file does not exist at the commit")
        except ValueError as exc:
            if "UTF-8" in str(exc):
                return error_result(PATH_DENIED, "file is not text")
            return error_result(INVALID_ARGS, "invalid argument")
        except Exception:
            # Falha de ferramenta nunca derruba a análise (RNF-20); sem detalhes
            # internos (caminhos do host, stack trace) na resposta.
            return error_result(TOOL_FAILURE, "tool failed unexpectedly")

    def _finalize(
        self,
        ctx: ToolContext,
        result: ToolResult,
        cursor: str | None,
    ) -> ToolResult:
        result = result.model_copy(deep=True)
        data = result.data
        truncated = result.truncated
        candidates = result.candidates

        if isinstance(data, dict) and isinstance(data.get("items"), list):
            items: list[dict[str, Any]] = data["items"]
            offset = 0

            if cursor is not None:
                if not _CURSOR.match(cursor) or int(cursor) > len(items):
                    raise ToolError(INVALID_ARGS, "invalid cursor")
                offset = int(cursor)

            # Reserva o que será acrescentado depois: cursor, `ev_id` por item
            # e o id repetido em `evidence_ids`.
            base_size = json_size(
                {**data, "items": [], "evidence_ids": [], "cursor": "9" * 9}
            )
            kept = 0
            used = base_size

            for item in items[offset:]:
                bare = {key: value for key, value in item.items() if key != "_cand"}
                size = json_size(bare) + 2
                if "_cand" in item:
                    size += EVIDENCE_REFERENCE_OVERHEAD
                if kept >= 1 and used + size > ctx.max_output_chars:
                    break
                used += size
                kept += 1

            page = items[offset : offset + kept]
            referenced = {
                item["_cand"] for item in items if isinstance(item.get("_cand"), int)
            }
            selected = {
                index for index in range(len(candidates)) if index not in referenced
            }
            selected |= {
                item["_cand"] for item in page if isinstance(item.get("_cand"), int)
            }

            registered: dict[int, str] = {}
            for index in sorted(selected):
                registered[index] = ctx.evidence.register(candidates[index]).id

            for item in page:
                index = item.pop("_cand", None)
                if isinstance(index, int):
                    item["ev_id"] = registered[index]

            data["items"] = page
            if registered:
                data["evidence_ids"] = list(dict.fromkeys(registered.values()))

            if offset + kept < len(items):
                truncated = True
                data["cursor"] = str(offset + kept)
        elif isinstance(data, dict):
            registered_ids = [ctx.evidence.register(item).id for item in candidates]
            if registered_ids:
                data["evidence_ids"] = list(dict.fromkeys(registered_ids))

        return ToolResult(ok=True, data=data, truncated=truncated)
