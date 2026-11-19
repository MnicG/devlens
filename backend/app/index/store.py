from __future__ import annotations

import sqlite3
from pathlib import Path


class IndexStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        self.connection = sqlite3.connect(self.database_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS indexed_files (
                repo_id TEXT NOT NULL,
                commit_sha TEXT NOT NULL,
                path TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                PRIMARY KEY (repo_id, commit_sha, path)
            );

            CREATE TABLE IF NOT EXISTS symbols (
                id INTEGER PRIMARY KEY,
                repo_id TEXT NOT NULL,
                commit_sha TEXT NOT NULL,
                path TEXT NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                line_start INTEGER NOT NULL,
                line_end INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_symbols_lookup
            ON symbols (repo_id, commit_sha, name);

            CREATE TABLE IF NOT EXISTS references_index (
                id INTEGER PRIMARY KEY,
                repo_id TEXT NOT NULL,
                commit_sha TEXT NOT NULL,
                path TEXT NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                line INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_references_lookup
            ON references_index (repo_id, commit_sha, name);

            CREATE VIRTUAL TABLE IF NOT EXISTS code_chunks
            USING fts5(
                repo_id UNINDEXED,
                commit_sha UNINDEXED,
                path UNINDEXED,
                line_start UNINDEXED,
                line_end UNINDEXED,
                content
            );
            """
        )
        self.connection.commit()

    def file_hash(
        self,
        repo_id: str,
        commit_sha: str,
        path: str,
    ) -> str | None:
        row = self.connection.execute(
            """
            SELECT content_hash
            FROM indexed_files
            WHERE repo_id = ? AND commit_sha = ? AND path = ?
            """,
            (repo_id, commit_sha, path),
        ).fetchone()

        return row["content_hash"] if row else None

    def replace_file_index(
        self,
        repo_id: str,
        commit_sha: str,
        path: str,
        content_hash: str,
        symbols: list[dict[str, object]],
        references: list[dict[str, object]],
        chunks: list[dict[str, object]],
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                DELETE FROM symbols
                WHERE repo_id = ? AND commit_sha = ? AND path = ?
                """,
                (repo_id, commit_sha, path),
            )
            self.connection.execute(
                """
                DELETE FROM references_index
                WHERE repo_id = ? AND commit_sha = ? AND path = ?
                """,
                (repo_id, commit_sha, path),
            )
            self.connection.execute(
                """
                DELETE FROM code_chunks
                WHERE repo_id = ? AND commit_sha = ? AND path = ?
                """,
                (repo_id, commit_sha, path),
            )

            self.connection.execute(
                """
                INSERT INTO indexed_files
                    (repo_id, commit_sha, path, content_hash)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(repo_id, commit_sha, path)
                DO UPDATE SET content_hash = excluded.content_hash
                """,
                (repo_id, commit_sha, path, content_hash),
            )

            self.connection.executemany(
                """
                INSERT INTO symbols
                    (repo_id, commit_sha, path, name, kind,
                     line_start, line_end)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        repo_id,
                        commit_sha,
                        path,
                        item["name"],
                        item["kind"],
                        item["line_start"],
                        item["line_end"],
                    )
                    for item in symbols
                ],
            )

            self.connection.executemany(
                """
                INSERT INTO references_index
                    (repo_id, commit_sha, path, name, kind, line)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        repo_id,
                        commit_sha,
                        path,
                        item["name"],
                        item["kind"],
                        item["line"],
                    )
                    for item in references
                ],
            )

            self.connection.executemany(
                """
                INSERT INTO code_chunks
                    (repo_id, commit_sha, path, line_start, line_end, content)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        repo_id,
                        commit_sha,
                        path,
                        item["line_start"],
                        item["line_end"],
                        item["content"],
                    )
                    for item in chunks
                ],
            )

    def find_matching_file(
        self,
        repo_id: str,
        path: str,
        content_hash: str,
        exclude_commit: str,
    ) -> str | None:
        row = self.connection.execute(
            """
            SELECT commit_sha
            FROM indexed_files
            WHERE repo_id = ?
                AND path = ?
                AND content_hash = ?
                AND commit_sha != ?
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (repo_id, path, content_hash, exclude_commit),
        ).fetchone()

        return row["commit_sha"] if row else None

    def copy_file_index(
        self,
        repo_id: str,
        source_commit: str,
        target_commit: str,
        path: str,
        content_hash: str,
    ) -> bool:
        existing = self.connection.execute(
            """
            SELECT 1
            FROM indexed_files
            WHERE repo_id = ? AND commit_sha = ?
                AND path = ? AND content_hash = ?
            """,
            (repo_id, source_commit, path, content_hash),
        ).fetchone()

        if existing is None:
            return False

        with self.connection:
            self.connection.execute(
                """
                INSERT INTO indexed_files
                    (repo_id, commit_sha, path, content_hash)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(repo_id, commit_sha, path)
                DO UPDATE SET content_hash = excluded.content_hash
                """,
                (repo_id, target_commit, path, content_hash),
            )

            for table, columns in (
                (
                    "symbols",
                    "name, kind, line_start, line_end",
                ),
                (
                    "references_index",
                    "name, kind, line",
                ),
            ):
                self.connection.execute(
                    f"""
                    DELETE FROM {table}
                    WHERE repo_id = ? AND commit_sha = ? AND path = ?
                    """,
                    (repo_id, target_commit, path),
                )
                self.connection.execute(
                    f"""
                    INSERT INTO {table}
                        (repo_id, commit_sha, path, {columns})
                    SELECT repo_id, ?, path, {columns}
                    FROM {table}
                    WHERE repo_id = ? AND commit_sha = ? AND path = ?
                    """,
                    (target_commit, repo_id, source_commit, path),
                )

            self.connection.execute(
                """
                DELETE FROM code_chunks
                WHERE repo_id = ? AND commit_sha = ? AND path = ?
                """,
                (repo_id, target_commit, path),
            )
            self.connection.execute(
                """
                INSERT INTO code_chunks
                    (repo_id, commit_sha, path, line_start, line_end, content)
                SELECT repo_id, ?, path, line_start, line_end, content
                FROM code_chunks
                WHERE repo_id = ? AND commit_sha = ? AND path = ?
                """,
                (target_commit, repo_id, source_commit, path),
            )

        return True

    def close(self) -> None:
        self.connection.close()
