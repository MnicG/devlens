from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from typing import Protocol

from app.index.parser import parse_source
from app.index.store import IndexStore


class IndexableSource(Protocol):
    """O que o indexador precisa de uma fonte (satisfeito por LocalRepoSource)."""

    def list_files(self, commit: str) -> list[str]: ...

    def read_file(self, commit: str, path: str) -> str: ...


EXCLUDED_DIRECTORIES = {
    ".git",
    "node_modules",
    "dist",
    "build",
    "vendor",
    "__pycache__",
    ".venv",
    "venv",
}

GENERATED_SUFFIXES = (
    ".min.js",
    ".min.css",
    ".map",
    ".lock",
)

CHUNK_SIZE = 40
CHUNK_OVERLAP = 5


class LocalCodeIndexer:
    def __init__(self, store: IndexStore) -> None:
        self.store = store

    def _should_skip(self, path: str) -> bool:
        parts = PurePosixPath(path).parts

        if any(part in EXCLUDED_DIRECTORIES for part in parts):
            return True

        if path.endswith(GENERATED_SUFFIXES):
            return True

        return path.endswith((".pyc", ".pyo"))

    def _make_chunks(
        self,
        content: str,
        symbols: list[dict[str, object]] | None = None,
    ) -> list[dict[str, object]]:
        lines = content.splitlines()
        chunks: list[dict[str, object]] = []

        boundaries = {
            int(str(item["line_start"])) - 1
            for item in symbols or []
            if item["kind"] in {"function", "class"}
        }

        start = 0

        while start < len(lines):
            end = min(start + CHUNK_SIZE, len(lines))

            if end < len(lines):
                candidates = [
                    boundary
                    for boundary in boundaries
                    if end - 10 <= boundary < end and boundary > start + CHUNK_OVERLAP
                ]

                if candidates:
                    end = max(candidates)

            chunks.append(
                {
                    "line_start": start + 1,
                    "line_end": end,
                    "content": "\n".join(lines[start:end]),
                }
            )

            if end == len(lines):
                break

            start = end - CHUNK_OVERLAP

        return chunks

    def build(
        self,
        repo_id: str,
        commit_sha: str,
        source: IndexableSource,
    ) -> dict[str, int]:
        paths = source.list_files(commit_sha)

        indexed = 0
        unchanged = 0
        skipped = 0

        for path in paths:
            if self._should_skip(path):
                skipped += 1
                continue

            try:
                content = source.read_file(commit_sha, path)
            except ValueError as exc:
                if str(exc) != "File is not valid UTF-8 text":
                    raise
                skipped += 1
                continue
            except (PermissionError, FileNotFoundError, UnicodeDecodeError):
                skipped += 1
                continue

            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

            previous_hash = self.store.file_hash(
                repo_id,
                commit_sha,
                path,
            )

            if previous_hash == content_hash:
                unchanged += 1
                continue

            matching_commit = self.store.find_matching_file(
                repo_id=repo_id,
                path=path,
                content_hash=content_hash,
                exclude_commit=commit_sha,
            )

            if matching_commit is not None and self.store.copy_file_index(
                repo_id=repo_id,
                source_commit=matching_commit,
                target_commit=commit_sha,
                path=path,
                content_hash=content_hash,
            ):
                unchanged += 1
                continue

            parsed = parse_source(path, content)

            symbols = [
                {
                    "name": item.name,
                    "kind": item.kind,
                    "line_start": item.line_start,
                    "line_end": item.line_end,
                }
                for item in parsed.symbols
            ]

            references = [
                {
                    "name": item.name,
                    "kind": item.kind,
                    "line": item.line,
                }
                for item in parsed.references
            ]

            self.store.replace_file_index(
                repo_id=repo_id,
                commit_sha=commit_sha,
                path=path,
                content_hash=content_hash,
                symbols=symbols,
                references=references,
                chunks=self._make_chunks(content, symbols),
            )

            indexed += 1

        return {
            "indexed": indexed,
            "unchanged": unchanged,
            "skipped": skipped,
        }
