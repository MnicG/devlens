"""Histórico e diff: git_log, git_blame, git_show, changed_files, get_diff
(docs/05, seções 2.1 e 2.3). Evidências do tipo `commit`."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated, Any

from app.evidence.candidates import commit_candidate
from app.gateway.redaction import redact_text
from app.schemas.contracts import Evidence, ToolResult
from app.tools.base import (
    INVALID_ARGS,
    MAX_SNIPPET_CHARS,
    NOT_FOUND,
    PATH_DENIED,
    SHA_ARG_PATTERN,
    PagedArgs,
    Tool,
    ToolArgs,
    ToolContext,
    ToolError,
    clip,
    clip_line,
    clip_lines,
    ensure_safe_path,
    json_size,
)
from pydantic import Field

MAX_LOG = 20
MAX_BLAME_LINES = 60
MAX_SHOW_DIFF_LINES = 200
MAX_DIFF_LINES = 400
MAX_CHANGED_FILES = 100

OMITTED = "[omitted: file is binary, too large or not readable]"


def _commit_item(entry: dict[str, str]) -> dict[str, Any]:
    return {
        "sha": entry["sha"],
        "autor": entry["autor"],
        "data": entry["data"],
        "assunto": clip(entry["assunto"], MAX_SNIPPET_CHARS),
    }


def _commit_candidate(
    ctx: ToolContext,
    entry: dict[str, str],
    **location: Any,
) -> Evidence:
    return commit_candidate(
        repo=ctx.repo,
        commit=ctx.commit,
        sha=entry["sha"],
        summary=f"{entry['sha'][:7]} {entry['assunto']}",
        **location,
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class GitLogArgs(PagedArgs):
    path: Annotated[str | None, Field(min_length=1, max_length=500)] = None
    since: Annotated[str | None, Field(min_length=1, max_length=40)] = None
    limit: Annotated[int, Field(ge=1, le=MAX_LOG)] = 10


class GitLog(Tool):
    name = "git_log"
    args_schema = GitLogArgs

    def run(self, ctx: ToolContext, args: GitLogArgs) -> ToolResult:
        path = ensure_safe_path(args.path) if args.path else None
        since: datetime | None = None

        if args.since is not None:
            try:
                since = datetime.fromisoformat(args.since)
            except ValueError as exc:
                raise ToolError(INVALID_ARGS, "since must be an ISO 8601 date") from exc

        entries = ctx.source.git_log(ctx.commit, path, MAX_LOG)

        if since is not None:
            entries = [
                entry
                for entry in entries
                if _aware(datetime.fromisoformat(entry["data"])) >= _aware(since)
            ]

        entries = entries[: args.limit]
        candidates = [_commit_candidate(ctx, entry) for entry in entries]
        items = [
            {**_commit_item(entry), "_cand": index}
            for index, entry in enumerate(entries)
        ]

        return ToolResult(ok=True, data={"items": items}, candidates=candidates)


class GitBlameArgs(PagedArgs):
    path: Annotated[str, Field(min_length=1, max_length=500)]
    line_start: Annotated[int, Field(ge=1)]
    line_end: Annotated[int, Field(ge=1)]


def _subject_of(ctx: ToolContext, sha: str) -> str:
    entries = ctx.source.git_log(sha, None, 1)
    return entries[0]["assunto"] if entries else ""


class GitBlame(Tool):
    name = "git_blame"
    args_schema = GitBlameArgs

    def run(self, ctx: ToolContext, args: GitBlameArgs) -> ToolResult:
        path = ensure_safe_path(args.path)

        if args.line_end < args.line_start:
            raise ToolError(INVALID_ARGS, "line_end must not precede line_start")

        if args.line_end - args.line_start + 1 > MAX_BLAME_LINES:
            raise ToolError(INVALID_ARGS, f"at most {MAX_BLAME_LINES} lines per call")

        view = ctx.file_view(path)

        if args.line_end > len(view.lines):
            raise ToolError(INVALID_ARGS, "line range is beyond the end of the file")

        blamed = ctx.source.git_blame(ctx.commit, path, args.line_start, args.line_end)

        candidates: list[Evidence] = []
        by_sha: dict[str, int] = {}
        items: list[dict[str, Any]] = []

        for row in blamed:
            sha = str(row["sha"])
            line = int(row["linha"])

            if sha not in by_sha:
                candidates.append(
                    _commit_candidate(
                        ctx,
                        {"sha": sha, "assunto": _subject_of(ctx, sha)},
                        path=path,
                        line_start=args.line_start,
                        line_end=args.line_end,
                    )
                )
                by_sha[sha] = len(candidates) - 1

            items.append(
                {
                    "linha": line,
                    "sha": sha,
                    "autor": row["autor"],
                    # Conteúdo vem do arquivo já redigido, não do blame bruto.
                    "conteudo": clip(view.redacted[line - 1], MAX_SNIPPET_CHARS),
                    "_cand": by_sha[sha],
                }
            )

        return ToolResult(ok=True, data={"items": items}, candidates=candidates)


def _readable(ctx: ToolContext, path: str, commits: tuple[str, ...]) -> bool:
    """O arquivo é texto legível (<= 1 MB) em algum dos commits?

    `False` só quando existe e é ilegível; arquivo ausente nos commits (criado
    ou removido) não é bloqueado, pois não há conteúdo a esconder.
    """
    for commit in commits:
        try:
            ctx.source.read_file(commit, path)
            return True
        except FileNotFoundError:
            continue
        except (PermissionError, ValueError):
            return False

    return True


def _header_path(header: str) -> str:
    rest = header[len("diff --git a/") :]
    return rest[: (len(rest) - 3) // 2] if len(rest) >= 3 else rest


def _drop_unreadable_sections(
    ctx: ToolContext, diff_text: str, commits: tuple[str, ...]
) -> str:
    """Troca o diff de arquivos binários, grandes ou sensíveis por um aviso.

    As regras de leitura de arquivo (1 MB, UTF-8, nomes sensíveis) valem
    também para o diff, que de outro modo contornaria `read_range`.
    """
    output: list[str] = []
    section: list[str] = []

    def flush() -> None:
        if not section:
            return
        if _readable(ctx, _header_path(section[0]), commits):
            output.extend(section)
        else:
            output.extend([section[0], OMITTED])

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            flush()
            section = [line]
        elif section:
            section.append(line)
        else:
            output.append(line)

    flush()
    return "\n".join(output)


def _diff_lines(ctx: ToolContext, base: str, head: str, path: str | None) -> list[str]:
    if path is not None and not _readable(ctx, path, (head, base)):
        raise ToolError(PATH_DENIED, "file is binary, too large or not readable")

    text = ctx.source.diff(base, head, path)

    if path is None:
        text = _drop_unreadable_sections(ctx, text, (head, base))

    return [clip_line(line) for line in redact_text(text).text.splitlines()]


class GitShowArgs(ToolArgs):
    sha: Annotated[str, Field(pattern=SHA_ARG_PATTERN)]
    path: Annotated[str | None, Field(min_length=1, max_length=500)] = None


class GitShow(Tool):
    name = "git_show"
    args_schema = GitShowArgs

    def run(self, ctx: ToolContext, args: GitShowArgs) -> ToolResult:
        path = ensure_safe_path(args.path) if args.path else None

        try:
            full = ctx.source.resolve_commit(args.sha)
        except ValueError as exc:
            raise ToolError(NOT_FOUND, "commit does not exist") from exc

        entry = ctx.source.git_log(full, None, 1)[0]

        try:
            parent: str | None = ctx.source.resolve_commit(f"{full}^")
        except ValueError:
            parent = None  # commit raiz: sem diff contra o pai

        diff_lines = _diff_lines(ctx, parent, full, path) if parent else []

        item = _commit_item(entry)
        overhead = json_size({**item, "diff": ""}) + 200
        shown = clip_lines(
            diff_lines, MAX_SHOW_DIFF_LINES, ctx.max_output_chars, overhead
        )

        data = {**item, "diff": "\n".join(shown)}

        return ToolResult(
            ok=True,
            data=data,
            truncated=len(shown) < len(diff_lines),
            candidates=[_commit_candidate(ctx, entry)],
        )


_RANGE = re.compile(r"^([^.\s][^\s]*?)\.\.([^.\s][^\s]*)$")


def _resolve_range(ctx: ToolContext, ref: str) -> tuple[str, str]:
    match = _RANGE.match(ref)

    if match is None:
        raise ToolError(INVALID_ARGS, "ref must be in the form base..head")

    try:
        return (
            ctx.source.resolve_commit(match.group(1)),
            ctx.source.resolve_commit(match.group(2)),
        )
    except ValueError as exc:
        raise ToolError(NOT_FOUND, "commit does not exist") from exc


class ChangedFilesArgs(PagedArgs):
    ref: Annotated[str, Field(min_length=3, max_length=200)]


def parse_changed_files(diff_text: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_hunk = False

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            current = {
                "path": _header_path(line),
                "status": "modified",
                "additions": 0,
                "deletions": 0,
            }
            files.append(current)
            in_hunk = False
        elif current is None:
            continue
        elif line.startswith("new file mode"):
            current["status"] = "added"
        elif line.startswith("deleted file mode"):
            current["status"] = "deleted"
        elif line.startswith("rename to "):
            current["status"] = "renamed"
            current["path"] = line[len("rename to ") :]
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk and line.startswith("+"):
            current["additions"] += 1
        elif in_hunk and line.startswith("-"):
            current["deletions"] += 1

    return files


class ChangedFiles(Tool):
    name = "changed_files"
    args_schema = ChangedFilesArgs

    def run(self, ctx: ToolContext, args: ChangedFilesArgs) -> ToolResult:
        base, head = _resolve_range(ctx, args.ref)
        files = parse_changed_files(ctx.source.diff(base, head, None))

        return ToolResult(
            ok=True,
            data={"items": files[:MAX_CHANGED_FILES]},
            truncated=len(files) > MAX_CHANGED_FILES,
        )


class GetDiffArgs(ToolArgs):
    ref: Annotated[str, Field(min_length=3, max_length=200)]
    path: Annotated[str | None, Field(min_length=1, max_length=500)] = None
    cursor: Annotated[str | None, Field(max_length=20)] = None


class GetDiff(Tool):
    name = "get_diff"
    args_schema = GetDiffArgs

    def run(self, ctx: ToolContext, args: GetDiffArgs) -> ToolResult:
        base, head = _resolve_range(ctx, args.ref)
        path = ensure_safe_path(args.path) if args.path else None

        lines = _diff_lines(ctx, base, head, path)

        offset = 0
        if args.cursor is not None:
            if not args.cursor.isdigit() or int(args.cursor) > len(lines):
                raise ToolError(INVALID_ARGS, "invalid cursor")
            offset = int(args.cursor)

        shown = clip_lines(lines[offset:], MAX_DIFF_LINES, ctx.max_output_chars, 200)
        next_offset = offset + len(shown)

        data: dict[str, Any] = {
            "line_start": offset + 1,
            "diff": "\n".join(shown),
        }

        truncated = next_offset < len(lines)
        if truncated:
            data["cursor"] = str(next_offset)

        return ToolResult(ok=True, data=data, truncated=truncated)
